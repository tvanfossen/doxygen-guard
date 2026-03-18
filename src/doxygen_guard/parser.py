"""Function detection and doxygen block extraction.

@brief Parse source files to find functions and their associated doxygen blocks.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


## @brief Represents a doxygen comment with its location and parsed tags.
#  @version 1.0
@dataclass
class DoxygenBlock:
    start_line: int  # 0-indexed
    end_line: int  # 0-indexed
    tags: dict[str, list[str]] = field(default_factory=dict)
    raw: str = ""


## @brief Represents a function with its location and optional doxygen block.
#  @version 1.0
@dataclass
class Function:
    name: str
    def_line: int  # 0-indexed
    body_end: int  # 0-indexed
    doxygen: DoxygenBlock | None = None


## @brief Parse all @tag entries from doxygen comment text.
#  @version 1.0
def parse_doxygen_tags(block_text: str) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    tag_pattern = re.compile(r"@(\w+)\s+(.*?)(?=\s*(?:@\w+\s|\*/|$))", re.DOTALL)

    for match in tag_pattern.finditer(block_text):
        tag_name = match.group(1)
        tag_value = match.group(2).strip()
        # Clean up multi-line continuation prefixes (C: leading *, Python: leading #)
        tag_value = re.sub(r"\n\s*[*#]\s*", " ", tag_value).strip()
        tags.setdefault(tag_name, []).append(tag_value)

    return tags


## @brief Find the doxygen comment block immediately before a function definition.
#  @version 1.0
#
#  Scans backward from func_line, skipping blank lines, looking for a comment
#  block that ends with comment_end and starts with comment_start (/** specifically,
#  not just /*).
def find_doxygen_block_before(
    lines: list[str],
    func_line: int,
    comment_start: str,
    comment_end: str,
) -> DoxygenBlock | None:
    # Compiled patterns — comment_start/comment_end are regex strings from config
    start_re = re.compile(comment_start)
    end_re = re.compile(comment_end)

    # Pattern for GCC/compiler attributes that may sit between doxygen and function
    attr_re = re.compile(r"^\s*__attribute__\s*\(\(")

    scan_line = func_line - 1
    # Skip blank lines and __attribute__ annotations between function and doxygen
    while scan_line >= 0:
        stripped = lines[scan_line].strip()
        if stripped == "" or attr_re.match(lines[scan_line]):
            scan_line -= 1
            continue
        break

    if scan_line < 0:
        return None

    # Check if this line contains the comment end marker
    if not end_re.search(lines[scan_line]):
        return None

    end_line = scan_line

    # Scan backward to find comment start.
    # Stop if we cross a closing brace (another function body) or hit
    # a second comment-end marker (a different comment block).
    while scan_line >= 0:
        if start_re.search(lines[scan_line]):
            block_text = "\n".join(lines[scan_line : end_line + 1])
            tags = parse_doxygen_tags(block_text)
            return DoxygenBlock(
                start_line=scan_line,
                end_line=end_line,
                tags=tags,
                raw=block_text,
            )

        # Crossed into another function body or a different comment — abort
        line_stripped = lines[scan_line].strip()
        if "}" in line_stripped:
            return None

        scan_line -= 1

    return None


## @brief Locate the end of a function body by matching braces.
#  @version 1.0
def find_body_end(lines: list[str], start_line: int) -> int:
    brace_depth = 0
    found_open = False

    for i in range(start_line, len(lines)):
        for char in lines[i]:
            if char == "{":
                brace_depth += 1
                found_open = True
            elif char == "}":
                brace_depth -= 1
                if found_open and brace_depth == 0:
                    return i

    # If no closing brace found, return the last line
    return len(lines) - 1


## @brief Locate the last line of a Python function body by tracking indentation.
#  @version 1.0
def find_body_end_indent(lines: list[str], start_line: int) -> int:
    # Find the indentation of the def line
    def_indent = len(lines[start_line]) - len(lines[start_line].lstrip())

    # The body starts after the colon; find the first non-empty body line to get body indent
    body_indent = None
    last_body_line = start_line

    for i in range(start_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            # Blank lines within a function body are fine
            continue

        current_indent = len(lines[i]) - len(lines[i].lstrip())

        if body_indent is None:
            # First non-blank line after def — establishes body indentation
            if current_indent > def_indent:
                body_indent = current_indent
                last_body_line = i
            else:
                # No body (e.g., just `def f(): pass` on one line)
                return start_line
        elif current_indent >= body_indent:
            last_body_line = i
        else:
            # Indentation dropped back to or below def level — body ended
            break

    return last_body_line


## @brief Detect forward declarations to skip them during validation.
#  @version 1.0
def is_forward_declaration(lines: list[str], func_line: int) -> bool:
    # Check current line and next few lines for semicolon before opening brace
    for i in range(func_line, min(func_line + 3, len(lines))):
        stripped = lines[i].rstrip()
        if "{" in stripped:
            return False
        if stripped.endswith(";"):
            return True

    return False


## @brief Parse source code to find functions and their associated doxygen comments.
#  @version 1.1
def parse_functions(
    content: str,
    function_pattern: str,
    exclude_names: list[str],
    comment_start: str,
    comment_end: str,
    skip_forward_declarations: bool = True,
    body_style: str = "braces",
) -> list[Function]:
    lines = content.splitlines()
    func_re = re.compile(function_pattern, re.MULTILINE)
    functions: list[Function] = []

    body_end_fn = find_body_end_indent if body_style == "indent" else find_body_end

    for i, line in enumerate(lines):
        match = func_re.match(line)
        if not match:
            continue

        func_name = match.group(1)
        if func_name in exclude_names:
            continue

        if skip_forward_declarations and is_forward_declaration(lines, i):
            logger.debug("Skipping forward declaration: %s at line %d", func_name, i + 1)
            continue

        body_end = body_end_fn(lines, i)
        doxygen = find_doxygen_block_before(lines, i, comment_start, comment_end)

        func = Function(
            name=func_name,
            def_line=i,
            body_end=body_end,
            doxygen=doxygen,
        )
        functions.append(func)
        logger.debug("Found function: %s at line %d", func_name, i + 1)

    return functions
