"""Function collection and emit inference for sequence diagram generation.

@brief Scan source files for tagged functions, resolve participants, and infer emits.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doxygen_guard.config import get_trace, get_validate, parse_source_file_with_content
from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer_models import (
    Participant,
    TaggedFunction,
)

if TYPE_CHECKING:
    from doxygen_guard.parser import Function

logger = logging.getLogger(__name__)


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
