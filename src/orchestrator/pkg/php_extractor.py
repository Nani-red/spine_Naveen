"""PHP front-end for the PKG extractor (9th language).

Maps PHP source onto the same universal ``facts`` vocabulary the Python/Java/TS/C#/C/
C++/Go extractors use. Parsing is via tree-sitter (the ``language_php_only`` grammar —
pure PHP, no HTML host layer), an OPTIONAL dependency: install the ``php`` extra
(``uv pip install 'synaptixs-spine[php]'``). The import is lazy so the base install
stays stdlib-only and importing this module never fails.

See ``docs/specs/php-support-roadmap.md`` for the design (decisions D0-D8).

**P1 (comprehension):** ``Module``, ``Type`` (class / interface / trait / enum),
``Function`` (free function / method), ``Field`` (property / promoted ctor param / class
constant / enum case); ``IMPORTS`` (``use``, and a literal ``require``/``include``
target — D7), ``CONTAINS``, and ``IMPLEMENTS`` (``extends`` / ``implements`` / a trait
``use`` inside a class body — D3) edges.

**P2 (CALLS, §3.2):** two passes per file — collect a resolver table (the `use` map,
current namespace, this file's declared types/functions), then resolve six call shapes
precision-first: ``$this->m()`` / ``self::``/``static::`` (sibling method, incl. a
same-file trait's method when the class doesn't override it), ``parent::m()`` (the base
class, only when its resolution isn't an unverified guess), ``new X()`` (the `Type`
node, guess-tolerant like `IMPLEMENTS` — `finalize` repoints a wrong one), ``X::m()``
(same rule as `parent::`), and a bare ``f()`` (same-file or `use function`-imported
only — PHP's global-namespace fallback for functions is never guessed; see
``_scan_calls_in``). Everything else — ``$obj->m()`` on an untyped receiver,
``$obj->$name()``, `call_user_func`, a qualified (non-fully) function call — is
deliberately unresolved: precision-first, no invention.

Node ids are namespace-qualified with dots (``php:App.Http.Controllers.Order`` — D2:
the backslash form breaks the dotted-prefix import join, the doc→symbol binder, and
every id-carrying UI url). A file with no namespace keys on its repo-relative path
instead (``php:inc/legacy.php``) — the common shape for non-namespaced WordPress-style
PHP, and also what makes D7's literal ``require`` targets path-suffix-matchable against
real modules, the way a C ``#include`` is.

``.blade.php`` (and any other multi-extension template) is skipped by filename inside
``extract`` (D4): ``Path.suffix`` of ``x.blade.php`` is ``.php``, so the dispatcher
would otherwise hand every Laravel view to this front-end and parse template markup as
PHP source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# The name resolver and the collected-type record are shared with php_routes.py and
# php_orm.py, so they live in a leaf module neither side imports the other for.
from orchestrator.pkg.php_names import (
    _join,
    _resolve_type_name,
    _text,
    _to_dotted,
    _TypeRec,
    _use_declaration_targets,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w\\]+)", re.M)

_TYPE_DECLS = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
    }
)

# Reserved words that stand for a class without naming one. `_resolve_type_name` must
# never see them: resolved as names they become `php:NS.self`, which nothing declares.
_RELATIVE_SCOPES = frozenset({"self", "static", "parent"})

# `require`/`require_once`/`include`/`include_once` — all four read the same shape
# (a single expression argument), so one handler covers them (D7).
_REQUIRE_KINDS = frozenset(
    {
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    }
)


@dataclass
class _FuncRec:
    """A collected top-level (namespaced) function — free-function CALLS resolution."""

    func_id: str
    name: str
    namespace: str
    use_map: dict[str, str]
    use_func_map: dict[str, str]
    node: TSNode


class PhpExtractor:
    """PHP front-end (tree-sitter). Install the ``php`` extra to use it."""

    language: str = "php"
    suffixes: tuple[str, ...] = (".php",)

    def module_name(self, path: Path, root: Path) -> str:
        # D2: namespace-keyed, dotted. No namespace -> the repo-relative path (also
        # what makes non-namespaced files' modules path-suffix-matchable for D7).
        if path.name.endswith(".blade.php"):
            return rel_module_name(path, root)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        m = _NAMESPACE_RE.search(text)
        return _to_dotted(m.group(1)) if m else rel_module_name(path, root)

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Repoint a dangling same-namespace *guess* to a bare-name external node —
        the same trade-off as the C# front-end's ``finalize`` (D2/D3), now covering
        both edge kinds that can carry one:

        - `IMPLEMENTS` (`extends`/`implements`/a trait `use`), and
        - `CALLS` to a `new X()` target (row 4 of §3.2 — a bare `Type` id, exactly the
          same shape as an `IMPLEMENTS` target).

        A target resolved through the `use` map (or already fully/qualified in source)
        is a claim read directly off the source, not invented, and already carries its
        own external node (added at emit time — see ``_add_implements`` and the
        `object_creation_expression` branch of ``_scan_calls_in``) — so it is never
        dangling and this loop never touches it. Only a same-namespace guess that
        turned out wrong has no node at all.

        This is safe to apply to *every* dangling `CALLS` edge without checking which
        row produced it: a call resolved to a *method* id (`X::m()`, `parent::m()`,
        rows 3/5) is never left dangling by construction — ``_scan_calls_in`` only
        emits those when the class-name resolution is verified (see its docstring), so
        by construction any dangling `php:` `CALLS` edge here is a `new X()` guess.
        """
        has_node = {n.id for n in batch.nodes}
        repointed = FactBatch()
        for node in batch.nodes:
            repointed.add_node(node)
        for edge in batch.edges:
            if (
                edge.kind not in (EdgeKind.IMPLEMENTS, EdgeKind.CALLS)
                or not edge.dst.startswith("php:")
                or edge.dst in has_node
            ):
                repointed.add_edge(edge)
                continue
            bare = edge.dst.rsplit(".", 1)[-1]
            target = f"php:{bare}"
            repointed.add_node(Node(target, NodeKind.TYPE, bare, "php", external=True))
            repointed.add_edge(Edge(edge.src, target, edge.kind, edge.provenance))
        return repointed

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        batch = FactBatch()
        if path.name.endswith(".blade.php"):
            return batch  # D4: a template, not PHP source — empty batch, dispatcher stays language-agnostic

        parser = _php_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        module_id = f"php:{module}" if module else "php:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "php", Provenance(rel, 1)))

        types: list[_TypeRec] = []
        free_funcs: list[_FuncRec] = []
        self._walk(
            tree.root_node.named_children, module_id, "", {}, {}, source, rel, batch, types, free_funcs
        )
        self._emit_requires(tree.root_node, module_id, source, rel, batch)

        # P2 — CALLS, once every declaration in the file is known (§3.2).
        local_type_ids = {t.type_id for t in types}
        _emit_calls(types, free_funcs, local_type_ids, source, rel, batch)

        # P3 — framework routes (§3.3). Local import: php_routes.py imports the
        # resolver helpers below, so a module-level import would be circular.
        from orchestrator.pkg.php_routes import emit_laravel_routes, scan_laravel_routes, scan_symfony_routes

        emit_laravel_routes(scan_laravel_routes(tree.root_node, source, rel), batch)
        scan_symfony_routes(types, source, rel, batch)

        # P4 — Eloquent/Doctrine entities (§3.4). Same local-import reason as php_routes.py.
        from orchestrator.pkg.php_orm import emit_entities

        emit_entities(types, source, rel, batch)
        return batch

    # ---- declarations -----------------------------------------------------

    def _walk(
        self,
        nodes: list[TSNode],
        module_id: str,
        namespace: str,
        use_map: dict[str, str],
        use_func_map: dict[str, str],
        source: bytes,
        rel: str,
        batch: FactBatch,
        types: list[_TypeRec],
        free_funcs: list[_FuncRec],
    ) -> None:
        """Walk top-level siblings, descending into a block-form ``namespace X { ... }``.

        A ``namespace_definition`` resets the current namespace and both `use` maps
        (PHP's `use` scoping is per-file, normally — but a braced multi-namespace file
        legitimately resets them per block; see the roadmap's gotchas). The semicolon
        form (``namespace X;``) has no body, so the reset simply applies to the rest of
        this sibling list — the `for` loop continues with the updated locals.
        """
        current_ns = namespace
        current_uses = use_map
        current_use_funcs = use_func_map
        for node in nodes:
            if node.type == "namespace_definition":
                name_node = node.child_by_field_name("name")
                current_ns = _to_dotted(_text(name_node, source)) if name_node is not None else ""
                current_uses = {}
                current_use_funcs = {}
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(
                        body.named_children,
                        module_id,
                        current_ns,
                        current_uses,
                        current_use_funcs,
                        source,
                        rel,
                        batch,
                        types,
                        free_funcs,
                    )
            elif node.type == "namespace_use_declaration":
                self._emit_uses(node, module_id, current_uses, current_use_funcs, source, rel, batch)
            elif node.type in _TYPE_DECLS:
                self._emit_type(
                    node,
                    module_id,
                    None,
                    current_ns,
                    current_uses,
                    current_use_funcs,
                    source,
                    rel,
                    batch,
                    types,
                )
            elif node.type == "function_definition":
                self._emit_function(
                    node,
                    module_id,
                    None,
                    current_ns,
                    current_uses,
                    current_use_funcs,
                    source,
                    rel,
                    batch,
                    free_funcs,
                )

    def _emit_uses(
        self,
        decl: TSNode,
        module_id: str,
        use_map: dict[str, str],
        use_func_map: dict[str, str],
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        line = decl.start_point[0] + 1
        for fqn_dotted, bind, kind in _use_declaration_targets(decl, source):
            if not fqn_dotted:
                continue
            tid = f"php:{fqn_dotted}"
            batch.add_node(Node(tid, NodeKind.MODULE, fqn_dotted, "php", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))
            # `use function` / `use const` share the syntax but not the class namespace —
            # a plain `use` feeds the type resolver, `use function` the CALLS resolver.
            if kind == "class" and bind:
                use_map[bind] = fqn_dotted
            elif kind == "function" and bind:
                use_func_map[bind] = fqn_dotted

    def _emit_type(
        self,
        node: TSNode,
        module_id: str,
        parent_type_id: str | None,
        namespace: str,
        use_map: dict[str, str],
        use_func_map: dict[str, str],
        source: bytes,
        rel: str,
        batch: FactBatch,
        types: list[_TypeRec],
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return  # anonymous class (`new class { ... }`) — no id, no node (§3.1)
        if parent_type_id is None:
            type_id = f"php:{_join(namespace, name)}"
            contains_parent = module_id
        else:
            type_id = f"{parent_type_id}.{name}"
            contains_parent = parent_type_id
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        batch.add_node(Node(type_id, NodeKind.TYPE, name, "php", Provenance(rel, line, end_line)))
        batch.add_edge(Edge(contains_parent, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))

        # extends (class base, or an interface's own `extends A, B`) + implements.
        bases = _base_type_names(node, source)
        for base in bases:
            self._add_implements(type_id, base, namespace, use_map, line, rel, batch)
        for iface in _interface_names(node, source):
            self._add_implements(type_id, iface, namespace, use_map, line, rel, batch)

        rec = _TypeRec(
            type_id=type_id,
            name=name,
            namespace=namespace,
            use_map=use_map,
            use_func_map=use_func_map,
            node=node,
            base_name=bases[0] if bases and node.type == "class_declaration" else None,
        )
        types.append(rec)

        body = node.child_by_field_name("body")
        if body is None:
            return
        self._emit_members(
            body, type_id, module_id, namespace, use_map, use_func_map, source, rel, batch, types, rec
        )

    def _emit_members(
        self,
        body: TSNode,
        type_id: str,
        module_id: str,
        namespace: str,
        use_map: dict[str, str],
        use_func_map: dict[str, str],
        source: bytes,
        rel: str,
        batch: FactBatch,
        types: list[_TypeRec],
        rec: _TypeRec,
    ) -> None:
        for member in body.named_children:
            mline = member.start_point[0] + 1
            if member.type == "use_declaration":  # trait use (D3) — conflict-resolution blocks ignored
                trait_names = _trait_use_names(member, source)
                rec.trait_names.extend(trait_names)
                for trait_name in trait_names:
                    self._add_implements(type_id, trait_name, namespace, use_map, mline, rel, batch)
            elif member.type == "const_declaration":
                for cname in _const_names(member, source):
                    self._add_member(type_id, cname, NodeKind.FIELD, mline, rel, batch)
            elif member.type == "property_declaration":
                for pname in _property_names(member, source):
                    self._add_member(type_id, pname, NodeKind.FIELD, mline, rel, batch)
                for pname, ptype in _typed_property_names(member, source):
                    rec.typed_props[pname] = ptype
            elif member.type == "method_declaration":
                mname = _field_text(member, "name", source)
                if mname:
                    mid = self._add_member(type_id, mname, NodeKind.FUNCTION, mline, rel, batch)
                    rec.methods.append((mname, mid, member))
                for pname in _promoted_param_names(member, source):
                    self._add_member(type_id, pname, NodeKind.FIELD, mline, rel, batch)
                for pname, ptype in _typed_promoted_param_names(member, source):
                    rec.typed_props[pname] = ptype
            elif member.type == "enum_case":
                cname = _field_text(member, "name", source)
                if cname:
                    self._add_member(type_id, cname, NodeKind.FIELD, mline, rel, batch)
            elif member.type in _TYPE_DECLS:  # nested type — rare, but CONTAINS should still be right
                self._emit_type(
                    member, module_id, type_id, namespace, use_map, use_func_map, source, rel, batch, types
                )

    def _emit_function(
        self,
        node: TSNode,
        module_id: str,
        parent_id: str | None,
        namespace: str,
        use_map: dict[str, str],
        use_func_map: dict[str, str],
        source: bytes,
        rel: str,
        batch: FactBatch,
        free_funcs: list[_FuncRec],
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return
        fid = f"php:{_join(namespace, name)}" if parent_id is None else f"{parent_id}.{name}"
        contains_parent = parent_id or module_id
        line = node.start_point[0] + 1
        batch.add_node(
            Node(fid, NodeKind.FUNCTION, name, "php", Provenance(rel, line, node.end_point[0] + 1))
        )
        batch.add_edge(Edge(contains_parent, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        if parent_id is None:  # only top-level (namespaced) functions are CALLS-scanned
            free_funcs.append(
                _FuncRec(
                    func_id=fid,
                    name=name,
                    namespace=namespace,
                    use_map=use_map,
                    use_func_map=use_func_map,
                    node=node,
                )
            )

    @staticmethod
    def _add_member(type_id: str, name: str, kind: NodeKind, line: int, rel: str, batch: FactBatch) -> str:
        mid = f"{type_id}.{name}"
        batch.add_node(Node(mid, kind, name, "php", Provenance(rel, line)))
        batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(rel, line)))
        return mid

    @staticmethod
    def _add_implements(
        type_id: str,
        name: str,
        namespace: str,
        use_map: dict[str, str],
        line: int,
        rel: str,
        batch: FactBatch,
    ) -> None:
        """One ``extends``/``implements``/trait-``use`` target: an explicit resolution
        (fully-qualified, or a `use` hit) gets its external node immediately, so
        `finalize` never mistakes a real out-of-repo symbol for a bad guess — see
        ``PhpExtractor.finalize``. A same-namespace guess gets no node here; it either
        lands on a sibling's own node once that sibling is emitted, or stays dangling
        for `finalize` to repoint."""
        dotted, is_guess = _resolve_type_name(name, namespace, use_map)
        target = f"php:{dotted}"
        if not is_guess:
            batch.add_node(Node(target, NodeKind.TYPE, dotted.rsplit(".", 1)[-1], "php", external=True))
        batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

    # ---- require / include (D7) --------------------------------------------

    def _emit_requires(self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
        """``IMPORTS`` to a literal ``require``/``include`` target — module-relative
        (POSIX) path, resolved against the importing file's own directory, D7's
        "literal only" discipline: a computed argument (a bare variable, a function
        call, anything but `__DIR__ . 'string'` or a bare string) yields nothing rather
        than a guess. Walks the whole file, not just the top level — legacy/WordPress
        code frequently guards a require inside an `if`."""
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type in _REQUIRE_KINDS:
                literal = _require_literal(node, source)
                if literal:
                    target_path = _resolve_require_target(rel, literal)
                    if target_path:
                        line = node.start_point[0] + 1
                        tid = f"php:{target_path}"
                        batch.add_node(Node(tid, NodeKind.MODULE, target_path, "php", external=True))
                        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))
                continue  # nothing inside a require's own argument matters further
            stack.extend(node.named_children)


# --- CALLS (P2, §3.2) --------------------------------------------------------


def _emit_calls(
    types: list[_TypeRec],
    free_funcs: list[_FuncRec],
    local_type_ids: set[str],
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """Resolve the six call shapes of §3.2, once every type/function in the file is
    known. File-local by design (D0/§2: PHP's class-name resolution needs no
    repo-wide symbol table) — the one whole-repo dependency, a `new X()`/`X::m()`
    guess that names a sibling declared in *another* file, is left to `finalize`
    (for the `Type`-shaped `new X()` case) or skipped outright (for a method-shaped
    `X::m()`/`parent::m()` guess — see ``_scan_calls_in``)."""
    local_funcs = {(f.namespace, f.name): f.func_id for f in free_funcs}
    types_by_id = {t.type_id: t for t in types}

    for rec in types:
        method_ids = _effective_methods(rec, types_by_id)
        base_target = None
        if rec.base_name is not None:
            dotted, is_guess = _resolve_type_name(rec.base_name, rec.namespace, rec.use_map)
            candidate = f"php:{dotted}"
            if (not is_guess) or (candidate in local_type_ids):
                base_target = candidate
        for _mname, mid, mnode in rec.methods:
            _scan_calls_in(
                mnode,
                mid,
                rec.namespace,
                rec.use_map,
                rec.use_func_map,
                method_ids,
                base_target,
                local_type_ids,
                local_funcs,
                rec.typed_props,
                _typed_params_of(mnode, source),
                source,
                rel,
                batch,
                self_type_id=rec.type_id,
            )

    for f in free_funcs:
        _scan_calls_in(
            f.node,
            f.func_id,
            f.namespace,
            f.use_map,
            f.use_func_map,
            {},
            None,
            local_type_ids,
            local_funcs,
            {},
            _typed_params_of(f.node, source),
            source,
            rel,
            batch,
        )


def _effective_methods(rec: _TypeRec, types_by_id: dict[str, _TypeRec]) -> dict[str, str]:
    """This type's own methods, plus a same-file `use`d trait's methods for any name
    it doesn't override (D3's `traits` corpus case: the override is the class's own
    `Function`, the trait's is the trait's — so the class's own dict entry must win).
    Cross-file trait flattening is a known gap: resolving a trait declared in another
    file needs the same whole-repo knowledge `finalize` has, which this per-file pass
    does not."""
    method_ids = {name: mid for name, mid, _ in rec.methods}
    for trait_name in rec.trait_names:
        dotted, is_guess = _resolve_type_name(trait_name, rec.namespace, rec.use_map)
        if is_guess and f"php:{dotted}" not in types_by_id:
            continue
        trait_rec = types_by_id.get(f"php:{dotted}")
        if trait_rec is None:
            continue
        for name, mid, _ in trait_rec.methods:
            method_ids.setdefault(name, mid)
    return method_ids


def _scan_calls_in(
    fn_node: TSNode,
    caller_id: str,
    namespace: str,
    use_map: dict[str, str],
    use_func_map: dict[str, str],
    method_ids: dict[str, str],
    base_target: str | None,
    local_type_ids: set[str],
    local_funcs: dict[tuple[str, str], str],
    typed_props: dict[str, str],
    typed_params: dict[str, str],
    source: bytes,
    rel: str,
    batch: FactBatch,
    *,
    self_type_id: str | None = None,
) -> None:
    """Walk one function/method body for the six P2 call shapes (§3.2 rows 1-6) plus
    P3's typed-receiver rows 7-8.

    ``self_type_id`` is the enclosing class (``None`` for a free function): it is what
    ``new self()`` / ``new static()`` name. Without it those two read as classes called
    ``self`` and ``static``, and ``finalize`` — seeing a target nothing declares —
    "repoints" them to one external ``Type`` per name that every factory method in the
    repository then appears to call. Reserved words are never class names.

    Three shapes are refused outright, because the source does not say what they call:
    a method named by a variable (``X::$m()``, ``$obj->$m()``, ``$this->p->$m()`` — the
    name child is a ``variable_name``, not a ``name``), ``new parent()`` with no verified
    base, and anything inside an anonymous class body, whose ``$this`` is the anonymous
    class and not the enclosing one (its methods have no node, so its calls have no
    caller either — the walk does not descend into it).

    Descends into closures/arrow functions too (their calls are attributed to the
    enclosing method/function, matching the C# front-end's precedent) — but never
    needs a shadowing guard, unlike C#/Python/Go: D8 is `NOT_APPLICABLE` for PHP
    because a local variable can never shadow a bare call (`$f()` and `f()` are
    different CST node shapes), and `$this`/`self`/`static`/`parent` are reserved
    words no local can rebind either.

    Method-shaped targets (`X::m()`, `parent::m()`) are only ever emitted **verified**
    — the class-name resolution is not an unverified guess (an explicit `use`/fully-
    qualified name, or a guess that matches a type declared in *this* file) — so they
    are never left dangling; `finalize` need not (and structurally cannot usefully)
    repoint a method id. An unverified guess is skipped outright: recall loss, zero
    invention. `new X()` (a `Type`-shaped target) is the one shape that tolerates a
    same-namespace guess, exactly like `IMPLEMENTS` — `finalize` repoints it if wrong.
    """
    body = fn_node.child_by_field_name("body")
    if body is None:
        return  # interface method / abstract signature — no body to scan
    stack = list(body.named_children)
    while stack:
        node = stack.pop()
        line = node.start_point[0] + 1

        if node.type == "member_call_expression":
            obj = node.child_by_field_name("object")
            name_node = node.child_by_field_name("name")
            # A `variable_name` here is `$obj->$m()` — the method is whatever `$m` holds
            # at runtime. An empty callee makes every branch below fall through.
            callee_name = (
                _text(name_node, source) if name_node is not None and name_node.type == "name" else ""
            )
            if (
                obj is not None
                and obj.type == "variable_name"
                and _text(obj, source) == "$this"
                and callee_name
            ):
                target = method_ids.get(callee_name)
                if target is not None:
                    batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))
            elif (
                obj is not None
                and obj.type == "member_access_expression"
                and callee_name
                and _is_this_property(obj, source)
            ):
                # `$this->prop->m()` — P3 row 7: the property's own declared type.
                prop_name = _text(obj.child_by_field_name("name"), source)
                ptype = typed_props.get(prop_name)
                if ptype is not None:
                    _emit_receiver_call(
                        caller_id, ptype, callee_name, namespace, use_map, local_type_ids, line, rel, batch
                    )
            elif (
                obj is not None
                and obj.type == "variable_name"
                and callee_name
                and _strip_sigil(_text(obj, source)) in typed_params
            ):
                # `$obj->m()` on a typed parameter — P3 row 8.
                ptype = typed_params[_strip_sigil(_text(obj, source))]
                _emit_receiver_call(
                    caller_id, ptype, callee_name, namespace, use_map, local_type_ids, line, rel, batch
                )
            # anything else ($obj->m() on an untyped receiver, $obj->$name(), …) — never:
            # precision-first, no guess.

        elif node.type == "scoped_call_expression":
            scope = node.child_by_field_name("scope")
            name_node = node.child_by_field_name("name")
            # `X::$m()` has a `variable_name` where the method name should be — skipped.
            if scope is not None and name_node is not None and name_node.type == "name":
                callee = _text(name_node, source)
                if scope.type == "relative_scope":
                    scope_text = _text(scope, source)
                    if scope_text in ("self", "static"):
                        target = method_ids.get(callee)
                        if target is not None:
                            batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))
                    elif scope_text == "parent" and base_target is not None:
                        method_target = f"{base_target}.{callee}"
                        batch.add_node(Node(method_target, NodeKind.FUNCTION, callee, "php", external=True))
                        batch.add_edge(Edge(caller_id, method_target, EdgeKind.CALLS, Provenance(rel, line)))
                elif scope.type in ("name", "qualified_name"):
                    dotted, is_guess = _resolve_type_name(_text(scope, source), namespace, use_map)
                    target_type = f"php:{dotted}"
                    if (not is_guess) or (target_type in local_type_ids):
                        method_target = f"{target_type}.{callee}"
                        batch.add_node(Node(method_target, NodeKind.FUNCTION, callee, "php", external=True))
                        batch.add_edge(Edge(caller_id, method_target, EdgeKind.CALLS, Provenance(rel, line)))
                    # else: an unverified same-namespace guess — skip (row 5's "skip
                    # when unresolved"; no finalize backstop exists for a method id).

        elif node.type == "object_creation_expression":
            name_node = next((c for c in node.named_children if c.type in ("name", "qualified_name")), None)
            first = node.named_children[0] if node.named_children else None
            relative = _text(first, source) if first is not None else ""
            if relative in _RELATIVE_SCOPES:
                # `new self()` / `new static()` name the enclosing class; `new parent()`
                # its verified base. Not a class name, so never resolved as one.
                target = base_target if relative == "parent" else self_type_id
                if target is not None:
                    batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))
            elif name_node is not None:  # else `new $class(...)` — dynamic, never guessed
                dotted, is_guess = _resolve_type_name(_text(name_node, source), namespace, use_map)
                target_type = f"php:{dotted}"
                if not is_guess:
                    batch.add_node(
                        Node(target_type, NodeKind.TYPE, dotted.rsplit(".", 1)[-1], "php", external=True)
                    )
                batch.add_edge(Edge(caller_id, target_type, EdgeKind.CALLS, Provenance(rel, line)))

        elif node.type == "function_call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "name":
                callee = _text(fn, source)
                target = local_funcs.get((namespace, callee))
                if target is None and callee in use_func_map:
                    target = f"php:{use_func_map[callee]}"
                    batch.add_node(Node(target, NodeKind.FUNCTION, callee, "php", external=True))
                if target is not None:
                    batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))
                # else: the global-fallback trap (§3.2) — `helper()` might mean
                # `\helper` at runtime. Never guessed; the `global_fallback` corpus
                # case exists to keep this honest.
            elif fn is not None and fn.type == "qualified_name" and _text(fn, source).startswith("\\"):
                # Fully qualified (`\helper()`) is unambiguous — not a guess, unlike
                # the bare form above.
                dotted = _to_dotted(_text(fn, source))
                target = f"php:{dotted}"
                batch.add_node(
                    Node(target, NodeKind.FUNCTION, dotted.rsplit(".", 1)[-1], "php", external=True)
                )
                batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))
            # A qualified (non-fully) function name, `$obj->$name()`, `call_user_func`,
            # a string/array callable — never: fabrication (§3.2's last row).

        if node.type != "anonymous_class":
            stack.extend(node.named_children)


