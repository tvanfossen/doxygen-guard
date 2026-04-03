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
#  @version 1.2
#  @internal
@dataclass
class ParsedFile:
    tree: Tree
    func_nodes: dict[str, Node] = field(default_factory=dict)
    module_name: str | None = None
    comment_map: dict[str, str] = field(default_factory=dict)


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
#  @version 1.3
#  @internal
def _parse_and_index(file_path: str, lang_name: str) -> ParsedFile | None:
    parser = get_parser_for_language(lang_name)
    spec = get_language_spec(lang_name)
    if parser is None or spec is None:
        return None
    content = Path(file_path).read_text(errors="replace")
    try:
        tree = parser.parse(content.encode("utf-8"))
    except (ValueError, OSError):
        logger.warning("Tree-sitter parse failed for %s, skipping AST", file_path)
        return None
    func_nodes, comment_map = _index_function_nodes(tree.root_node, spec)
    return ParsedFile(tree=tree, func_nodes=func_nodes, comment_map=comment_map)


## @brief Index function definition nodes by name from the AST root.
#  @version 1.1
#  @internal
#  @return Tuple of (func_nodes dict, comment_map dict)
def _index_function_nodes(
    root: Node,
    spec: LanguageSpec,
) -> tuple[dict[str, Node], dict[str, str]]:
    func_nodes: dict[str, Node] = {}
    comment_map: dict[str, str] = {}
    _index_recursive(root, spec, func_nodes, comment_map)
    return func_nodes, comment_map


## @brief Recursively index function nodes and their doxygen comments.
#  @version 1.1
#  @internal
def _index_recursive(
    node: Node,
    spec: LanguageSpec,
    func_nodes: dict[str, Node],
    comment_map: dict[str, str],
) -> None:
    for child in node.children:
        func_node = _resolve_function_node(child, spec)
        if func_node:
            name = _extract_function_name(func_node, spec)
            if name:
                func_nodes[name] = func_node
                comment = _find_doxygen_comment(child, spec)
                if comment:
                    comment_map[name] = comment
        else:
            _index_recursive(child, spec, func_nodes, comment_map)


## @brief Resolve a child node to a function_definition, handling wrappers.
#  @version 1.0
#  @req REQ-PARSE-002
#  @return The function_definition node, or None
def _resolve_function_node(child: Node, spec: LanguageSpec) -> Node | None:
    unwrapped = _unwrap_decorated(child)
    if unwrapped.type in spec.function_node_types:
        return unwrapped
    if child.type == "template_declaration":
        for sub in child.children:
            if sub.type in spec.function_node_types:
                return sub
    return None


## @brief Find the doxygen comment preceding a node via AST sibling.
#  @version 1.0
#  @req REQ-PARSE-002
#  @return Raw comment text, or None if no doxygen comment found
def _find_doxygen_comment(node: Node, spec: LanguageSpec) -> str | None:
    prev = node.prev_sibling
    if prev is None or prev.type not in spec.comment_node_types:
        return None
    text = prev.text.decode("utf-8")
    if text.startswith("/**") or text.startswith("##"):
        return text
    return None


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
#  @version 1.2
#  @req REQ-PARSE-002
def _find_preceding_doxygen(
    func_node: Node,
    spec: LanguageSpec,
    comment_start_pattern: str,
) -> DoxygenBlock | None:
    target = func_node
    if func_node.parent and func_node.parent.type in (
        "decorated_definition",
        "template_declaration",
    ):
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
#  @version 1.1
#  @internal
def _collect_functions(
    node: Node,
    spec: LanguageSpec,
    exclude: set[str],
    comment_start_pattern: str,
    functions: list[Function],
) -> None:
    for child in node.children:
        func_node = _resolve_function_node(child, spec)
        if func_node:
            name = _extract_function_name(func_node, spec)
            if name is None or name in exclude:
                continue
            body_node = func_node.child_by_field_name("body")
            if body_node is None:
                continue
            doxygen = _find_preceding_doxygen(func_node, spec, comment_start_pattern)
            functions.append(
                Function(
                    name=name,
                    def_line=func_node.start_point[0],
                    body_end=func_node.end_point[0],
                    doxygen=doxygen,
                )
            )
        else:
            _collect_functions(child, spec, exclude, comment_start_pattern, functions)
