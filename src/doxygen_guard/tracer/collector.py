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
#  @version 1.7
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
    file_module = _extract_file_module(content)
    tagged: list[TaggedFunction] = []
    for func in functions:
        tf = _extract_tagged_function(
            func, str(source_file), req_participant_map, lines, config, file_module
        )
        if tf is not None:
            tagged.append(tf)
    return tagged


## @brief Extract @module tag from file-level doxygen block.
#  @version 1.0
#  @internal
def _extract_file_module(content: str) -> str | None:
    for line in content.splitlines()[:30]:
        stripped = re.sub(r"^[\s/*#]+|[\s*/]+$", "", line)
        if stripped.startswith("@module"):
            value = stripped[len("@module") :].strip()
            return value if value else None
    return None


## @brief Walk source directories and collect ALL tagged functions.
#  @version 1.8
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
    trace_options = get_trace(config).get("options", {})
    source_roots = _discover_infrastructure_roots(tagged)
    if source_roots.get("emit_fns"):
        trace_options["event_emit_functions"] = source_roots["emit_fns"]
    if source_roots.get("handle_fns"):
        trace_options["event_register_functions"] = source_roots["handle_fns"]
    if trace_options.get("infer_ext", True):
        _apply_ext_inference(tagged)
    _infer_handles_from_registration(tagged, trace_options)
    _warn_unreferenced_functions(tagged)

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
#  @version 1.9
#  @internal
def _extract_tagged_function(
    func: Function,
    file_path: str,
    req_participant_map: dict[str, str],
    lines: list[str],
    config: dict[str, Any] | None = None,
    file_module: str | None = None,
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
    has_infra_tags = tags.get("emit_source") or tags.get("handle_source")
    if not has_trace_tags and not has_infra_tags and not reqs and not supports:
        return None

    body_text = "\n".join(lines[func.def_line : func.body_end + 1])
    declared_emits = tags.get("emits", [])

    marker_tags = {t for t in ("emit_source", "handle_source") if t in tags}

    tf = TaggedFunction(
        name=func.name,
        file_path=file_path,
        participant_name=file_module or _resolve_participant_from_reqs(reqs, req_participant_map),
        emits=declared_emits,
        handles=tags.get("handles", []),
        ext=tags.get("ext", []),
        triggers=tags.get("triggers", []),
        reqs=reqs,
        supports=supports,
        assumes=assumes,
        body=body_text,
        marker_tags=marker_tags,
    )

    if config:
        _apply_emit_inference(tf, body_text, config)

    return tf


## @brief Infer @emits from emit function calls found in the function body.
#  @version 1.1
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
            if constant not in declared:
                tf.emits.append(constant)
                declared.add(constant)
                logger.info("Inferred @emits %s in %s()", constant, tf.name)


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
#  @version 1.1
#  @internal
def detect_phantom_emits(
    tf: TaggedFunction,
    config: dict[str, Any],
) -> list[str]:
    trace_options = get_trace(config).get("options", {})
    emit_fns = trace_options.get("event_emit_functions", ["event_post"])

    called_constants: set[str] = set()
    for fn_name in emit_fns:
        pattern = rf"\b{re.escape(fn_name)}\s*\(\s*(\w+)"
        for match in re.finditer(pattern, tf.body):
            called_constants.add(match.group(1))

    phantoms: list[str] = []
    for event in tf.emits:
        if event not in called_constants:
            phantoms.append(event)
            logger.warning(
                "Possible phantom @emits %s in %s() — no matching call found", event, tf.name
            )
    return phantoms


## @brief Infer cross-module ext calls from function body scan.
#  @details For each tagged function, scan body for calls to functions owned by
#  different participants. If found and not already declared via @ext, add the
#  inferred ext reference. Manual @ext always takes precedence.
#  @version 1.0
#  @internal
def _apply_ext_inference(all_tagged: list[TaggedFunction]) -> None:
    for tf in all_tagged:
        if tf.participant_name:
            _infer_ext_for_function(tf, all_tagged)


## @brief Scan a single function's body for cross-module calls.
#  @version 1.0
#  @internal
def _infer_ext_for_function(tf: TaggedFunction, all_tagged: list[TaggedFunction]) -> None:
    declared_ext_funcs = {ref.split("::", 1)[-1] for ref in tf.ext}
    for target in all_tagged:
        if target.name == tf.name or target.name in declared_ext_funcs:
            continue
        if not target.participant_name or target.participant_name == tf.participant_name:
            continue
        if re.search(rf"\b{re.escape(target.name)}\s*\(", tf.body):
            module = Path(target.file_path).stem
            ext_ref = f"{module}::{target.name}"
            tf.ext.append(ext_ref)
            logger.info("Inferred @ext %s in %s()", ext_ref, tf.name)


_EVENT_CONSTANT_PATTERN = re.compile(r"\b(EVENT_\w+)\b")


## @brief Build regex pattern for handle source function calls.
#  @version 1.1
#  @internal
def _build_register_pattern(handle_fns: list[str]) -> re.Pattern:
    names = "|".join(re.escape(fn) for fn in handle_fns)
    return re.compile(rf"(?:{names})\s*\(\s*([^,]+),\s*(\w+)\s*\)", re.DOTALL)


## @brief Infer handles from registration function call patterns.
#  @details Parses calls to @handle_source functions. Extracts event constants
#  from first argument (bitmask) and handler name from second argument.
#  @version 1.1
#  @internal
def _infer_handles_from_registration(
    all_tagged: list[TaggedFunction],
    trace_options: dict[str, Any],
) -> None:
    handle_fns = trace_options.get("event_register_functions", ["Event_register"])
    register_pattern = _build_register_pattern(handle_fns)
    name_to_tf = {tf.name: tf for tf in all_tagged}

    for tf in all_tagged:
        for match in register_pattern.finditer(tf.body):
            bitmask_expr = match.group(1)
            handler_name = match.group(2)
            handler_tf = name_to_tf.get(handler_name)
            if handler_tf is None:
                continue
            _add_inferred_handles(bitmask_expr, handler_tf)


## @brief Extract event constants from bitmask and add to handler's handles.
#  @version 1.1
#  @internal
def _add_inferred_handles(
    bitmask_expr: str,
    handler_tf: TaggedFunction,
) -> None:
    declared = set(handler_tf.handles)
    for const_match in _EVENT_CONSTANT_PATTERN.finditer(bitmask_expr):
        constant = const_match.group(1)
        if constant not in declared:
            handler_tf.handles.append(constant)
            declared.add(constant)
            logger.info("Inferred @handles %s for %s()", constant, handler_tf.name)


## @brief Discover infrastructure root functions from @emit_source / @handle_source tags.
#  @details Scans the parsed doxygen tags dict for emit_source and handle_source
#  marker tags. These are set during parse_doxygen_tags as tag names with empty values.
#  @version 1.1
#  @internal
def _discover_infrastructure_roots(
    all_tagged: list[TaggedFunction],
) -> dict[str, list[str]]:
    roots: dict[str, list[str]] = {}
    for tf in all_tagged:
        if "emit_source" in tf.marker_tags:
            roots.setdefault("emit_fns", []).append(tf.name)
            logger.info("Discovered @emit_source: %s()", tf.name)
        if "handle_source" in tf.marker_tags:
            roots.setdefault("handle_fns", []).append(tf.name)
            logger.info("Discovered @handle_source: %s()", tf.name)
    return roots


## @brief Warn on tagged functions that are never called or registered in scanned source.
#  @details Scans all function bodies for call references. Functions that have
#  doxygen tags but are never referenced as a callee or handler argument are
#  likely dead code or missing Event_register() calls.
#  @version 1.0
#  @internal
def _warn_unreferenced_functions(
    all_tagged: list[TaggedFunction],
) -> None:
    all_names = {tf.name for tf in all_tagged}
    referenced: set[str] = set()
    for tf in all_tagged:
        for name in all_names:
            if name != tf.name and re.search(rf"\b{re.escape(name)}\b", tf.body):
                referenced.add(name)

    for tf in all_tagged:
        has_behavioral = tf.emits or tf.handles or tf.ext or tf.triggers
        if tf.name not in referenced and has_behavioral and not tf.marker_tags:
            logger.warning(
                "%s() has behavioral tags but is never called or registered in scanned source"
                " — possible dead code or missing Event_register()",
                tf.name,
            )
