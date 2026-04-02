"""AST-ordered edge building for sequence diagrams.

@brief Walk tree-sitter AST to produce edges in source execution order with control flow blocks.
@version 1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doxygen_guard.tracer_models import (
    Edge,
    Participant,
    TaggedFunction,
    resolve_by_prefix,
    resolve_ext_target,
)

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
#  @version 1.1
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
    show_returns: bool = True
    participants: list[Participant] | None = None
    cross_req_depth: int = 1
    cross_req_hops: int = 0


## @brief Mutable state threaded through the recursive AST walk.
#  @version 1.1
#  @internal
@dataclass
class _WalkState:
    from_name: str
    tf: TaggedFunction
    ctx: WalkContext
    depth: int
    emit_set: set[str]
    emits_placed: set[str]
    ext_refs: dict[str, str]
    edges: list[ASTEdge]


## @brief Walk a function body AST to produce edges in source execution order.
#  @version 1.2
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
        emit_set=set(tf.emits),
        emits_placed=set(),
        ext_refs={ref.split("::", 1)[-1]: ref for ref in tf.ext},
        edges=edges,
    )

    _walk_statements(body, state)

    remaining = state.emit_set - state.emits_placed
    if remaining:
        _flush_remaining_emits(remaining, from_name, tf.name, ctx, depth, edges)

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
#  @version 1.4
#  @internal
def _handle_call(
    call_node: Node,
    state: _WalkState,
) -> None:
    callee = _extract_callee_name(call_node, state.ctx.spec)
    if callee is None:
        return

    unplaced = state.emit_set - state.emits_placed
    if callee in state.ctx.emit_functions and unplaced:
        _place_matched_emit(call_node, state)
        return

    if callee in state.ext_refs:
        _place_ext_edge(
            callee, state.ext_refs[callee], state.from_name, state.ctx, state.edges, call_node
        )
        return

    if _is_tagged_call_target(callee, state.ctx):
        target = _find_tagged_function(callee, state.ctx)
        if target:
            to_name = target.participant_name or target.name
            state.edges.append(
                ASTEdge(kind="call", edge=Edge(state.from_name, to_name, f"{callee}()"))
            )


## @brief Place an emit at the call site by matching the event argument.
#  @version 1.1
#  @internal
def _place_matched_emit(call_node: Node, state: _WalkState) -> None:
    event = _extract_emit_event_arg(call_node, state)
    if event and event in state.emit_set:
        state.emits_placed.add(event)
        _place_emit_edge(event, state)
        return

    unplaced = state.emit_set - state.emits_placed
    if unplaced:
        event = min(unplaced)
        state.emits_placed.add(event)
        _place_emit_edge(event, state)


## @brief Place a single emit edge and follow the handler chain.
#  @version 1.0
#  @internal
def _place_emit_edge(event: str, state: _WalkState) -> None:
    emit_edge, handler_tf = _resolve_emit(event, state.from_name, state.tf.name, state.ctx)
    if emit_edge:
        state.edges.append(ASTEdge(kind="emit", edge=emit_edge))
    if handler_tf and state.depth < state.ctx.max_depth:
        chain_edges = _follow_handler_chain(handler_tf, state.ctx, state.depth)
        state.edges.extend(chain_edges)


## @brief Extract the event name from an emit function call's first argument.
#  @version 1.0
#  @internal
def _extract_emit_event_arg(call_node: Node, state: _WalkState) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type == "identifier":
            raw = child.text.decode("utf-8")
            return _map_constant_to_event(raw, state.emit_set)
    return None


## @brief Map a C constant name to the matching event in the emit set.
#  @version 1.1
#  @internal
def _map_constant_to_event(constant: str, emit_set: set[str]) -> str | None:
    if constant in emit_set:
        return constant
    for event in emit_set:
        tag_name = event.split(":", 1)[-1] if ":" in event else event
        const_suffix = (
            constant.replace("EVENT_", "", 1) if constant.startswith("EVENT_") else constant
        )
        if tag_name == const_suffix:
            return event
    return None


## @brief Flush remaining emits that weren't matched to any call site.
#  @version 1.3
#  @internal
def _flush_remaining_emits(
    remaining: set[str],
    from_name: str,
    func_name: str,
    ctx: WalkContext,
    depth: int,
    edges: list[ASTEdge],
) -> None:
    for event in sorted(remaining):
        emit_edge, handler_tf = _resolve_emit(event, from_name, func_name, ctx)
        if emit_edge:
            edges.append(ASTEdge(kind="emit", edge=emit_edge))
        if handler_tf and depth < ctx.max_depth:
            chain_edges = _follow_handler_chain(handler_tf, ctx, depth)
            edges.extend(chain_edges)


## @brief Resolve an emit event to an edge and optionally a handler for chain following.
#  @version 1.1
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
        label = f"{func_name}() -> {handler.name}()"
        edge = Edge(from_name, to_name, label, event, "-->")
        return edge, handler

    prefix_target = resolve_by_prefix(event, ctx.externals)
    if prefix_target:
        edge = Edge(from_name, prefix_target, f"{func_name}()", event, "-->")
        return edge, None

    logger.warning("Unresolved event '%s' from %s()", event, func_name)
    edge = Edge(from_name, from_name, event, style="-->")
    return edge, None


## @brief Extract a compact argument string from a call expression for diagram labeling.
#  @version 1.0
#  @internal
def _extract_call_args_label(call_node: Node | None) -> str:
    if call_node is None:
        return ""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return ""
    raw = args.text.decode("utf-8")
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return " ".join(raw.split())


## @brief Place an ext edge for a resolved external call.
#  @version 1.2
#  @internal
def _place_ext_edge(
    callee: str,
    ext_ref: str,
    from_name: str,
    ctx: WalkContext,
    edges: list[ASTEdge],
    call_node: Node | None = None,
) -> None:
    parts = ext_ref.split("::", 1)
    func_name = parts[1] if len(parts) == 2 else ext_ref

    module = parts[0] if len(parts) == 2 else ""
    all_participants = ctx.participants if ctx.participants else ctx.externals
    to_name = (
        resolve_ext_target(func_name, module, ctx.all_tagged, all_participants)
        or module
        or parts[0]
    )
    if to_name == from_name and module:
        to_name = module.replace("_", " ").title()
    is_async = any(to_name == p.name for p in ctx.externals if p.receives_prefix)
    style = "-->" if is_async else "->"
    args_text = _extract_call_args_label(call_node) if call_node else ""
    label = f"{func_name}({args_text})" if args_text else f"{func_name}()"
    edges.append(ASTEdge(kind="ext", edge=Edge(from_name, to_name, label, style=style)))
    if ctx.show_returns:
        edges.append(ASTEdge(kind="ext", edge=Edge(to_name, from_name, "return", style="-->")))


## @brief Follow a handler's body to produce continuation edges.
#  @version 1.2
#  @internal
def _follow_handler_chain(
    handler_tf: TaggedFunction,
    ctx: WalkContext,
    depth: int,
) -> list[ASTEdge]:
    visited = ctx.visited or set()
    is_cross = ctx.req_id and ctx.req_id not in handler_tf.reqs
    new_hops = ctx.cross_req_hops + (1 if is_cross else 0)
    cross_blocked = ctx.cross_req_depth >= 0 and new_hops > ctx.cross_req_depth

    if handler_tf.name in visited or cross_blocked:
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
        show_returns=ctx.show_returns,
        participants=ctx.participants,
        cross_req_depth=ctx.cross_req_depth,
        cross_req_hops=new_hops,
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


## @brief Handle a control flow statement dispatching by puml_type.
#  @version 1.3
#  @internal
def _handle_control_flow(
    node: Node,
    state: _WalkState,
) -> None:
    puml_type = state.ctx.spec.control_flow_types[node.type]
    dispatched = _dispatch_specialized_control_flow(node, state, puml_type)
    if dispatched:
        return
    _handle_standard_control_flow(node, state, puml_type)


## @brief Dispatch specialized control flow types (try, switch, throw, goto, group).
#  @version 1.0
#  @internal
def _dispatch_specialized_control_flow(
    node: Node,
    state: _WalkState,
    puml_type: str,
) -> bool:
    if puml_type in ("throw_note", "goto_note"):
        _dispatch_note(node, state, puml_type)
        return True
    handlers = {"try": _handle_try_block, "switch": _handle_switch, "group": _handle_group}
    handler = handlers.get(puml_type)
    if handler:
        handler(node, state)
        return True
    return False


## @brief Handle standard loop/alt control flow blocks with pruning.
#  @version 1.0
#  @internal
def _handle_standard_control_flow(
    node: Node,
    state: _WalkState,
    puml_type: str,
) -> None:
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
#  @version 1.1
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
    elif not alternative:
        req_ctx = f" for {state.ctx.req_id}" if state.ctx.req_id else ""
        logger.warning(
            "alt block in %s() has no else — failure path undocumented%s",
            state.tf.name,
            req_ctx,
        )
    state.edges.append(ASTEdge(kind="alt_end"))


## @brief Handle a try/catch/finally/else block.
#  @details Error blocks (catch/except/finally) are NEVER pruned — their existence
#  is valuable even without tagged calls. Only the outer try is prunable if neither
#  body nor any handler has tagged content.
#  @version 1.0
#  @internal
def _handle_try_block(node: Node, state: _WalkState) -> None:
    body_node = node.child_by_field_name("body")
    handlers = _collect_try_children(node)
    ext_names = set(state.ext_refs.keys())

    has_content = (body_node and _has_tagged_content(body_node, state.ctx, ext_names)) or any(
        _has_tagged_content(h, state.ctx, ext_names) for h in handlers
    )
    if not has_content and not handlers:
        return

    state.edges.append(ASTEdge(kind="try_start"))
    if body_node:
        _walk_statements(body_node, state)
    state.edges.append(ASTEdge(kind="try_end"))

    for handler in handlers:
        _render_try_handler(handler, state)


## @brief Collect catch/except/finally/else children of a try statement.
#  @version 1.0
#  @internal
def _collect_try_children(node: Node) -> list:
    handler_types = (
        "catch_clause",
        "except_clause",
        "finally_clause",
        "else_clause",
    )
    return [c for c in node.named_children if c.type in handler_types]


## @brief Render a single try handler (catch/except/finally/else).
#  @version 1.0
#  @internal
def _render_try_handler(handler: Node, state: _WalkState) -> None:
    if handler.type in ("catch_clause", "except_clause"):
        label = _extract_exception_type(handler)
        state.edges.append(ASTEdge(kind="catch_start", label=label))
        _walk_statements(handler, state)
        state.edges.append(ASTEdge(kind="catch_end"))
    elif handler.type == "finally_clause":
        state.edges.append(ASTEdge(kind="finally_start"))
        _walk_statements(handler, state)
        state.edges.append(ASTEdge(kind="finally_end"))
    elif handler.type == "else_clause":
        _walk_statements(handler, state)


## @brief Extract the exception type from a catch/except clause.
#  @version 1.0
#  @internal
def _extract_exception_type(node: Node) -> str:
    for child in node.named_children:
        if child.type in ("type_identifier", "scoped_type_identifier", "identifier"):
            return f"({child.text.decode('utf-8')})"
    return ""


## @brief Handle a switch/case/match block.
#  @version 1.0
#  @internal
def _handle_switch(node: Node, state: _WalkState) -> None:
    condition = _extract_condition_text(node)
    body = node.child_by_field_name("body")
    if body is None:
        return

    cases = _collect_switch_cases(body)
    ext_names = set(state.ext_refs.keys())
    if not any(_has_tagged_content(c, state.ctx, ext_names) for c in cases):
        return

    first = True
    for case_node in cases:
        label = _extract_case_label(case_node)
        if first:
            state.edges.append(ASTEdge(kind="switch_start", label=f"{condition} [{label}]"))
            first = False
        elif label == "default":
            state.edges.append(ASTEdge(kind="switch_default"))
        else:
            state.edges.append(ASTEdge(kind="switch_case", label=f" [{label}]"))
        _walk_statements(case_node, state)
    state.edges.append(ASTEdge(kind="switch_end"))


## @brief Collect case/default children from a switch body.
#  @version 1.0
#  @internal
def _collect_switch_cases(body: Node) -> list:
    case_types = ("case_statement", "case_clause", "default_statement")
    return [c for c in body.named_children if c.type in case_types]


## @brief Extract label text from a case node.
#  @version 1.0
#  @internal
def _extract_case_label(case_node: Node) -> str:
    if "default" in case_node.type:
        return "default"
    value = case_node.child_by_field_name("value")
    if value:
        text = value.text.decode("utf-8").strip()
        return text[:30] if len(text) > 30 else text
    return ""


## @brief Emit a throw/raise or goto note edge.
#  @version 1.0
#  @internal
def _dispatch_note(node: Node, state: _WalkState, puml_type: str) -> None:
    label = _extract_note_label(node, puml_type)
    state.edges.append(ASTEdge(kind=puml_type, label=label))


## @brief Extract label text for throw/goto notes.
#  @version 1.0
#  @internal
def _extract_note_label(node: Node, puml_type: str) -> str:
    if puml_type == "goto_note":
        label_node = node.child_by_field_name("label")
        label = label_node.text.decode("utf-8") if label_node else ""
        return f"goto {label}"
    for child in node.named_children:
        if child.type in ("type_identifier", "identifier", "scoped_type_identifier"):
            return f"<<throws {child.text.decode('utf-8')}>>"
    return "<<throws>>"


## @brief Handle a with statement (Python) as a generic group.
#  @version 1.0
#  @internal
def _handle_group(node: Node, state: _WalkState) -> None:
    body_node = node.child_by_field_name("body")
    if body_node is None:
        return
    ext_names = set(state.ext_refs.keys())
    if not _has_tagged_content(body_node, state.ctx, ext_names):
        return
    label = _extract_with_label(node)
    state.edges.append(ASTEdge(kind="group_start", label=label))
    _walk_statements(body_node, state)
    state.edges.append(ASTEdge(kind="group_end"))


## @brief Extract the resource name from a with statement.
#  @version 1.0
#  @internal
def _extract_with_label(node: Node) -> str:
    for child in node.named_children:
        if child.type == "with_clause":
            text = child.text.decode("utf-8").strip()
            return text[:40] if len(text) > 40 else text
        if child.type in ("as_pattern", "with_item"):
            text = child.text.decode("utf-8").strip()
            return text[:40] if len(text) > 40 else text
    return "with"


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
#  @version 1.1
#  @internal
def _extract_condition_text(node: Node) -> str:
    cond = node.child_by_field_name("condition")
    if cond:
        text = cond.text.decode("utf-8").strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1].strip()
        return text[:50] if len(text) > 50 else text
    return ""
