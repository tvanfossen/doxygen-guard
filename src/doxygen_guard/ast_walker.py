"""AST-ordered edge building for sequence diagrams.

@brief Walk tree-sitter AST to produce edges in source execution order with control flow blocks.
@version 1.0
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doxygen_guard.tracer import Edge, Participant, TaggedFunction

if TYPE_CHECKING:
    from tree_sitter import Node

    from doxygen_guard.ts_languages import LanguageSpec
    from doxygen_guard.ts_parser import ParsedFile

logger = logging.getLogger(__name__)


## @brief An edge or control flow marker produced by the AST walk.
#  @version 1.0
#  @internal
@dataclass
class ASTEdge:
    kind: str
    edge: Edge | None = None
    label: str = ""


## @brief Context passed through the recursive AST walk.
#  @version 1.0
#  @internal
@dataclass
class WalkContext:
    handler_map: dict[str, list[TaggedFunction]]
    all_tagged: list[TaggedFunction]
    externals: list[Participant]
    emit_functions: set[str]
    spec: LanguageSpec
    req_id: str | None = None
    max_depth: int = 3
    visited: set[str] | None = None
    file_cache: dict[str, Any] | None = None


## @brief Mutable state threaded through the recursive AST walk.
#  @version 1.0
#  @internal
@dataclass
class _WalkState:
    from_name: str
    tf: TaggedFunction
    ctx: WalkContext
    depth: int
    emit_queue: deque[str]
    emits_placed: bool
    ext_refs: dict[str, str]
    edges: list[ASTEdge]


## @brief Walk a function body AST to produce edges in source execution order.
#  @version 1.1
#  @req REQ-TRACE-001
def walk_function_body(
    func_node: Node,
    tf: TaggedFunction,
    ctx: WalkContext,
    depth: int = 0,
) -> list[ASTEdge]:
    body = func_node.child_by_field_name("body")
    if body is None:
        return []

    from_name = tf.participant_name or tf.name
    edges: list[ASTEdge] = []

    for trigger in tf.triggers:
        edges.append(
            ASTEdge(kind="trigger", edge=Edge(from_name, from_name, trigger, style="note"))
        )

    state = _WalkState(
        from_name=from_name,
        tf=tf,
        ctx=ctx,
        depth=depth,
        emit_queue=deque(tf.emits),
        emits_placed=False,
        ext_refs={ref.split("::", 1)[-1]: ref for ref in tf.ext},
        edges=edges,
    )

    _walk_statements(body, state)

    if state.emit_queue:
        _flush_remaining_emits(state.emit_queue, from_name, tf.name, ctx, depth, edges)

    return edges


## @brief Recursively walk AST statements, emitting edges in source order.
#  @version 1.1
#  @internal
def _walk_statements(
    node: Node,
    state: _WalkState,
) -> None:
    for child in node.named_children:
        if child.type in state.ctx.spec.control_flow_types:
            _handle_control_flow(child, state)
        elif child.type == state.ctx.spec.call_node_type:
            _handle_call(child, state)
        else:
            _walk_statements(child, state)


## @brief Handle a call expression node, producing the appropriate edge type.
#  @version 1.2
#  @internal
def _handle_call(
    call_node: Node,
    state: _WalkState,
) -> None:
    callee = _extract_callee_name(call_node, state.ctx.spec)
    if callee is None:
        return

    if callee in state.ctx.emit_functions and state.emit_queue:
        _place_one_emit(
            state.emit_queue, state.from_name, state.tf.name, state.ctx, state.depth, state.edges
        )
        state.emits_placed = True
        return

    if callee in state.ext_refs:
        _place_ext_edge(callee, state.ext_refs[callee], state.from_name, state.ctx, state.edges)
        return

    if _is_tagged_call_target(callee, state.ctx):
        target = _find_tagged_function(callee, state.ctx)
        if target:
            to_name = target.participant_name or target.name
            state.edges.append(
                ASTEdge(kind="call", edge=Edge(state.from_name, to_name, f"{callee}()"))
            )


## @brief Place ONE emit edge from the queue at the current AST position.
#  @version 1.2
#  @internal
def _place_one_emit(
    emit_queue: deque[str],
    from_name: str,
    func_name: str,
    ctx: WalkContext,
    depth: int,
    edges: list[ASTEdge],
) -> None:
    if not emit_queue:
        return
    event = emit_queue.popleft()
    emit_edge, handler_tf = _resolve_emit(event, from_name, func_name, ctx)
    if emit_edge:
        edges.append(ASTEdge(kind="emit", edge=emit_edge))

    if handler_tf and depth < ctx.max_depth:
        chain_edges = _follow_handler_chain(handler_tf, ctx, depth)
        edges.extend(chain_edges)


## @brief Flush remaining emits that weren't matched to any call site.
#  @version 1.1
#  @internal
def _flush_remaining_emits(
    emit_queue: deque[str],
    from_name: str,
    func_name: str,
    ctx: WalkContext,
    depth: int,
    edges: list[ASTEdge],
) -> None:
    while emit_queue:
        _place_one_emit(emit_queue, from_name, func_name, ctx, depth, edges)


## @brief Resolve an emit event to an edge and optionally a handler for chain following.
#  @version 1.0
#  @internal
def _resolve_emit(
    event: str,
    from_name: str,
    func_name: str,
    ctx: WalkContext,
) -> tuple[Edge | None, TaggedFunction | None]:
    handlers = ctx.handler_map.get(event, [])
    if handlers:
        handler = handlers[0]
        to_name = handler.participant_name or handler.name
        label = f"{func_name}() \u2192 {handler.name}()"
        edge = Edge(from_name, to_name, label, event, "-->")
        return edge, handler

    from doxygen_guard.tracer import _resolve_by_prefix

    prefix_target = _resolve_by_prefix(event, ctx.externals)
    if prefix_target:
        edge = Edge(from_name, prefix_target, f"{func_name}()", event, "-->")
        return edge, None

    logger.warning("Unresolved event '%s' from %s()", event, func_name)
    edge = Edge(from_name, from_name, event, style="-->")
    return edge, None


## @brief Place an ext edge for a resolved external call.
#  @version 1.0
#  @internal
def _place_ext_edge(
    callee: str,
    ext_ref: str,
    from_name: str,
    ctx: WalkContext,
    edges: list[ASTEdge],
) -> None:
    parts = ext_ref.split("::", 1)
    func_name = parts[1] if len(parts) == 2 else ext_ref

    from doxygen_guard.tracer import _resolve_ext_target

    to_name = _resolve_ext_target(func_name, parts[0], ctx.all_tagged) or parts[0]
    edges.append(ASTEdge(kind="ext", edge=Edge(from_name, to_name, f"{func_name}()")))


## @brief Follow a handler's body to produce continuation edges.
#  @version 1.0
#  @internal
def _follow_handler_chain(
    handler_tf: TaggedFunction,
    ctx: WalkContext,
    depth: int,
) -> list[ASTEdge]:
    visited = ctx.visited or set()
    if handler_tf.name in visited:
        return []
    visited.add(handler_tf.name)

    handler_node = _lookup_handler_node(handler_tf, ctx)
    if handler_node is None:
        return []

    chain_ctx = WalkContext(
        handler_map=ctx.handler_map,
        all_tagged=ctx.all_tagged,
        externals=ctx.externals,
        emit_functions=ctx.emit_functions,
        spec=ctx.spec,
        req_id=ctx.req_id,
        max_depth=ctx.max_depth,
        visited=visited,
        file_cache=ctx.file_cache,
    )
    return walk_function_body(handler_node, handler_tf, chain_ctx, depth + 1)


## @brief Look up a handler's AST function node from the file cache.
#  @version 1.0
#  @internal
def _lookup_handler_node(
    handler_tf: TaggedFunction,
    ctx: WalkContext,
) -> Node | None:
    if ctx.file_cache is None:
        return None

    parsed: ParsedFile | None = ctx.file_cache.get(handler_tf.file_path)
    if parsed is None:
        return None
    return parsed.func_nodes.get(handler_tf.name)


## @brief Handle a control flow statement (while/for/if).
#  @version 1.1
#  @internal
def _handle_control_flow(
    node: Node,
    state: _WalkState,
) -> None:
    puml_type = state.ctx.spec.control_flow_types[node.type]
    body_node = node.child_by_field_name("body") or node.child_by_field_name("consequence")
    if body_node is None:
        return

    ext_names = set(state.ext_refs.keys())
    if not _has_tagged_content(body_node, state.ctx, ext_names):
        return

    condition = _extract_condition_text(node)

    if puml_type == "loop":
        state.edges.append(ASTEdge(kind="loop_start", label=condition))
        _walk_statements(body_node, state)
        state.edges.append(ASTEdge(kind="loop_end"))
    elif puml_type == "alt":
        _handle_alt_block(node, state, condition, ext_names)


## @brief Handle an alt (if/else) control flow block.
#  @version 1.0
#  @internal
def _handle_alt_block(
    node: Node,
    state: _WalkState,
    condition: str,
    ext_names: set[str],
) -> None:
    state.edges.append(ASTEdge(kind="alt_start", label=condition))
    consequence = node.child_by_field_name("consequence")
    if consequence:
        _walk_statements(consequence, state)
    alternative = node.child_by_field_name("alternative")
    if alternative and _has_tagged_content(alternative, state.ctx, ext_names):
        state.edges.append(ASTEdge(kind="else"))
        _walk_statements(alternative, state)
    state.edges.append(ASTEdge(kind="alt_end"))


## @brief Check if an AST subtree contains any tagged call expressions.
#  @version 1.0
#  @internal
def _has_tagged_content(
    node: Node,
    ctx: WalkContext,
    ext_names: set[str] | None = None,
) -> bool:
    if node.type == ctx.spec.call_node_type:
        callee = _extract_callee_name(node, ctx.spec)
        if callee and (
            callee in ctx.emit_functions
            or _is_tagged_call_target(callee, ctx)
            or (ext_names and callee in ext_names)
        ):
            return True
    return any(_has_tagged_content(child, ctx, ext_names) for child in node.named_children)


## @brief Check if a callee name is a known tagged function (REQ-filtered).
#  @version 1.1
#  @internal
def _is_tagged_call_target(callee: str, ctx: WalkContext) -> bool:
    for tf in ctx.all_tagged:
        if tf.name != callee:
            continue
        if ctx.req_id is None:
            return True
        is_relevant = ctx.req_id in tf.reqs or bool(tf.handles or tf.ext)
        is_support_only = ctx.req_id in tf.supports and ctx.req_id not in tf.reqs
        return is_relevant and not is_support_only
    return False


## @brief Find a TaggedFunction by callee name.
#  @version 1.0
#  @internal
def _find_tagged_function(callee: str, ctx: WalkContext) -> TaggedFunction | None:
    for tf in ctx.all_tagged:
        if tf.name == callee:
            return tf
    return None


## @brief Extract the callee function name from a call expression node.
#  @version 1.1
#  @internal
def _extract_callee_name(call_node: Node, spec: LanguageSpec) -> str | None:
    func_node = call_node.child_by_field_name("function")
    return _identifier_from_func_node(func_node) if func_node else None


## @brief Resolve a function AST node to its identifier string.
#  @version 1.0
#  @internal
def _identifier_from_func_node(func_node: Node) -> str | None:
    if func_node.type == "identifier":
        return func_node.text.decode("utf-8")
    target = (
        func_node.child_by_field_name("field")
        if func_node.type in ("field_expression", "member_expression")
        else next((c for c in func_node.named_children if c.type == "identifier"), None)
    )
    return target.text.decode("utf-8") if target else None


## @brief Extract condition text from a control flow node for labeling.
#  @version 1.0
#  @internal
def _extract_condition_text(node: Node) -> str:
    cond = node.child_by_field_name("condition")
    if cond:
        text = cond.text.decode("utf-8").strip()
        return text[:50] if len(text) > 50 else text
    return ""
