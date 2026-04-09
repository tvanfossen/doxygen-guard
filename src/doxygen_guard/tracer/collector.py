"""Function collection and emit inference for sequence diagram generation.

@brief Scan source files for tagged functions, resolve participants, and infer emits.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doxygen_guard.config import (
    get_trace,
    get_trace_options,
    get_validate,
    parse_source_file_with_content,
)
from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer_models import (
    Participant,
    TaggedFunction,
    calls_func_name,
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
#  @version 1.3
#  @req REQ-TRACE-003
#  @return List of Participant objects declared as external in config
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
                        boundary_functions=cfg.get("boundary_functions", []),
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
#  @version 1.8
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
            func, str(source_file), req_participant_map, lines, file_module
        )
        if tf is not None:
            tagged.append(tf)
    return tagged


## @brief Extract @participant tag from file-level doxygen block.
#  @version 1.1
#  @internal
def _extract_file_module(content: str) -> str | None:
    for line in content.splitlines()[:30]:
        stripped = re.sub(r"^[\s/*#]+|[\s*/]+$", "", line)
        if stripped.startswith("@participant"):
            value = stripped[len("@participant") :].strip()
            return value if value else None
    return None


## @brief Walk source directories and collect ALL tagged functions.
#  @version 2.2
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
        source_files = [f for f in _find_source_files(source_dir, config) if f.exists()]
        file_count += len(source_files)
        for source_file in source_files:
            tagged.extend(_process_source_file(source_file, config, req_participant_map))
            _cache_parsed_file(str(source_file), config, file_cache)
    trace_options = get_trace_options(config)
    source_roots = _discover_infrastructure_roots(tagged)
    if source_roots.get("emit_fns"):
        existing = set(trace_options.get("event_send_functions", []))
        trace_options["event_send_functions"] = list(existing | set(source_roots["emit_fns"]))
    if source_roots.get("handle_fns"):
        existing = set(trace_options.get("event_receive_functions", []))
        trace_options["event_receive_functions"] = list(existing | set(source_roots["handle_fns"]))
    if trace_options.get("infer_sends", True):
        for tf in tagged:
            _apply_emit_inference(tf, tf.body, config, file_cache)
    if trace_options.get("infer_calls", True):
        _apply_ext_inference(tagged, file_cache)
    _infer_handles_from_registration(tagged, trace_options)
    _warn_unreferenced_functions(tagged, file_cache)

    logger.info(
        "Trace scan: %d file(s), %d tagged function(s), %d participant(s)",
        file_count,
        len(tagged),
        len(all_participants),
    )
    return tagged, all_participants, file_cache


## @brief Cache a parsed file's AST tree and function node index.
#  @version 1.1
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
        if parsed.module_name is None:
            content = Path(file_path).read_text(errors="replace")
            parsed.module_name = _extract_file_module(content)
        file_cache[file_path] = parsed


## @brief List tracked files under source_dir via git ls-files.
#  @version 1.2
#  @internal
def _git_ls_files(source_dir: str, extensions: set[str]) -> list[Path] | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", source_dir, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    base = Path(source_dir)
    return [
        base / line
        for line in result.stdout.splitlines()
        if line and any(line.endswith(ext) for ext in extensions)
    ]


## @brief Find source files, preferring git ls-files with rglob fallback.
#  @version 1.3
#  @internal
def _find_source_files(source_dir: str, config: dict[str, Any]) -> list[Path]:
    languages = get_validate(config).get("languages", {})
    exclude_patterns = get_validate(config).get("exclude", [])
    extensions: set[str] = set()
    for lang_config in languages.values():
        extensions.update(lang_config.get("extensions", []))

    git_files = _git_ls_files(source_dir, extensions)
    candidates = git_files if git_files is not None else _rglob_source_files(source_dir, extensions)
    if candidates is None:
        return []

    return sorted(f for f in candidates if not any(re.search(p, str(f)) for p in exclude_patterns))


## @brief Fallback file discovery via filesystem glob.
#  @version 1.0
#  @internal
def _rglob_source_files(source_dir: str, extensions: set[str]) -> list[Path] | None:
    source_path = Path(source_dir)
    if not source_path.exists():
        logger.warning("Source directory not found: %s", source_dir)
        return None
    files: list[Path] = []
    for ext in extensions:
        for f in source_path.rglob(f"*{ext}"):
            rel = str(f.relative_to(Path.cwd())) if f.is_absolute() else str(f)
            files.append(Path(rel))
    return files


## @brief Build a TaggedFunction, resolving participant and capturing body text.
#  @version 2.2
#  @internal
def _extract_tagged_function(
    func: Function,
    file_path: str,
    req_participant_map: dict[str, str],
    lines: list[str],
    file_module: str | None = None,
) -> TaggedFunction | None:
    if func.doxygen is None:
        return None

    tags = func.doxygen.tags
    reqs = tags.get("req", [])
    after = tags.get("after", [])
    has_trace_tags = (
        tags.get("sends") or tags.get("receives") or tags.get("calls") or tags.get("note")
    )
    has_infra_tags = tags.get("send_source") or tags.get("receive_source")
    if not has_trace_tags and not has_infra_tags and not reqs:
        return None

    body_text = "\n".join(lines[func.def_line : func.body_end + 1])
    declared_sends = tags.get("sends", [])

    marker_tags = {t for t in ("send_source", "receive_source") if t in tags}
    return_vals = tags.get("return") or tags.get("returns")
    return_desc = return_vals[0] if return_vals else None

    tf = TaggedFunction(
        name=func.name,
        file_path=file_path,
        participant_name=_resolve_participant_from_reqs(reqs, req_participant_map) or file_module,
        sends=declared_sends,
        receives=tags.get("receives", []),
        calls=tags.get("calls", []),
        notes=tags.get("note", []),
        reqs=reqs,
        after=after,
        loop=tags.get("loop", [None])[0] if tags.get("loop") else None,
        group=tags.get("group", [None])[0] if tags.get("group") else None,
        body=body_text,
        marker_tags=marker_tags,
        return_desc=return_desc,
        is_internal="internal" in tags,
    )

    return tf


## @brief Infer @sends from emit function calls found in the function body.
#  @version 1.6
#  @req REQ-TRACE-001
def _apply_emit_inference(
    tf: TaggedFunction,
    body_text: str,
    config: dict[str, Any],
    file_cache: dict | None = None,
) -> None:
    trace_options = get_trace_options(config)
    if not trace_options.get("infer_sends", True):
        return

    emit_fns = trace_options.get("event_emit_functions", [])
    name_pattern = trace_options.get("event_name_pattern", r"^[A-Z][A-Z0-9_]*$")
    declared = set(tf.sends)

    body_node = _get_ast_body(tf, file_cache)
    if body_node is not None:
        call_type = _call_node_type_for_file(tf.file_path)
        constants = _ast_emit_call_args(body_node, call_type, set(emit_fns))
    else:
        constants = []
        for fn_name in emit_fns:
            pattern = rf"\b{re.escape(fn_name)}\s*\(\s*(\w+)"
            constants.extend(m.group(1) for m in re.finditer(pattern, body_text))

    for constant in constants:
        if not re.match(name_pattern, constant):
            logger.warning(
                "Rejected inferred event '%s' in %s() — fails pattern", constant, tf.name
            )
            continue
        if constant not in declared:
            tf.sends.append(constant)
            declared.add(constant)
            logger.info("Inferred @sends %s in %s()", constant, tf.name)


## @brief Detect phantom @sends — declared but no matching call in body.
#  @version 1.6
#  @req REQ-TRACE-001
#  @return List of event names declared but not called in function body
def detect_phantom_emits(
    tf: TaggedFunction,
    config: dict[str, Any],
    file_cache: dict | None = None,
) -> list[str]:
    trace_options = get_trace_options(config)
    emit_fns = trace_options.get("event_emit_functions", [])

    body_node = _get_ast_body(tf, file_cache)
    if body_node is not None:
        call_type = _call_node_type_for_file(tf.file_path)
        called_constants = set(_ast_emit_call_args(body_node, call_type, set(emit_fns)))
    else:
        called_constants: set[str] = set()
        for fn_name in emit_fns:
            pattern = rf"\b{re.escape(fn_name)}\s*\(\s*(\w+)"
            for match in re.finditer(pattern, tf.body):
                called_constants.add(match.group(1))

    phantoms: list[str] = []
    for event in tf.sends:
        if event not in called_constants:
            phantoms.append(event)
            logger.warning(
                "Possible phantom @sends %s in %s() — no matching call found", event, tf.name
            )
    return phantoms


## @brief Infer cross-module ext calls from function body scan.
#  @details For each tagged function, scan body for calls to functions owned by
#  different participants. If found and not already declared via @ext, add the
#  inferred ext reference. Manual @calls always takes precedence.
#  Also detects system boundary calls (callees not defined in scanned source).
#  @version 1.3
#  @req REQ-TRACE-001
def _apply_ext_inference(
    all_tagged: list[TaggedFunction],
    file_cache: dict | None = None,
) -> None:
    for tf in all_tagged:
        if tf.participant_name:
            _infer_ext_for_function(tf, all_tagged, file_cache)


## @brief Scan a single function's body for cross-module calls.
#  @version 1.4
#  @req REQ-TRACE-001
#  @return None (modifies tf.calls in place)
def _infer_ext_for_function(
    tf: TaggedFunction,
    all_tagged: list[TaggedFunction],
    file_cache: dict | None = None,
) -> None:
    declared_ext_funcs = {calls_func_name(ref) for ref in tf.calls}
    callees = _resolve_body_callees(tf, file_cache)

    for target in all_tagged:
        if target.name == tf.name or target.name in declared_ext_funcs:
            continue
        if not target.participant_name or target.participant_name == tf.participant_name:
            continue
        if not _body_calls_target(target.name, callees, tf.body):
            continue
        module = Path(target.file_path).stem
        ext_ref = f"{module}::{target.name}"
        tf.calls.append(ext_ref)
        logger.info("Inferred @calls%s in %s()", ext_ref, tf.name)


## @brief Resolve callee set from AST or return None for regex fallback.
#  @version 1.0
#  @internal
#  @return Set of callee names, or None if AST unavailable
def _resolve_body_callees(
    tf: TaggedFunction,
    file_cache: dict | None,
) -> set[str] | None:
    body_node = _get_ast_body(tf, file_cache)
    if body_node is None:
        return None
    call_type = _call_node_type_for_file(tf.file_path)
    return _ast_callee_set(body_node, call_type)


## @brief Check if a target function name appears in body callees or via regex.
#  @version 1.0
#  @internal
#  @return True if the target name is called in the body
def _body_calls_target(target_name: str, callees: set[str] | None, body: str) -> bool:
    if callees is not None:
        return target_name in callees
    return bool(re.search(rf"\b{re.escape(target_name)}\s*\(", body))


_EVENT_CONSTANT_PATTERN = re.compile(r"\b(EVENT_\w+)\b")


## @brief Look up a tagged function's AST body node from file cache.
#  @version 1.0
#  @internal
#  @return AST body node, or None if unavailable
def _get_ast_body(tf: TaggedFunction, file_cache: dict | None) -> Any:
    if file_cache is None:
        return None
    parsed = file_cache.get(tf.file_path)
    func_node = parsed.func_nodes.get(tf.name) if parsed else None
    return func_node.child_by_field_name("body") if func_node else None


## @brief Determine the call expression node type from a file path.
#  @version 1.0
#  @internal
#  @return Tree-sitter node type string for call expressions
def _call_node_type_for_file(file_path: str) -> str:
    return "call" if file_path.endswith(".py") else "call_expression"


## @brief Walk AST body for calls to target functions, extract first arg constants.
#  @version 1.0
#  @internal
#  @return List of first-argument identifier texts from matching calls
def _ast_emit_call_args(
    body_node: Any,
    call_type: str,
    target_callees: set[str],
) -> list[str]:
    result: list[str] = []
    _collect_emit_first_args(body_node, call_type, target_callees, result)
    return result


## @brief Extract the first identifier argument from a call node.
#  @version 1.0
#  @internal
#  @return First identifier argument text, or None
def _first_identifier_arg(call_node: Any) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


## @brief Recursively collect first argument identifiers from matching call nodes.
#  @version 1.0
#  @internal
def _collect_emit_first_args(
    node: Any,
    call_type: str,
    target_callees: set[str],
    result: list[str],
) -> None:
    if node.type == call_type:
        func_node = node.child_by_field_name("function")
        if func_node and func_node.type == "identifier":
            callee = func_node.text.decode("utf-8")
            if callee in target_callees:
                arg = _first_identifier_arg(node)
                if arg:
                    result.append(arg)
    for child in node.named_children:
        _collect_emit_first_args(child, call_type, target_callees, result)


## @brief Walk AST body and collect all callee function names.
#  @version 1.0
#  @internal
#  @return Set of callee names from call expressions
def _ast_callee_set(body_node: Any, call_type: str) -> set[str]:
    result: set[str] = set()
    _collect_callee_names_ast(body_node, call_type, result)
    return result


## @brief Recursively collect callee names from call expression nodes.
#  @version 1.0
#  @internal
def _collect_callee_names_ast(node: Any, call_type: str, result: set[str]) -> None:
    if node.type == call_type:
        func_node = node.child_by_field_name("function")
        if func_node and func_node.type == "identifier":
            result.add(func_node.text.decode("utf-8"))
    for child in node.named_children:
        _collect_callee_names_ast(child, call_type, result)


## @brief Walk AST body and collect all identifier references.
#  @version 1.0
#  @internal
#  @return Set of all identifier names in the body
def _ast_all_identifiers(body_node: Any) -> set[str]:
    result: set[str] = set()
    _collect_identifiers_ast(body_node, result)
    return result


## @brief Recursively collect identifier names from AST nodes.
#  @version 1.0
#  @internal
def _collect_identifiers_ast(node: Any, result: set[str]) -> None:
    if node.type == "identifier":
        result.add(node.text.decode("utf-8"))
    for child in node.named_children:
        _collect_identifiers_ast(child, result)


## @brief Build regex pattern for handle source function calls.
#  @version 1.1
#  @internal
def _build_register_pattern(handle_fns: list[str]) -> re.Pattern:
    names = "|".join(re.escape(fn) for fn in handle_fns)
    return re.compile(rf"(?:{names})\s*\(\s*([^,]+),\s*(\w+)\s*\)", re.DOTALL)


## @brief Infer handles from registration function call patterns.
#  @details Parses calls to @receive_source functions. Extracts event constants
#  from first argument (bitmask) and handler name from second argument.
#  @version 1.3
#  @req REQ-TRACE-001
def _infer_handles_from_registration(
    all_tagged: list[TaggedFunction],
    trace_options: dict[str, Any],
) -> None:
    handle_fns = trace_options.get("event_register_functions", [])
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
#  @version 1.2
#  @internal
def _add_inferred_handles(
    bitmask_expr: str,
    handler_tf: TaggedFunction,
) -> None:
    declared = set(handler_tf.receives)
    for const_match in _EVENT_CONSTANT_PATTERN.finditer(bitmask_expr):
        constant = const_match.group(1)
        if constant not in declared:
            handler_tf.receives.append(constant)
            declared.add(constant)
            logger.info("Inferred @receives %s for %s()", constant, handler_tf.name)


## @brief Discover infrastructure root functions from @send_source / @receive_source tags.
#  @details Scans the parsed doxygen tags dict for send_source and receive_source
#  marker tags. These are set during parse_doxygen_tags as tag names with empty values.
#  @version 1.3
#  @req REQ-TRACE-001
#  @return Dict mapping "emit_fns"/"handle_fns" to lists of function names
def _discover_infrastructure_roots(
    all_tagged: list[TaggedFunction],
) -> dict[str, list[str]]:
    roots: dict[str, list[str]] = {}
    for tf in all_tagged:
        if "send_source" in tf.marker_tags:
            roots.setdefault("emit_fns", []).append(tf.name)
            logger.info("Discovered @send_source: %s()", tf.name)
        if "receive_source" in tf.marker_tags:
            roots.setdefault("handle_fns", []).append(tf.name)
            logger.info("Discovered @receive_source: %s()", tf.name)
    return roots


## @brief Warn on tagged functions that are never called or registered in scanned source.
#  @details Scans all function bodies for call references. Functions that have
#  doxygen tags but are never referenced as a callee or handler argument are
#  likely dead code or missing Event_register() calls.
#  @version 1.2
#  @internal
def _warn_unreferenced_functions(
    all_tagged: list[TaggedFunction],
    file_cache: dict | None = None,
) -> None:
    all_names = {tf.name for tf in all_tagged}
    referenced = _collect_referenced_names(all_tagged, all_names, file_cache)

    for tf in all_tagged:
        if tf.name not in referenced and tf.receives and not tf.marker_tags:
            logger.warning(
                "%s() has behavioral tags but is never called or registered in scanned source"
                " — possible dead code or missing Event_register()",
                tf.name,
            )


## @brief Collect names of tagged functions that are referenced in any body.
#  @version 1.0
#  @internal
#  @return Set of referenced function names
def _collect_referenced_names(
    all_tagged: list[TaggedFunction],
    all_names: set[str],
    file_cache: dict | None,
) -> set[str]:
    referenced: set[str] = set()
    for tf in all_tagged:
        body_node = _get_ast_body(tf, file_cache)
        if body_node is not None:
            identifiers = _ast_all_identifiers(body_node)
            referenced.update((identifiers & all_names) - {tf.name})
        else:
            for name in all_names:
                if name != tf.name and re.search(rf"\b{re.escape(name)}\b", tf.body):
                    referenced.add(name)
    return referenced
