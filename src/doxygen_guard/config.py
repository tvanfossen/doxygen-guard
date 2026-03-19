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
                r"^(?:(?:static|inline|extern|STATIC|INLINE|WEAK)\s+)*"
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
                r"^(?:(?:static|inline|extern|virtual|explicit|constexpr)\s+)*"
                r"(?:template\s*<[^>]*>\s*)?"
                r"(?:(?:const|volatile|unsigned|signed|long|short|struct|enum)\s+)*"
                r"(?:[A-Za-z_]\w*(?:<[^>]*>)?)[\s*&]+"
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
    "options": {
        "autonumber": True,
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
        "presence": {"require_doxygen": bool, "skip_forward_declarations": bool},
        "version": {
            "tag": str,
            "require_present": bool,
            "require_increment_on_change": bool,
        },
        "tags": _OPEN_DICT,
        "exclude": list,
        "version_gate": {"current_version": str, "version_field": str},
    },
    "trace": {
        "format": str,
        "participant_field": str,
        "external": list,
        "options": {"autonumber": bool},
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
#  @version 1.0
#  @req REQ-CONFIG-001
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
#  @version 1.1
#  @req REQ-CONFIG-001
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

    return deep_merge(CONFIG_DEFAULTS, user_config)


## @brief Match a file path to its language config by extension.
#  @version 1.0
#  @req REQ-CONFIG-002
def get_language_config(config: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    ext = Path(file_path).suffix
    languages = config.get("validate", {}).get("languages", {})

    for _lang_name, lang_config in languages.items():
        if ext in lang_config.get("extensions", []):
            return lang_config
    return None


## @brief Resolve comment style and body style for a given language config.
#  @version 1.1
#  @req REQ-CONFIG-002
def resolve_parse_settings(config: dict[str, Any], lang_config: dict[str, Any]) -> ParseSettings:
    from doxygen_guard.parser import ParseSettings

    global_style = config.get("validate", {}).get("comment_style", {})
    lang_style = lang_config.get("comment_style", {})
    return ParseSettings(
        comment_start=lang_style.get("start", global_style.get("start", r"/\*\*(?!\*)")),
        comment_end=lang_style.get("end", global_style.get("end", r"\*/")),
        body_style=lang_config.get("body_style", "braces"),
    )
