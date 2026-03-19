"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.3
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doxygen_guard.config import get_impact, get_trace, get_validate, parse_source_file_with_content
from doxygen_guard.impact import load_requirements_full

if TYPE_CHECKING:
    from doxygen_guard.parser import Function

logger = logging.getLogger(__name__)


## @brief A named actor in a sequence diagram, optionally receiving unhandled events by prefix.
#  @version 1.2
#  @internal
@dataclass
class Participant:
    name: str
    receives_prefix: list[str] = field(default_factory=list)


## @brief Function metadata needed for diagram generation.
#  @version 1.3
#  @internal
@dataclass
class TaggedFunction:
    name: str
    file_path: str
    participant_name: str | None = None
    emits: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    ext: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    reqs: list[str] = field(default_factory=list)
    body: str = ""


## @brief A directed edge in a sequence diagram.
#  @version 1.0
#  @internal
@dataclass
class Edge:
    from_name: str
    to_name: str
    label: str
    event: str | None = None
    style: str = "->"


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


## @brief Route an unhandled event to an external participant via bus prefix.
#  @version 1.0
#  @req REQ-TRACE-003
def _resolve_by_prefix(
    event: str,
    externals: list[Participant],
) -> str | None:
    for p in externals:
        for prefix in p.receives_prefix:
            if event.startswith(prefix):
                return p.name
    return None


## @brief Parse a single source file and extract tagged functions.
#  @version 1.5
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
        tf = _extract_tagged_function(func, str(source_file), req_participant_map, lines)
        if tf is not None:
            tagged.append(tf)
    return tagged


## @brief Walk source directories and collect ALL tagged functions.
#  @version 1.4
#  @req REQ-TRACE-001
def collect_all_tagged_functions(
    source_dirs: list[str],
    config: dict[str, Any],
    full_reqs: dict[str, dict[str, str]] | None = None,
) -> tuple[list[TaggedFunction], list[Participant]]:
    if full_reqs is None:
        full_reqs = load_requirements_full(config)
    req_participant_map = _build_req_participant_map(config, full_reqs)
    externals = _load_external_participants(config)
    all_participants = _collect_all_participants(req_participant_map, externals)

    tagged: list[TaggedFunction] = []
    file_count = 0
    for source_dir in source_dirs:
        source_files = _find_source_files(source_dir, config)
        file_count += len(source_files)
        for source_file in source_files:
            tagged.extend(_process_source_file(source_file, config, req_participant_map))
    logger.info(
        "Trace scan: %d file(s), %d tagged function(s), %d participant(s)",
        file_count,
        len(tagged),
        len(all_participants),
    )
    return tagged, all_participants


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
#  @version 1.3
#  @internal
def _extract_tagged_function(
    func: Function,
    file_path: str,
    req_participant_map: dict[str, str],
    lines: list[str],
) -> TaggedFunction | None:
    if func.doxygen is None:
        return None

    tags = func.doxygen.tags
    reqs = tags.get("req", [])
    has_trace_tags = (
        tags.get("emits") or tags.get("handles") or tags.get("ext") or tags.get("triggers")
    )
    if not has_trace_tags and not reqs:
        return None

    body_text = "\n".join(lines[func.def_line : func.body_end + 1])

    return TaggedFunction(
        name=func.name,
        file_path=file_path,
        participant_name=_resolve_participant_from_reqs(reqs, req_participant_map),
        emits=tags.get("emits", []),
        handles=tags.get("handles", []),
        ext=tags.get("ext", []),
        triggers=tags.get("triggers", []),
        reqs=reqs,
        body=body_text,
    )


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
#  @version 1.5
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
                label = f"{tf.name}() \u2192 {handler.name}()"
                edges.append(Edge(from_name, to_name, label, event, "-->"))
        else:
            prefix_target = _resolve_by_prefix(event, externals)
            if prefix_target:
                edges.append(Edge(from_name, prefix_target, f"{tf.name}()", event, "-->"))
            else:
                warnings.append(f"Unresolved event '{event}' emitted by {tf.name}()")
    return edges, warnings


