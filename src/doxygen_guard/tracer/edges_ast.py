"""AST-based edge building and topological sorting for sequence diagrams.

@brief Build edges from AST walk results with causal ordering and entry inference.
@version 1.0
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import Any

from doxygen_guard.config import get_trace, get_trace_options
from doxygen_guard.tracer_models import (
    ASTEdge,
    Edge,
    Participant,
    TaggedFunction,
    WalkContext,
    resolve_by_prefix,
)

from .edges import _build_handler_map

logger = logging.getLogger(__name__)


## @brief Collect unique @assumes REQ IDs from a list of tagged functions.
#  @version 1.0
#  @internal
def _collect_assumes(funcs: list[TaggedFunction]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tf in funcs:
        for req in tf.assumes:
            if req not in seen:
                seen.add(req)
                result.append(req)
    return result


## @brief Group entry edges by handler; wrap multi-event handlers in alt blocks.
#  @version 1.1
#  @req REQ-TRACE-001
#  @return List of ASTEdge objects with entry edges and alt wrappers
def _group_entry_edges(entries: list[Edge]) -> list:
    groups: dict[str, list[Edge]] = {}
    for edge in entries:
        groups.setdefault(edge.label, []).append(edge)

    result: list = []
    for edges in groups.values():
        if len(edges) == 1:
            result.append(ASTEdge(kind="entry", edge=edges[0]))
            continue
        for i, edge in enumerate(edges):
            event_label = edge.event or ""
            if i == 0:
                result.append(ASTEdge(kind="switch_start", label=event_label))
            else:
                result.append(ASTEdge(kind="switch_case", label=f"[{event_label}]"))
            result.append(ASTEdge(kind="entry", edge=edge))
        result.append(ASTEdge(kind="switch_end"))
    return result


## @brief Infer entry edges from unresolved events and uncalled functions in a REQ scope.
#  @version 1.4
#  @req REQ-TRACE-001
#  @return List of entry edges for external triggers and uncalled entry points
def _infer_entry_edges(
    req_funcs: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    fallback_name: str = "External",
    file_cache: dict | None = None,
) -> list[Edge]:
    req_names = {tf.name for tf in req_funcs}
    emitter_map = _build_emitter_participant_map(all_tagged)

    externals = [p for p in participants if p.receives_prefix]
    entries: list[Edge] = []
    seen_events: set[str] = set()
    seen_funcs: set[str] = set()

    for tf in req_funcs:
        for event in tf.handles:
            if event in seen_events:
                continue
            emitter_participant = emitter_map.get(event)
            if emitter_participant and emitter_participant[1] in req_names:
                continue
            seen_events.add(event)
            seen_funcs.add(tf.name)
            source = (
                resolve_by_prefix(event, externals)
                or (emitter_participant[0] if emitter_participant else None)
                or fallback_name
            )
            to_name = tf.display_name
            label = f"{tf.name}()"
            entries.append(Edge(source, to_name, label, event=event, style="-->"))

    uncalled = _find_uncalled_entry_points(req_funcs, file_cache)
    for tf in uncalled:
        if tf.name not in seen_funcs:
            to_name = tf.display_name
            entries.append(Edge(fallback_name, to_name, f"{tf.name}()", style="-->"))

    return entries


## @brief Find functions in a REQ scope not called by any other function in scope.
#  @details Only includes functions whose body calls at least one other project
#  function — this filters out pure formatters/utilities that have no outgoing
#  interactions but happen to be tagged with the same REQ.
#  @version 1.0
#  @req REQ-TRACE-001
#  @return List of entry-point TaggedFunctions
def _find_uncalled_entry_points(
    req_funcs: list[TaggedFunction],
    file_cache: dict | None,
) -> list[TaggedFunction]:
    if len(req_funcs) <= 1:
        return req_funcs
    req_names = {tf.name for tf in req_funcs}
    called_by_peer: set[str] = set()
    has_outgoing: set[str] = set()
    all_project_names = _all_project_func_names(file_cache)
    for tf in req_funcs:
        callees = _get_body_callees(tf, file_cache)
        called_by_peer.update(callees & req_names)
        if callees & all_project_names:
            has_outgoing.add(tf.name)
    return [tf for tf in req_funcs if tf.name not in called_by_peer and tf.name in has_outgoing]


## @brief Collect all function names from file cache.
#  @version 1.0
#  @internal
#  @return Set of all project-defined function names
def _all_project_func_names(file_cache: dict | None) -> set[str]:
    if file_cache is None:
        return set()
    names: set[str] = set()
    for parsed in file_cache.values():
        names.update(parsed.func_nodes.keys())
    return names


## @brief Extract callee function names from a function's AST body.
#  @version 1.0
#  @internal
#  @return Set of callee names, empty if AST unavailable
def _get_body_callees(tf: TaggedFunction, file_cache: dict | None) -> set[str]:
    if file_cache is None:
        return set()
    parsed = file_cache.get(tf.file_path)
    func_node = parsed.func_nodes.get(tf.name) if parsed else None
    body = func_node.child_by_field_name("body") if func_node else None
    if body is None:
        return set()
    call_type = "call" if tf.file_path.endswith(".py") else "call_expression"
    result: set[str] = set()
    _collect_callee_names(body, call_type, result)
    return result


## @brief Recursively collect callee names from AST call nodes.
#  @version 1.0
#  @internal
def _collect_callee_names(node: Any, call_type: str, result: set[str]) -> None:
    if node.type == call_type:
        func_node = node.child_by_field_name("function")
        if func_node and func_node.type == "identifier":
            result.add(func_node.text.decode("utf-8"))
    for child in node.named_children:
        _collect_callee_names(child, call_type, result)


## @brief Map events to their emitter's (participant_name, func_name).
#  @version 1.1
#  @req REQ-TRACE-001
#  @return Dict mapping event name to (participant, func_name) of first emitter
def _build_emitter_participant_map(
    all_tagged: list[TaggedFunction],
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for tf in sorted(all_tagged, key=lambda t: t.name):
        for event in tf.emits:
            if event not in result and tf.participant_name:
                result[event] = (tf.participant_name, tf.name)
    return result


## @brief Build a causal dependency graph from emitters' emit/handle relationships.
#  @version 1.1
#  @internal
def _build_causal_graph(
    emitters: list[TaggedFunction],
) -> tuple[dict[str, list[str]], dict[str, int]]:
    emit_map: dict[str, str] = {}
    for tf in emitters:
        for event in tf.emits:
            emit_map[event] = tf.name

    adj: dict[str, list[str]] = {tf.name: [] for tf in emitters}
    in_degree: dict[str, int] = {tf.name: 0 for tf in emitters}

    for tf in emitters:
        for event in tf.handles:
            producer = emit_map.get(event)
            if producer and producer != tf.name and producer in adj:
                adj[producer].append(tf.name)
                in_degree[tf.name] += 1

    return adj, in_degree


## @brief Sort emitters by causal dependency so product diagrams show correct order.
#  @details If emitter A emits EVENT:X and emitter B handles EVENT:X, A precedes B.
#  Entry functions (with handles tags or inferred entry edges) come first.
#  Cycles broken by appending remaining emitters in original order.
#  @version 1.3
#  @internal
def _toposort_emitters(
    emitters: list[TaggedFunction],
    entry_names: set[str] | None = None,
) -> list[TaggedFunction]:
    if len(emitters) <= 1:
        return emitters

    name_to_tf: dict[str, TaggedFunction] = {tf.name: tf for tf in emitters}
    adj, in_degree = _build_causal_graph(emitters)

    entries = entry_names or set()
    zeros = [name for name, deg in in_degree.items() if deg == 0]
    zeros.sort(key=lambda n: (n not in entries, not bool(name_to_tf[n].handles)))
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


## @brief Detect the dominant language spec from a list of emitters' file paths.
#  @version 1.1
#  @internal
def _detect_dominant_spec(
    emitters: list[TaggedFunction],
    config: dict[str, Any],
) -> Any:
    from doxygen_guard.ts_languages import language_for_file

    lang_counts: dict[str, int] = {}
    for tf in emitters:
        lang = language_for_file(tf.file_path, config)
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    if not lang_counts:
        return None
    dominant = max(lang_counts, key=lang_counts.get)  # type: ignore[arg-type]

    from doxygen_guard.ts_languages import get_language_spec

    return get_language_spec(dominant)


## @brief Build AST-ordered edges for a REQ's functions using the AST walker.
#  @version 1.12
#  @req REQ-TRACE-001
def build_sequence_edges_ast(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    req_id: str | None = None,
    file_cache: dict | None = None,
) -> list:
    from doxygen_guard.ast_walker import walk_function_body
    from doxygen_guard.ts_languages import get_language_spec

    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix or p.boundary_functions]
    boundary_map = _build_boundary_map(participants)

    trace_config = get_trace(config)
    trace_options = get_trace_options(config)
    emit_fns = set(trace_options.get("event_emit_functions", []))
    max_depth = trace_options.get("max_chain_depth", 3)
    fallback_name = trace_config.get("external_fallback", "External")
    show_returns = trace_options.get("show_returns", True)
    cross_req_depth = trace_options.get("cross_req_depth", 1)
    show_return_values = trace_options.get("show_return_values", True)
    max_condition_length = trace_options.get("max_condition_length", 80)
    show_project_calls = trace_options.get("show_project_calls", True)

    spec = _detect_dominant_spec(emitters, config) or get_language_spec("c")
    if not spec:
        return []

    extra_qualifiers = _collect_extra_qualifiers(config)
    return_type_map = (
        _build_return_type_map(file_cache, spec, extra_qualifiers) if show_return_values else {}
    )
    project_functions = (
        _build_project_functions_map(file_cache, all_tagged) if show_project_calls else None
    )
    tagged_names = {tf.name for tf in all_tagged}

    ast_edges: list[ASTEdge] = []
    visited: set[str] = set()

    entry_edges = _infer_entry_edges(emitters, all_tagged, participants, fallback_name, file_cache)
    ast_edges.extend(_group_entry_edges(entry_edges))

    entry_names = {e.label.split("(")[0] for e in entry_edges}
    sorted_emitters = _toposort_emitters(emitters, entry_names)
    emitter_count = 0

    for tf in sorted_emitters:
        if tf.name in visited:
            continue

        func_node = _lookup_func_node(tf, file_cache)
        if func_node is None:
            continue

        if emitter_count > 0 or entry_edges:
            ast_edges.append(ASTEdge(kind="section", label=f"{tf.name}()"))
        emitter_count += 1

        file_spec = _spec_for_file(tf.file_path, config) or spec
        ctx = WalkContext(
            handler_map=handler_map,
            all_tagged=all_tagged,
            externals=externals,
            emit_functions=emit_fns,
            spec=file_spec,
            req_id=req_id,
            max_depth=max_depth,
            visited=visited,
            file_cache=file_cache,
            show_returns=show_returns,
            participants=participants,
            cross_req_depth=cross_req_depth,
            extra_qualifiers=extra_qualifiers,
            return_type_map=return_type_map,
            max_condition_length=max_condition_length,
            project_functions=project_functions,
            tagged_names=tagged_names,
            boundary_functions=boundary_map,
        )
        visited.add(tf.name)
        ast_edges.extend(walk_function_body(func_node, tf, ctx))

    return ast_edges


## @brief Build map from boundary function names to external participant names.
#  @version 1.0
#  @req REQ-TRACE-003
#  @return Dict mapping function name to participant name
def _build_boundary_map(participants: list[Participant]) -> dict[str, str]:
    result: dict[str, str] = {}
    for p in participants:
        for fn in p.boundary_functions:
            if fn not in result:
                result[fn] = p.name
    return result


## @brief Map every project-defined function to its participant name.
#  @version 1.1
#  @req REQ-TRACE-001
#  @return Dict mapping function name to participant name
def _build_project_functions_map(
    file_cache: dict | None,
    all_tagged: list[TaggedFunction],
) -> dict[str, str]:
    if file_cache is None:
        return {}
    tagged_participant = {tf.name: tf.participant_name for tf in all_tagged if tf.participant_name}
    result: dict[str, str] = {}
    for file_path, parsed in file_cache.items():
        participant = parsed.module_name or Path(file_path).stem.replace("_", " ").title()
        for func_name in parsed.func_nodes:
            if func_name not in result:
                result[func_name] = tagged_participant.get(func_name, participant)
    return result


## @brief Collect extra qualifier macros from all language configs.
#  @version 1.0
#  @internal
def _collect_extra_qualifiers(config: dict[str, Any]) -> set[str]:
    from doxygen_guard.config import get_validate

    quals: set[str] = set()
    for lang_cfg in get_validate(config).get("languages", {}).values():
        quals.update(lang_cfg.get("extra_qualifiers", []))
    return quals


## @brief Build a map of function name → return type string from file cache AST.
#  @version 1.0
#  @internal
def _build_return_type_map(
    file_cache: dict | None,
    spec: Any,
    extra_qualifiers: set[str],
) -> dict[str, str]:
    from doxygen_guard.ast_walker import _extract_return_type

    if file_cache is None:
        return {}
    result: dict[str, str] = {}
    for parsed in file_cache.values():
        for func_name, func_node in parsed.func_nodes.items():
            ret = _extract_return_type(func_node, spec, extra_qualifiers)
            if ret:
                result[func_name] = ret
    return result


## @brief Look up a function's AST node from the file cache.
#  @version 1.0
#  @internal
def _lookup_func_node(tf: TaggedFunction, file_cache: dict | None) -> Any:
    if file_cache is None:
        return None
    parsed = file_cache.get(tf.file_path)
    if parsed is None:
        return None
    return parsed.func_nodes.get(tf.name)


## @brief Get the LanguageSpec for a source file.
#  @version 1.0
#  @internal
def _spec_for_file(file_path: str, config: dict[str, Any]) -> Any:
    from doxygen_guard.ts_languages import get_language_spec, language_for_file

    lang = language_for_file(file_path, config)
    return get_language_spec(lang) if lang else None