def _is_this_property(member_access: TSNode, source: bytes) -> bool:
    """True for ``$this->prop`` — the object half of a `$this->prop->m()` chain."""
    obj = member_access.child_by_field_name("object")
    return obj is not None and obj.type == "variable_name" and _text(obj, source) == "$this"


def _emit_receiver_call(
    caller_id: str,
    type_name_raw: str,
    method_name: str,
    namespace: str,
    use_map: dict[str, str],
    local_type_ids: set[str],
    line: int,
    rel: str,
    batch: FactBatch,
) -> None:
    """P3's typed-receiver rows (7-8): resolve a declared type name to a method-shaped
    `CALLS` target, under the same **verified**-only rule as `X::m()`/`parent::m()` —
    no `finalize` backstop exists for a method id, so an unverified guess is skipped
    rather than risking a permanently dangling or wrong edge."""
    dotted, is_guess = _resolve_type_name(type_name_raw, namespace, use_map)
    target_type = f"php:{dotted}"
    if (not is_guess) or (target_type in local_type_ids):
        target = f"{target_type}.{method_name}"
        batch.add_node(Node(target, NodeKind.FUNCTION, method_name, "php", external=True))
        batch.add_edge(Edge(caller_id, target, EdgeKind.CALLS, Provenance(rel, line)))


