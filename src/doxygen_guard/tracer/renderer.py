"""PlantUML generation and rendering for sequence diagrams.

@brief Generate PlantUML source from edges and render to files.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from doxygen_guard.config import get_impact, get_trace_options
from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer_models import (
    DiagramBuildParams,
    DiagramContext,
    Edge,
    Participant,
    TaggedFunction,
    ext_func_name,
)

from .edges_ast import _collect_assumes, build_sequence_edges_ast

logger = logging.getLogger(__name__)


## @brief Collect all participant names from edges and function listings.
#  @version 1.2
#  @internal
def _collect_all_active_names(
    edges: list[Edge],
    functions: list[TaggedFunction],
) -> list[str]:
    names = _collect_active_participants(edges)
    for tf in functions:
        pname = tf.display_name
        if pname not in names:
            names.append(pname)
    return names


## @brief Render notes for init-only functions not referenced in edges or sections.
#  @version 1.4
#  @internal
def _render_unlisted_functions(
    functions: list[TaggedFunction],
    edges: list[Edge],
    init_only_names: set[str] | None = None,
    section_names: set[str] | None = None,
) -> list[str]:
    funcs_in_edges = {e.label for e in edges}
    seen: set[str] = set()
    lines: list[str] = []
    for tf in functions:
        if tf.name in seen:
            continue
        seen.add(tf.name)
        if any(tf.name in label for label in funcs_in_edges):
            continue
        if section_names and tf.name in section_names:
            continue
        if init_only_names is not None and tf.name not in init_only_names:
            continue
        pname = _safe_id(tf.display_name)
        lines.append(f"note over {pname}: {tf.name}()")
    return lines


## @brief Render supports annotations as diagram notes.
#  @version 1.1
#  @internal
def _render_supports_notes(functions: list[TaggedFunction]) -> list[str]:
    lines: list[str] = []
    for tf in functions:
        if tf.supports:
            pname = _safe_id(tf.display_name)
            supports_text = ", ".join(tf.supports)
            lines.append(f"note over {pname}: {tf.name}() supports {supports_text}")
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
#  @version 1.2
#  @req REQ-TRACE-001
#  @return List of PlantUML participant declaration lines
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
#  @version 1.13
#  @req REQ-TRACE-001
def generate_plantuml(
    req_id: str,
    edges: list[Edge],
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    context: DiagramContext | None = None,
) -> str:
    options = get_trace_options(config)
    name_col = _get_name_col(config)
    req_row = context.req_row if context else None
    preconditions = context.preconditions if context else None
    req_name = req_row.get(name_col) if req_row else None
    title = _safe_filename(f"{req_id} {req_name}") if req_name else req_id

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
    lines.extend(_render_supports_notes(functions))

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


## @brief Humanize a trigger/note label for readability.
#  @version 1.0
#  @internal
def _humanize_note(label: str) -> str:
    return label.replace("_", " ").title()


## @brief Convert a participant name to a safe PlantUML identifier.
#  @version 1.2
#  @utility
def _safe_id(name: str) -> str:
    return re.sub(r"[^\w]", "_", name)


## @brief Sanitize a label for safe embedding in PlantUML output.
#  @version 1.2
#  @internal
def _sanitize_label(label: str) -> str:
    result = label.replace("`", "'").replace(";", ",")
    result = result.replace("<<", "((").replace(">>", "))")
    result = re.sub(r"(?<!-)(<)(?!-)", "(", result)
    result = re.sub(r"(?<!-)(>)(?!-)", ")", result)
    result = re.sub(r"^!", "_", result, flags=re.MULTILINE)
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
#  @version 1.8
#  @req REQ-TRACE-001
#  @return PlantUML line string for the edge
def _render_edge(edge: Edge, label_mode: str = "full") -> str:
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    if edge.style == "note":
        return f"note right of {f}: {_humanize_note(_sanitize_label(edge.label))}"
    label = _select_label_text(edge.label, edge.event, label_mode)
    return f"{f} {edge.style} {t}: {label}"


## @brief Emit activate/deactivate for a legacy edge.
#  @version 1.1
#  @internal
def _render_edge_activation(edge: Edge) -> list[str]:
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    if edge.label == "return":
        return [f"deactivate {t}"]
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
#  @version 1.1
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
            timeout=60,
        )
        logger.info("Rendered PNG: %s", puml_file.with_suffix(".png"))
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as e:
        logger.warning("PNG render failed for %s: %s", puml_file, e)


## @brief Sanitize a string for safe use as a filename or PlantUML title.
#  @version 1.1
#  @internal
def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name).strip("_")


## @brief Save .puml content to the configured output directory.
#  @version 1.6
#  @req REQ-TRACE-001
#  @return Path to the written .puml file
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


## @brief Remove section separators that have no content after them.
#  @version 1.0
#  @internal
def _prune_empty_sections(ast_edges: list) -> list:
    pruned: list = []
    i = 0
    while i < len(ast_edges):
        if ast_edges[i].kind != "section":
            pruned.append(ast_edges[i])
            i += 1
            continue
        j = i + 1
        while j < len(ast_edges) and ast_edges[j].kind == "section":
            j += 1
        if j < len(ast_edges):
            pruned.append(ast_edges[j - 1])
        i = j
    return pruned


## @brief Emit deactivate lines for all currently active participants.
#  @version 1.0
#  @internal
def _close_activations(active: set[str], lines: list[str]) -> None:
    for p in sorted(active):
        lines.append(f"deactivate {p}")
    active.clear()


## @brief Render ASTEdge list as PlantUML lines with activate/deactivate.
#  @version 1.7
#  @req REQ-TRACE-001
#  @return List of PlantUML lines
def _render_ast_edges(ast_edges: list, label_mode: str = "full") -> list[str]:
    edges = _prune_empty_sections(ast_edges)
    lines: list[str] = []
    active: set[str] = set()
    for ae in edges:
        if ae.kind == "section":
            lines.append(f"== {ae.label} ==")
        elif ae.kind == "recovery_note":
            lines.append(f"note right: {ae.label}")
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
    _close_activations(active, lines)
    return lines


## @brief Emit activate/deactivate lines for an edge.
#  @details Only activates on entry edges and @ext calls. Deactivates on returns.
#  Avoids stacking activations inside loops or handler chains.
#  @version 1.2
#  @req REQ-TRACE-001
#  @return List of activate/deactivate PlantUML lines
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
#  @version 1.3
#  @internal
def _compute_activation(kind: str, label: str, f: str, t: str, active: set[str]) -> str | None:
    if label.startswith("return") and t in active:
        active.discard(t)
        return f"deactivate {t}"
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
#  @version 1.2
#  @internal
#  @return List of PlantUML legend lines
def _render_legend() -> list[str]:
    return [
        "",
        "legend right",
        "  -> solid: synchronous call",
        "  --> dashed: asynchronous event",
        "  <-- dashed: return value",
        "end legend",
    ]


## @brief Generate PlantUML from AST-ordered edges.
#  @version 1.6
#  @req REQ-TRACE-001
def generate_plantuml_ast(
    req_id: str,
    ast_edges: list,
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    context: DiagramContext | None = None,
) -> str:
    options = get_trace_options(config)
    name_col = _get_name_col(config)
    req_row = context.req_row if context else None
    preconditions = context.preconditions if context else None
    init_only_names = context.init_only_names if context else None
    req_name = req_row.get(name_col) if req_row else None
    title = _safe_filename(f"{req_id} {req_name}") if req_name else req_id

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
    section_names = {ae.label.rstrip("()") for ae in ast_edges if ae.kind == "section"}
    lines.extend(_render_unlisted_functions(functions, flat_edges, init_only_names, section_names))
    lines.extend(_render_supports_notes(functions))

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


## @brief Collect infrastructure function names from config and marker tags.
#  @version 1.2
#  @internal
def _get_infra_fn_names(config: dict[str, Any], all_tagged: list[TaggedFunction]) -> set[str]:
    trace_options = get_trace_options(config)
    names = set(trace_options.get("event_emit_functions", []))
    names |= set(trace_options.get("event_register_functions", []))
    names |= {t.name for t in all_tagged if t.marker_tags}
    return names


## @brief Check if a function only calls infrastructure root functions (init-only).
#  @version 1.3
#  @internal
def _is_init_only(tf: TaggedFunction, infra_fn_names: set[str]) -> bool:
    if tf.emits or tf.handles or tf.triggers:
        return False
    if not tf.ext:
        return False
    return all(ext_func_name(ref) in infra_fn_names for ref in tf.ext)


## @brief Generate PlantUML for a single requirement using AST or legacy path.
#  @version 1.4
#  @req REQ-TRACE-001
#  @return Tuple of (PlantUML content or None, warning list)
def _generate_req_diagram(
    r: str,
    funcs: list[TaggedFunction],
    params: DiagramBuildParams,
    diagram_ctx: DiagramContext,
) -> tuple[str | None, list[str]]:
    from .edges import build_sequence_edges

    warnings: list[str] = []
    at = params.all_tagged
    pp = params.participants
    cfg = params.config
    min_edges = get_trace_options(cfg).get("min_edges", 0)

    if params.file_cache:
        infra_fns = _get_infra_fn_names(cfg, at)
        emitters = [tf for tf in funcs if not _is_init_only(tf, infra_fns)]
        ast_edges = build_sequence_edges_ast(
            emitters, at, pp, cfg, req_id=r, file_cache=params.file_cache
        )
        behavioral = [ae for ae in ast_edges if ae.edge is not None]
        if min_edges and len(behavioral) < min_edges:
            logger.info("Skipping %s: %d edges below min_edges=%d", r, len(behavioral), min_edges)
            return None, warnings
        diagram_ctx.init_only_names = {tf.name for tf in funcs if _is_init_only(tf, infra_fns)}
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
#  @version 1.11
#  @req REQ-TRACE-001
#  @return Tuple of (written file paths, warning list)
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
