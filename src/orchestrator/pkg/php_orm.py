"""Eloquent + Doctrine ORM models in PHP source → ``Entity`` nodes + entity→entity
``REFERENCES`` edges (P4, §3.4).

Ids are ``php:entity:NS.Order`` — the C# precedent (a parallel id alongside the class's own
``Type`` node, not a replacement for it), **not** the table-keyed ``py:entity:<table>`` form
Python uses. Either way, :func:`~orchestrator.pkg.data_layer_link.link_data_layer` needs
nothing PHP-specific to reconcile these against a real ``.sql`` schema in the same repo — it
matches any non-``sql:``-prefixed `Entity` node by its **name** alone, language-agnostic by
design (verified by reading it, not assumed).

Two ORMs, both statically readable from tree-sitter, matching the roadmap's own precision
bar — no ORM marker, no `Entity` (the highest-risk rule `python_orm.py` documents: a Pydantic
model, a dataclass and an ORM model are indistinguishable at field level, so the gate is a
*real* marker, never the shape of the class):

- **Eloquent** — a class whose base is (bare-name) ``Model``. Its own ``belongsTo``/
  ``hasMany``/``hasOne``/``belongsToMany`` calls (``return $this->belongsTo(Customer::class)``,
  the idiomatic form, though any occurrence in the method body is read) name the related model.
- **Doctrine** — a class carrying ``#[ORM\\Entity]`` (matched by bare attribute name, like
  Symfony's `#[Route]` in ``php_routes.py``). A property's ``#[ORM\\ManyToOne(targetEntity:
  X::class)]`` (``OneToMany``/``ManyToMany``/``OneToOne`` likewise) names the relation.

A relation's target resolves through the same namespace-rule resolver
(:func:`orchestrator.pkg.php_extractor._resolve_type_name`) every other PHP fact uses. It
always gets a node — grounded when the target is itself a detected entity in this file,
external otherwise ("the third-party target stays external", the `eloquent` corpus case's
own wording) — so a `REFERENCES` edge is never left dangling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.php_extractor import _resolve_type_name

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

    from orchestrator.pkg.php_extractor import _TypeRec

_ELOQUENT_RELATIONS = frozenset({"belongsTo", "hasMany", "hasOne", "belongsToMany"})
_DOCTRINE_RELATIONS = frozenset({"ManyToOne", "OneToMany", "ManyToMany", "OneToOne"})


def _text(node: TSNode, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _bare(name: str) -> str:
    """``ORM\\Entity`` -> ``Entity``; a plain name passes through unchanged."""
    return name.rsplit("\\", 1)[-1]


def _attributes(node: TSNode, source: bytes) -> list[tuple[str, TSNode]]:
    """``(name, attribute_node)`` for each ``#[...]`` attribute directly on ``node`` — a
    class or a property, both of which carry `attribute_list` as a direct named child."""
    out: list[tuple[str, TSNode]] = []
    for child in node.named_children:
        if child.type != "attribute_list":
            continue
        for group in child.named_children:
            if group.type != "attribute_group":
                continue
            for attr in group.named_children:
                if attr.type != "attribute":
                    continue
                name_node = attr.child_by_field_name("name") or (
                    attr.named_children[0] if attr.named_children else None
                )
                if name_node is not None:
                    out.append((_text(name_node, source), attr))
    return out


def _attribute_args(attribute: TSNode) -> list[TSNode]:
    args = attribute.child_by_field_name("parameters")
    return [c for c in args.named_children if c.type == "argument"] if args is not None else []


def _arg_name(arg: TSNode, source: bytes) -> str | None:
    name_node = arg.child_by_field_name("name")
    return _text(name_node, source) if name_node is not None else None


def _arg_value(arg: TSNode) -> TSNode | None:
    return arg.named_children[-1] if arg.named_children else None


def _named_arg(attribute: TSNode, name: str, source: bytes) -> TSNode | None:
    for arg in _attribute_args(attribute):
        if _arg_name(arg, source) == name:
            return _arg_value(arg)
    return None


def _class_ref_name(node: TSNode | None, source: bytes) -> str | None:
    """``Customer::class`` -> ``"Customer"``; ``None`` for anything else (a variable, a
    computed expression — never guessed)."""
    if node is None or node.type != "class_constant_access_expression" or not node.named_children:
        return None
    return _text(node.named_children[0], source)


def _eloquent_relation_targets(method_node: TSNode, source: bytes) -> list[str]:
    """Every ``$this->belongsTo(X::class)`` (/``hasMany``/``hasOne``/``belongsToMany``) target
    class name in one method body."""
    body = method_node.child_by_field_name("body")
    if body is None:
        return []
    out: list[str] = []
    stack = list(body.named_children)
    while stack:
        node = stack.pop()
        if node.type == "member_call_expression":
            obj = node.child_by_field_name("object")
            name_node = node.child_by_field_name("name")
            if (
                obj is not None
                and obj.type == "variable_name"
                and _text(obj, source) == "$this"
                and name_node is not None
                and _text(name_node, source) in _ELOQUENT_RELATIONS
            ):
                args = node.child_by_field_name("arguments")
                parts = [c for c in args.named_children if c.type == "argument"] if args is not None else []
                if parts:
                    target = _class_ref_name(_arg_value(parts[0]), source)
                    if target is not None:
                        out.append(target)
        stack.extend(node.named_children)
    return out


def emit_entities(types: list[_TypeRec], source: bytes, rel: str, batch: FactBatch) -> None:
    """`Entity` nodes for every Eloquent/Doctrine model in the file, plus their relations."""
    entity_ids: dict[str, str] = {}
    for rec in types:
        is_eloquent = rec.base_name is not None and _bare(rec.base_name) == "Model"
        is_doctrine = any(_bare(name) == "Entity" for name, _ in _attributes(rec.node, source))
        if is_eloquent or is_doctrine:
            entity_ids[rec.type_id] = f"php:entity:{rec.type_id[len('php:') :]}"

    for rec in types:
        eid = entity_ids.get(rec.type_id)
        if eid is None:
            continue
        line = rec.node.start_point[0] + 1
        batch.add_node(Node(eid, NodeKind.ENTITY, rec.name, "php", Provenance(rel, line)))

        for _mname, _mid, mnode in rec.methods:
            for target_name in _eloquent_relation_targets(mnode, source):
                _add_reference(eid, target_name, rec.namespace, rec.use_map, entity_ids, line, rel, batch)

        body = rec.node.child_by_field_name("body")
        if body is None:
            continue
        for member in body.named_children:
            if member.type != "property_declaration":
                continue
            for aname, anode in _attributes(member, source):
                if _bare(aname) not in _DOCTRINE_RELATIONS:
                    continue
                doctrine_target = _class_ref_name(_named_arg(anode, "targetEntity", source), source)
                if doctrine_target is not None:
                    _add_reference(
                        eid, doctrine_target, rec.namespace, rec.use_map, entity_ids, line, rel, batch
                    )


def _add_reference(
    src_eid: str,
    target_name: str,
    namespace: str,
    use_map: dict[str, str],
    entity_ids: dict[str, str],
    line: int,
    rel: str,
    batch: FactBatch,
) -> None:
    dotted, _is_guess = _resolve_type_name(target_name, namespace, use_map)
    target_type_id = f"php:{dotted}"
    is_local_entity = target_type_id in entity_ids
    target_eid = entity_ids[target_type_id] if is_local_entity else f"php:entity:{dotted}"
    if not is_local_entity:
        batch.add_node(Node(target_eid, NodeKind.ENTITY, dotted.rsplit(".", 1)[-1], "php", external=True))
    batch.add_edge(Edge(src_eid, target_eid, EdgeKind.REFERENCES, Provenance(rel, line)))


__all__ = ["emit_entities"]
