"""Configuration loading, defaults, and merging for doxygen-guard.

@brief Load and validate .doxygen-guard.yaml configuration.
@version 1.0
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doxygen_guard.parser import ParseSettings

import yaml

logger = logging.getLogger(__name__)

VALIDATE_DEFAULTS: dict[str, Any] = {
    "languages": {
        "c": {
            "extensions": [".c", ".h"],
            "function_pattern": (
                r"^\s*(?:(?:static|inline|extern|STATIC|INLINE|WEAK)\s+)*"
                r"(?:(?:const|volatile|unsigned|signed|long|short|struct|enum)\s+)*"
                r"(?:[A-Za-z_]\w*)[\s*]+"
                r"(\w+)\s*\("
            ),
            "exclude_names": [
                "if",
                "for",
                "while",
                "switch",
                "return",
                "sizeof",
                "typedef",
                "define",
                "elif",
                "ifdef",
                "ifndef",
                "include",
            ],
            "extra_qualifiers": ["STATIC", "INLINE", "WEAK"],
        },
        "cpp": {
            "extensions": [".cpp", ".hpp", ".cc", ".cxx"],
            "function_pattern": (
                r"^\s*(?:(?:static|inline|extern(?:\s+\"C\")?|virtual|explicit|constexpr)\s+)*"
                r"(?:template\s*<[^>]*>\s*)?"
                r"(?:(?:const|volatile|unsigned|signed|long|short|struct|enum)\s+)*"
                r"(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*(?:<[^>]*>)?)[\s*&]+"
                r"(?:[A-Za-z_]\w*::)*(\w+)\s*\("
            ),
            "exclude_names": [
                "if",
                "for",
                "while",
                "switch",
                "return",
                "sizeof",
                "typedef",
                "define",
                "elif",
                "ifdef",
                "ifndef",
                "include",
            ],
            "extra_qualifiers": [],
        },
        "java": {
            "extensions": [".java"],
            "function_pattern": (
                r"^\s*(?:(?:public|private|protected|static|final|abstract|"
                r"synchronized|native)\s+)*"
                r"(?:(?:void|boolean|byte|char|short|int|long|float|double|"
                r"[A-Z]\w+(?:<[^>]*>)?)\s+)"
                r"(\w+)\s*\("
            ),
            "exclude_names": ["if", "for", "while", "switch", "return"],
        },
        "python": {
            "extensions": [".py"],
            "function_pattern": r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
            "exclude_names": [],
            "comment_style": {
                "start": r"^\s*##(?!#)",
                "end": r"^\s*#",
            },
            "body_style": "indent",
        },
    },
    "comment_style": {
        "start": r"/\*\*(?!\*)",
        "end": r"\*/",
    },
    "presence": {
        "require_doxygen": True,
        "skip_forward_declarations": True,
        "require_return": True,
    },
    "version": {
        "tag": "@version",
        "require_present": True,
        "require_increment_on_change": True,
    },
    "tags": {},
    "exclude": [],
}

TRACE_DEFAULTS: dict[str, Any] = {
    "format": "plantuml",
    "external_fallback": "External",
    "options": {
        "autonumber": True,
        "box_label": "System",
        "event_name_pattern": r"^[A-Z][A-Z0-9_]*$",
        "infer_emits": True,
        "infer_ext": True,
        "event_emit_functions": [],
        "show_returns": True,
        "min_edges": 1,
        "label_mode": "full",
        "legend": False,
        "cross_req_depth": 1,
        "show_return_values": True,
        "show_recovery_notes": True,
        "max_condition_length": 80,
        "show_project_calls": True,
    },
}

IMPACT_DEFAULTS: dict[str, Any] = {
    "requirements": None,
}

CONFIG_DEFAULTS: dict[str, Any] = {
    "output_dir": "docs/generated/",
    "validate": VALIDATE_DEFAULTS,
    "trace": TRACE_DEFAULTS,
    "impact": IMPACT_DEFAULTS,
}


_OPEN_DICT = object()

CONFIG_SCHEMA: dict[str, Any] = {
    "output_dir": str,
    "validate": {
        "languages": _OPEN_DICT,
        "comment_style": {"start": str, "end": str},
        "presence": {
            "require_doxygen": bool,
            "skip_forward_declarations": bool,
            "require_return": bool,
        },
        "version": {
            "tag": str,
            "require_present": bool,
            "require_increment_on_change": bool,
        },
        "tags": _OPEN_DICT,
        "exclude": list,
        "extra_tags": list,
        "known_tags_warn": bool,
        "version_gate": {"current_version": str, "version_field": str},
    },
    "trace": {
        "format": str,
        "participant_field": str,
        "external_fallback": str,
        "external": list,
        "static_participants": list,
        "options": _OPEN_DICT,
    },
    "impact": {
        "requirements": {
            "file": str,
            "format": str,
            "id_column": str,
            "name_column": str,
        },
        "output": {
            "format": str,
            "file": str,
        },
    },
}


## @brief Build a dotted config path from parent path and key.
#  @version 1.0
#  @internal
def _config_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


## @brief Validate dict keys against schema, recursing into sub-nodes.
#  @version 1.0
#  @internal
def _validate_dict_node(user: dict, schema: dict, path: str) -> list[str]:
    errors: list[str] = []
    for key in user:
        child_path = _config_path(path, key)
        if key not in schema:
            errors.append(f"Unknown config key: {child_path}")
        else:
            errors.extend(_validate_node(user[key], schema[key], child_path))
    return errors


## @brief Validate a single config node against its schema spec.
#  @version 1.4
#  @internal
def _validate_node(user: Any, schema: Any, path: str) -> list[str]:
    if schema is _OPEN_DICT or not isinstance(schema, type | dict):
        return []
    if isinstance(schema, type):
        return (
            []
            if isinstance(user, schema)
            else [f"{path}: expected {schema.__name__}, got {type(user).__name__}"]
        )
    return (
        [f"{path}: expected dict, got {type(user).__name__}"]
        if not isinstance(user, dict)
        else _validate_dict_node(user, schema, path)
    )


## @brief Validate user config keys and types against CONFIG_SCHEMA.
#  @version 1.1
#  @req REQ-CONFIG-001
#  @return List of error strings, empty if config is valid
def validate_config_schema(user_config: dict[str, Any]) -> list[str]:
    return _validate_node(user_config, CONFIG_SCHEMA, "")


## @brief Parse a version string like "v1.8.2" into a comparable tuple.
#  @version 1.1
#  @internal
def parse_version(version_str: str) -> tuple[int, ...]:
    cleaned = version_str.strip().lstrip("vV")
    # Strip pre-release and build metadata (e.g., -rc1, +build123)
    cleaned = cleaned.split("-")[0].split("+")[0]
    try:
        return tuple(int(p) for p in cleaned.split("."))
    except ValueError:
        logger.warning("Could not parse version: %s", version_str)
        return (0,)


## @brief Compare two version tuples, padding shorter one with zeros.
#  @version 1.0
#  @internal
def compare_versions(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    max_len = max(len(a), len(b))
    a_padded = a + (0,) * (max_len - len(a))
    b_padded = b + (0,) * (max_len - len(b))
    if a_padded < b_padded:
        return -1
    if a_padded > b_padded:
        return 1
    return 0


## @brief Recursively merge two dicts; override values win for non-dict leaves.
#  @version 1.0
#  @utility
def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


## @brief Load .doxygen-guard.yaml and merge with built-in defaults.
#  @version 1.3
#  @req REQ-CONFIG-001
#  @return Merged config dict with defaults applied
def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = Path(".doxygen-guard.yaml")

    if not config_path.exists():
        logger.info("No config file found at %s, using defaults", config_path)
        return deep_merge(CONFIG_DEFAULTS, {})

    logger.info("Loading config from %s", config_path)
    with open(config_path) as f:
        user_config = yaml.safe_load(f) or {}

    if not isinstance(user_config, dict):
        logger.warning("Config file %s is not a mapping, using defaults", config_path)
        return deep_merge(CONFIG_DEFAULTS, {})

    errors = validate_config_schema(user_config)
    if errors:
        for err in errors:
            print(f"doxygen-guard config error: {err}", file=sys.stderr)
        sys.exit(1)

    merged = deep_merge(CONFIG_DEFAULTS, user_config)
    _validate_trace_options(merged)
    return merged


## @brief Validate types of trace.options values after merge.
#  @version 1.0
#  @internal
def _validate_trace_options(config: dict[str, Any]) -> None:
    options = get_trace(config).get("options", {})
    int_keys = {"min_edges": 0, "max_condition_length": 1, "max_chain_depth": 1}
    bool_keys = {
        "show_returns",
        "show_return_values",
        "show_recovery_notes",
        "show_project_calls",
        "legend",
        "autonumber",
        "infer_emits",
        "infer_ext",
    }
    for key, min_val in int_keys.items():
        if key in options and (not isinstance(options[key], int) or options[key] < min_val):
            logger.warning("trace.options.%s must be int >= %d, got %r", key, min_val, options[key])
    if "cross_req_depth" in options:
        val = options["cross_req_depth"]
        if not isinstance(val, int) or val < -1:
            logger.warning("trace.options.cross_req_depth must be int >= -1, got %r", val)
    for key in bool_keys:
        if key in options and not isinstance(options[key], bool):
            logger.warning("trace.options.%s must be bool, got %r", key, options[key])


## @brief Access the validate section of config.
#  @version 1.0
#  @internal
def get_validate(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("validate", {})


## @brief Access the trace section of config.
#  @version 1.0
#  @internal
def get_trace(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("trace", {})


## @brief Access the trace options subsection of config.
#  @version 1.0
#  @internal
#  @return The trace.options dict, or empty dict if not configured
def get_trace_options(config: dict[str, Any]) -> dict[str, Any]:
    return get_trace(config).get("options", {})


## @brief Access the impact section of config.
#  @version 1.0
#  @internal
def get_impact(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("impact", {})


## @brief Reject output paths containing directory traversal or absolute components.
#  @version 1.3
#  @utility
#  @supports REQ-CONFIG-001
#  @supports REQ-TRACE-001
#  @supports REQ-IMPACT-003
def validate_output_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        msg = f"Output path '{path}' must be relative"
        raise ValueError(msg)
    if ".." in p.parts:
        msg = f"Output path '{path}' contains directory traversal"
        raise ValueError(msg)
    return p


## @brief Match a file path to its language config by extension.
#  @version 1.2
#  @req REQ-CONFIG-002
#  @return Language config dict, or None if no language matches the file extension
def get_language_config(config: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    ext = Path(file_path).suffix
    languages = get_validate(config).get("languages", {})

    for _lang_name, lang_config in languages.items():
        if ext in lang_config.get("extensions", []):
            return lang_config
    return None


## @brief Parse all functions from a source file using language-aware settings.
#  @version 1.2
#  @req REQ-PARSE-001
def parse_source_file(
    file_path: str,
    config: dict[str, Any],
    skip_forward_declarations: bool = True,
) -> list | None:
    result = parse_source_file_with_content(file_path, config, skip_forward_declarations)
    if result is None:
        return None
    return result[0]


## @brief Parse functions and return both the function list and file content.
#  @version 1.2
#  @req REQ-PARSE-001
def parse_source_file_with_content(
    file_path: str,
    config: dict[str, Any],
    skip_forward_declarations: bool = True,
) -> tuple[list, str] | None:
    from doxygen_guard.parser import parse_functions
    from doxygen_guard.ts_languages import language_for_file

    lang_config = get_language_config(config, file_path)
    if lang_config is None:
        return None

    content = Path(file_path).read_text(errors="replace")
    settings = resolve_parse_settings(config, lang_config)
    lang_name = language_for_file(file_path, config)
    functions = parse_functions(
        content=content,
        function_pattern=lang_config["function_pattern"],
        exclude_names=lang_config.get("exclude_names", []),
        settings=settings,
        skip_forward_declarations=skip_forward_declarations,
        lang_name=lang_name,
    )
    return functions, content


## @brief Resolve comment style and body style for a given language config.
#  @version 1.4
#  @req REQ-CONFIG-002
#  @return ParseSettings with comment style and body detection mode
def resolve_parse_settings(config: dict[str, Any], lang_config: dict[str, Any]) -> ParseSettings:
    from doxygen_guard.parser import ParseSettings

    default_style = VALIDATE_DEFAULTS["comment_style"]
    global_style = get_validate(config).get("comment_style", {})
    lang_style = lang_config.get("comment_style", {})
    return ParseSettings(
        comment_start=lang_style.get("start", global_style.get("start", default_style["start"])),
        comment_end=lang_style.get("end", global_style.get("end", default_style["end"])),
        body_style=lang_config.get("body_style", "braces"),
    )
