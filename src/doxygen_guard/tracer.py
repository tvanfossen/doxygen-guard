"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doxygen_guard.config import get_language_config, resolve_parse_settings
from doxygen_guard.parser import Function, parse_functions

logger = logging.getLogger(__name__)


## @brief Maps source path prefixes to diagram participants.
#  @version 1.0
@dataclass
class Participant:
    id: str
    label: str
    match: str


## @brief Function metadata needed for diagram generation.
#  @version 1.0
@dataclass
class TaggedFunction:
    name: str
    file_path: str
    participant: Participant | None
    emits: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    ext: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    reqs: list[str] = field(default_factory=list)


## @brief Find the participant whose match prefix appears in the file path.
#  @version 1.0
def resolve_participant(file_path: str, participants: list[Participant]) -> Participant | None:
    for participant in participants:
        if participant.match in file_path:
            return participant
    return None


## @brief Parse a single source file and extract tagged functions.
#  @version 1.1
def _process_source_file(
    source_file: Path,
    config: dict[str, Any],
    participants: list[Participant],
    req_filter: str | None,
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

    tagged: list[TaggedFunction] = []
    for func in functions:
        tf = _extract_tagged_function(func, str(source_file), participants)
        if tf is None:
            continue
        if req_filter and req_filter not in tf.reqs:
            continue
        if tf.emits or tf.handles or tf.ext or tf.triggers:
            tagged.append(tf)

    return tagged


## @brief Walk source directories, parse functions, extract trace tags.
#  @version 1.1
def collect_tagged_functions(
    source_dirs: list[str],
    config: dict[str, Any],
    req_filter: str | None = None,
) -> list[TaggedFunction]:
    trace_config = config.get("trace", {})
    participants = [
        Participant(id=p["id"], label=p["label"], match=p["match"])
        for p in trace_config.get("participants", [])
    ]

    tagged: list[TaggedFunction] = []
    for source_dir in source_dirs:
        for source_file in _find_source_files(source_dir, config):
            tagged.extend(_process_source_file(source_file, config, participants, req_filter))
    return tagged


## @brief Recursively find source files with extensions matching language config.
#  @version 1.0
def _find_source_files(source_dir: str, config: dict[str, Any]) -> list[Path]:
    languages = config.get("validate", {}).get("languages", {})
    extensions: set[str] = set()
    for lang_config in languages.values():
        extensions.update(lang_config.get("extensions", []))

    source_path = Path(source_dir)
    if not source_path.exists():
        logger.warning("Source directory not found: %s", source_dir)
        return []

    files: list[Path] = []
    for ext in extensions:
        files.extend(source_path.rglob(f"*{ext}"))
    return sorted(files)


## @brief Build a TaggedFunction from a Function's doxygen tags.
#  @version 1.0
def _extract_tagged_function(
    func: Function,
    file_path: str,
    participants: list[Participant],
) -> TaggedFunction | None:
    if func.doxygen is None:
        return None

    tags = func.doxygen.tags
    return TaggedFunction(
        name=func.name,
        file_path=file_path,
        participant=resolve_participant(file_path, participants),
        emits=tags.get("emits", []),
        handles=tags.get("handles", []),
        ext=tags.get("ext", []),
        triggers=tags.get("triggers", []),
        reqs=tags.get("req", []),
    )


## @brief Build edges from @emits events to their @handles participants.
#  @version 1.1
def _build_emit_edges(
    tf: TaggedFunction,
    from_id: str,
    handler_map: dict[str, TaggedFunction],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for event in tf.emits:
        handler = handler_map.get(event)
        to_id = handler.participant.id if handler and handler.participant else "unknown"
        edges.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "label": f"{tf.name}()",
                "event": event,
                "style": "-->",
            }
        )
    return edges


## @brief Build edges from @ext references to their target participants.
#  @version 1.1
def _build_ext_edges(
    tf: TaggedFunction,
    from_id: str,
    tagged_functions: list[TaggedFunction],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for ext_ref in tf.ext:
        parts = ext_ref.split("::", 1)
        mod = parts[0] if len(parts) == 2 else ext_ref
        func_name = parts[1] if len(parts) == 2 else ext_ref
        to_id = _resolve_ext_participant(mod, tagged_functions)
        edges.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "label": f"{func_name}()",
                "event": None,
                "style": "->",
            }
        )
    return edges


## @brief Build note edges from @triggers annotations.
#  @version 1.1
def _build_trigger_edges(tf: TaggedFunction, from_id: str) -> list[dict[str, Any]]:
    return [
        {"from_id": from_id, "to_id": from_id, "label": t, "event": None, "style": "note"}
        for t in tf.triggers
    ]


