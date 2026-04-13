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
#  @version 1.2
#  @req REQ-PARSE-004
#  @return ParsedFile instance, or None if parsing fails
def get_parsed_file(file_path: str, lang_name: str) -> ParsedFile | None:
    if file_path in _file_cache:
        return _file_cache[file_path]
    parsed = _parse_and_index(file_path, lang_name)
    if parsed is not None:
        _file_cache[file_path] = parsed
    return parsed


## @brief Parse a source file and index its function nodes.
#  @version 1.4
#  @req REQ-PARSE-004
#  @return ParsedFile with AST tree and function index, or None on failure
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
#  @version 1.2
#  @req REQ-PARSE-004
#  @return The function_definition node, or None
def _resolve_function_node(child: Node, spec: LanguageSpec) -> Node | None:
    unwrapped = _unwrap_decorated(child)
    if unwrapped.type in spec.function_node_types:
        return unwrapped
    if child.type in ("template_declaration", "linkage_specification"):
        for sub in child.children:
            if sub.type in spec.function_node_types:
                return sub
    return None


## @brief Find the doxygen comment preceding a node via AST sibling.
#  @version 1.2
#  @req REQ-PARSE-004
#  @return Raw comment text, or None if no doxygen comment found
def _find_doxygen_comment(node: Node, spec: LanguageSpec) -> str | None:
    prev = node.prev_sibling
    comment = _resolve_comment_node(prev, spec)
    if comment is None:
        return None
    text = comment.text.decode("utf-8")
    if text.startswith("/**") or text.startswith("##"):
        return text
    return None


## @brief Resolve a preceding sibling to a comment node.
#  @details When a macro call like FSM_INITIAL_STATE() precedes a doxygen block,
#  tree-sitter parses the comment as a child of the expression_statement. This
#  function checks the last child of such nodes to find swallowed comments.
#  @version 1.0
#  @req REQ-PARSE-004
#  @return The comment node, or None
def _resolve_comment_node(prev: Node | None, spec: LanguageSpec) -> Node | None:
    if prev is None:
        return None
    if prev.type in spec.comment_node_types:
        return prev
    # Macro calls (e.g. FSM_INITIAL_STATE) can swallow trailing comments as children
    last = (
        prev.named_children[-1]
        if prev.type == "expression_statement" and prev.named_children
        else None
    )
    return last if last is not None and last.type in spec.comment_node_types else None


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
#  @version 1.1
#  @internal
def _name_from_declarator(declarator: Node) -> str | None:
    leaf_types = ("identifier", "field_identifier", "destructor_name")
    if declarator.type in leaf_types:
        return declarator.text.decode("utf-8") if declarator.text else None
    recurse_types = (
        "identifier",
        "function_declarator",
        "qualified_identifier",
        "field_identifier",
        "destructor_name",
    )
    match = next((c for c in declarator.children if c.type in recurse_types), None)
    return _name_from_declarator(match) if match is not None else None


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
#  @version 1.1
#  @internal
def _collect_c_comment(
    prev: Node | None,
    spec: LanguageSpec,
) -> list[Node] | None:
    comment = _resolve_comment_node(prev, spec)
    if comment is None:
        return None
    block_text = comment.text.decode("utf-8").strip()
    if not block_text.startswith("/**") or block_text.startswith("/***"):
        return None
    return [comment]


## @brief Find the doxygen comment block preceding a function node.
#  @version 1.4
#  @req REQ-PARSE-004
def _find_preceding_doxygen(
    func_node: Node,
    spec: LanguageSpec,
    comment_start_pattern: str,
) -> DoxygenBlock | None:
    target = func_node
    if func_node.parent and func_node.parent.type in (
        "decorated_definition",
        "template_declaration",
        "linkage_specification",
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
#  @version 1.1
#  @req REQ-PARSE-004
#  @return List of Function objects detected in the source content
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
#  @version 1.3
#  @req REQ-PARSE-004
def _collect_functions(
    node: Node,
    spec: LanguageSpec,
    exclude: set[str],
    comment_start_pattern: str,
    functions: list[Function],
    enclosing_class: str | None = None,
) -> None:
    for child in node.children:
        new_enclosing = _enclosing_class_for(child, enclosing_class)
        func_node = _resolve_function_node(child, spec)
        if func_node:
            name = _extract_function_name(func_node, spec)
            if name is None or name in exclude:
                continue
            body_node = func_node.child_by_field_name("body")
            if body_node is None:
                continue
            doxygen = _find_preceding_doxygen(func_node, spec, comment_start_pattern)
            resolved_enclosing = new_enclosing or _qualified_enclosing(func_node)
            functions.append(
                Function(
                    name=name,
                    def_line=func_node.start_point[0],
                    body_end=func_node.end_point[0],
                    doxygen=doxygen,
                    enclosing_class=resolved_enclosing,
                )
            )
        else:
            _collect_functions(
                child, spec, exclude, comment_start_pattern, functions, new_enclosing
            )


## @brief Resolve enclosing class name when entering a class/struct node.
#  @version 1.1
#  @internal
#  @return Class/struct name if node is a class definition, else parent value
def _enclosing_class_for(child: Node, parent: str | None) -> str | None:
    if child.type in ("class_specifier", "struct_specifier", "class_definition"):
        name_node = child.child_by_field_name("name")
        if name_node and name_node.text:
            return name_node.text.decode("utf-8")
    return parent


## @brief Extract enclosing class from a qualified function declarator (Foo::bar).
#  @version 1.0
#  @internal
#  @return Class name from qualifier prefix, or None if not qualified
def _qualified_enclosing(func_node: Node) -> str | None:
    declarator = func_node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type == "qualified_identifier":
            ns = declarator.child_by_field_name("scope")
            if ns and ns.text:
                return ns.text.decode("utf-8")
            return None
        declarator = declarator.child_by_field_name("declarator")
    return None
