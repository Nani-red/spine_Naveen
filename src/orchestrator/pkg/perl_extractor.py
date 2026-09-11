"""Perl front-end for the PKG extractor (10th language, P1 — comprehension only).

CALLS resolution is P2 (perl-support-roadmap.md §3.2); this module emits nothing but
``Module``/``Type``/``Function``/``Field`` and ``IMPORTS``/``CONTAINS``/``IMPLEMENTS``.

Parsing is via tree-sitter (``tree-sitter-perl``), an OPTIONAL dependency — install the
``perl`` extra. The import is lazy so the base install stays stdlib-only.

**D2 is load-bearing.** A Perl ``package`` is both namespace and class — there is no
separate class-declaration syntax, and ``bless`` makes any package a class. So: ``Module``
is always path-keyed (``perl:lib/Shop/Cart.pm`` — a Perl file has no reliable one-module
identity the way a Java/C# file does), and **every** ``package``/5.38 ``class`` declaration
in the file is its own ``Type`` (``perl:Shop.Cart``, dotted from the source ``::`` per D3 —
matches ``import_link._DOTTED_PREFIXES`` join sites, the doc binder, and registry URLs).
Functions in an implicit ``main`` (no ``package`` seen yet) key on the file itself:
``perl:bin/report.pl.usage``.

**D5 — five inheritance spellings, literal-only:** ``use parent``/``use base`` lists,
``our @ISA = (...)``/``push @ISA``, Moo/Moose ``extends``, ``use Mojo::Base 'X'``, and 5.38
``class Foo :isa(X)``. A computed ``@ISA`` (a variable, a call, anything but a string
literal) yields nothing — inheritance in Perl is data, and a guess here would fabricate an
edge no reader could verify. D4 roles (``with 'Role'`` / ``with qw(A B)``) resolve to the
same ``IMPLEMENTS`` edge kind — a mixin is the behavioural claim IMPLEMENTS already
documents.

**D6 — Field is declared accessors only:** Moo/Moose ``has``, ``Class::Accessor``
``mk_accessors``, 5.38 ``field``. Never inferred from ``$self->{key}`` — a hash access is
not a declaration.

**D7 — require/use:** ``use X qw(...)`` → ``IMPORTS`` to the dotted package placeholder
(upgraded by ``FactBatch`` dedup when the target is grounded elsewhere in the repo); a
lowercase-initial ``use`` target (``strict``, ``warnings``, ``utf8``, ``feature``, …) is a
pragma and skipped. ``require Foo::Bar;`` (bareword) resolves like a ``use``;
``require "lib/x.pl";`` (a literal string) is a path-keyed target, joined by
``import_link.py``'s path-suffix matcher (the C rule) — a computed require argument yields
nothing.

**No ``finalize()``:** unlike PHP's same-namespace ``extends Bar`` guess (which needs a
deferred repoint because a bare name could resolve either way), every Perl D5/D4 target here
is already a literal, fully-qualified name — ``_emit_implements`` adds the external
placeholder node eagerly, and ``FactBatch.add_node``'s own dedup upgrades it to grounded if
the target is declared elsewhere in the repo. There is no guess to repoint.

**Windows note (not a repo defect):** ``tree_sitter_perl.language()`` returns a bare Python
``int`` (``PyLong_FromVoidPtr``), unlike grammars that return a ``PyCapsule``. tree-sitter's
Windows binding parses a bare int via a 32-bit ``unsigned long`` format code, which
overflows for a real 64-bit pointer — ``OverflowError: Python int too large to convert to
C unsigned long``. Linux (CI's ``ubuntu-latest``, 8-byte ``unsigned long``) is unaffected;
this is a local-Windows-development wrinkle in the upstream binding, not in this extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

# `use parent`/`use base`/`use Mojo::Base` are inheritance spellings (D5), never a plain
# IMPORTS edge — the module named here doesn't get *used*, it sets @ISA.
_PARENT_LIKE_USE = frozenset({"parent", "base", "Mojo::Base"})


def _to_dotted(name: str) -> str:
    return name.replace("::", ".")


def _strip_sigil(name: str) -> str:
    return name.lstrip("$@%")


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _first_named_of_type(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.named_children:
        if child.type == type_name:
            return child
    return None


def _varname_of(sigil_node: TSNode | None, source: bytes) -> str | None:
    if sigil_node is None:
        return None
    vn = _first_named_of_type(sigil_node, "varname")
    return _text(vn, source) if vn is not None else None


def _string_content_of(node: TSNode, source: bytes) -> str | None:
    sc = _first_named_of_type(node, "string_content")
    return _text(sc, source) if sc is not None else None


def _plain_string_literal_text(node: TSNode, source: bytes) -> str | None:
    """A ``'...'``/``"..."`` literal with no interpolation — ``None`` for anything computed.

    An interpolated scalar (``"lib/$x.pl"``) nests its ``scalar`` node *inside* the
    ``string_content`` node, not as a sibling — so the content node's own ``named_children``
    must be checked too, not just its parent's child count, or `"lib/$x.pl"` reads as the
    literal text `lib/$x.pl` instead of a computed value.
    """
    if node.type not in ("string_literal", "interpolated_string_literal"):
        return None
    if len(node.named_children) != 1 or node.named_children[0].type != "string_content":
        return None
    content = node.named_children[0]
    if content.named_children:
        return None
    return _text(content, source)


def _string_or_wordlist_targets(node: TSNode | None, source: bytes) -> list[str]:
    """Literal name(s) from ``'X'`` / ``qw(A B)`` / ``'X', 'Y'`` — never a computed value.

    Any element that isn't a plain string/word (a variable, a call, a bareword flag like
    ``-norequire``) is simply not a name — it's skipped, not treated as a reason to bail on
    the rest of the list, matching D5/D4's "read only the literal forms" rule.
    """
    if node is None:
        return []
    if node.type == "string_literal":
        text = _plain_string_literal_text(node, source)
        return [text] if text else []
    if node.type == "quoted_word_list":
        content = _string_content_of(node, source)
        return content.split() if content else []
    if node.type in ("list_expression", "parenthesized_expression"):
        out: list[str] = []
        for child in node.named_children:
            out.extend(_string_or_wordlist_targets(child, source))
        return out
    return []


def _literal_isa_targets(rhs: TSNode | None, source: bytes) -> list[str]:
    """``our @ISA = (...)``'s RHS — ``None``/anything non-literal anywhere means "computed",
    so the whole assignment yields nothing (D5), unlike the mixed-list tolerance above.
    """
    if rhs is None:
        return []
    out: list[str] = []
    stack = [rhs]
    while stack:
        n = stack.pop()
        if n.type == "string_literal":
            text = _plain_string_literal_text(n, source)
            if text is None:
                return []
            out.append(text)
        elif n.type in ("parenthesized_expression", "list_expression"):
            stack.extend(n.named_children)
        else:
            return []  # a variable, a call, anything computed — the whole thing is unusable
    return out


def _has_field_names(args: TSNode | None, source: bytes) -> list[str]:
    """Field name(s) from ``has NAME => (...)`` / ``has [qw(a b)]`` / ``has qw(a b)``."""
    if args is None:
        return []
    if args.type == "list_expression":
        first = args.named_children[0] if args.named_children else None
        if first is not None and first.type == "autoquoted_bareword":
            return [_text(first, source)]
        return []
    if args.type == "anonymous_array_expression":
        inner = _first_named_of_type(args, "quoted_word_list")
        return _string_or_wordlist_targets(inner, source) if inner is not None else []
    if args.type in ("quoted_word_list", "autoquoted_bareword"):
        return _string_or_wordlist_targets(args, source) or (
            [_text(args, source)] if args.type == "autoquoted_bareword" else []
        )
    return []


@dataclass
class _Ctx:
    """The package in scope. Mutated in place for statement-form ``package X;`` so the rest
    of the *same* sibling loop sees the new owner; block-form gets its own fresh ``_Ctx``.
    """

    package_id: str | None
    package_name: str | None


class PerlExtractor:
    """Perl front-end (tree-sitter). Install the ``perl`` extra to use it."""

    language: str = "perl"
    suffixes: tuple[str, ...] = (".pl", ".pm", ".t")

    def module_name(self, path: Path, root: Path) -> str:
        # D2: always path-keyed — a Perl file has no single reliable namespace of its own
        # the way a Java/C# file does (a file may hold zero, one, or several packages).
        return rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _perl_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"perl:{module or rel}"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "perl", Provenance(rel, 1)))
        ctx = _Ctx(package_id=None, package_name=None)
        self._walk_siblings(tree.root_node.named_children, module_id, ctx, source, rel, batch)
        return batch

    # --- top-level / block-body dispatch --------------------------------------

    def _walk_siblings(
        self,
        nodes: list[TSNode],
        module_id: str,
        ctx: _Ctx,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        for node in nodes:
            t = node.type
            if t in ("package_statement", "class_statement"):
                self._handle_package(
                    node, module_id, ctx, source, rel, batch, is_class=t == "class_statement"
                )
            elif t == "use_statement":
                self._handle_use(node, module_id, ctx, source, rel, batch)
            elif t == "require_expression":
                self._handle_require(node, module_id, source, rel, batch)
            elif t == "expression_statement":
                for child in node.named_children:
                    self._handle_statement_expr(child, module_id, ctx, source, rel, batch)
            elif t in ("subroutine_declaration_statement", "method_declaration_statement"):
                self._handle_sub(node, module_id, ctx, source, rel, batch)
            # Control flow / other statements are out of scope for P1 comprehension.

    def _handle_statement_expr(
        self, expr: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        t = expr.type
        if t == "require_expression":
            self._handle_require(expr, module_id, source, rel, batch)
        elif t == "ambiguous_function_call_expression":
            self._handle_bareword_call(expr, ctx, source, rel, batch)
        elif t == "assignment_expression":
            self._handle_assignment(expr, ctx, source, rel, batch)
        elif t == "variable_declaration":
            self._maybe_field_decl(expr, ctx, source, rel, batch)
        elif t == "method_call_expression":
            self._handle_class_accessor_call(expr, ctx, source, rel, batch)

    # --- package / class -------------------------------------------------------

    def _handle_package(
        self,
        node: TSNode,
        module_id: str,
        ctx: _Ctx,
        source: bytes,
        rel: str,
        batch: FactBatch,
        *,
        is_class: bool,
    ) -> None:
        name_node = _first_named_of_type(node, "package")
        if name_node is None:
            return
        name = _text(name_node, source)
        type_id = f"perl:{_to_dotted(name)}"
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        batch.add_node(Node(type_id, NodeKind.TYPE, name, "perl", Provenance(rel, line, end_line)))
        batch.add_edge(Edge(module_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))

        if is_class:
            self._handle_class_isa_attribute(node, type_id, source, rel, line, batch)

        block = _first_named_of_type(node, "block")
        if block is not None:
            child_ctx = _Ctx(package_id=type_id, package_name=name)
            self._walk_siblings(block.named_children, module_id, child_ctx, source, rel, batch)
        else:
            # Statement form: scope runs to the next package/class statement or EOF — mutate
            # the shared ctx so the rest of THIS sibling loop sees the new owner.
            ctx.package_id = type_id
            ctx.package_name = name

    def _handle_class_isa_attribute(
        self, node: TSNode, type_id: str, source: bytes, rel: str, line: int, batch: FactBatch
    ) -> None:
        """5.38 ``class Foo :isa(Bar)`` — D5's fifth spelling."""
        attrlist = _first_named_of_type(node, "attrlist")
        if attrlist is None:
            return
        for attr in attrlist.named_children:
            if attr.type != "attribute":
                continue
            aname = _first_named_of_type(attr, "attribute_name")
            aval = _first_named_of_type(attr, "attribute_value")
            if aname is not None and aval is not None and _text(aname, source) == "isa":
                self._emit_implements(type_id, _text(aval, source), rel, line, batch)

    # --- use / require -----------------------------------------------------

    def _handle_use(
        self, node: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        target_node = _first_named_of_type(node, "package")
        if target_node is None:
            return
        target = _text(target_node, source)
        line = node.start_point[0] + 1
        args = node.named_children[1] if len(node.named_children) > 1 else None

        if target in _PARENT_LIKE_USE:
            if ctx.package_id is None:
                return
            for base in _string_or_wordlist_targets(args, source):
                self._emit_implements(ctx.package_id, base, rel, line, batch)
            return
        if target[:1].islower():
            return  # a pragma (strict, warnings, utf8, feature, lib, constant, …)

        # D2: a `use` target names a PACKAGE, and package == Type (not Module) — the
        # placeholder must share the dotted Type-id shape a real `package Foo::Bar`
        # declaration would produce, or the two would never dedup onto one grounded node.
        tid = f"perl:{_to_dotted(target)}"
        batch.add_node(Node(tid, NodeKind.TYPE, target, "perl", external=True))
        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))

    def _handle_require(
        self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        line = node.start_point[0] + 1
        arg = node.named_children[0] if node.named_children else None
        if arg is None:
            return
        if arg.type == "bareword":
            # A bareword require names a PACKAGE, same as `use` (D2: package == Type).
            target = _text(arg, source)
            tid = f"perl:{_to_dotted(target)}"
            batch.add_node(Node(tid, NodeKind.TYPE, target, "perl", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))
            return
        literal = _plain_string_literal_text(arg, source)
        if literal is None:
            return  # a computed require target — never a guess (D7)
        tid = f"perl:{literal}"
        batch.add_node(Node(tid, NodeKind.MODULE, literal, "perl", external=True))
        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))

    # --- inheritance / roles / fields via bareword calls ------------------

    def _handle_bareword_call(
        self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        fn_node = _first_named_of_type(expr, "function")
        fn_text = _text(fn_node, source) if fn_node is not None else ""
        args = expr.named_children[1] if len(expr.named_children) > 1 else None
        line = expr.start_point[0] + 1

        if fn_text == "push":
            self._handle_push_isa(args, ctx, rel, line, source, batch)
        elif fn_text in ("extends", "with"):
            if ctx.package_id is None:
                return
            for target in _string_or_wordlist_targets(args, source):
                self._emit_implements(ctx.package_id, target, rel, line, batch)
        elif fn_text == "has":
            if ctx.package_id is None:
                return
            for name in _has_field_names(args, source):
                self._emit_field(ctx.package_id, name, rel, line, batch)

    def _handle_push_isa(
        self, args: TSNode | None, ctx: _Ctx, rel: str, line: int, source: bytes, batch: FactBatch
    ) -> None:
        if ctx.package_id is None or args is None or args.type != "list_expression":
            return
        children = args.named_children
        if not children or children[0].type != "array" or _varname_of(children[0], source) != "ISA":
            return
        targets: list[str] = []
        for c in children[1:]:
            text = _plain_string_literal_text(c, source) if c.type == "string_literal" else None
            if text is None:
                return  # a computed element anywhere means the whole push is unusable (D5)
            targets.append(text)
        for t in targets:
            self._emit_implements(ctx.package_id, t, rel, line, batch)

    def _handle_assignment(self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch) -> None:
        children = expr.named_children
        lhs = children[0] if children else None
        if lhs is None or lhs.type != "variable_declaration":
            return
        keyword = _text(lhs.children[0], source) if lhs.children else ""
        if keyword == "field":
            self._maybe_field_decl(lhs, ctx, source, rel, batch)
            return
        if keyword != "our" or ctx.package_id is None:
            return
        arr = _first_named_of_type(lhs, "array")
        if arr is None or _varname_of(arr, source) != "ISA":
            return
        rhs = children[1] if len(children) > 1 else None
        line = expr.start_point[0] + 1
        for target in _literal_isa_targets(rhs, source):
            self._emit_implements(ctx.package_id, target, rel, line, batch)

    def _maybe_field_decl(self, decl: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch) -> None:
        """5.38 ``field $x :param;`` / ``field $y :param = 0;`` (D6)."""
        if ctx.package_id is None or not decl.children or _text(decl.children[0], source) != "field":
            return
        scalar = _first_named_of_type(decl, "scalar")
        varname = _varname_of(scalar, source)
        if varname:
            self._emit_field(ctx.package_id, varname, rel, decl.start_point[0] + 1, batch)

    def _handle_class_accessor_call(
        self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        """``__PACKAGE__->mk_accessors(qw(a b))`` (D6, Class::Accessor)."""
        if ctx.package_id is None:
            return
        children = expr.named_children
        if len(children) < 2:
            return
        receiver, method_node = children[0], _first_named_of_type(expr, "method")
        if receiver.type != "func0op_call_expression" or _text(receiver, source) != "__PACKAGE__":
            return
        if method_node is None or _text(method_node, source) != "mk_accessors":
            return
        line = expr.start_point[0] + 1
        for arg in children[2:]:
            for name in _string_or_wordlist_targets(arg, source):
                self._emit_field(ctx.package_id, name, rel, line, batch)

    # --- subs ----------------------------------------------------------------

    def _handle_sub(
        self, node: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        name_node = _first_named_of_type(node, "bareword")
        if name_node is None:
            return
        name = _text(name_node, source)
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        owner_id = ctx.package_id or module_id
        fid = f"{owner_id}.{name}"
        batch.add_node(Node(fid, NodeKind.FUNCTION, name, "perl", Provenance(rel, line, end_line)))
        batch.add_edge(Edge(owner_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))

    # --- shared emitters -------------------------------------------------------

    @staticmethod
    def _emit_implements(owner_id: str, target_name: str, rel: str, line: int, batch: FactBatch) -> None:
        if not target_name:
            return
        tid = f"perl:{_to_dotted(target_name)}"
        batch.add_node(Node(tid, NodeKind.TYPE, target_name, "perl", external=True))
        batch.add_edge(Edge(owner_id, tid, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

    @staticmethod
    def _emit_field(owner_id: str, name: str, rel: str, line: int, batch: FactBatch) -> None:
        clean = _strip_sigil(name)
        if not clean:
            return
        fid = f"{owner_id}.{clean}"
        batch.add_node(Node(fid, NodeKind.FIELD, clean, "perl", Provenance(rel, line)))
        batch.add_edge(Edge(owner_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))


def _perl_language(raw: Any) -> Any:
    from tree_sitter import Language

    try:
        return Language(raw)
    except OverflowError:
        # Windows only (see the module docstring): tree_sitter_perl.language() returns a
        # bare pointer-sized int rather than a PyCapsule, and tree-sitter's Windows binding
        # parses a bare int via a 32-bit `unsigned long` format code — a real 64-bit pointer
        # overflows it. Wrap it in a capsule ourselves rather than make Perl extraction
        # unusable for every Windows-based contributor over a third-party binding quirk.
        import ctypes

        pythonapi = ctypes.pythonapi
        pythonapi.PyCapsule_New.restype = ctypes.py_object
        pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        capsule = pythonapi.PyCapsule_New(ctypes.c_void_p(raw), b"tree_sitter.Language", None)
        return Language(capsule)


def _perl_parser() -> Any:
    try:
        import tree_sitter_perl
        from tree_sitter import Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Perl extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-perl>=2.0.0'"
        ) from exc
    language = _perl_language(tree_sitter_perl.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["PerlExtractor"]