## @brief Build ext call edges.
#  @version 1.5
#  @req REQ-TRACE-001
def _build_ext_edges(
    tf: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
) -> tuple[list[Edge], list[str]]:
    edges: list[Edge] = []
    warnings: list[str] = []
    for ext_ref in tf.ext:
        parts = ext_ref.split("::", 1)
        func_name = parts[1] if len(parts) == 2 else ext_ref
        mod = parts[0] if len(parts) == 2 else ext_ref
        resolved = _resolve_ext_target(func_name, mod, all_tagged)
        if not resolved:
            warnings.append(
                f"Unresolved @ext '{ext_ref}' in {tf.name}() — using '{mod}' as participant"
            )
        to_name = resolved or mod
        edges.append(Edge(from_name, to_name, f"{func_name}()"))
    return edges, warnings


## @brief Build note edges from trigger annotations.
#  @version 1.3
#  @req REQ-TRACE-001
def _build_trigger_edges(
    tf: TaggedFunction,
    from_name: str,
) -> list[Edge]:
    return [Edge(from_name, from_name, t, style="note") for t in tf.triggers]


## @brief Resolve an ext reference to a participant via function name or module path.
#  @version 1.4
#  @internal
def _resolve_ext_target(
    func_name: str,
    module: str,
    all_tagged: list[TaggedFunction],
) -> str | None:
    for tf in all_tagged:
        if tf.name == func_name and tf.participant_name:
            return tf.participant_name
    for tf in all_tagged:
        if tf.participant_name and module in Path(tf.file_path).parts:
            return tf.participant_name
    return None


## @brief Scan function bodies for calls to other known functions.
#  @version 1.2
#  @req REQ-TRACE-001
def _build_call_edges(
    caller: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
) -> list[Edge]:
    ext_func_names = {ref.split("::", 1)[-1] for ref in caller.ext}
    edges: list[Edge] = []
    for target in all_tagged:
        if target.name == caller.name:
            continue
        if target.name in ext_func_names:
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", caller.body):
            to_name = target.participant_name or target.name
            edges.append(Edge(from_name, to_name, f"{target.name}()"))
    return edges


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


## @brief Build edges for emitting functions, using global handler resolution.
#  @version 1.6
#  @req REQ-TRACE-001
def build_sequence_edges(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
) -> tuple[list[Edge], list[str]]:
    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix]
    edges: list[Edge] = []
    all_warnings: list[str] = []

    inbound = _find_inbound_callers(emitters, all_tagged)
    all_emitters = list(emitters) + inbound

    for tf in all_emitters:
        from_name = tf.participant_name or tf.name
        emit_edges, warnings = _build_emit_edges(tf, from_name, handler_map, externals)
        edges.extend(emit_edges)
        all_warnings.extend(warnings)
        ext_edges, ext_warnings = _build_ext_edges(tf, from_name, all_tagged)
        edges.extend(ext_edges)
        all_warnings.extend(ext_warnings)
        edges.extend(_build_call_edges(tf, from_name, all_tagged))
        edges.extend(_build_trigger_edges(tf, from_name))

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


## @brief Render participant declarations with box grouping and entity stereotypes.
#  @version 1.0
#  @internal
def _render_participants(
    active_names: list[str],
    participants: list[Participant],
    participant_set: set[str],
    options: dict[str, Any],
) -> list[str]:
    internal, external = _partition_participants(active_names, participants)
    lines: list[str] = []

    for pname in external:
        if pname in participant_set:
            lines.append(f'entity "{pname}" as {_safe_id(pname)}')

    if external and internal:
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


## @brief Render requirement context as a header note in the diagram.
#  @version 1.0
#  @internal
def _render_req_header(
    req_id: str,
    req_row: dict[str, str] | None,
    name_col: str,
) -> list[str]:
    if not req_row:
        return []
    parts = [f"**{req_id}**"]
    name = req_row.get(name_col, "")
    if name:
        parts[0] = f"**{req_id}: {name}**"
    for key in ("Description", "description", "Acceptance Criteria", "acceptance_criteria"):
        value = req_row.get(key, "").strip()
        if value:
            label = key.replace("_", " ").title()
            parts.append(f"//{label}:// {value}")
    if len(parts) <= 1:
        return []
    lines = ["header"]
    lines.extend(f"  {p}" for p in parts)
    lines.append("end header")
    lines.append("")
    return lines