# --- require/include literal detection (D7) ----------------------------------


def _require_literal(node: TSNode, source: bytes) -> str | None:
    """A literal (or `__DIR__ . 'literal'`) argument; ``None`` for anything computed."""
    args = node.named_children
    if not args:
        return None
    arg = args[0]
    if arg.type == "string":
        return _string_content(arg, source)
    if arg.type == "binary_expression":
        left = arg.child_by_field_name("left")
        right = arg.child_by_field_name("right")
        if (
            left is not None
            and right is not None
            and left.type == "name"
            and _text(left, source) == "__DIR__"
            and right.type == "string"
        ):
            return _string_content(right, source)
    return None


def _resolve_require_target(importing_rel: str, literal: str) -> str:
    """A repo-relative-ish POSIX path for the import-link path-suffix match (D7),
    resolved against the *importing* file's own directory (what `__DIR__` denotes)."""
    literal = literal.strip().lstrip("/")
    if not literal:
        return ""
    base = PurePosixPath(importing_rel).parent
    joined = (base / literal).parts
    parts: list[str] = []
    for part in joined:
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
        elif part in (".", ""):
            continue
        else:
            parts.append(part)
    return "/".join(parts)


# --- structural helpers -------------------------------------------------------


def _base_type_names(node: TSNode, source: bytes) -> list[str]:
    for child in node.named_children:
        if child.type == "base_clause":
            return [_text(c, source) for c in child.named_children if c.type in ("name", "qualified_name")]
    return []


