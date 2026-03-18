"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doxygen_guard.config import get_language_config
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


## @brief Walk source directories, parse functions, extract @emits/@handles/@ext/@triggers.
#  @version 1.0
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

    validate_config = config.get("validate", {})
    comment_style = validate_config.get("comment_style", {})
    comment_start = comment_style.get("start", r"/\*\*(?!\*)")
    comment_end = comment_style.get("end", r"\*/")

    tagged: list[TaggedFunction] = []

    for source_dir in source_dirs:
        for source_file in _find_source_files(source_dir, config):
            lang_config = get_language_config(config, str(source_file))
            if lang_config is None:
                continue

            content = source_file.read_text()
            functions = parse_functions(
                content=content,
                function_pattern=lang_config["function_pattern"],
                exclude_names=lang_config.get("exclude_names", []),
                comment_start=comment_start,
                comment_end=comment_end,
            )

            for func in functions:
                tf = _extract_tagged_function(func, str(source_file), participants)
                if tf is None:
                    continue

                if req_filter and req_filter not in tf.reqs:
                    continue

                if tf.emits or tf.handles or tf.ext or tf.triggers:
                    tagged.append(tf)

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


## @brief Resolve @emits->@handles pairs and @ext calls into directed edges.
#  @version 1.0
def build_sequence_edges(
    tagged_functions: list[TaggedFunction],
) -> list[dict[str, Any]]:
    # Build handler lookup: event_name → participant that handles it
    handler_map: dict[str, TaggedFunction] = {}
    for tf in tagged_functions:
        for event in tf.handles:
            handler_map[event] = tf

    edges: list[dict[str, Any]] = []

    for tf in tagged_functions:
        from_id = tf.participant.id if tf.participant else "unknown"

        # @emits → arrow to handler
        for event in tf.emits:
            handler = handler_map.get(event)
            to_id = handler.participant.id if handler and handler.participant else "unknown"
            edges.append(
                {
                    "from_id": from_id,
                    "to_id": to_id,
                    "label": f"{tf.name}()",
                    "event": event,
                    "style": "-->",  # dashed for async events
                }
            )

        # @ext mod::func → arrow to participant matching mod
        for ext_ref in tf.ext:
            parts = ext_ref.split("::", 1)
            mod = parts[0] if len(parts) == 2 else ext_ref
            func_name = parts[1] if len(parts) == 2 else ext_ref
            # Find participant matching the module
            to_id = _resolve_ext_participant(mod, tagged_functions)
            edges.append(
                {
                    "from_id": from_id,
                    "to_id": to_id,
                    "label": f"{func_name}()",
                    "event": None,
                    "style": "->",  # solid for direct calls
                }
            )

        # @triggers → notes on the emitting participant
        for trigger in tf.triggers:
            edges.append(
                {
                    "from_id": from_id,
                    "to_id": from_id,
                    "label": trigger,
                    "event": None,
                    "style": "note",
                }
            )

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
#  @version 1.0
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

    # Collect participants that appear in edges
    participant_ids = _collect_active_participants(edges)
    participants = {p["id"]: p for p in trace_config.get("participants", [])}

    for pid in participant_ids:
        p = participants.get(pid)
        if p:
            lines.append(f'participant "{p["label"]}" as {pid}')
        elif pid != "unknown":
            lines.append(f'participant "{pid}" as {pid}')

    lines.append("")

    # Render edges
    for edge in edges:
        if edge["style"] == "note":
            lines.append(f"note right of {edge['from_id']}: {edge['label']}")
        else:
            arrow = edge["style"]
            label = edge["label"]
            if edge.get("event"):
                label = f"{edge['event']}"
            lines.append(f"{edge['from_id']} {arrow} {edge['to_id']}: {label}")

    lines.append("")
    lines.append("@enduml")

    return "\n".join(lines)


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


## @brief Save .puml content to the configured output directory.
#  @version 1.0
def write_diagram(
    req_id: str,
    puml_content: str,
    output_dir: str,
) -> Path:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    puml_file = out_path / f"{req_id}.puml"
    puml_file.write_text(puml_content)
    logger.info("Wrote diagram: %s", puml_file)

    return puml_file


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.0
def run_trace(
    source_dirs: list[str],
    config: dict[str, Any],
    req_id: str | None = None,
    trace_all: bool = False,
) -> list[Path]:
    trace_config = config.get("trace", {})
    output_dir = trace_config.get("output_dir", "docs/generated/sequences/")

    if not trace_all and not req_id:
        logger.error("Must specify --req or --all for trace command")
        return []

    tagged = collect_tagged_functions(source_dirs, config, req_filter=req_id)

    if not tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return []

    if req_id:
        # Single requirement trace
        edges = build_sequence_edges(tagged)
        puml = generate_plantuml(req_id, edges, config)
        return [write_diagram(req_id, puml, output_dir)]

    # Trace all: group by requirement
    req_groups: dict[str, list[TaggedFunction]] = {}
    for tf in tagged:
        for req in tf.reqs:
            req_groups.setdefault(req, []).append(tf)

    written: list[Path] = []
    for req, funcs in sorted(req_groups.items()):
        edges = build_sequence_edges(funcs)
        puml = generate_plantuml(req, edges, config)
        written.append(write_diagram(req, puml, output_dir))

    return written
