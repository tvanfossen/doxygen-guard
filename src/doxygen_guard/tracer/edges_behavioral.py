"""Annotation-driven behavioral edge building for sequence diagrams.

@brief Build edges from @sends/@receives/@calls annotations with
causal ordering, entry chain support, and boundary-argument extraction.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from doxygen_guard.config import get_trace, get_trace_options
from doxygen_guard.tracer_models import (
    ASTEdge,
    Edge,
    Participant,
    TaggedFunction,
    resolve_by_prefix,
    split_calls_ref,
)

logger = logging.getLogger(__name__)


## @brief Build the global handler map from ALL tagged functions.
#  @version 2.0
#  @internal
#  @return Dict mapping event name to list of handler TaggedFunctions
def _build_handler_map(
    all_tagged: list[TaggedFunction],
) -> dict[str, list[TaggedFunction]]:
    handler_map: dict[str, list[TaggedFunction]] = {}
    for tf in all_tagged:
        for event in tf.receives:
            if event in handler_map:
                existing = handler_map[event][0]
                logger.warning(
                    "Duplicate handler for '%s': %s() and %s()",
                    event,
                    existing.name,
                    tf.name,
                )
            handler_map.setdefault(event, []).append(tf)
    return handler_map


## @brief Map events to their emitter's (participant_name, func_name).
#  @version 2.0
#  @req REQ-TRACE-001
#  @return Dict mapping event name to (participant, func_name) of first emitter
def _build_emitter_participant_map(
    all_tagged: list[TaggedFunction],
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for tf in sorted(all_tagged, key=lambda t: t.name):
        for event in tf.sends:
            if event not in result and tf.participant_name:
                result[event] = (tf.participant_name, tf.name)
    return result


## @brief Collect participant names targeted by a function's @calls.
#  @version 1.0
#  @internal
#  @return Set of participant names this function calls across boundaries
def _calls_target_participants(
    tf: TaggedFunction, externals: list[Participant], participant_configs: dict[str, dict]
) -> set[str]:
    targets: set[str] = set()
    for ref in tf.calls:
        _, func_name = split_calls_ref(ref)
        target_p = _resolve_calls_target(func_name, "", externals, participant_configs)
        if target_p:
            targets.add(target_p)
    return targets


## @brief Add causal links from @calls targets to @receives handlers on same participant.
#  @version 1.0
#  @internal
def _add_calls_receives_links(
    emitters: list[TaggedFunction],
    adj: dict[str, list[str]],
    in_degree: dict[str, int],
    externals: list[Participant] | None,
    participant_configs: dict[str, dict] | None,
) -> None:
    if not externals:
        return
    for tf in emitters:
        targets = _calls_target_participants(tf, externals, participant_configs or {})
        if not targets:
            continue
        for other in emitters:
            if other.name == tf.name or other.name not in adj:
                continue
            if _receives_from_any(other, targets, externals) and other.name not in adj[tf.name]:
                adj[tf.name].append(other.name)
                in_degree[other.name] = in_degree.get(other.name, 0) + 1


## @brief Check if a function receives from any of the given participants.
#  @version 1.0
#  @internal
#  @return True if any @receives event matches a target participant
def _receives_from_any(tf: TaggedFunction, targets: set[str], externals: list[Participant]) -> bool:
    for event in tf.receives:
        source = resolve_by_prefix(event, externals)
        if source in targets:
            return True
    return False


## @brief Build a causal dependency graph from sends/receives relationships.
#  @version 2.1
#  @internal
#  @return Tuple of (adjacency dict, in-degree dict)
def _build_causal_graph(
    emitters: list[TaggedFunction],
    externals: list[Participant] | None = None,
    participant_configs: dict[str, dict] | None = None,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    send_map: dict[str, str] = {}
    for tf in emitters:
        for event in tf.sends:
            send_map[event] = tf.name

    adj: dict[str, list[str]] = {tf.name: [] for tf in emitters}
    in_degree: dict[str, int] = {tf.name: 0 for tf in emitters}

    for tf in emitters:
        for event in tf.receives:
            producer = send_map.get(event)
            if producer and producer != tf.name and producer in adj:
                adj[producer].append(tf.name)
                in_degree[tf.name] += 1

    _add_calls_receives_links(emitters, adj, in_degree, externals, participant_configs)
    return adj, in_degree


## @brief Sort emitters by causal dependency.
#  @version 2.1
#  @internal
#  @return List of TaggedFunctions in causal order
def _toposort_emitters(
    emitters: list[TaggedFunction],
    entry_names: set[str] | None = None,
    externals: list[Participant] | None = None,
    participant_configs: dict[str, dict] | None = None,
) -> list[TaggedFunction]:
    if len(emitters) <= 1:
        return emitters

    name_to_tf: dict[str, TaggedFunction] = {tf.name: tf for tf in emitters}
    adj, in_degree = _build_causal_graph(emitters, externals, participant_configs)

    entries = entry_names or set()
    zeros = [name for name, deg in in_degree.items() if deg == 0]
    zeros.sort(key=lambda n: (n not in entries, not bool(name_to_tf[n].receives)))
    queue = deque(zeros)
    result: list[TaggedFunction] = []

    while queue:
        name = queue.popleft()
        result.append(name_to_tf[name])
        for neighbor in adj[name]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    seen = {tf.name for tf in result}
    for tf in emitters:
        if tf.name not in seen:
            result.append(tf)

    return result


## @brief Group entry edges by handler; wrap multi-event handlers in alt blocks.
#  @version 2.0
#  @req REQ-TRACE-001
#  @return List of ASTEdge objects with entry edges and alt wrappers
def _group_entry_edges(entries: list[Edge]) -> list[ASTEdge]:
    groups: dict[str, list[Edge]] = {}
    for edge in entries:
        groups.setdefault(edge.label, []).append(edge)

    result: list[ASTEdge] = []
    for edges in groups.values():
        if len(edges) == 1:
            result.append(ASTEdge(kind="entry", edge=edges[0]))
            continue
        for i, edge in enumerate(edges):
            event_label = edge.event or ""
            kind = "switch_start" if i == 0 else "switch_case"
            label = event_label if i == 0 else f"[{event_label}]"
            result.append(ASTEdge(kind=kind, label=label))
            result.append(ASTEdge(kind="entry", edge=edge))
        result.append(ASTEdge(kind="switch_end"))
    return result


## @brief Collect unique @after REQ IDs from a list of tagged functions.
#  @version 2.0
#  @internal
#  @return Deduplicated list of precondition REQ IDs
def _collect_after(funcs: list[TaggedFunction]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tf in funcs:
        for req in tf.after:
            if req not in seen:
                seen.add(req)
                result.append(req)
    return result


## @brief Shared context for building edges within a single diagram.
#  @version 1.0
#  @internal
@dataclass
class _EdgeContext:
    handler_map: dict[str, list[TaggedFunction]]
    externals: list[Participant]
    participant_configs: dict[str, dict]
    label_mode: str
    send_fns: set[str]
    file_cache: dict | None
    req_names: set[str] = field(default_factory=set)
    call_type_c: str = "call_expression"


## @brief Infer entry edges from unresolved @receives events.
#  @version 2.3
#  @req REQ-TRACE-001
#  @return List of entry edges for external triggers
def _infer_entry_edges(
    req_funcs: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    file_cache: dict | None = None,
) -> list[Edge]:
    ctx = _EntryResolveCtx(
        req_names={tf.name for tf in req_funcs},
        emitter_map=_build_emitter_participant_map(all_tagged),
        externals=[p for p in participants if p.receives_prefix],
        all_tagged=all_tagged,
        file_cache=file_cache,
    )

    entries: list[Edge] = []
    seen_events: set[str] = set()

    for tf in req_funcs:
        if tf.loop or tf.group:
            continue
        for event in tf.receives:
            if event in seen_events:
                continue
            entry = _resolve_entry_edge(event, tf, ctx)
            seen_events.add(event)
            if entry:
                entries.append(entry)

    return entries


## @brief Bundle of resolution dependencies for entry edge inference.
#  @version 1.0
#  @internal
@dataclass
class _EntryResolveCtx:
    req_names: set[str]
    emitter_map: dict[str, tuple[str, str]]
    externals: list[Participant]
    all_tagged: list[TaggedFunction] | None = None
    file_cache: dict | None = None


## @brief Resolve a single entry edge for an unresolved @receives event.
#  @details If the event is internal (no prefix match), backtracks one hop through
#  the global emitter map to find the upstream function's external entry point.
#  @version 1.3
#  @internal
#  @return Edge if source resolved, None if unresolvable
def _resolve_entry_edge(event: str, tf: TaggedFunction, ctx: _EntryResolveCtx) -> Edge | None:
    emitter_participant = ctx.emitter_map.get(event)
    if emitter_participant and emitter_participant[1] in ctx.req_names:
        return None

    resolved = _resolve_entry_source(
        event, tf, emitter_participant, ctx.externals, ctx.all_tagged, ctx.file_cache
    )
    if resolved:
        keys = _collect_req_dispatch_keys(tf, ctx.all_tagged)
        label = _append_dispatch_keys(resolved[1], keys)
        return Edge(resolved[0], tf.display_name, label, event=resolved[2], style="-->")

    logger.warning(
        "No source participant for @receives '%s' in %s() — omitting entry edge",
        event,
        tf.name,
    )
    return None


## @brief Resolve the source participant for an entry edge, with hub backtracking.
#  @version 1.2
#  @internal
#  @return Tuple of (source, label, event) or None
def _resolve_entry_source(
    event: str,
    tf: TaggedFunction,
    emitter_participant: tuple[str, str] | None,
    externals: list[Participant],
    all_tagged: list[TaggedFunction] | None,
    file_cache: dict | None = None,
) -> tuple[str, str, str] | None:
    source = resolve_by_prefix(event, externals) or (
        emitter_participant[0] if emitter_participant else None
    )
    is_self = source is not None and source == tf.display_name

    if source and not is_self:
        # Scope payload to events received by other REQ functions (downstream handlers)
        downstream_events = _collect_downstream_events(tf, all_tagged) if all_tagged else None
        label = _enrich_with_payload(
            _strip_prefix(event), tf, file_cache, target_events=downstream_events
        )
        return (source, label, event)

    # Backtrack through dispatch hub: trace emitter's @receives to find external entry
    if emitter_participant and all_tagged:
        return _backtrack_to_external_entry(
            emitter_participant[1],
            all_tagged,
            externals,
            file_cache,
            target_handler=tf.name,
            target_events=set(tf.receives),
        )

    return None


## @brief Collect events that REQ-scope downstream handlers receive.
#  @details Used when the entry handler is itself a hub: scopes payload extraction
#  to discriminator branches that route to events handled by REQ-tagged handlers.
#  @version 1.0
#  @internal
#  @return Set of event names received by other functions in the same REQ scope
def _collect_downstream_events(
    hub: TaggedFunction, all_tagged: list[TaggedFunction]
) -> set[str] | None:
    hub_reqs = set(hub.reqs)
    if not hub_reqs:
        return None
    events: set[str] = set()
    for tf in all_tagged:
        if tf.name == hub.name:
            continue
        if hub_reqs & set(tf.reqs):
            events.update(tf.receives)
    return events or None


## @brief Append @dispatch_key fragments to an entry label.
#  @details Each dispatch_key value becomes a "\n*VALUE" payload line. Used when
#  AST extraction can't infer the discriminator (e.g., multi-hop dispatch chains).
#  @version 1.0
#  @internal
#  @return Label with dispatch_key fragments appended
def _append_dispatch_keys(label: str, dispatch_keys: list[str]) -> str:
    if not dispatch_keys:
        return label
    return label + "".join(f"\\n*{k}" for k in dispatch_keys)


## @brief Collect @dispatch_key values from all functions in the same REQ scope.
#  @details The entry edge for a REQ may be produced by a hub function, but the
#  @dispatch_key annotations live on the REQ-tagged downstream handlers.
#  @version 1.0
#  @internal
#  @return Deduplicated list of dispatch_key values from REQ-scope functions
def _collect_req_dispatch_keys(
    entry_tf: TaggedFunction, all_tagged: list[TaggedFunction] | None
) -> list[str]:
    keys: list[str] = list(entry_tf.dispatch_keys)
    if all_tagged is None:
        return keys
    seen = set(keys)
    entry_reqs = set(entry_tf.reqs)
    if not entry_reqs:
        return keys
    for tf in all_tagged:
        if tf.name == entry_tf.name:
            continue
        if entry_reqs & set(tf.reqs):
            for k in tf.dispatch_keys:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
    return keys


## @brief Enrich an entry label with payload context from conditional comparisons.
#  @details Scans the handler function's body for strcmp/== patterns and appends
#  extracted string literals as payload fragments ("\n*state:clean"). When
#  target_call is provided (hub backtracking), only payloads inside conditionals
#  that route to a call to target_call are included.
#  @version 1.1
#  @internal
#  @return Label with appended payload fragments, or original label if none found
def _enrich_with_payload(
    base_label: str,
    tf: TaggedFunction,
    file_cache: dict | None,
    target_call: str | None = None,
    target_events: set[str] | None = None,
) -> str:
    body_node = _get_body_node(tf, file_cache)
    if body_node is None:
        return base_label
    payloads: list[str] = []
    if target_call or target_events:
        _scan_payloads_routing_to(body_node, target_call or "", payloads, target_events)
    else:
        _scan_for_string_comparisons(body_node, payloads)
    if not payloads:
        return base_label
    return base_label + "".join(f"\\n*{p}" for p in payloads)


## @brief Find payload literals that gate routing to a specific target.
#  @details Walks if/switch blocks; when a block contains a routing path to the
#  target (direct call, or event_post of an event in target_events), extracts
#  the discriminator literals from the controlling condition.
#  @version 1.1
#  @internal
def _scan_payloads_routing_to(
    node: Any,
    target_call: str,
    payloads: list[str],
    target_events: set[str] | None = None,
) -> None:
    target_events = target_events or set()
    if node.type == "if_statement":
        _extract_if_payloads(node, target_call, target_events, payloads)
    elif node.type == "case_statement" and _block_routes_to(node, target_call, target_events):
        _extract_case_payloads(node, payloads)
    for child in node.named_children:
        _scan_payloads_routing_to(child, target_call, payloads, target_events)


## @brief Extract payloads from an if_statement's condition if it routes to target.
#  @version 1.0
#  @internal
def _extract_if_payloads(
    node: Any, target_call: str, target_events: set[str], payloads: list[str]
) -> None:
    consequent = node.child_by_field_name("consequence")
    if not consequent or not _block_routes_to(consequent, target_call, target_events):
        return
    cond = node.child_by_field_name("condition")
    if cond:
        _scan_for_string_comparisons(cond, payloads)


## @brief Extract payloads from a case_statement's literal/identifier children.
#  @version 1.0
#  @internal
def _extract_case_payloads(node: Any, payloads: list[str]) -> None:
    for child in node.children:
        if child.type == "string_literal" and child.text:
            payloads.append(child.text.decode("utf-8").strip('"'))
        elif child.type == "identifier" and child.text:
            payloads.append(child.text.decode("utf-8"))


## @brief Check if a block routes to target via direct call or event_post.
#  @version 1.0
#  @internal
#  @return True if any descendant calls target or posts a target_event
def _block_routes_to(node: Any, target_call: str, target_events: set[str]) -> bool:
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func and func.text:
            fname = func.text.decode("utf-8")
            if fname == target_call:
                return True
            if target_events and _call_posts_target_event(node, target_events):
                return True
    return any(_block_routes_to(child, target_call, target_events) for child in node.named_children)


## @brief Check if a call_expression posts an event in target_events.
#  @version 1.0
#  @internal
#  @return True if the first argument matches a target event name
def _call_posts_target_event(call_node: Any, target_events: set[str]) -> bool:
    args = call_node.child_by_field_name("arguments")
    if not args:
        return False
    for child in args.named_children:
        if child.text and child.text.decode("utf-8") in target_events:
            return True
    return False


## @brief Backtrack from an internal emitter to its external entry point.
#  @details Finds the upstream function by name, checks its @receives for
#  an externally-resolvable event, and returns (source, label, event).
#  When target_handler is provided, payload extraction is scoped to
#  conditionals in the hub that route to the named handler.
#  @version 1.2
#  @internal
#  @return Tuple of (source_participant, label, event) or None
def _backtrack_to_external_entry(
    emitter_name: str,
    all_tagged: list[TaggedFunction],
    externals: list[Participant],
    file_cache: dict | None = None,
    target_handler: str | None = None,
    target_events: set[str] | None = None,
) -> tuple[str, str, str] | None:
    upstream_tf = next((tf for tf in all_tagged if tf.name == emitter_name), None)
    if not upstream_tf:
        return None
    for upstream_event in upstream_tf.receives:
        source = resolve_by_prefix(upstream_event, externals)
        if source:
            label = _enrich_with_payload(
                _strip_prefix(upstream_event),
                upstream_tf,
                file_cache,
                target_handler,
                target_events,
            )
            return (source, label, upstream_event)
    return None


## @brief Strip bus prefix from event name for label display.
#  @version 1.0
#  @internal
#  @return Event name with prefix removed
def _strip_prefix(event: str) -> str:
    return event.split(":", 1)[-1] if ":" in event else event


## @brief Build entry chain edges from participant config.
#  @version 1.0
#  @internal
#  @return List of ASTEdge chain arrows
def _build_entry_chain_from_config(
    pconf: dict,
    req_name: str,
) -> list[ASTEdge]:
    chain = pconf.get("entry_chain", [])
    result: list[ASTEdge] = []
    for step in chain:
        from_name = step.get("from", "")
        to_name = step.get("to", "")
        label = step.get("label", "{req_name}").replace("{req_name}", req_name)
        if from_name and to_name:
            result.append(ASTEdge(kind="entry", edge=Edge(from_name, to_name, label, style="->")))
    return result


## @brief Extract arguments from boundary function call sites using tree-sitter.
#  @version 1.0
#  @internal
#  @return List of argument lists, one per call site found
def _extract_boundary_args(func_name: str, body_node: Any, call_type: str) -> list[list[str]]:
    results: list[list[str]] = []
    _find_boundary_calls(body_node, func_name, call_type, results)
    return results


## @brief Recursively find calls to a specific function and extract args.
#  @version 1.0
#  @internal
def _find_boundary_calls(node: Any, target: str, call_type: str, results: list[list[str]]) -> None:
    if node.type == call_type:
        _try_extract_call_args(node, target, results)
    for child in node.named_children:
        _find_boundary_calls(child, target, call_type, results)


## @brief Extract arguments from a call node if it matches the target function.
#  @version 1.0
#  @internal
def _try_extract_call_args(node: Any, target: str, results: list[list[str]]) -> None:
    func_node = node.child_by_field_name("function")
    if not (func_node and func_node.text and func_node.text.decode("utf-8") == target):
        return
    args = node.child_by_field_name("arguments")
    if not args:
        return
    arg_texts = [child.text.decode("utf-8") if child.text else "" for child in args.named_children]
    results.append(arg_texts)


## @brief Apply label_template to extracted arguments.
#  @version 1.0
#  @internal
#  @return Formatted label string
def _apply_label_template(template: str, args: list[str]) -> str:
    result = template
    for i, arg in enumerate(args):
        cleaned = arg.strip('"').strip("'")
        result = result.replace(f"{{arg{i}}}", cleaned)
    return result


## @brief Format a boundary call label from function name and arguments.
#  @version 1.0
#  @internal
#  @return Formatted label for the boundary call edge
def _format_boundary_label(func_name: str, args: list[str], label_template: str | None) -> str:
    if label_template and args:
        return _apply_label_template(label_template, args)
    if args:
        return f"{func_name}({', '.join(args)})"
    return f"{func_name}()"


## @brief Extract condition text from a control-flow node.
#  @version 1.0
#  @internal
#  @return Condition text, truncated if too long
def _extract_condition_text(node: Any, max_len: int) -> str:
    condition = node.child_by_field_name("condition")
    text = condition.text.decode("utf-8") if condition and condition.text else ""
    return text[:max_len] + "..." if len(text) > max_len else text


## @brief Find the enclosing control-flow block for a call node.
#  @version 1.0
#  @internal
#  @return Tuple of (block_type, condition_text) or None
def _find_enclosing_control_flow(node: Any, max_condition: int = 80) -> tuple[str, str] | None:
    current = node.parent
    while current:
        if current.type in ("while_statement", "for_statement"):
            return ("loop", _extract_condition_text(current, max_condition))
        if current.type == "if_statement":
            return ("alt", _extract_condition_text(current, max_condition))
        current = current.parent
    return None


## @brief Recursively scan for string comparison patterns.
#  @version 1.0
#  @internal
def _scan_for_string_comparisons(node: Any, payloads: list[str]) -> None:
    _check_strcmp_call(node, payloads)
    _check_equality_comparison(node, payloads)
    for child in node.named_children:
        _scan_for_string_comparisons(child, payloads)


## @brief Check if node is a strcmp() call and extract string literal args.
#  @version 1.0
#  @internal
def _check_strcmp_call(node: Any, payloads: list[str]) -> None:
    if node.type != "call_expression":
        return
    func = node.child_by_field_name("function")
    if not (func and func.text and func.text.decode("utf-8") == "strcmp"):
        return
    args = node.child_by_field_name("arguments")
    if not args:
        return
    for child in args.named_children:
        if child.type == "string_literal" and child.text:
            payloads.append(child.text.decode("utf-8").strip('"'))


## @brief Check if node is an == comparison with a string literal.
#  @version 1.0
#  @internal
def _check_equality_comparison(node: Any, payloads: list[str]) -> None:
    if node.type != "binary_expression":
        return
    op = node.child_by_field_name("operator")
    if not (op and op.text and op.text.decode("utf-8") == "=="):
        return
    for child in node.named_children:
        if child.type == "string_literal" and child.text:
            payloads.append(child.text.decode("utf-8").strip('"'))


## @brief Build annotation-driven behavioral edges for a REQ's functions.
#  @version 1.2
#  @req REQ-TRACE-001
#  @return List of ASTEdge objects representing the behavioral sequence diagram
def build_behavioral_edges(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    req_id: str | None = None,
    file_cache: dict | None = None,
) -> list[ASTEdge]:
    ctx = _build_edge_context(all_tagged, participants, config, file_cache)
    ctx.req_names = {tf.name for tf in emitters}
    participant_configs = ctx.participant_configs
    req_name = _resolve_req_name(req_id, config)

    entry_edges = _infer_entry_edges(emitters, all_tagged, participants, config, file_cache)
    ast_edges: list[ASTEdge] = _build_entry_section(entry_edges, participant_configs, req_name)

    entry_names = {e.label.split("(")[0] for e in entry_edges if "(" in e.label}
    for tf in _toposort_emitters(emitters, entry_names, ctx.externals, ctx.participant_configs):
        ast_edges.extend(_build_edges_for_function(tf, ctx))

    return ast_edges


## @brief Create the edge context from config and participants.
#  @version 1.0
#  @internal
#  @return Populated _EdgeContext
def _build_edge_context(
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    file_cache: dict | None,
) -> _EdgeContext:
    trace_config = get_trace(config)
    trace_options = get_trace_options(config)

    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix or p.boundary_functions]

    ext_config = trace_config.get("external", [])
    participant_configs: dict[str, dict] = {}
    for ext_entry in ext_config:
        if isinstance(ext_entry, dict):
            for pname, pconf in ext_entry.items():
                participant_configs[pname] = pconf if isinstance(pconf, dict) else {}

    return _EdgeContext(
        handler_map=handler_map,
        externals=externals,
        participant_configs=participant_configs,
        label_mode=trace_options.get("label_mode", "brief"),
        send_fns=set(trace_options.get("event_send_functions", [])),
        file_cache=file_cache,
    )


## @brief Resolve requirement name from CSV for entry chain labels.
#  @version 1.0
#  @internal
#  @return Requirement name string, empty if not found
def _resolve_req_name(req_id: str | None, config: dict[str, Any]) -> str:
    if not req_id:
        return ""
    from doxygen_guard.impact import load_requirements_full

    req_data = load_requirements_full(config)
    if req_id not in req_data:
        return req_id
    name_col = config.get("impact", {}).get("requirements", {}).get("name_column", "name")
    return req_data[req_id].get(name_col, req_id)


## @brief Build entry section: chains + entry edges.
#  @version 1.0
#  @internal
#  @return List of ASTEdge for the entry section
def _build_entry_section(
    entry_edges: list[Edge],
    participant_configs: dict[str, dict],
    req_name: str,
) -> list[ASTEdge]:
    ast_edges: list[ASTEdge] = []
    for entry in entry_edges:
        pconf = participant_configs.get(entry.from_name)
        if pconf:
            ast_edges.extend(_build_entry_chain_from_config(pconf, req_name))
        ast_edges.append(ASTEdge(kind="entry", edge=entry))
    return ast_edges


## @brief Build behavioral edges for a single function.
#  @version 1.1
#  @internal
#  @return List of ASTEdge for this function's section
def _build_edges_for_function(tf: TaggedFunction, ctx: _EdgeContext) -> list[ASTEdge]:
    result: list[ASTEdge] = []
    body_node = _get_body_node(tf, ctx.file_cache)
    call_type = "call" if tf.file_path.endswith(".py") else ctx.call_type_c

    _append_wrappers_open(tf, result)
    _append_inline_receives(tf, ctx, result)
    result.append(ASTEdge(kind="section", label=f"{tf.name}()"))
    _append_notes(tf, result)
    _append_send_edges(tf, ctx, body_node, call_type, result)
    _append_calls_edges(tf, ctx, body_node, call_type, result)
    _append_wrappers_close(tf, result)

    return result


## @brief Append @loop/@group open markers.
#  @version 1.0
#  @internal
def _append_wrappers_open(tf: TaggedFunction, result: list[ASTEdge]) -> None:
    if tf.loop:
        result.append(ASTEdge(kind="loop_start", label=tf.loop))
    if tf.group:
        result.append(ASTEdge(kind="group_start", label=tf.group))


## @brief Append @loop/@group close markers.
#  @version 1.0
#  @internal
def _append_wrappers_close(tf: TaggedFunction, result: list[ASTEdge]) -> None:
    if tf.group:
        result.append(ASTEdge(kind="group_end"))
    if tf.loop:
        result.append(ASTEdge(kind="loop_end"))


## @brief Append inline @receives entry edges for response handlers in loop/group.
#  @version 1.0
#  @internal
def _append_inline_receives(tf: TaggedFunction, ctx: _EdgeContext, result: list[ASTEdge]) -> None:
    if not (tf.loop or tf.group):
        return
    for event in tf.receives:
        source = resolve_by_prefix(event, ctx.externals)
        if source:
            label = _strip_prefix(event)
            result.append(
                ASTEdge(
                    kind="entry",
                    edge=Edge(source, tf.display_name, label, event=event, style="-->"),
                )
            )


## @brief Append @note edges for a function.
#  @version 1.0
#  @internal
def _append_notes(tf: TaggedFunction, result: list[ASTEdge]) -> None:
    for note_text in tf.notes:
        note_label = _humanize_note(note_text)
        result.append(
            ASTEdge(
                kind="trigger",
                edge=Edge(tf.display_name, tf.display_name, note_label, style="note"),
            )
        )


## @brief Append @sends edges with control-flow framing.
#  @version 1.1
#  @internal
def _append_send_edges(
    tf: TaggedFunction, ctx: _EdgeContext, body_node: Any, call_type: str, result: list[ASTEdge]
) -> None:
    placed: set[str] = set()
    is_dispatch = len(tf.sends) > 1
    for event in tf.sends:
        if is_dispatch and ctx.req_names:
            handlers = ctx.handler_map.get(event, [])
            if not any(h.name in ctx.req_names for h in handlers):
                continue
        edge = _make_send_edge(event, tf, ctx.handler_map, ctx.label_mode)
        if body_node and ctx.send_fns:
            cf = _find_send_in_body(event, body_node, call_type, ctx.send_fns)
            if cf:
                result.append(ASTEdge(kind=f"{cf[0]}_start", label=cf[1]))
                result.append(edge)
                result.append(ASTEdge(kind=f"{cf[0]}_end"))
                placed.add(event)
                continue
        result.append(edge)
        placed.add(event)


## @brief Split @calls value into (module::func, optional_manual_label).
#  @details Syntax: '@calls module::func "Label text"' returns ('module::func', 'Label text').
#  Without quotes: returns (value, None).
#  @version 1.1
#  @internal
#  @return Tuple of (reference, manual_label or None)
def _split_calls_value(value: str) -> tuple[str, str | None]:
    m = re.match(r'^(\S+)\s+"([^"]+)"\s*$', value)
    if m:
        return (m.group(1), m.group(2))
    return (value.strip(), None)


## @brief Append @calls edges with manual label override or boundary-argument extraction.
#  @version 1.1
#  @internal
def _append_calls_edges(
    tf: TaggedFunction, ctx: _EdgeContext, body_node: Any, call_type: str, result: list[ASTEdge]
) -> None:
    for calls_ref in tf.calls:
        ref, manual_label = _split_calls_value(calls_ref)
        module, func_name = split_calls_ref(ref)
        target = _resolve_calls_target(func_name, module, ctx.externals, ctx.participant_configs)
        to_name = target or module or func_name

        if manual_label:
            result.append(
                ASTEdge(kind="ext", edge=Edge(tf.display_name, to_name, manual_label, style="->"))
            )
            continue

        template = _get_label_template(func_name, ctx.participant_configs)
        call_sites = _extract_boundary_args(func_name, body_node, call_type) if body_node else []
        if call_sites:
            for args in call_sites:
                label = _format_boundary_label(func_name, args, template)
                result.append(
                    ASTEdge(kind="ext", edge=Edge(tf.display_name, to_name, label, style="->"))
                )
        else:
            label = _format_boundary_label(func_name, [], template)
            result.append(
                ASTEdge(kind="ext", edge=Edge(tf.display_name, to_name, label, style="->"))
            )


## @brief Create a send edge (dashed arrow) from emitter to handler's participant.
#  @version 1.0
#  @internal
#  @return ASTEdge with emit kind
def _make_send_edge(
    event: str,
    tf: TaggedFunction,
    handler_map: dict[str, list[TaggedFunction]],
    label_mode: str,
) -> ASTEdge:
    handlers = handler_map.get(event, [])
    to_name = handlers[0].display_name if handlers else tf.display_name
    label = _strip_prefix(event) if label_mode == "brief" else event
    return ASTEdge(
        kind="emit", edge=Edge(tf.display_name, to_name, label, event=event, style="-->")
    )


## @brief Find a send (event_post) call in the body and check for control flow.
#  @version 1.0
#  @internal
#  @return Tuple of (block_type, condition) or None
def _find_send_in_body(
    event: str, body_node: Any, call_type: str, send_fns: set[str]
) -> tuple[str, str] | None:
    call_node = _find_event_post_call(event, body_node, call_type, send_fns)
    return _find_enclosing_control_flow(call_node) if call_node else None


## @brief Find the call node for event_post(EVENT_X) in the body.
#  @version 1.0
#  @internal
#  @return The matching call AST node, or None
def _find_event_post_call(event: str, node: Any, call_type: str, send_fns: set[str]) -> Any | None:
    if node.type == call_type and _is_event_post_of(node, event, send_fns):
        return node
    for child in node.named_children:
        found = _find_event_post_call(event, child, call_type, send_fns)
        if found is not None:
            return found
    return None


## @brief Check if a call node is an event_post call with the given event argument.
#  @version 1.0
#  @internal
#  @return True if the call matches
def _is_event_post_of(node: Any, event: str, send_fns: set[str]) -> bool:
    func = node.child_by_field_name("function")
    if not (func and func.text and func.text.decode("utf-8") in send_fns):
        return False
    args = node.child_by_field_name("arguments")
    if not args:
        return False
    return any(child.text and child.text.decode("utf-8") == event for child in args.named_children)


## @brief Get function body AST node from file cache.
#  @version 1.0
#  @internal
#  @return Body AST node or None
def _get_body_node(tf: TaggedFunction, file_cache: dict | None) -> Any | None:
    if not file_cache:
        return None
    parsed = file_cache.get(tf.file_path)
    func_node = parsed.func_nodes.get(tf.name) if parsed else None
    return func_node.child_by_field_name("body") if func_node else None


## @brief Resolve a @calls target to a participant name.
#  @version 1.0
#  @internal
#  @return Participant name or None
def _resolve_calls_target(
    func_name: str,
    module: str,
    externals: list[Participant],
    participant_configs: dict[str, dict],
) -> str | None:
    for p in externals:
        if func_name in p.boundary_functions:
            return p.name
    for pname, pconf in participant_configs.items():
        if func_name in pconf.get("boundary_functions", []):
            return pname
    return module.replace("_", " ").title() if module else None


## @brief Get label_template for a boundary function from participant configs.
#  @version 1.0
#  @internal
#  @return Template string or None
def _get_label_template(func_name: str, participant_configs: dict[str, dict]) -> str | None:
    for pconf in participant_configs.values():
        if func_name in pconf.get("boundary_functions", []):
            return pconf.get("label_template")
    return None


## @brief Convert UPPER_CASE note names to Title Case.
#  @version 1.0
#  @internal
#  @return Humanized note text
def _humanize_note(name: str) -> str:
    if re.match(r"^[A-Z][A-Z0-9_]+$", name):
        return name.replace("_", " ").title()
    return name