def _interface_names(node: TSNode, source: bytes) -> list[str]:
    for child in node.named_children:
        if child.type == "class_interface_clause":
            return [_text(c, source) for c in child.named_children if c.type in ("name", "qualified_name")]
    return []


def _trait_use_names(node: TSNode, source: bytes) -> list[str]:
    return [_text(c, source) for c in node.named_children if c.type in ("name", "qualified_name")]


def _const_names(node: TSNode, source: bytes) -> list[str]:
    out: list[str] = []
    for child in node.named_children:
        if child.type != "const_element":
            continue
        name_node = child.named_children[0] if child.named_children else None
        if name_node is not None and name_node.type == "name":
            out.append(_text(name_node, source))
    return out


def _property_names(node: TSNode, source: bytes) -> list[str]:
    out: list[str] = []
    for child in node.named_children:
        if child.type != "property_element":
            continue
        var_node = child.child_by_field_name("name")
        if var_node is not None:
            out.append(_strip_sigil(_text(var_node, source)))
    return out


def _promoted_param_names(method: TSNode, source: bytes) -> list[str]:
    params = method.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for param in params.named_children:
        if param.type != "property_promotion_parameter":
            continue
        var_node = param.child_by_field_name("name")
        if var_node is not None:
            out.append(_strip_sigil(_text(var_node, source)))
    return out