## @brief Render edges and function listings as a PlantUML block.
#  @version 1.8
#  @req REQ-TRACE-001
def generate_plantuml(
    req_id: str,
    edges: list[Edge],
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    req_row: dict[str, str] | None = None,
) -> str:
    options = get_trace(config).get("options", {})
    name_col = _get_name_col(config)
    req_name = req_row.get(name_col) if req_row else None
    title = f"{req_id} {req_name}" if req_name else req_id

    lines = [f"@startuml {title}"]
    if options.get("autonumber", True):
        lines.append("autonumber")
    lines.append("")

    active_names = _collect_all_active_names(edges, functions)
    participant_set = {p.name for p in participants}
    lines.extend(_render_participants(active_names, participants, participant_set, options))

    lines.append("")
    lines.extend(_render_req_header(req_id, req_row, name_col))
    lines.extend(_render_unlisted_functions(functions, edges))

    if functions and edges:
        lines.append("")

    for edge in edges:
        lines.append(_render_edge(edge))

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


## @brief Convert a participant name to a safe PlantUML identifier.
#  @version 1.2
#  @utility
def _safe_id(name: str) -> str:
    return re.sub(r"[^\w]", "_", name)


## @brief Render a single edge as a PlantUML line.
#  @version 1.4
#  @internal
def _render_edge(edge: Edge) -> str:
    f = _safe_id(edge.from_name)
    t = _safe_id(edge.to_name)
    if edge.style == "note":
        return f"note right of {f}: {edge.label}"
    label = edge.label
    if edge.event and label:
        label = f"{edge.event}\\n{label}"
    elif edge.event:
        label = edge.event
    return f"{f} {edge.style} {t}: {label}"


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
#  @version 1.4
#  @req REQ-TRACE-001
def write_diagram(req_id: str, puml_content: str, output_dir: str) -> Path:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(req_id)
    puml_file = out_path / f"{safe_name}.puml"
    puml_file.write_text(puml_content)
    logger.info("Wrote diagram: %s", puml_file)
    _render_png(puml_file)
    return puml_file


## @brief Generate diagrams, filtering emitters by REQ but resolving handlers globally.
#  @version 1.5
#  @internal
def _write_diagrams_for_reqs(
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    output_dir: str,
    req_id: str | None = None,
    req_data: dict[str, dict[str, str]] | None = None,
) -> tuple[list[Path], list[str]]:
    all_warnings: list[str] = []
    if req_data is None:
        req_data = load_requirements_full(config)
    logger.info("Requirements loaded: %d entries", len(req_data))

    if req_id:
        funcs = [tf for tf in all_tagged if req_id in tf.reqs]
        if not funcs:
            return [], all_warnings
        row = req_data.get(req_id)
        edges, warnings = build_sequence_edges(funcs, all_tagged, participants)
        all_warnings.extend(warnings)
        puml = generate_plantuml(
            req_id,
            edges,
            funcs,
            participants,
            config,
            req_row=row,
        )
        return [write_diagram(req_id, puml, output_dir)], all_warnings

    req_groups: dict[str, list[TaggedFunction]] = {}
    for tf in all_tagged:
        for req in tf.reqs:
            req_groups.setdefault(req, []).append(tf)

    written: list[Path] = []
    for r, funcs in sorted(req_groups.items()):
        row = req_data.get(r)
        edges, warnings = build_sequence_edges(funcs, all_tagged, participants)
        all_warnings.extend(warnings)
        puml = generate_plantuml(
            r,
            edges,
            funcs,
            participants,
            config,
            req_row=row,
        )
        written.append(write_diagram(r, puml, output_dir))
    return written, all_warnings


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.4
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
    all_tagged, participants = collect_all_tagged_functions(source_dirs, config, full_reqs)
    if not all_tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return [], []

    base_dir = config.get("output_dir", "docs/generated/")
    seq_dir = str(Path(base_dir) / "sequences")
    return _write_diagrams_for_reqs(
        all_tagged,
        participants,
        config,
        seq_dir,
        req_id,
        full_reqs,
    )
