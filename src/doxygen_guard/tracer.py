"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.4
"""

from __future__ import annotations

import logging
import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doxygen_guard.config import get_impact, get_trace, get_validate, parse_source_file_with_content
from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer_models import (
    DiagramBuildParams,
    DiagramContext,
    Edge,
    Participant,
    TaggedFunction,
    resolve_by_prefix,
    resolve_ext_target,
)

if TYPE_CHECKING:
    from doxygen_guard.parser import Function

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
_resolve_by_prefix = resolve_by_prefix
_resolve_ext_target = resolve_ext_target

__all__ = [
    "DiagramBuildParams",
    "DiagramContext",
    "Edge",
    "Participant",
    "TaggedFunction",
]


## @brief Build the REQ ID -> participant name mapping from requirements data.
#  @version 1.3
#  @req REQ-TRACE-002
def _build_req_participant_map(
    config: dict[str, Any],
    full_reqs: dict[str, dict[str, str]],
) -> dict[str, str]:
    trace_config = get_trace(config)
    participant_field = trace_config.get("participant_field")
    if not participant_field:
        return {}

    return {
        req_id: row.get(participant_field, "")
        for req_id, row in full_reqs.items()
        if row.get(participant_field)
    }


## @brief Resolve a function's participant from its requirement tags via the requirements file.
#  @version 1.1
#  @req REQ-TRACE-002
def _resolve_participant_from_reqs(
    reqs: list[str],
    req_participant_map: dict[str, str],
) -> str | None:
    for req in reqs:
        participant = req_participant_map.get(req)
        if participant:
            return participant
    return None


## @brief Load external participants from trace config.
#  @version 1.1
#  @req REQ-TRACE-003
def _load_external_participants(config: dict[str, Any]) -> list[Participant]:
    raw = get_trace(config).get("external", [])
    participants: list[Participant] = []
    for entry in raw:
        if isinstance(entry, dict):
            for pname, pconfig in entry.items():
                cfg = pconfig or {}
                participants.append(
                    Participant(
                        name=pname,
                        receives_prefix=cfg.get("receives_prefix", []),
                    )
                )
        elif isinstance(entry, str):
            participants.append(Participant(name=entry))
    return participants


## @brief Collect all known participant names from requirements and externals.
#  @version 1.0
#  @internal
def _collect_all_participants(
    req_participant_map: dict[str, str],
    externals: list[Participant],
) -> list[Participant]:
    seen: set[str] = set()
    participants: list[Participant] = []

    for pname in req_participant_map.values():
        if pname not in seen:
            seen.add(pname)
            participants.append(Participant(name=pname))

    for ext in externals:
        if ext.name not in seen:
            seen.add(ext.name)
            participants.append(ext)

    return participants


## @brief Parse a single source file and extract tagged functions.
#  @version 1.6
#  @internal
def _process_source_file(
    source_file: Path,
    config: dict[str, Any],
    req_participant_map: dict[str, str],
) -> list[TaggedFunction]:
    result = parse_source_file_with_content(str(source_file), config)
    if result is None:
        return []

    functions, content = result
    lines = content.splitlines()
    tagged: list[TaggedFunction] = []
    for func in functions:
        tf = _extract_tagged_function(func, str(source_file), req_participant_map, lines, config)
        if tf is not None:
            tagged.append(tf)
    return tagged


## @brief Walk source directories and collect ALL tagged functions.
#  @version 1.5
#  @req REQ-TRACE-001
def collect_all_tagged_functions(
    source_dirs: list[str],
    config: dict[str, Any],
    full_reqs: dict[str, dict[str, str]] | None = None,
) -> tuple[list[TaggedFunction], list[Participant], dict]:
    if full_reqs is None:
        full_reqs = load_requirements_full(config)
    req_participant_map = _build_req_participant_map(config, full_reqs)
    externals = _load_external_participants(config)
    all_participants = _collect_all_participants(req_participant_map, externals)

    tagged: list[TaggedFunction] = []
    file_cache: dict = {}
    file_count = 0
    for source_dir in source_dirs:
        source_files = _find_source_files(source_dir, config)
        file_count += len(source_files)
        for source_file in source_files:
            tagged.extend(_process_source_file(source_file, config, req_participant_map))
            _cache_parsed_file(str(source_file), config, file_cache)
    logger.info(
        "Trace scan: %d file(s), %d tagged function(s), %d participant(s)",
        file_count,
        len(tagged),
        len(all_participants),
    )
    return tagged, all_participants, file_cache


## @brief Cache a parsed file's AST tree and function node index.
#  @version 1.0
#  @internal
def _cache_parsed_file(
    file_path: str,
    config: dict[str, Any],
    file_cache: dict,
) -> None:
    from doxygen_guard.ts_languages import language_for_file
    from doxygen_guard.ts_parser import get_parsed_file

    lang = language_for_file(file_path, config)
    if lang is None:
        return
    parsed = get_parsed_file(file_path, lang)
    if parsed is not None:
        file_cache[file_path] = parsed


## @brief Recursively find source files, respecting validate.exclude patterns.
#  @version 1.2
#  @internal
def _find_source_files(source_dir: str, config: dict[str, Any]) -> list[Path]:
    languages = get_validate(config).get("languages", {})
    exclude_patterns = get_validate(config).get("exclude", [])
    extensions: set[str] = set()
    for lang_config in languages.values():
        extensions.update(lang_config.get("extensions", []))

    source_path = Path(source_dir)
    if not source_path.exists():
        logger.warning("Source directory not found: %s", source_dir)
        return []

    files: list[Path] = []
    for ext in extensions:
        for f in source_path.rglob(f"*{ext}"):
            rel = str(f.relative_to(Path.cwd())) if f.is_absolute() else str(f)
            if not any(re.search(p, rel) for p in exclude_patterns):
                files.append(f)
    return sorted(files)


## @brief Build a TaggedFunction, resolving participant and capturing body text.
#  @version 1.5
#  @internal
def _extract_tagged_function(
    func: Function,
    file_path: str,
    req_participant_map: dict[str, str],
    lines: list[str],
    config: dict[str, Any] | None = None,
) -> TaggedFunction | None:
    if func.doxygen is None:
        return None

    tags = func.doxygen.tags
    reqs = tags.get("req", [])
    supports = tags.get("supports", [])
    assumes = tags.get("assumes", [])
    has_trace_tags = (
        tags.get("emits") or tags.get("handles") or tags.get("ext") or tags.get("triggers")
    )
    if not has_trace_tags and not reqs and not supports:
        return None

    body_text = "\n".join(lines[func.def_line : func.body_end + 1])
    declared_emits = tags.get("emits", [])

    tf = TaggedFunction(
        name=func.name,
        file_path=file_path,
        participant_name=_resolve_participant_from_reqs(reqs, req_participant_map),
        emits=declared_emits,
        handles=tags.get("handles", []),
        ext=tags.get("ext", []),
        triggers=tags.get("triggers", []),
        reqs=reqs,
        supports=supports,
        assumes=assumes,
        body=body_text,
    )

    if config:
        _apply_emit_inference(tf, body_text, config)

    return tf


## @brief Infer @emits from emit function calls found in the function body.
#  @version 1.0
#  @internal
def _apply_emit_inference(
    tf: TaggedFunction,
    body_text: str,
    config: dict[str, Any],
) -> None:
    trace_options = get_trace(config).get("options", {})
    if not trace_options.get("infer_emits", True):
        return

    emit_fns = trace_options.get("event_emit_functions", ["event_post"])
    event_prefix = trace_options.get("event_constant_prefix", "EVENT_")
    tag_prefix = trace_options.get("event_tag_prefix", "EVENT:")
    name_pattern = trace_options.get("event_name_pattern", r"^[A-Z][A-Z0-9_]*$")
    declared = set(tf.emits)

    for fn_name in emit_fns:
        pattern = rf"\b{re.escape(fn_name)}\s*\(\s*(\w+)"
        for match in re.finditer(pattern, body_text):
            constant = match.group(1)
            if not re.match(name_pattern, constant):
                logger.warning(
                    "Rejected inferred event '%s' in %s() — fails pattern", constant, tf.name
                )
                continue
            event = _constant_to_event_tag(constant, event_prefix, tag_prefix)
            if event and event not in declared:
                tf.emits.append(event)
                declared.add(event)
                logger.info("Inferred @emits %s in %s()", event, tf.name)


## @brief Convert a C constant name to an EVENT: tag.
#  @version 1.0
#  @internal
def _constant_to_event_tag(
    constant: str,
    event_prefix: str,
    tag_prefix: str,
) -> str | None:
    if not constant.startswith(event_prefix):
        return None
    suffix = constant[len(event_prefix) :]
    return f"{tag_prefix}{suffix}"


## @brief Detect phantom @emits — declared but no matching call in body.
#  @version 1.0
#  @internal
def detect_phantom_emits(
    tf: TaggedFunction,
    config: dict[str, Any],
) -> list[str]:
    trace_options = get_trace(config).get("options", {})
    emit_fns = trace_options.get("event_emit_functions", ["event_post"])
    event_prefix = trace_options.get("event_constant_prefix", "EVENT_")

    called_constants: set[str] = set()
    for fn_name in emit_fns:
        pattern = rf"\b{re.escape(fn_name)}\s*\(\s*(\w+)"
        for match in re.finditer(pattern, tf.body):
            called_constants.add(match.group(1))

    phantoms: list[str] = []
    for event in tf.emits:
        suffix = event.split(":", 1)[-1] if ":" in event else event
        expected_constant = f"{event_prefix}{suffix}"
        if expected_constant not in called_constants:
            phantoms.append(event)
            logger.warning(
                "Possible phantom @emits %s in %s() — no matching call found", event, tf.name
            )
    return phantoms


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
#  @version 1.6
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
                to_name = handler.participant_name or handler.name
                label = f"{tf.name}() -> {handler.name}()"
                edges.append(Edge(from_name, to_name, label, event, "-->"))
        else:
            prefix_target = _resolve_by_prefix(event, externals)
            if prefix_target:
                edges.append(Edge(from_name, prefix_target, f"{tf.name}()", event, "-->"))
            else:
                warnings.append(f"Unresolved event '{event}' emitted by {tf.name}()")
    return edges, warnings


## @brief Resolve a single ext reference to a target participant name.
#  @version 1.0
#  @internal
def _resolve_ext_participant(
    ext_ref: str,
    from_name: str,
    all_tagged: list[TaggedFunction],
    participants: list[Participant] | None,
) -> tuple[str, str, str | None]:
    parts = ext_ref.split("::", 1)
    func_name = parts[1] if len(parts) == 2 else ext_ref
    mod = parts[0] if len(parts) == 2 else ext_ref
    resolved = _resolve_ext_target(func_name, mod, all_tagged, participants)
    to_name = resolved or mod
    if to_name == from_name and mod != ext_ref:
        to_name = mod.replace("_", " ").title()
    warning = None if resolved else f"Unresolved @ext '{ext_ref}' — using '{mod}' as participant"
    return func_name, to_name, warning


## @brief Build ext call edges.
#  @version 1.7
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
            edges.append(Edge(to_name, from_name, "return", style="-->"))
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
#  @version 1.3
#  @req REQ-TRACE-001
def _build_call_edges(
    caller: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
    req_id: str | None = None,
) -> list[Edge]:
    ext_func_names = {ref.split("::", 1)[-1] for ref in caller.ext}
    edges: list[Edge] = []
    for target in all_tagged:
        if target.name == caller.name:
            continue
        if target.name in ext_func_names:
            continue
        if req_id and not _is_req_relevant_target(target, req_id):
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", caller.body):
            to_name = target.participant_name or target.name
            edges.append(Edge(from_name, to_name, f"{target.name}()"))
    return edges


## @brief Check if a target function is relevant to the current REQ's diagram.
#  @version 1.2
#  @internal
def _is_req_relevant_target(target: TaggedFunction, req_id: str) -> bool:
    if req_id in target.supports and req_id not in target.reqs:
        return False
    return req_id in target.reqs or bool(target.handles or target.ext)


## @brief Find functions that reference any of the target functions via ext or body call.
#  @version 1.1
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
        ext_targets = {ref.split("::", 1)[-1] for ref in tf.ext}
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
#  @version 1.0
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
            to_name = target.participant_name or target.name
            edges.append(Edge(from_name, to_name, f"{target.name}()"))
    return edges


## @brief Build edges for emitting functions, using global handler resolution.
#  @version 1.8
#  @req REQ-TRACE-001
def build_sequence_edges(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    req_id: str | None = None,
) -> tuple[list[Edge], list[str]]:
    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix]
    edges: list[Edge] = []
    all_warnings: list[str] = []

    emitter_names = {tf.name for tf in emitters}
    inbound = _find_inbound_callers(emitters, all_tagged)

    # Direct emitters get full edge building
    for tf in emitters:
        from_name = tf.participant_name or tf.name
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
        from_name = tf.participant_name or tf.name
        edges.extend(_build_inbound_edges(tf, from_name, emitter_names, all_tagged))

    return edges, all_warnings


## @brief Collect all participant names from edges and function listings.
#  @version 1.1
#  @internal
def _collect_all_active_names(
    edges: list[Edge],
    functions: list[TaggedFunction],
) -> list[str]:
    names = _collect_active_participants(edges)
    for tf in functions:
        pname = tf.participant_name or tf.name
        if pname not in names:
            names.append(pname)
    return names


## @brief Render function notes for functions not referenced in any edge.
#  @version 1.1
#  @internal
def _render_unlisted_functions(
    functions: list[TaggedFunction],
    edges: list[Edge],
) -> list[str]:
    funcs_in_edges = {e.label for e in edges}
    lines: list[str] = []
    for tf in functions:
        if not any(tf.name in label for label in funcs_in_edges):
            pname = _safe_id(tf.participant_name or tf.name)
            lines.append(f"note over {pname}: {tf.name}()")
    return lines


## @brief Partition active participant names into internal and external groups.
#  @version 1.0
#  @internal
def _partition_participants(
    active_names: list[str],
    participants: list[Participant],
) -> tuple[list[str], list[str]]:
    external_names = {p.name for p in participants if p.receives_prefix}
    internal = [n for n in active_names if n not in external_names]
    external = [n for n in active_names if n in external_names]
    return internal, external


## @brief Find participant names referenced in edges but not in the declared set.
#  @version 1.1
#  @internal
def _find_undeclared_participants(
    active_names: list[str],
    participant_set: set[str],
) -> list[str]:
    return [name for name in active_names if name not in participant_set]


## @brief Render participant declarations with box grouping and entity stereotypes.
#  @version 1.1
#  @internal
def _render_participants(
    active_names: list[str],
    participants: list[Participant],
    participant_set: set[str],
    options: dict[str, Any],
) -> list[str]:
    internal, external = _partition_participants(active_names, participants)
    undeclared = _find_undeclared_participants(active_names, participant_set)
    lines: list[str] = []

    for pname in external:
        if pname in participant_set:
            lines.append(f'entity "{pname}" as {_safe_id(pname)}')

    for pname in undeclared:
        lines.append(f'entity "{pname}" as {_safe_id(pname)}')

    if (external or undeclared) and internal:
        lines.append("")

    box_label = options.get("box_label", "System")
    if internal:
        lines.append(f'box "{box_label}" #LightBlue')
        for pname in internal:
            if pname in participant_set:
                lines.append(f'  participant "{pname}" as {_safe_id(pname)}')
        lines.append("end box")

    return lines


## @brief Get the requirements name column from config.
#  @version 1.1
#  @internal
def _get_name_col(config: dict[str, Any]) -> str:
    return get_impact(config).get("requirements", {}).get("name_column", "Name")


## @brief Wrap text at word boundaries for PlantUML display.
#  @version 1.0
#  @internal
def _wrap_text(text: str, width: int = 60) -> str:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        if length + len(word) + len(current) > width and current:
            lines.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += len(word)
    if current:
        lines.append(" ".join(current))
    return "\\n".join(lines)


## @brief Build header parts from requirement row metadata.
#  @version 1.2
#  @internal
def _build_req_header_parts(
    req_id: str,
    req_row: dict[str, str] | None,
    name_col: str,
) -> list[str]:
    parts = [f"**{req_id}**"]
    if not req_row:
        return parts
    name = req_row.get(name_col, "")
    if name:
        parts[0] = f"**{req_id}: {name}**"
    for key in ("Description", "description", "Acceptance Criteria", "acceptance_criteria"):
        value = req_row.get(key, "").strip()
        if value:
            label = key.replace("_", " ").title()
            parts.append(f"//{label}:// {_wrap_text(value, 60)}")
    return parts


## @brief Render requirement context as a header note in the diagram.
#  @version 1.2
#  @internal
def _render_req_header(
    req_id: str,
    req_row: dict[str, str] | None,
    name_col: str,
    preconditions: list[str] | None = None,
) -> list[str]:
    parts = _build_req_header_parts(req_id, req_row, name_col)
    if preconditions:
        parts.append(f"//Preconditions:// {', '.join(preconditions)}")
    if len(parts) <= 1:
        return []
    lines = ["header"]
    lines.extend(f"  {p}" for p in parts)
    lines.append("end header")
    lines.append("")
    return lines


## @brief Render edges and function listings as a PlantUML block.
#  @version 1.10
#  @req REQ-TRACE-001
def generate_plantuml(
    req_id: str,
    edges: list[Edge],
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    context: DiagramContext | None = None,
) -> str:
    options = get_trace(config).get("options", {})
    name_col = _get_name_col(config)
    req_row = context.req_row if context else None
    preconditions = context.preconditions if context else None
    req_name = req_row.get(name_col) if req_row else None
    title = f"{req_id} {req_name}" if req_name else req_id

    lines = [f"@startuml {title}"]
    if options.get("autonumber", True):
        lines.append("autonumber")
    lines.extend(_render_skinparam(options))
    lines.append("")

    active_names = _collect_all_active_names(edges, functions)
    participant_set = {p.name for p in participants}
    lines.extend(_render_participants(active_names, participants, participant_set, options))

    lines.append("")
    lines.extend(_render_req_header(req_id, req_row, name_col, preconditions=preconditions))
    lines.extend(_render_unlisted_functions(functions, edges))

    label_mode = options.get("label_mode", "full")
    if functions and edges:
        lines.append("")

    for edge in edges:
        lines.append(_render_edge(edge, label_mode))
        lines.extend(_render_edge_activation(edge))

    if options.get("legend", False):
        lines.extend(_render_legend())

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


## @brief Convert a participant name to a safe PlantUML identifier.
#  @version 1.2
#  @utility
def _safe_id(name: str) -> str:
    return re.sub(r"[^\w]", "_", name)


## @brief Sanitize a label for safe embedding in PlantUML output.
#  @version 1.1
#  @internal
def _sanitize_label(label: str) -> str:
    result = label.replace("`", "'").replace(";", ",")
    result = result.replace("<<", "((").replace(">>", "))")
    result = re.sub(r"(?<!-)(<)(?!-)", "(", result)
    result = re.sub(r"(?<!-)(>)(?!-)", ")", result)
    return result


## @brief Select the raw label text based on label_mode.
#  @version 1.0
#  @internal
def _select_label_text(label: str, event: str | None, mode: str) -> str:
    if mode == "full":
        san_label = _sanitize_label(label)
        if event and san_label:
            return f"{_sanitize_label(event)}\\n{san_label}"
        return _sanitize_label(event) if event else san_label
    raw = event if event else label
    if mode == "brief":
        raw = raw.split(":", 1)[-1] if ":" in raw else raw
    return _sanitize_label(raw)


## @brief Render default skinparam lines for PlantUML diagrams.
#  @version 1.0
#  @internal
def _render_skinparam(options: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    defaults = {"maxMessageSize": "200", "responseMessageBelowArrow": "true"}
    user_params = options.get("skinparam", {})
    merged = {**defaults, **user_params}
    for key, value in merged.items():
        lines.append(f"skinparam {key} {value}")
    return lines


## @brief Render a single edge as a PlantUML line.
#  @version 1.6
#  @internal
def _render_edge(edge: Edge, label_mode: str = "full") -> str:
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    if edge.style == "note":
        return f"note right of {f}: {_sanitize_label(edge.label)}"
    label = _select_label_text(edge.label, edge.event, label_mode)
    return f"{f} {edge.style} {t}: {label}"


## @brief Emit activate/deactivate for a legacy edge.
#  @version 1.0
#  @internal
def _render_edge_activation(edge: Edge) -> list[str]:
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    if edge.label == "return":
        return [f"deactivate {f}"]
    activate = f != t and edge.style != "note"
    return [f"activate {t}"] if activate else []


## @brief Extract ordered participant names from edges.
#  @version 1.2
#  @internal
def _collect_active_participants(edges: list[Edge]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for edge in edges:
        for pname in (edge.from_name, edge.to_name):
            if pname not in seen:
                seen.add(pname)
                ordered.append(pname)
    return ordered


## @brief Render a .puml file to PNG if plantuml is available.
#  @version 1.0
#  @internal
def _render_png(puml_file: Path) -> None:
    import shutil
    import subprocess

    plantuml = shutil.which("plantuml")
    if not plantuml:
        logger.debug("plantuml not found, skipping PNG render")
        return
    try:
        subprocess.run(
            [plantuml, str(puml_file)],
            capture_output=True,
            check=True,
        )
        logger.info("Rendered PNG: %s", puml_file.with_suffix(".png"))
    except (subprocess.CalledProcessError, OSError) as e:
        logger.warning("PNG render failed for %s: %s", puml_file, e)


## @brief Sanitize a requirement ID for safe use as a filename.
#  @version 1.0
#  @internal
def _safe_filename(req_id: str) -> str:
    return re.sub(r"[^\w\-.]", "_", req_id)


## @brief Save .puml content to the configured output directory.
#  @version 1.5
#  @req REQ-TRACE-001
def write_diagram(req_id: str, puml_content: str, output_dir: str) -> Path:
    out_path = Path(output_dir)
    if ".." in out_path.parts:
        msg = f"Output path '{output_dir}' contains directory traversal"
        raise ValueError(msg)
    out_path.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(req_id)
    puml_file = out_path / f"{safe_name}.puml"
    puml_file.write_text(puml_content)
    logger.info("Wrote diagram: %s", puml_file)
    _render_png(puml_file)
    return puml_file


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


## @brief Infer entry edges from unresolved handles events within a REQ scope.
#  @version 1.1
#  @req REQ-TRACE-001
def _infer_entry_edges(
    req_funcs: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    fallback_name: str = "External",
) -> list[Edge]:
    all_emitted: set[str] = set()
    for tf in all_tagged:
        all_emitted.update(tf.emits)

    externals = [p for p in participants if p.receives_prefix]
    entries: list[Edge] = []
    seen: set[str] = set()

    for tf in req_funcs:
        for event in tf.handles:
            if event in all_emitted or event in seen:
                continue
            seen.add(event)
            source = _resolve_by_prefix(event, externals) or fallback_name
            to_name = tf.participant_name or tf.name
            label = f"{tf.name}()"
            entries.append(Edge(source, to_name, label, event=event, style="-->"))

    return entries


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
#  Entry functions (handling events not emitted by any emitter in the set) come first.
#  Cycles broken by appending remaining emitters in original order.
#  @version 1.1
#  @internal
def _toposort_emitters(emitters: list[TaggedFunction]) -> list[TaggedFunction]:
    if len(emitters) <= 1:
        return emitters

    name_to_tf: dict[str, TaggedFunction] = {tf.name: tf for tf in emitters}
    adj, in_degree = _build_causal_graph(emitters)

    queue = deque(name for name, deg in in_degree.items() if deg == 0)
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
#  @version 1.2
#  @req REQ-TRACE-001
def build_sequence_edges_ast(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    req_id: str | None = None,
    file_cache: dict | None = None,
) -> list:
    from doxygen_guard.ast_walker import ASTEdge, WalkContext, walk_function_body
    from doxygen_guard.ts_languages import get_language_spec

    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix]

    trace_config = get_trace(config)
    trace_options = trace_config.get("options", {})
    emit_fns = set(trace_options.get("event_emit_functions", ["event_post"]))
    max_depth = trace_options.get("max_chain_depth", 3)
    fallback_name = trace_config.get("external_fallback", "External")
    show_returns = trace_options.get("show_returns", True)

    spec = _detect_dominant_spec(emitters, config) or get_language_spec("c")
    if not spec:
        return []

    ast_edges: list[ASTEdge] = []
    visited: set[str] = set()

    entry_edges = _infer_entry_edges(emitters, all_tagged, participants, fallback_name)
    for edge in entry_edges:
        ast_edges.append(ASTEdge(kind="entry", edge=edge))

    sorted_emitters = _toposort_emitters(emitters)
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
        )
        visited.add(tf.name)
        ast_edges.extend(walk_function_body(func_node, tf, ctx))

    return ast_edges


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


_EDGE_KINDS = frozenset(("emit", "ext", "call", "trigger", "entry"))

_SIMPLE_KINDS: dict[str, str] = {
    "loop_end": "end loop",
    "alt_end": "end alt",
    "else": "else",
    "try_end": "end group",
    "catch_end": "end group",
    "finally_end": "end group",
    "switch_end": "end alt",
    "group_end": "end group",
}


## @brief Render ASTEdge list as PlantUML lines with activate/deactivate.
#  @version 1.3
#  @internal
def _render_ast_edges(ast_edges: list, label_mode: str = "full") -> list[str]:
    lines: list[str] = []
    active: set[str] = set()
    for ae in ast_edges:
        if ae.kind == "section":
            lines.append(f"== {ae.label} ==")
        elif ae.kind in _EDGE_KINDS:
            if ae.edge:
                lines.append(_render_edge(ae.edge, label_mode))
                lines.extend(_render_activation(ae, active))
        elif ae.kind in _SIMPLE_KINDS:
            lines.append(_SIMPLE_KINDS[ae.kind])
        else:
            line = _render_block_start(ae)
            if line:
                lines.append(line)
    return lines


## @brief Emit activate/deactivate lines for an edge.
#  @details Only activates on entry edges and @ext calls. Deactivates on returns.
#  Avoids stacking activations inside loops or handler chains.
#  @version 1.1
#  @internal
def _render_activation(ae, active: set[str] | None = None) -> list[str]:
    edge = ae.edge
    if edge is None or ae.kind == "trigger" or edge.style == "note":
        return []
    if active is None:
        active = set()
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    result = _compute_activation(ae.kind, edge.label, f, t, active)
    return [result] if result else []


## @brief Determine the activate/deactivate line for an edge.
#  @version 1.1
#  @internal
def _compute_activation(kind: str, label: str, f: str, t: str, active: set[str]) -> str | None:
    if label == "return" and f in active:
        active.discard(f)
        return f"deactivate {f}"
    if f != t and kind in ("entry", "ext") and t not in active:
        active.add(t)
        return f"activate {t}"
    return None


_LABEL_BLOCK_KINDS: dict[str, str] = {
    "loop_start": "loop",
    "alt_start": "alt",
    "catch_start": "group catch",
    "switch_start": "alt",
    "switch_case": "else",
    "group_start": "group",
}

_FIXED_BLOCK_KINDS: dict[str, str] = {
    "try_start": "group try",
    "finally_start": "group finally",
    "switch_default": "else default",
}


## @brief Render a block-start ASTEdge kind as a PlantUML line.
#  @version 1.1
#  @internal
def _render_block_start(ae) -> str | None:
    label = f" {ae.label}" if ae.label else ""
    if ae.kind in _LABEL_BLOCK_KINDS:
        return f"{_LABEL_BLOCK_KINDS[ae.kind]}{label}"
    if ae.kind in _FIXED_BLOCK_KINDS:
        return _FIXED_BLOCK_KINDS[ae.kind]
    note_label = ae.label if ae.kind in ("throw", "goto_note") else None
    return f"note right: {note_label}" if note_label else None


## @brief Render an arrow style legend block.
#  @version 1.1
#  @internal
def _render_legend() -> list[str]:
    return [
        "",
        "legend right",
        "  -> solid: synchronous call",
        "  --> dashed: asynchronous event",
        "end legend",
    ]


## @brief Generate PlantUML from AST-ordered edges.
#  @version 1.1
#  @req REQ-TRACE-001
def generate_plantuml_ast(
    req_id: str,
    ast_edges: list,
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    context: DiagramContext | None = None,
) -> str:
    options = get_trace(config).get("options", {})
    name_col = _get_name_col(config)
    req_row = context.req_row if context else None
    preconditions = context.preconditions if context else None
    req_name = req_row.get(name_col) if req_row else None
    title = f"{req_id} {req_name}" if req_name else req_id

    label_mode = options.get("label_mode", "full")

    lines = [f"@startuml {title}"]
    if options.get("autonumber", True):
        lines.append("autonumber")
    lines.extend(_render_skinparam(options))
    lines.append("")

    flat_edges = [ae.edge for ae in ast_edges if ae.edge is not None]
    active_names = _collect_all_active_names(flat_edges, functions)
    participant_set = {p.name for p in participants}
    lines.extend(_render_participants(active_names, participants, participant_set, options))

    lines.append("")
    lines.extend(_render_req_header(req_id, req_row, name_col, preconditions=preconditions))
    lines.extend(_render_unlisted_functions(functions, flat_edges))

    if ast_edges:
        lines.append("")
    lines.extend(_render_ast_edges(ast_edges, label_mode))

    if options.get("legend", False):
        lines.extend(_render_legend())

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


## @brief Resolve @assumes REQ IDs to display labels with names.
#  @version 1.0
#  @internal
def _resolve_preconditions(
    assumes: list[str],
    req_data: dict[str, dict[str, str]],
    name_col: str,
) -> list[str]:
    labels: list[str] = []
    for req_id in assumes:
        row = req_data.get(req_id, {})
        name = row.get(name_col, "")
        labels.append(f"{req_id} ({name})" if name else req_id)
    return labels


## @brief Generate PlantUML for a single requirement using AST or legacy path.
#  @version 1.1
#  @internal
def _generate_req_diagram(
    r: str,
    funcs: list[TaggedFunction],
    params: DiagramBuildParams,
    diagram_ctx: DiagramContext,
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    at = params.all_tagged
    pp = params.participants
    cfg = params.config
    min_edges = get_trace(cfg).get("options", {}).get("min_edges", 0)

    if params.file_cache:
        ast_edges = build_sequence_edges_ast(
            funcs, at, pp, cfg, req_id=r, file_cache=params.file_cache
        )
        behavioral = [ae for ae in ast_edges if ae.edge is not None]
        if min_edges and len(behavioral) < min_edges:
            logger.info("Skipping %s: %d edges below min_edges=%d", r, len(behavioral), min_edges)
            return None, warnings
        puml = generate_plantuml_ast(r, ast_edges, funcs, pp, cfg, context=diagram_ctx)
    else:
        edges, edge_warnings = build_sequence_edges(funcs, at, pp, req_id=r)
        warnings.extend(edge_warnings)
        if min_edges and len(edges) < min_edges:
            logger.info("Skipping %s: %d edges below min_edges=%d", r, len(edges), min_edges)
            return None, warnings
        puml = generate_plantuml(r, edges, funcs, pp, cfg, context=diagram_ctx)
    return puml, warnings


## @brief Generate diagrams, filtering emitters by REQ but resolving handlers globally.
#  @version 1.10
#  @internal
def _write_diagrams_for_reqs(
    params: DiagramBuildParams,
    output_dir: str,
    req_id: str | None = None,
    req_data: dict[str, dict[str, str]] | None = None,
) -> tuple[list[Path], list[str]]:
    all_warnings: list[str] = []
    if req_data is None:
        req_data = load_requirements_full(params.config)
    logger.info("Requirements loaded: %d entries", len(req_data))

    all_tagged = params.all_tagged
    name_col = _get_name_col(params.config)

    if req_id:
        req_groups = {req_id: [tf for tf in all_tagged if req_id in tf.reqs]}
        if not req_groups[req_id]:
            return [], all_warnings
    else:
        req_groups = {}
        for tf in all_tagged:
            for req in tf.reqs:
                req_groups.setdefault(req, []).append(tf)

    written: list[Path] = []
    for r, funcs in sorted(req_groups.items()):
        ctx = _build_diagram_context(funcs, req_data, r, name_col)
        puml, warnings = _generate_req_diagram(r, funcs, params, ctx)
        all_warnings.extend(warnings)
        if puml is not None:
            written.append(write_diagram(r, puml, output_dir))
    return written, all_warnings


## @brief Build DiagramContext for a requirement.
#  @version 1.0
#  @internal
def _build_diagram_context(
    funcs: list[TaggedFunction],
    req_data: dict[str, dict[str, str]],
    req_id: str,
    name_col: str,
) -> DiagramContext:
    row = req_data.get(req_id)
    assumes = _collect_assumes(funcs)
    preconditions = _resolve_preconditions(assumes, req_data, name_col) if assumes else None
    return DiagramContext(req_row=row, preconditions=preconditions)


## @brief Generate infrastructure overview table from supports tags.
#  @version 1.0
#  @req REQ-TRACE-001
def generate_infrastructure_table(
    all_tagged: list[TaggedFunction],
) -> str:
    rows: list[tuple[str, str, str]] = []
    for tf in all_tagged:
        if not tf.supports:
            continue
        module = Path(tf.file_path).stem
        supports_str = ", ".join(tf.supports)
        rows.append((tf.name, module, supports_str))

    if not rows:
        return ""

    rows.sort(key=lambda r: (r[1], r[0]))
    lines = ["## Infrastructure Overview", ""]
    lines.append("| Function | Module | Supports |")
    lines.append("|----------|--------|----------|")
    for name, module, supports in rows:
        lines.append(f"| {name} | {module} | {supports} |")
    return "\n".join(lines) + "\n"


## @brief Write infrastructure table to output directory.
#  @version 1.0
#  @req REQ-TRACE-001
def write_infrastructure_table(
    all_tagged: list[TaggedFunction],
    output_dir: str,
) -> Path | None:
    content = generate_infrastructure_table(all_tagged)
    if not content:
        return None
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    infra_file = out_path / "infrastructure.md"
    infra_file.write_text(content)
    logger.info("Wrote infrastructure table: %s", infra_file)
    return infra_file


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.7
#  @req REQ-TRACE-001
def run_trace(
    source_dirs: list[str],
    config: dict[str, Any],
    req_id: str | None = None,
    trace_all: bool = False,
) -> tuple[list[Path], list[str]]:
    if not trace_all and not req_id:
        logger.error("Must specify --req or --all for trace command")
        return [], []

    full_reqs = load_requirements_full(config)
    all_tagged, participants, file_cache = collect_all_tagged_functions(
        source_dirs, config, full_reqs
    )
    if not all_tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return [], []

    for tf in all_tagged:
        detect_phantom_emits(tf, config)

    base_dir = config.get("output_dir", "docs/generated/")
    seq_dir = str(Path(base_dir) / "sequences")
    params = DiagramBuildParams(all_tagged, participants, config, file_cache)
    written, warnings = _write_diagrams_for_reqs(params, seq_dir, req_id, full_reqs)

    if trace_all:
        infra_path = write_infrastructure_table(all_tagged, base_dir)
        if infra_path:
            written.append(infra_path)

    return written, warnings