def _type_name(type_node: TSNode | None, source: bytes) -> str | None:
    """A class-name type annotation's raw text — ``None`` for anything that isn't one
    (a primitive like `int`/`string`/`array`, a union/intersection type — ambiguous,
    so P3 skips rather than guesses which arm applies)."""
    if type_node is None:
        return None
    if type_node.type == "named_type":
        return _text(type_node, source)
    if type_node.type == "optional_type":  # `?Order` — unwrap the nullable marker
        inner = type_node.named_children[0] if type_node.named_children else None
        return _type_name(inner, source)
    return None


def _typed_property_names(node: TSNode, source: bytes) -> list[tuple[str, str]]:
    """``(name, raw_type)`` for each *typed* declared property in one `property_declaration`
    (P3's typed-receiver rule, §3.2 row 7)."""
    ptype = _type_name(node.child_by_field_name("type"), source)
    if ptype is None:
        return []
    return [(name, ptype) for name in _property_names(node, source)]


def _typed_promoted_param_names(method: TSNode, source: bytes) -> list[tuple[str, str]]:
    """``(name, raw_type)`` for each *typed* promoted ctor param — these become
    properties too, reachable via `$this->prop->m()` from any method, not just the
    constructor (P3's typed-receiver rule)."""
    params = method.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[tuple[str, str]] = []
    for param in params.named_children:
        if param.type != "property_promotion_parameter":
            continue
        var_node = param.child_by_field_name("name")
        ptype = _type_name(param.child_by_field_name("type"), source)
        if var_node is not None and ptype is not None:
            out.append((_strip_sigil(_text(var_node, source)), ptype))
    return out


