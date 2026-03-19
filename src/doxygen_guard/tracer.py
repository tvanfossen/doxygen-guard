"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.3
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doxygen_guard.config import get_language_config, resolve_parse_settings
from doxygen_guard.impact import load_requirements_full
from doxygen_guard.parser import Function, parse_functions

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


## @brief Build the REQ ID -> participant name mapping from requirements file.
#  @version 1.1
#  @req REQ-TRACE-002
def _build_req_participant_map(
    config: dict[str, Any],
) -> dict[str, str]:
    trace_config = config.get("trace", {})
    participant_field = trace_config.get("participant_field")
    if not participant_field:
        return {}

    full_reqs = load_requirements_full(config)
    return {
        req_id: row.get(participant_field, "")
        for req_id, row in full_reqs.items()
        if row.get(participant_field)
    }


## @brief Resolve a function's participant from its @req tags via the requirements file.
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
#  @version 1.0
#  @req REQ-TRACE-003
def _load_external_participants(config: dict[str, Any]) -> list[Participant]:
    raw = config.get("trace", {}).get("external", [])
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
#  @version 1.3
#  @internal
def _process_source_file(
    source_file: Path,
    config: dict[str, Any],
    req_participant_map: dict[str, str],
) -> list[TaggedFunction]:
    lang_config = get_language_config(config, str(source_file))
    if lang_config is None:
        return []

    content = source_file.read_text()
    settings = resolve_parse_settings(config, lang_config)
    functions = parse_functions(
        content=content,
        function_pattern=lang_config["function_pattern"],
        exclude_names=lang_config.get("exclude_names", []),
        settings=settings,
    )

    lines = content.splitlines()
    tagged: list[TaggedFunction] = []
    for func in functions:
        tf = _extract_tagged_function(func, str(source_file), req_participant_map, lines)
        if tf is not None:
            tagged.append(tf)
    return tagged


## @brief Walk source directories and collect ALL tagged functions.
#  @version 1.3
#  @req REQ-TRACE-001
def collect_all_tagged_functions(
    source_dirs: list[str],
    config: dict[str, Any],
) -> tuple[list[TaggedFunction], list[Participant]]:
    req_participant_map = _build_req_participant_map(config)
    externals = _load_external_participants(config)
    all_participants = _collect_all_participants(req_participant_map, externals)

    tagged: list[TaggedFunction] = []
    for source_dir in source_dirs:
        for source_file in _find_source_files(source_dir, config):
            tagged.extend(_process_source_file(source_file, config, req_participant_map))
    return tagged, all_participants


## @brief Recursively find source files, respecting validate.exclude patterns.
#  @version 1.1
#  @internal
def _find_source_files(source_dir: str, config: dict[str, Any]) -> list[Path]:
    languages = config.get("validate", {}).get("languages", {})
    exclude_patterns = config.get("validate", {}).get("exclude", [])
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
#  @version 1.0
#  @internal
def _build_handler_map(
    all_tagged: list[TaggedFunction],
) -> dict[str, TaggedFunction]:
    handler_map: dict[str, TaggedFunction] = {}
    for tf in all_tagged:
        for event in tf.handles:
            handler_map[event] = tf
    return handler_map


## @brief Build emit edges, resolving handlers globally and falling back to prefix routing.
#  @version 1.3
#  @req REQ-TRACE-001
def _build_emit_edges(
    tf: TaggedFunction,
    from_name: str,
    handler_map: dict[str, TaggedFunction],
    externals: list[Participant],
) -> tuple[list[dict[str, Any]], list[str]]:
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    for event in tf.emits:
        handler = handler_map.get(event)
        if handler and handler.participant_name:
            to_name = handler.participant_name
            handler_label = handler.name
        else:
            prefix_target = _resolve_by_prefix(event, externals)
            if prefix_target:
                to_name = prefix_target
                handler_label = None
            else:
                warnings.append(f"Unresolved event '{event}' emitted by {tf.name}()")
                continue
        label = f"{tf.name}() \u2192 {handler_label}()" if handler_label else f"{tf.name}()"
        edges.append(
            {
                "from": from_name,
                "to": to_name,
                "label": label,
                "event": event,
                "style": "-->",
            }
        )
    return edges, warnings


