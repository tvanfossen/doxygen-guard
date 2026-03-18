"""Configuration loading, defaults, and merging for doxygen-guard.

@brief Load and validate .doxygen-guard.yaml configuration.
@version 1.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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
                "if", "for", "while", "switch", "return", "sizeof", "typedef",
                "define", "elif", "ifdef", "ifndef", "include",
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
                "if", "for", "while", "switch", "return", "sizeof", "typedef",
                "define", "elif", "ifdef", "ifndef", "include",
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
    "output_dir": "docs/generated/sequences/",
    "participants": [],
    "tag_mapping": {
        "emits": "arrow_to_handler",
        "handles": "receives_arrow",
        "ext": "call_arrow",
        "triggers": "note",
    },
    "options": {
        "show_notes": True,
        "show_returns": False,
        "group_by_bus": True,
        "autonumber": True,
    },
}

IMPACT_DEFAULTS: dict[str, Any] = {
    "requirements": None,
    "test_mapping": [],
    "output": {
        "format": "markdown",
        "file": None,
    },
}

CONFIG_DEFAULTS: dict[str, Any] = {
    "validate": VALIDATE_DEFAULTS,
    "trace": TRACE_DEFAULTS,
    "impact": IMPACT_DEFAULTS,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge override into base, returning a new dict.

    @brief Recursively merge two dicts; override values win for non-dict leaves.
    @version 1.0
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load config from YAML file, merging with defaults.

    @brief Load .doxygen-guard.yaml and merge with built-in defaults.
    @version 1.0
    """
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

    return deep_merge(CONFIG_DEFAULTS, user_config)


def get_language_config(config: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    """Find the language config matching a file's extension.

    @brief Match a file path to its language config by extension.
    @version 1.0
    """
    ext = Path(file_path).suffix
    languages = config.get("validate", {}).get("languages", {})

    for _lang_name, lang_config in languages.items():
        if ext in lang_config.get("extensions", []):
            return lang_config
    return None