def _typed_params_of(fn_node: TSNode, source: bytes) -> dict[str, str]:
    """``name -> raw_type`` for every *typed* parameter of one function/method (P3's
    typed-receiver rule, §3.2 row 8) — scoped to this one function, unlike
    ``typed_props`` which is shared across a whole type's methods."""
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return {}
    out: dict[str, str] = {}
    for param in params.named_children:
        if param.type not in ("simple_parameter", "property_promotion_parameter"):
            continue
        var_node = param.child_by_field_name("name")
        ptype = _type_name(param.child_by_field_name("type"), source)
        if var_node is not None and ptype is not None:
            out[_strip_sigil(_text(var_node, source))] = ptype
    return out


def _strip_sigil(name: str) -> str:
    """``$count`` -> ``count`` — kept out of both the id and ``name`` (§3.1: consistent
    with what a reader types)."""
    return name[1:] if name.startswith("$") else name


def _string_content(node: TSNode, source: bytes) -> str:
    for child in node.named_children:
        if child.type == "string_content":
            return _text(child, source)
    return _text(node, source).strip("'\"")


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return _text(child, source) if child is not None else ""


def _php_parser() -> Any:
    try:
        import tree_sitter_php
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "PHP extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-php>=0.23'"
        ) from exc
    # D1: the pure-PHP grammar, not the HTML-host one — source files open with `<?php`
    # and never leave it, so the HTML `text` layer every node would otherwise sit
    # under is pure overhead (and a mixed-HTML file still parses, ERROR-tolerant).
    language = Language(tree_sitter_php.language_php_only())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["PhpExtractor"]