## @brief Build ext call edges.
#  @version 1.2
#  @req REQ-TRACE-001
def _build_ext_edges(
    tf: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for ext_ref in tf.ext:
        parts = ext_ref.split("::", 1)
        func_name = parts[1] if len(parts) == 2 else ext_ref
        mod = parts[0] if len(parts) == 2 else ext_ref
        to_name = _resolve_ext_target(mod, all_tagged) or mod
        edges.append(
            {
                "from": from_name,
                "to": to_name,
                "label": f"{func_name}()",
                "event": None,
                "style": "->",
            }
        )
    return edges


## @brief Build note edges from @triggers annotations.
#  @version 1.2
#  @req REQ-TRACE-001
def _build_trigger_edges(
    tf: TaggedFunction,
    from_name: str,
) -> list[dict[str, Any]]:
    return [
        {"from": from_name, "to": from_name, "label": t, "event": None, "style": "note"}
        for t in tf.triggers
    ]


## @brief Resolve an @ext module reference to a participant name.
#  @version 1.2
#  @internal
def _resolve_ext_target(
    module: str,
    all_tagged: list[TaggedFunction],
) -> str | None:
    for tf in all_tagged:
        if tf.participant_name and module in tf.file_path:
            return tf.participant_name
    return None


## @brief Scan function bodies for calls to other known functions.
#  @version 1.0
#  @req REQ-TRACE-001
def _build_call_edges(
    caller: TaggedFunction,
    from_name: str,
    all_tagged: list[TaggedFunction],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for target in all_tagged:
        if target.name == caller.name:
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", caller.body):
            to_name = target.participant_name or target.name
            edges.append(
                {
                    "from": from_name,
                    "to": to_name,
                    "label": f"{target.name}()",
                    "event": None,
                    "style": "->",
                }
            )
    return edges


## @brief Build edges for emitting functions, using global handler resolution.
#  @version 1.3
#  @req REQ-TRACE-001
def build_sequence_edges(
    emitters: list[TaggedFunction],
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
) -> tuple[list[dict[str, Any]], list[str]]:
    handler_map = _build_handler_map(all_tagged)
    externals = [p for p in participants if p.receives_prefix]
    edges: list[dict[str, Any]] = []
    all_warnings: list[str] = []

    for tf in emitters:
        from_name = tf.participant_name or tf.name
        emit_edges, warnings = _build_emit_edges(tf, from_name, handler_map, externals)
        edges.extend(emit_edges)
        all_warnings.extend(warnings)
        edges.extend(_build_ext_edges(tf, from_name, all_tagged))
        edges.extend(_build_call_edges(tf, from_name, all_tagged))
        edges.extend(_build_trigger_edges(tf, from_name))

    return edges, all_warnings


## @brief Collect all participant names from edges and function listings.
#  @version 1.0
#  @internal
def _collect_all_active_names(
    edges: list[dict[str, Any]],
    functions: list[TaggedFunction],
) -> list[str]:
    names = _collect_active_participants(edges)
    for tf in functions:
        pname = tf.participant_name or tf.name
        if pname not in names:
            names.append(pname)
    return names


## @brief Render function notes for functions not referenced in any edge.
#  @version 1.0
#  @internal
def _render_unlisted_functions(
    functions: list[TaggedFunction],
    edges: list[dict[str, Any]],
) -> list[str]:
    funcs_in_edges = {e.get("label", "") for e in edges}
    lines: list[str] = []
    for tf in functions:
        if not any(tf.name in label for label in funcs_in_edges):
            pname = _safe_id(tf.participant_name or tf.name)
            lines.append(f"note over {pname}: {tf.name}()")
    return lines


## @brief Render edges and function listings as a PlantUML block.
#  @version 1.5
#  @req REQ-TRACE-001
def generate_plantuml(
    req_id: str,
    edges: list[dict[str, Any]],
    functions: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    req_name: str | None = None,
) -> str:
    options = config.get("trace", {}).get("options", {})
    title = f"{req_id} {req_name}" if req_name else req_id

    lines = [f"@startuml {title}"]
    if options.get("autonumber", True):
        lines.append("autonumber")
    lines.append("")

    active_names = _collect_all_active_names(edges, functions)
    participant_set = {p.name for p in participants}
    for pname in active_names:
        if pname in participant_set:
            lines.append(f'participant "{pname}" as {_safe_id(pname)}')

    lines.append("")
    lines.extend(_render_unlisted_functions(functions, edges))

    if functions and edges:
        lines.append("")

    for edge in edges:
        lines.append(_render_edge(edge))

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


## @brief Convert a participant name to a safe PlantUML identifier.
#  @version 1.1
#  @utility
def _safe_id(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_")


## @brief Render a single edge as a PlantUML line.
#  @version 1.3
#  @internal
def _render_edge(edge: dict[str, Any]) -> str:
    f = _safe_id(edge["from"])
    t = _safe_id(edge["to"])
    if edge["style"] == "note":
        return f"note right of {f}: {edge['label']}"
    event = edge.get("event")
    label = edge["label"]
    if event and label:
        label = f"{event}\\n{label}"
    elif event:
        label = event
    return f"{f} {edge['style']} {t}: {label}"


## @brief Extract ordered participant names from edges.
#  @version 1.1
#  @internal
def _collect_active_participants(edges: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for edge in edges:
        for pname in (edge["from"], edge["to"]):
            if pname not in seen:
                seen.add(pname)
                ordered.append(pname)
    return ordered


## @brief Reject output paths containing directory traversal components.
#  @version 1.1
#  @internal
def _validate_output_path(path: str) -> Path:
    p = Path(path)
    if ".." in p.parts:
        msg = f"Output path '{path}' contains directory traversal"
        raise ValueError(msg)
    return p


## @brief Save .puml content to the configured output directory.
#  @version 1.1
#  @req REQ-TRACE-001
def write_diagram(req_id: str, puml_content: str, output_dir: str) -> Path:
    out_path = _validate_output_path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    puml_file = out_path / f"{req_id}.puml"
    puml_file.write_text(puml_content)
    logger.info("Wrote diagram: %s", puml_file)
    return puml_file


## @brief Generate diagrams, filtering emitters by REQ but resolving handlers globally.
#  @version 1.3
#  @internal
def _write_diagrams_for_reqs(
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    config: dict[str, Any],
    output_dir: str,
    req_id: str | None = None,
) -> tuple[list[Path], list[str]]:
    all_warnings: list[str] = []

    if req_id:
        funcs = [tf for tf in all_tagged if req_id in tf.reqs]
        if not funcs:
            return [], all_warnings
        edges, warnings = build_sequence_edges(funcs, all_tagged, participants)
        all_warnings.extend(warnings)
        puml = generate_plantuml(req_id, edges, funcs, participants, config)
        return [write_diagram(req_id, puml, output_dir)], all_warnings

    req_groups: dict[str, list[TaggedFunction]] = {}
    for tf in all_tagged:
        for req in tf.reqs:
            req_groups.setdefault(req, []).append(tf)

    written: list[Path] = []
    for r, funcs in sorted(req_groups.items()):
        edges, warnings = build_sequence_edges(funcs, all_tagged, participants)
        all_warnings.extend(warnings)
        puml = generate_plantuml(r, edges, funcs, participants, config)
        written.append(write_diagram(r, puml, output_dir))
    return written, all_warnings


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.3
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

    all_tagged, participants = collect_all_tagged_functions(source_dirs, config)
    if not all_tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return [], []

    base_dir = config.get("output_dir", "docs/generated/")
    seq_dir = str(Path(base_dir) / "sequences")
    return _write_diagrams_for_reqs(all_tagged, participants, config, seq_dir, req_id)