## @brief Resolve @emits->@handles pairs and @ext calls into directed edges.
#  @version 1.1
def build_sequence_edges(
    tagged_functions: list[TaggedFunction],
) -> list[dict[str, Any]]:
    handler_map: dict[str, TaggedFunction] = {}
    for tf in tagged_functions:
        for event in tf.handles:
            handler_map[event] = tf

    edges: list[dict[str, Any]] = []
    for tf in tagged_functions:
        from_id = tf.participant.id if tf.participant else "unknown"
        edges.extend(_build_emit_edges(tf, from_id, handler_map))
        edges.extend(_build_ext_edges(tf, from_id, tagged_functions))
        edges.extend(_build_trigger_edges(tf, from_id))

    return edges


## @brief Find the participant whose file path contains the module name.
#  @version 1.0
def _resolve_ext_participant(
    module: str,
    tagged_functions: list[TaggedFunction],
) -> str:
    for tf in tagged_functions:
        if tf.participant and module in tf.file_path:
            return tf.participant.id
    return "unknown"


## @brief Render edges as a PlantUML @startuml/@enduml block.
#  @version 1.1
def generate_plantuml(
    req_id: str,
    edges: list[dict[str, Any]],
    config: dict[str, Any],
    req_name: str | None = None,
) -> str:
    trace_config = config.get("trace", {})
    options = trace_config.get("options", {})
    title = f"{req_id} {req_name}" if req_name else req_id

    lines = [f"@startuml {title}"]
    if options.get("autonumber", True):
        lines.append("autonumber")
    lines.append("")

    participant_ids = _collect_active_participants(edges)
    participants = {p["id"]: p for p in trace_config.get("participants", [])}
    for pid in participant_ids:
        p = participants.get(pid)
        if p:
            lines.append(f'participant "{p["label"]}" as {pid}')
        elif pid != "unknown":
            lines.append(f'participant "{pid}" as {pid}')

    lines.append("")
    for edge in edges:
        lines.append(_render_edge(edge))

    lines.extend(["", "@enduml"])
    return "\n".join(lines)


## @brief Render a single edge as a PlantUML line.
#  @version 1.0
def _render_edge(edge: dict[str, Any]) -> str:
    if edge["style"] == "note":
        return f"note right of {edge['from_id']}: {edge['label']}"
    label = edge.get("event") or edge["label"]
    return f"{edge['from_id']} {edge['style']} {edge['to_id']}: {label}"


## @brief Extract ordered participant list from edges for diagram declaration.
#  @version 1.0
def _collect_active_participants(edges: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for edge in edges:
        for pid in (edge["from_id"], edge["to_id"]):
            if pid not in seen and pid != "unknown":
                seen.add(pid)
                ordered.append(pid)
    return ordered


## @brief Reject output paths containing directory traversal components.
#  @version 1.1
def _validate_output_path(path: str) -> Path:
    p = Path(path)
    if ".." in p.parts:
        msg = f"Output path '{path}' contains directory traversal"
        raise ValueError(msg)
    return p


## @brief Save .puml content to the configured output directory.
#  @version 1.1
def write_diagram(req_id: str, puml_content: str, output_dir: str) -> Path:
    out_path = _validate_output_path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    puml_file = out_path / f"{req_id}.puml"
    puml_file.write_text(puml_content)
    logger.info("Wrote diagram: %s", puml_file)
    return puml_file


## @brief Generate diagrams for a set of tagged functions grouped by requirement.
#  @version 1.1
def _write_diagrams_for_reqs(
    tagged: list[TaggedFunction],
    config: dict[str, Any],
    output_dir: str,
    req_id: str | None = None,
) -> list[Path]:
    if req_id:
        edges = build_sequence_edges(tagged)
        puml = generate_plantuml(req_id, edges, config)
        return [write_diagram(req_id, puml, output_dir)]

    req_groups: dict[str, list[TaggedFunction]] = {}
    for tf in tagged:
        for req in tf.reqs:
            req_groups.setdefault(req, []).append(tf)

    written: list[Path] = []
    for r, funcs in sorted(req_groups.items()):
        edges = build_sequence_edges(funcs)
        puml = generate_plantuml(r, edges, config)
        written.append(write_diagram(r, puml, output_dir))
    return written


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.1
def run_trace(
    source_dirs: list[str],
    config: dict[str, Any],
    req_id: str | None = None,
    trace_all: bool = False,
) -> list[Path]:
    if not trace_all and not req_id:
        logger.error("Must specify --req or --all for trace command")
        return []

    tagged = collect_tagged_functions(source_dirs, config, req_filter=req_id)
    if not tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return []

    output_dir = config.get("trace", {}).get("output_dir", "docs/generated/sequences/")
    return _write_diagrams_for_reqs(tagged, config, output_dir, req_id)
