"""Laravel / Slim / Symfony routes in PHP source → ``Endpoint`` nodes + ``EXPOSES`` edges (P3, §3.3).

Nothing in PHP *calls* a route handler — the framework's router does, at runtime — so
without this a route handler has zero callers and ``impact_of`` reports a public endpoint
as safe to change. Same wrong answer this closes for every other front-end's routes
(``python_routes.py``, ``typescript_routes.py``, ``go_routes.py``, C#'s attribute
controllers in ``csharp_extractor.py``).

Three shapes, precision-first throughout — a computed path or an unresolvable handler
yields no edge, never a guess:

- **Laravel** (`Route::get('/x', [Ctl::class, 'm'])` / `'Ctl@m'` / a closure) and
  **Slim/Lumen** (`$app->get('/x', [Ctl::class, 'm'])`) share one reader: a static/method
  call named after an HTTP verb, a literal path, and either an array handler
  (``[Ctl::class, 'm']``), a string handler (``'Ctl@m'``), or a closure. A closure
  yields the `Endpoint` but no `EXPOSES` — there is no named symbol to point at, and
  inventing one is the fabrication this graph refuses (the Gin/TypeScript rule).
  `Route::prefix('/v1')->group(fn () => …)` composes a prefix onto every route
  registered inside the closure, arbitrarily nested. `Route::any`/`match` and
  `Route::resource` are **not** read — see D2 of endpoints-typescript-go.md (no `ANY`)
  and the roadmap's D2/§9 (defer `resource` until demand is measured).
- **Symfony** attributes (`#[Route('/orders', methods: ['GET'])]` on a method, with an
  optional class-level `#[Route('/api')]` prefix) — the same attribute-controller shape
  `csharp_extractor.py` reads for ASP.NET, matched by the attribute's bare name (not its
  resolved FQN, matching that precedent). A `#[Route]` with no `methods:` is verb-less →
  nothing (same D2).

Handler class names resolve through :func:`orchestrator.pkg.php_names._resolve_type_name`
— the same namespace-rule resolver every other PHP fact uses, so a route handler lands on
the exact id its own class declaration would produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.php_names import _resolve_type_name, _to_dotted, _use_declaration_targets

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

    from orchestrator.pkg.php_names import _TypeRec

_LARAVEL_VERBS = frozenset({"get", "post", "put", "patch", "delete", "options"})
_SLIM_RECEIVER = "$app"


@dataclass(frozen=True)
class PendingRoute:
    """One resolved Laravel/Slim route registration."""

    verb: str
    path: str
    handler_type: str | None  # a dotted `php:` id, or None (closure / unresolved)
    handler_method: str | None
    provenance: Provenance


def _text(node: TSNode, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _arg_value(arg: TSNode) -> TSNode | None:
    """The value node of an ``argument`` wrapper — last named child works for both a
    positional argument (one child) and a named one (``name`` field, then the value)."""
    return arg.named_children[-1] if arg.named_children else None


def _arg_name(arg: TSNode, source: bytes) -> str | None:
    name_node = arg.child_by_field_name("name")
    return _text(name_node, source) if name_node is not None else None


def _call_args(node: TSNode) -> list[TSNode]:
    args = node.child_by_field_name("arguments")
    return [c for c in args.named_children if c.type == "argument"] if args is not None else []


def _literal_string(node: TSNode | None, source: bytes) -> str | None:
    """A literal string value; ``None`` for anything computed or interpolated."""
    if node is None:
        return None
    if node.type == "string":
        for child in node.named_children:
            if child.type == "string_content":
                return _text(child, source)
        return _text(node, source).strip("'\"")
    if node.type == "encapsed_string":  # double-quoted — literal only with no `$var`
        parts: list[str] = []
        for child in node.named_children:
            if child.type != "string_content":
                return None
            parts.append(_text(child, source))
        return "".join(parts)
    return None


def _string_list(node: TSNode | None, source: bytes) -> list[str]:
    if node is None or node.type != "array_creation_expression":
        return []
    out: list[str] = []
    for elem in node.named_children:
        if elem.type != "array_element_initializer" or not elem.named_children:
            continue
        lit = _literal_string(elem.named_children[0], source)
        if lit is not None:
            out.append(lit)
    return out


def _join_route(prefix: str, suffix: str) -> str:
    parts = [p.strip("/") for p in (prefix, suffix) if p and p.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def _handler_from_array(node: TSNode, source: bytes) -> tuple[str, str] | None:
    """``[OrderController::class, 'index']`` -> ``(class_name_text, method_name)``."""
    if node.type != "array_creation_expression":
        return None
    elems = [c for c in node.named_children if c.type == "array_element_initializer"]
    if len(elems) != 2 or not elems[0].named_children or not elems[1].named_children:
        return None
    cls_node = elems[0].named_children[0]
    if cls_node.type != "class_constant_access_expression" or not cls_node.named_children:
        return None
    method = _literal_string(elems[1].named_children[0], source)
    if method is None:
        return None
    return _text(cls_node.named_children[0], source), method


def _handler_from_string(value: str) -> tuple[str, str] | None:
    """``'OrderController@index'`` -> ``(class_name_text, method_name)``."""
    if "@" not in value:
        return None
    cls, _, method = value.partition("@")
    return (cls, method) if cls and method else None


def _resolve_handler(
    handler_arg: TSNode | None, namespace: str, use_map: dict[str, str], source: bytes
) -> tuple[str | None, str | None]:
    """``(handler_type_id, handler_method)`` — both ``None`` for a closure or anything
    unresolvable (precision-first: no guess, just no `EXPOSES`)."""
    if handler_arg is None or handler_arg.type in ("anonymous_function", "arrow_function"):
        return None, None
    pair = _handler_from_array(handler_arg, source)
    if pair is None:
        lit = _literal_string(handler_arg, source)
        pair = _handler_from_string(lit) if lit is not None else None
    if pair is None:
        return None, None
    cls_name, method = pair
    dotted, _is_guess = _resolve_type_name(cls_name, namespace, use_map)
    return f"php:{dotted}", method


# --- Laravel + Slim/Lumen (a shared reader) -----------------------------------


def scan_laravel_routes(root: TSNode, source: bytes, rel: str) -> list[PendingRoute]:
    """Collect every Laravel/Slim route registration in the file, in source order (so a
    `Route::prefix(...)->group(...)` nesting resolves before the routes inside it need it)."""
    routes: list[PendingRoute] = []
    _walk(root.named_children, "", {}, "", source, rel, routes)
    return routes


def _walk(
    nodes: list[TSNode],
    namespace: str,
    use_map: dict[str, str],
    prefix: str,
    source: bytes,
    rel: str,
    routes: list[PendingRoute],
) -> None:
    current_ns = namespace
    current_uses = use_map
    for node in nodes:
        if node.type == "namespace_definition":
            name_node = node.child_by_field_name("name")
            current_ns = _to_dotted(_text(name_node, source)) if name_node is not None else ""
            current_uses = {}
            body = node.child_by_field_name("body")
            if body is not None:
                _walk(body.named_children, current_ns, current_uses, prefix, source, rel, routes)
            continue
        if node.type == "namespace_use_declaration":
            for fqn_dotted, bind, kind in _use_declaration_targets(node, source):
                if kind == "class" and bind and fqn_dotted:
                    current_uses[bind] = fqn_dotted
            continue
        _walk_expr(node, current_ns, current_uses, prefix, source, rel, routes)


def _walk_expr(
    node: TSNode,
    namespace: str,
    use_map: dict[str, str],
    prefix: str,
    source: bytes,
    rel: str,
    routes: list[PendingRoute],
) -> None:
    if node.type == "member_call_expression":
        obj = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        method = _text(name_node, source) if name_node is not None else ""
        if method == "group" and obj is not None:
            group_prefix = _group_prefix_of_chain(obj, source)
            if group_prefix is not None:
                args = _call_args(node)
                _walk_group_body(
                    _arg_value(args[0]) if args else None,
                    namespace,
                    use_map,
                    _join_route(prefix, group_prefix),
                    source,
                    rel,
                    routes,
                )
            # else: a prefix this reader cannot resolve (`Route::prefix($v)`, a receiver
            # that is not the `Route` facade). The body is NOT walked with the outer
            # prefix: every route inside would be emitted at the wrong path and presented
            # as grounded — the cross-repo false join go_routes.py refuses the same way.
            return
        if (
            obj is not None
            and obj.type == "variable_name"
            and _text(obj, source) == _SLIM_RECEIVER
            and method in _LARAVEL_VERBS
        ):
            _register_route(method, _call_args(node), namespace, use_map, prefix, source, rel, node, routes)
            return
    elif node.type == "scoped_call_expression":
        scope = node.child_by_field_name("scope")
        name_node = node.child_by_field_name("name")
        if (
            scope is not None
            and scope.type == "name"
            and _text(scope, source) == "Route"
            and name_node is not None
            and _text(name_node, source) in _LARAVEL_VERBS
        ):
            _register_route(
                _text(name_node, source),
                _call_args(node),
                namespace,
                use_map,
                prefix,
                source,
                rel,
                node,
                routes,
            )
            return
        if (
            scope is not None
            and scope.type == "name"
            and _text(scope, source) == "Route"
            and name_node is not None
            and _text(name_node, source) == "group"
        ):
            # The classic array form: `Route::group(['prefix' => 'v1', ...], fn)`.
            args = _call_args(node)
            attrs = _arg_value(args[0]) if args else None
            group_prefix = _array_prefix(attrs, source) if attrs is not None else None
            if group_prefix is not None and len(args) > 1:
                _walk_group_body(
                    _arg_value(args[1]),
                    namespace,
                    use_map,
                    _join_route(prefix, group_prefix),
                    source,
                    rel,
                    routes,
                )
            return  # same rule as above: an unreadable prefix means the body is not walked
        # `Route::any`/`Route::match`/`Route::resource` and anything else — no verb this
        # graph will assert (D2); fall through so a nested closure argument still scans.

    for child in node.named_children:
        _walk_expr(child, namespace, use_map, prefix, source, rel, routes)


def _walk_group_body(
    closure: TSNode | None,
    namespace: str,
    use_map: dict[str, str],
    prefix: str,
    source: bytes,
    rel: str,
    routes: list[PendingRoute],
) -> None:
    if closure is None:
        return
    for child in _closure_body_nodes(closure):
        _walk_expr(child, namespace, use_map, prefix, source, rel, routes)


def _group_prefix_of_chain(obj: TSNode, source: bytes) -> str | None:
    """The literal prefix a ``->group(...)`` receiver chain contributes.

    ``Route::prefix('/v1')->middleware('auth')`` and ``Route::middleware('auth')->prefix('/v1')``
    both give ``'/v1'``; ``Route::middleware('auth')`` gives ``''``. ``None`` when any link is
    unreadable — a computed ``prefix($v)``, or a chain that does not root at the ``Route``
    facade (``$app->group(...)``, ``$router->group(...)``) — because a group whose prefix is
    unknown makes every path inside it unknown too. Only ``prefix`` moves the path; the other
    chain methods (``middleware``, ``name``, ``controller``, ``domain``, ``where``) do not.
    """
    segments: list[str] = []
    cur: TSNode | None = obj
    while cur is not None:
        name_node = cur.child_by_field_name("name")
        method = _text(name_node, source) if name_node is not None else ""
        if method == "prefix":
            args = _call_args(cur)
            lit = _literal_string(_arg_value(args[0]), source) if args else None
            if lit is None:
                return None
            segments.append(lit)
        if cur.type == "member_call_expression":
            cur = cur.child_by_field_name("object")
            continue
        if cur.type == "scoped_call_expression":
            scope = cur.child_by_field_name("scope")
            if scope is None or scope.type != "name" or _text(scope, source) != "Route":
                return None
            composed = ""
            for seg in reversed(segments):  # outermost call first
                composed = _join_route(composed, seg)
            return composed
        return None
    return None


def _array_prefix(attrs: TSNode, source: bytes) -> str | None:
    """``['prefix' => 'v1', 'middleware' => 'auth']`` -> ``'v1'``; ``''`` when there is no
    ``prefix`` key; ``None`` when the attributes are not a literal array or the prefix is
    computed."""
    if attrs.type != "array_creation_expression":
        return None
    for elem in attrs.named_children:
        if elem.type != "array_element_initializer" or len(elem.named_children) < 2:
            continue
        if _literal_string(elem.named_children[0], source) == "prefix":
            return _literal_string(elem.named_children[1], source)
    return ""


def _closure_body_nodes(closure: TSNode) -> list[TSNode]:
    if closure.type == "anonymous_function":
        body = closure.child_by_field_name("body")
        return list(body.named_children) if body is not None else []
    if closure.type == "arrow_function":
        body = closure.child_by_field_name("body")
        return [body] if body is not None else []
    return []


def _register_route(
    verb: str,
    args: list[TSNode],
    namespace: str,
    use_map: dict[str, str],
    prefix: str,
    source: bytes,
    rel: str,
    call_node: TSNode,
    routes: list[PendingRoute],
) -> None:
    if not args:
        return
    path = _literal_string(_arg_value(args[0]), source)
    if path is None:
        return  # a computed path — precision-first, no edge at all
    handler_arg = _arg_value(args[1]) if len(args) > 1 else None
    handler_type, handler_method = _resolve_handler(handler_arg, namespace, use_map, source)
    full = _join_route(prefix, path)
    line = call_node.start_point[0] + 1
    routes.append(PendingRoute(verb.upper(), full, handler_type, handler_method, Provenance(rel, line)))


def emit_laravel_routes(routes: list[PendingRoute], batch: FactBatch) -> None:
    for route in routes:
        endpoint_id = f"php:endpoint:{route.verb} {route.path}"
        batch.add_node(
            Node(endpoint_id, NodeKind.ENDPOINT, f"{route.verb} {route.path}", "php", route.provenance)
        )
        if route.handler_type is not None and route.handler_method is not None:
            target = f"{route.handler_type}.{route.handler_method}"
            batch.add_node(Node(target, NodeKind.FUNCTION, route.handler_method, "php", external=True))
            batch.add_edge(Edge(endpoint_id, target, EdgeKind.EXPOSES, route.provenance))


# --- Symfony attribute routes --------------------------------------------------


def scan_symfony_routes(types: list[_TypeRec], source: bytes, rel: str, batch: FactBatch) -> None:
    """`#[Route(...)]` on a method, with an optional class-level prefix — the same
    attribute-controller shape as ASP.NET's `[Route]` in ``csharp_extractor.py``."""
    for rec in types:
        class_prefix = ""
        unreadable_prefix = False
        for aname, anode in _attributes(rec.node, source):
            if aname == "Route":
                path = _route_path(anode, source)
                if path is None:
                    # `#[Route(self::PREFIX)]`, a constant expression: the prefix exists
                    # and cannot be read, so every method path under it would be wrong.
                    unreadable_prefix = True
                else:
                    class_prefix = path
                break
        if unreadable_prefix:
            continue
        for _mname, mid, mnode in rec.methods:
            for aname, anode in _attributes(mnode, source):
                if aname != "Route":
                    continue
                path = _route_path(anode, source)
                verbs = _string_list(_named_arg(anode, "methods", source), source)
                if path is None or not verbs:
                    continue  # a verb-less #[Route] responds to everything — nothing (D2)
                full = _join_route(class_prefix, path)
                line = mnode.start_point[0] + 1
                for verb in verbs:
                    eid = f"php:endpoint:{verb.upper()} {full}"
                    batch.add_node(
                        Node(eid, NodeKind.ENDPOINT, f"{verb.upper()} {full}", "php", Provenance(rel, line))
                    )
                    batch.add_edge(Edge(eid, mid, EdgeKind.EXPOSES, Provenance(rel, line)))


def _attributes(node: TSNode, source: bytes) -> list[tuple[str, TSNode]]:
    """``(name, attribute_node)`` for each `#[...]` attribute directly on ``node``."""
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


def _first_positional_string(attribute: TSNode, source: bytes) -> str | None:
    for arg in _attribute_args(attribute):
        if _arg_name(arg, source) is not None:
            continue  # a named argument (`methods: [...]`) — not the positional path
        return _literal_string(_arg_value(arg), source)
    return None


def _named_arg(attribute: TSNode, name: str, source: bytes) -> TSNode | None:
    for arg in _attribute_args(attribute):
        if _arg_name(arg, source) == name:
            return _arg_value(arg)
    return None


def _route_path(attribute: TSNode, source: bytes) -> str | None:
    """A ``#[Route]``'s path — the positional form or Symfony's documented named form
    ``#[Route(path: '/api')]``. ``None`` for anything computed."""
    named = _named_arg(attribute, "path", source)
    if named is not None:
        return _literal_string(named, source)
    return _first_positional_string(attribute, source)


__all__ = [
    "PendingRoute",
    "emit_laravel_routes",
    "scan_laravel_routes",
    "scan_symfony_routes",
]
