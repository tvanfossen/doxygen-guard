"""Tree-sitter based function detection and doxygen block extraction.

@brief Parse source files using tree-sitter AST to find functions and their doxygen blocks.
@version 1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from doxygen_guard.parser import DoxygenBlock, Function, parse_doxygen_tags
from doxygen_guard.ts_languages import (
    get_language_spec,
    get_parser_for_language,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

    from doxygen_guard.ts_languages import LanguageSpec

logger = logging.getLogger(__name__)


## @brief Cached parse result for a source file, retaining AST for walker use.
#  @version 1.0
#  @internal
@dataclass
class ParsedFile:
    tree: Tree
    func_nodes: dict[str, Node] = field(default_factory=dict)


_file_cache: dict[str, ParsedFile] = {}


## @brief Get or create a ParsedFile for the given source.
#  @version 1.1
#  @internal
def get_parsed_file(file_path: str, lang_name: str) -> ParsedFile | None:
    if file_path in _file_cache:
        return _file_cache[file_path]
    parsed = _parse_and_index(file_path, lang_name)
    if parsed is not None:
        _file_cache[file_path] = parsed
    return parsed


## @brief Parse a source file and index its function nodes.
#  @version 1.1
#  @internal
def _parse_and_index(file_path: str, lang_name: str) -> ParsedFile | None:
    parser = get_parser_for_language(lang_name)
    spec = get_language_spec(lang_name)
    if parser is None or spec is None:
        return None
    content = Path(file_path).read_text(errors="replace")
    try:
        tree = parser.parse(content.encode("utf-8"))
    except Exception:
        logger.warning("Tree-sitter parse failed for %s, skipping AST", file_path)
        return None
    func_nodes = _index_function_nodes(tree.root_node, spec)
    return ParsedFile(tree=tree, func_nodes=func_nodes)


## @brief Clear the parsed file cache.
#  @version 1.0
#  @internal
def clear_cache() -> None:
    _file_cache.clear()


## @brief Index function definition nodes by name from the AST root.
#  @version 1.0
#  @internal
def _index_function_nodes(
    root: Node,
    spec: LanguageSpec,
) -> dict[str, Node]:
    result: dict[str, Node] = {}
    _index_recursive(root, spec, result)
    return result


## @brief Recursively index function nodes by name.
#  @version 1.0
#  @internal
def _index_recursive(
    node: Node,
    spec: LanguageSpec,
    result: dict[str, Node],
) -> None:
    for child in node.children:
        unwrapped = _unwrap_decorated(child)
        if unwrapped.type in spec.function_node_types:
            name = _extract_function_name(unwrapped, spec)
            if name:
                result[name] = unwrapped
        else:
            _index_recursive(child, spec, result)


## @brief Unwrap a decorated_definition to get the inner definition.
#  @version 1.0
#  @internal
def _unwrap_decorated(node: Node) -> Node:
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


## @brief Extract function name from an AST function node.
#  @version 1.0
#  @internal
def _extract_function_name(node: Node, spec: LanguageSpec) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node:
        return name_node.text.decode("utf-8")
    declarator = node.child_by_field_name("declarator")
    return _name_from_declarator(declarator) if declarator else None


## @brief Extract the identifier from a C/C++ function_declarator node.
#  @version 1.0
#  @internal
def _name_from_declarator(declarator: Node) -> str | None:
    if declarator.type == "identifier":
        return declarator.text.decode("utf-8")
    recurse_types = (
        "identifier",
        "function_declarator",
        "qualified_identifier",
        "field_identifier",
    )
    match = next((c for c in declarator.children if c.type in recurse_types), None)
    if match is None:
        return None
    return (
        match.text.decode("utf-8") if match.type == "identifier" else _name_from_declarator(match)
    )


## @brief Collect Python-style doxygen comment lines preceding a node.
#  @version 1.0
#  @internal
def _collect_python_comments(
    prev: Node | None,
    spec: LanguageSpec,
) -> list[Node] | None:
    lines: list[Node] = []
    while prev and prev.type in spec.comment_node_types:
        lines.insert(0, prev)
        prev = prev.prev_named_sibling
    if not lines:
        return None
    first_text = lines[0].text.decode("utf-8").strip()
    return lines if first_text.startswith("##") else None


## @brief Collect C-style doxygen comment block preceding a node.
#  @version 1.0
#  @internal
def _collect_c_comment(
    prev: Node | None,
    spec: LanguageSpec,
) -> list[Node] | None:
    if prev is None or prev.type not in spec.comment_node_types:
        return None
    block_text = prev.text.decode("utf-8").strip()
    if not block_text.startswith("/**") or block_text.startswith("/***"):
        return None
    return [prev]


## @brief Find the doxygen comment block preceding a function node.
#  @version 1.1
#  @internal
def _find_preceding_doxygen(
    func_node: Node,
    spec: LanguageSpec,
    comment_start_pattern: str,
) -> DoxygenBlock | None:
    target = func_node
    if func_node.parent and func_node.parent.type == "decorated_definition":
        target = func_node.parent

    prev = target.prev_named_sibling
    is_python = "##" in comment_start_pattern
    comment_lines = (
        _collect_python_comments(prev, spec) if is_python else _collect_c_comment(prev, spec)
    )
    if not comment_lines:
        return None

    raw = "\n".join(n.text.decode("utf-8") for n in comment_lines)
    tags = parse_doxygen_tags(raw)
    return DoxygenBlock(
        start_line=comment_lines[0].start_point[0],
        end_line=comment_lines[-1].end_point[0],
        tags=tags,
        raw=raw,
    )


## @brief Parse functions from source content using tree-sitter.
#  @version 1.0
#  @req REQ-PARSE-001
def parse_functions_ts(
    content: str,
    lang_name: str,
    exclude_names: list[str] | None = None,
    comment_start_pattern: str = r"/\*\*(?!\*)",
) -> list[Function]:
    parser = get_parser_for_language(lang_name)
    if parser is None:
        logger.warning("No parser for language %s, skipping", lang_name)
        return []

    spec = get_language_spec(lang_name)
    if spec is None:
        return []

    exclude = set(exclude_names or [])
    tree = parser.parse(content.encode("utf-8"))
    functions: list[Function] = []

    _collect_functions(tree.root_node, spec, exclude, comment_start_pattern, functions)

    return functions


## @brief Recursively collect function definitions from the AST.
#  @version 1.0
#  @internal
def _collect_functions(
    node: Node,
    spec: LanguageSpec,
    exclude: set[str],
    comment_start_pattern: str,
    functions: list[Function],
) -> None:
    for child in node.children:
        unwrapped = _unwrap_decorated(child)
        if unwrapped.type in spec.function_node_types:
            name = _extract_function_name(unwrapped, spec)
            if name is None or name in exclude:
                continue

            body_node = unwrapped.child_by_field_name("body")
            if body_node is None:
                continue

            doxygen = _find_preceding_doxygen(unwrapped, spec, comment_start_pattern)

            functions.append(
                Function(
                    name=name,
                    def_line=unwrapped.start_point[0],
                    body_end=unwrapped.end_point[0],
                    doxygen=doxygen,
                )
            )
        else:
            _collect_functions(child, spec, exclude, comment_start_pattern, functions)
