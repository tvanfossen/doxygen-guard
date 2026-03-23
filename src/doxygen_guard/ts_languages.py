"""Tree-sitter grammar loading and language-specific node type maps.

@brief Initialize tree-sitter parsers and provide node type mappings per language.
@version 1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from tree_sitter import Language, Parser

logger = logging.getLogger(__name__)


## @brief Node type mappings and grammar module for a single language.
#  @version 1.0
#  @internal
@dataclass(frozen=True)
class LanguageSpec:
    grammar_module: str
    function_node_types: tuple[str, ...]
    call_node_type: str
    comment_node_types: tuple[str, ...]
    control_flow_types: dict[str, str] = field(default_factory=dict)


LANGUAGE_SPECS: dict[str, LanguageSpec] = {
    "c": LanguageSpec(
        grammar_module="tree_sitter_c",
        function_node_types=("function_definition",),
        call_node_type="call_expression",
        comment_node_types=("comment",),
        control_flow_types={
            "while_statement": "loop",
            "for_statement": "loop",
            "do_statement": "loop",
            "if_statement": "alt",
        },
    ),
    "cpp": LanguageSpec(
        grammar_module="tree_sitter_cpp",
        function_node_types=("function_definition",),
        call_node_type="call_expression",
        comment_node_types=("comment",),
        control_flow_types={
            "while_statement": "loop",
            "for_statement": "loop",
            "do_statement": "loop",
            "if_statement": "alt",
        },
    ),
    "python": LanguageSpec(
        grammar_module="tree_sitter_python",
        function_node_types=("function_definition",),
        call_node_type="call",
        comment_node_types=("comment",),
        control_flow_types={
            "while_statement": "loop",
            "for_statement": "loop",
            "if_statement": "alt",
        },
    ),
    "java": LanguageSpec(
        grammar_module="tree_sitter_java",
        function_node_types=("method_declaration", "constructor_declaration"),
        call_node_type="method_invocation",
        comment_node_types=("block_comment", "line_comment"),
        control_flow_types={
            "while_statement": "loop",
            "for_statement": "loop",
            "do_statement": "loop",
            "if_statement": "alt",
        },
    ),
}

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".py": "python",
    ".java": "java",
}


## @brief Load a tree-sitter Language object by importing the grammar module.
#  @version 1.0
#  @internal
@lru_cache(maxsize=8)
def _load_language(grammar_module: str) -> Language:
    import importlib

    mod = importlib.import_module(grammar_module)
    return Language(mod.language())


## @brief Get a tree-sitter Parser for a named language.
#  @version 1.0
#  @req REQ-TRACE-001
def get_parser_for_language(lang_name: str) -> Parser | None:
    spec = LANGUAGE_SPECS.get(lang_name)
    if spec is None:
        logger.warning("No tree-sitter spec for language: %s", lang_name)
        return None
    language = _load_language(spec.grammar_module)
    return Parser(language)


## @brief Get the LanguageSpec for a named language.
#  @version 1.0
#  @req REQ-TRACE-001
def get_language_spec(lang_name: str) -> LanguageSpec | None:
    return LANGUAGE_SPECS.get(lang_name)


## @brief Resolve a file extension to a language name.
#  @version 1.0
#  @req REQ-TRACE-001
def language_for_extension(ext: str) -> str | None:
    return EXTENSION_TO_LANGUAGE.get(ext)


## @brief Resolve a file path to a language name using config extensions.
#  @version 1.0
#  @req REQ-TRACE-001
def language_for_file(file_path: str, config: dict[str, Any]) -> str | None:
    from pathlib import Path

    ext = Path(file_path).suffix
    lang = language_for_extension(ext)
    if lang:
        return lang

    languages = config.get("validate", {}).get("languages", {})
    for lang_name, lang_config in languages.items():
        if ext in lang_config.get("extensions", []):
            return lang_name if lang_name in LANGUAGE_SPECS else None
    return None
