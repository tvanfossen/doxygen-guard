"""Legacy edge building for sequence diagrams.

@brief Build edges from tagged function metadata using global handler resolution.
@version 1.0
"""

from __future__ import annotations

import logging
import re

from doxygen_guard.tracer_models import (
    Edge,
    Participant,
    TaggedFunction,
    ext_func_name,
    is_req_relevant,
    resolve_by_prefix,
    resolve_ext_target,
    split_ext_ref,
)

logger = logging.getLogger(__name__)


## @brief Build the global handler map from ALL tagged functions.
#  @version 1.1
#  @internal
def _build_handler_map(
    all_tagged: list[TaggedFunction],
) -> dict[str, list[TaggedFunction]]:
    handler_map: dict[str, list[TaggedFunction]] = {}
    for tf in all_tagged:
        for event in tf.handles:
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


## @brief Build emit edges, resolving handlers globally and falling back to prefix routing.
#  @version 1.7
#  @req REQ-TRACE-001
def _build_emit_edges(
    tf: TaggedFunction,
    from_name: str,
    handler_map: dict[str, list[TaggedFunction]],
    externals: list[Participant],
) -> tuple[list[Edge], list[str]]:
    edges: list[Edge] = []
    warnings: list[str] = []
    for event in tf.emits:
        handlers = handler_map.get(event, [])
        if handlers:
            for handler in handlers:
                to_name = handler.display_name
                label = f"{tf.name}() -> {handler.name}()"
                edges.append(Edge(from_name, to_name, label, event, "-->"))
        else:
            prefix_target = resolve_by_prefix(event, externals)
            if prefix_target:
                edges.append(Edge(from_name, prefix_target, f"{tf.name}()", event, "-->"))
            else:
                warnings.append(f"Unresolved event '{event}' emitted by {tf.name}()")
    return edges, warnings


## @brief Resolve a single ext reference to a target participant name.
#  @version 1.2
#  @internal
def _resolve_ext_participant(
    ext_ref: str,
    from_name: str,
    all_tagged: list[TaggedFunction],
    participants: list[Participant] | None,
) -> tuple[str, str, str | None]:
    mod, func_name = split_ext_ref(ext_ref)
    if not mod:
        mod = ext_ref
    resolved = resolve_ext_target(func_name, mod, all_tagged, participants)
    to_name = resolved or mod
    if to_name == from_name and mod != ext_ref:
        to_name = mod.replace("_", " ").title()
    warning = None if resolved else f"Unresolved @ext '{ext_ref}' — using '{mod}' as participant"
    return func_name, to_name, warning


## @brief Build ext call edges.
#  @version 1.8
#  @req REQ-TRACE-001
def _build_ext_edges(
    tf: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
    participants: list[Participant] | None = None,
    show_returns: bool = True,
) -> tuple[list[Edge], list[str]]:
    edges: list[Edge] = []
    warnings: list[str] = []
    for ext_ref in tf.ext:
        func_name, to_name, warning = _resolve_ext_participant(
            ext_ref, from_name, all_tagged, participants
        )
        if warning:
            warnings.append(f"{warning} in {tf.name}()")
        is_async = participants and any(
            to_name == p.name for p in participants if p.receives_prefix
        )
        style = "-->" if is_async else "->"
        edges.append(Edge(from_name, to_name, f"{func_name}()", style=style))
        if show_returns:
            edges.append(Edge(to_name, from_name, "return", style="<--"))
    return edges, warnings


## @brief Build note edges from trigger annotations.
#  @version 1.3
#  @req REQ-TRACE-001
def _build_trigger_edges(
    tf: TaggedFunction,
    from_name: str,
) -> list[Edge]:
    return [Edge(from_name, from_name, t, style="note") for t in tf.triggers]


## @brief Scan function bodies for calls to other known functions.
#  @version 1.5
#  @req REQ-TRACE-001
def _build_call_edges(
    caller: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
    req_id: str | None = None,
) -> list[Edge]:
    ext_func_names = {ext_func_name(ref) for ref in caller.ext}
    edges: list[Edge] = []
    for target in all_tagged:
        if target.name == caller.name:
            continue
        if target.name in ext_func_names:
            continue
        if req_id and not _is_req_relevant_target(target, req_id):
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", caller.body):
            to_name = target.display_name
            edges.append(Edge(from_name, to_name, f"{target.name}()"))
    return edges


## @brief Check if a target function is relevant to the current REQ's diagram.
#  @version 1.3
#  @internal
def _is_req_relevant_target(target: TaggedFunction, req_id: str) -> bool:
    return is_req_relevant(target, req_id)


## @brief Find functions that reference any of the target functions via ext or body call.
#  @version 1.2
#  @internal
def _find_inbound_callers(
    target_funcs: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
) -> list[TaggedFunction]:
    target_names = {tf.name for tf in target_funcs}
    callers: list[TaggedFunction] = []
    seen: set[str] = set()
    for tf in all_tagged:
        if tf.name in target_names or tf.name in seen:
            continue
        ext_targets = {ext_func_name(ref) for ref in tf.ext}
        if ext_targets & target_names:
            callers.append(tf)
            seen.add(tf.name)
            continue
        for target_name in target_names:
            if re.search(rf"\b{re.escape(target_name)}\s*\(", tf.body):
                callers.append(tf)
                seen.add(tf.name)
                break
    return callers


## @brief Build inbound caller edges scoped to only the target function call.
#  @version 1.1
#  @internal
def _build_inbound_edges(
    caller: TaggedFunction,
    from_name: str,
    target_names: set[str],
    all_tagged: list[TaggedFunction],
) -> list[Edge]:
    edges: list[Edge] = []
    for target in all_tagged:
        if target.name not in target_names:
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", caller.body):
            to_name = target.display_name
            edges.append(Edge(from_name, to_name, f"{target.name}()"))
    return edges


## @brief Build edges for emitting functions, using global handler resolution.
#  @version 1.10
#  @req REQ-TRACE-001
#  @return Tuple of (edges, warnings) for the requirement
def build_sequence_edges(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    req_id: str | None = None,
) -> tuple[list[Edge], list[str]]:
    import warnings

    warnings.warn(
        "build_sequence_edges is deprecated; use build_sequence_edges_ast with file_cache",
        DeprecationWarning,
        stacklevel=2,
    )
    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix]
    edges: list[Edge] = []
    all_warnings: list[str] = []

    emitter_names = {tf.name for tf in emitters}
    inbound = _find_inbound_callers(emitters, all_tagged)

    # Direct emitters get full edge building
    for tf in emitters:
        from_name = tf.display_name
        emit_edges, warnings = _build_emit_edges(tf, from_name, handler_map, externals)
        edges.extend(emit_edges)
        all_warnings.extend(warnings)
        ext_edges, ext_warnings = _build_ext_edges(tf, from_name, all_tagged, participants)
        edges.extend(ext_edges)
        all_warnings.extend(ext_warnings)
        edges.extend(_build_call_edges(tf, from_name, all_tagged, req_id=req_id))
        edges.extend(_build_trigger_edges(tf, from_name))

    # Inbound callers get ONLY edges to target functions
    for tf in inbound:
        from_name = tf.display_name
        edges.extend(_build_inbound_edges(tf, from_name, emitter_names, all_tagged))

    return edges, all_warnings
