"""Function detection and doxygen block extraction.

@brief Parse source files to find functions and their associated doxygen blocks.
@version 1.1
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


## @brief Represents a doxygen comment with its location and parsed tags.
#  @version 1.0
#  @internal
@dataclass
class DoxygenBlock:
    start_line: int  # 0-indexed
    end_line: int  # 0-indexed
    tags: dict[str, list[str]] = field(default_factory=dict)
    raw: str = ""


## @brief Represents a function with its location and optional doxygen block.
#  @version 1.0
#  @internal
@dataclass
class Function:
    name: str
    def_line: int  # 0-indexed
    body_end: int  # 0-indexed
    doxygen: DoxygenBlock | None = None


## @brief Parse settings for comment style and body detection.
#  @version 1.0
#  @req REQ-CONFIG-002
@dataclass
class ParseSettings:
    comment_start: str = r"/\*\*(?!\*)"
    comment_end: str = r"\*/"
    body_style: str = "braces"


## @brief Split a line containing multiple @tag entries into separate segments.
#  @version 1.0
#  @internal
def _split_inline_tags(line: str) -> list[str]:
    parts = re.split(r"(?=\s@\w+)", line)
    return [p.strip() for p in parts if p.strip()]


## @brief Process a single cleaned line, updating current tag state.
#  @version 1.0
#  @internal
def _process_tag_line(
    line: str,
    tags: dict[str, list[str]],
    current_tag: str | None,
    current_value: list[str],
    tag_start_re: re.Pattern,
) -> tuple[str | None, list[str]]:
    match = tag_start_re.match(line)
    if match:
        if current_tag is not None:
            tags.setdefault(current_tag, []).append(" ".join(current_value).strip())
        return match.group(1), [match.group(2) or ""]
    if not line and current_tag is not None:
        tags.setdefault(current_tag, []).append(" ".join(current_value).strip())
        return None, []
    if current_tag is not None and line:
        current_value.append(line)
    return current_tag, current_value


## @brief Parse all @tag entries from doxygen comment text.
#  @version 1.3
#  @req REQ-PARSE-002
def parse_doxygen_tags(block_text: str) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    current_tag: str | None = None
    current_value: list[str] = []
    prefix_re = re.compile(r"^\s*[/*#]+\s?")
    suffix_re = re.compile(r"\s*\*/\s*$")
    tag_start_re = re.compile(r"^@(\w+)(?:\s+(.*))?$")

    for raw_line in block_text.splitlines():
        line = prefix_re.sub("", raw_line)
        line = suffix_re.sub("", line).strip()

        # Split lines with multiple @tags (e.g., "@brief X. @version 1.0")
        segments = _split_inline_tags(line) if line.count("@") > 1 else [line]
        for segment in segments:
            current_tag, current_value = _process_tag_line(
                segment,
                tags,
                current_tag,
                current_value,
                tag_start_re,
            )

    if current_tag is not None:
        tags.setdefault(current_tag, []).append(" ".join(current_value).strip())

    return tags


## @brief Scan backward from func_line to find the first non-blank, non-attribute line.
#  @version 1.1
#  @internal
def _skip_blanks_and_attrs(lines: list[str], func_line: int) -> int:
    attr_re = re.compile(r"^\s*__attribute__\s*\(\(")
    scan = func_line - 1
    while scan >= 0:
        stripped = lines[scan].strip()
        if stripped == "" or attr_re.match(lines[scan]):
            scan -= 1
            continue
        break
    return scan


## @brief Scan backward from end_line to find the comment start marker.
#  @version 1.0
#  @internal
def _scan_for_comment_start(
    lines: list[str],
    end_line: int,
    start_re: re.Pattern,
) -> DoxygenBlock | None:
    scan = end_line
    while scan >= 0:
        if start_re.search(lines[scan]):
            block_text = "\n".join(lines[scan : end_line + 1])
            tags = parse_doxygen_tags(block_text)
            return DoxygenBlock(
                start_line=scan,
                end_line=end_line,
                tags=tags,
                raw=block_text,
            )
        if "}" in lines[scan].strip():
            return None
        scan -= 1
    return None


## @brief Find the doxygen comment block immediately before a function definition.
#  @version 1.1
#  @req REQ-PARSE-002
def find_doxygen_block_before(
    lines: list[str],
    func_line: int,
    comment_start: str,
    comment_end: str,
) -> DoxygenBlock | None:
    try:
        start_re = re.compile(comment_start)
        end_re = re.compile(comment_end)
    except re.error as e:
        logger.error("Invalid comment style regex: %s", e)
        return None

    scan_line = _skip_blanks_and_attrs(lines, func_line)
    if scan_line < 0 or not end_re.search(lines[scan_line]):
        return None

    return _scan_for_comment_start(lines, scan_line, start_re)


## @brief Locate the end of a function body by matching braces.
#  @version 1.0
#  @req REQ-PARSE-001
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

    return len(lines) - 1


## @brief Locate the last line of a Python function body by tracking indentation.
#  @version 1.0
#  @req REQ-PARSE-003
def find_body_end_indent(lines: list[str], start_line: int) -> int:
    def_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    body_indent = None
    last_body_line = start_line

    for i in range(start_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue

        current_indent = len(lines[i]) - len(lines[i].lstrip())

        if body_indent is None:
            if current_indent > def_indent:
                body_indent = current_indent
                last_body_line = i
            else:
                return start_line
        elif current_indent >= body_indent:
            last_body_line = i
        else:
            break

    return last_body_line


## @brief Detect forward declarations to skip them during validation.
#  @version 1.0
#  @req REQ-PARSE-001
def is_forward_declaration(lines: list[str], func_line: int) -> bool:
    for i in range(func_line, min(func_line + 3, len(lines))):
        stripped = lines[i].rstrip()
        if "{" in stripped:
            return False
        if stripped.endswith(";"):
            return True
    return False


## @brief Parse source code to find functions and their associated doxygen comments.
#  @version 1.2
#  @req REQ-PARSE-001
def parse_functions(
    content: str,
    function_pattern: str,
    exclude_names: list[str],
    settings: ParseSettings | None = None,
    skip_forward_declarations: bool = True,
) -> list[Function]:
    s = settings or ParseSettings()
    lines = content.splitlines()
    try:
        func_re = re.compile(function_pattern, re.MULTILINE)
    except re.error as e:
        logger.error("Invalid function pattern regex: %s", e)
        return []

    body_end_fn = find_body_end_indent if s.body_style == "indent" else find_body_end
    functions: list[Function] = []

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

        functions.append(
            Function(
                name=func_name,
                def_line=i,
                body_end=body_end_fn(lines, i),
                doxygen=find_doxygen_block_before(lines, i, s.comment_start, s.comment_end),
            )
        )
        logger.debug("Found function: %s at line %d", func_name, i + 1)

    return functions
