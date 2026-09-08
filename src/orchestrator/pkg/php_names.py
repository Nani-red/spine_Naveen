"""What the three PHP modules share — names, and the record they all read.

``php_extractor.py`` emits declarations and calls; ``php_routes.py`` and ``php_orm.py``
emit the framework facts on top. All three resolve a class name the same way (D2 of
``php-support-roadmap.md``: the PHP RFC's fully-qualified / qualified / unqualified rule,
converted to dots once) and all three read the same collected ``_TypeRec``. When those
lived in ``php_extractor.py`` the two framework modules imported them from it while it
imported them back — a cycle CodeQL flagged six times. It never failed at import time,
because the extractor's side was function-local, but a dependency that only works because
of *where* an import statement sits is one refactor from an ``ImportError``.

So this module is a leaf: it imports ``tree_sitter`` types for annotation only and nothing
from its three siblings, the shape ``go_routes.py`` has always had. Everything here is
private to the PHP front-end; the public surface stays ``PhpExtractor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _to_dotted(name: str) -> str:
    """``App\\Http\\Controllers`` -> ``App.Http.Controllers`` (D2)."""
    return name.replace("\\", ".").strip(".")


def _join(namespace: str, name: str) -> str:
    if namespace and name:
        return f"{namespace}.{name}"
    return name or namespace


@dataclass
class _TypeRec:
    """A collected class/interface/trait/enum — the working set the CALLS pass (P2)
    reasons over, once every declaration in the file is known."""

    type_id: str
    name: str
    namespace: str
    use_map: dict[str, str]
    use_func_map: dict[str, str]
    node: TSNode  # the declaration node itself — php_routes.py reads its attributes (P3)
    # The single `extends` target's raw name (classes only — D3's trait/interface
    # `use`/`implements` never feed `parent::`), for CALLS row 3.
    base_name: str | None = None
    methods: list[tuple[str, str, TSNode]] = field(default_factory=list)  # (name, id, node)
    # Trait names this type `use`s (D3) — resolved lazily in the CALLS pass so a
    # same-file trait's methods count as this type's own for `$this->`/`self::` calls
    # it doesn't override (the `traits` corpus case's whole point).
    trait_names: list[str] = field(default_factory=list)
    # name -> raw type text, for every TYPED property and TYPED promoted ctor param
    # (both become properties reachable via `$this->prop->m()`) — P3's typed-receiver
    # rule (§3.2 rows 7-8). An untyped property/param is simply absent here.
    typed_props: dict[str, str] = field(default_factory=dict)


# --- use-declaration parsing -------------------------------------------------


def _use_clause(clause: TSNode, source: bytes) -> tuple[str, str, str]:
    """``(fqn, bind_name, kind)`` for one ``namespace_use_clause``.

    ``kind`` is ``class`` unless a ``function``/``const`` marker is present. ``fqn`` is
    left backslash-separated (the caller joins group prefixes before converting to dots
    once). ``bind_name`` is the alias if aliased, else the FQN's own last segment.
    """
    kind = "class"
    type_tok = clause.child_by_field_name("type")
    if type_tok is not None and _text(type_tok, source) in ("function", "const"):
        kind = _text(type_tok, source)
    name_node = next((c for c in clause.named_children if c.type in ("qualified_name", "name")), None)
    fqn = _text(name_node, source) if name_node is not None else ""
    alias_node = clause.child_by_field_name("alias")
    bind = _text(alias_node, source) if alias_node is not None else fqn.rsplit("\\", 1)[-1]
    return fqn, bind, kind


def _use_declaration_targets(decl: TSNode, source: bytes) -> list[tuple[str, str, str]]:
    """``(fqn_dotted, bind_name, kind)`` for every clause in a ``namespace_use_declaration``,
    grouped (``use App\\Models\\{Customer, Invoice as Inv};``) or not."""
    out: list[tuple[str, str, str]] = []
    prefix = ""
    group: TSNode | None = None
    for child in decl.named_children:
        if child.type == "namespace_name":
            prefix = _text(child, source)
        elif child.type == "namespace_use_group":
            group = child
        elif child.type == "namespace_use_clause":
            fqn, bind, kind = _use_clause(child, source)
            out.append((_to_dotted(fqn), bind, kind))
    if group is not None:
        for clause in group.named_children:
            if clause.type != "namespace_use_clause":
                continue
            fqn, bind, kind = _use_clause(clause, source)
            full = f"{prefix}\\{fqn}" if prefix else fqn
            out.append((_to_dotted(full), bind, kind))
    return out


# --- PHP's class-name resolution (D0/§2) -------------------------------------


def _resolve_type_name(name: str, namespace: str, use_map: dict[str, str]) -> tuple[str, bool]:
    """Fully-qualified, qualified, or unqualified -> current namespace unless `use`d
    (the PHP RFC rule). Returns ``(dotted_id, is_guess)``: ``is_guess`` is True only
    for the same-namespace assumption made when nothing resolves the name — the one
    case `finalize` may need to correct. Every other shape (a leading `\\`, or a hit
    in the `use` map) is a claim read directly off the source and must never be
    treated as provisional, even though it is very often *external* — see the
    `finalize` docstring in ``php_extractor.py`` for why the distinction matters."""
    name = name.strip()
    if name.startswith("\\"):
        return _to_dotted(name), False  # fully qualified — already absolute
    if "\\" in name:  # qualified: resolve the first segment, keep the rest
        head, _, rest = name.partition("\\")
        if head in use_map:
            base = use_map[head]
            return (f"{base}.{_to_dotted(rest)}" if rest else base), False
        return _join(namespace, _to_dotted(name)), True
    if name in use_map:  # unqualified, explicitly `use`d
        return use_map[name], False
    return _join(namespace, name), True  # unqualified, not `use`d — assume current namespace


__all__ = [
    "_TypeRec",
    "_join",
    "_resolve_type_name",
    "_text",
    "_to_dotted",
    "_use_declaration_targets",
]
