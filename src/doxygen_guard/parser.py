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


# Known doxygen tags that signal the start of a new tag entry.
# Used to split single-line multi-tag blocks like "@brief X. @version 1.0"
_KNOWN_TAGS = {
    "brief",
    "version",
    "param",
    "return",
    "returns",
    "req",
    "emits",
    "handles",
    "ext",
    "triggers",
    "utility",
    "internal",
    "callback",
    "deprecated",
    "since",
    "see",
    "note",
    "warning",
    "pre",
    "post",
    "throws",
    "todo",
    "dispatches",
    "receives",
    "ack",
}


## @brief Store the current tag's value and reset state.
#  @version 1.1
#  @internal
def _finalize_tag(
    tags: dict[str, list[str]],
    tag: str | None,
    value: list[str],
) -> None:
    if tag is not None:
        tags.setdefault(tag, []).append(" ".join(value).strip())


_INLINE_TAG_RE = re.compile(r"\s@(" + "|".join(_KNOWN_TAGS) + r")(?:\s|$)")
_TAG_RE = re.compile(r"@(\w+)(?:\s+(.*))?$")


## @brief Handle a tag match, splitting inline multi-tag values if needed.
#  @version 1.1
#  @internal
def _handle_tag_match(
    tag_name: str,
    value_text: str,
    tags: dict[str, list[str]],
) -> tuple[str | None, list[str]]:
    inner = _INLINE_TAG_RE.search(value_text)
    if not inner:
        return tag_name, [value_text]
    _finalize_tag(tags, tag_name, [value_text[: inner.start()].strip()])
    remainder = value_text[inner.start() :].strip()
    m2 = _TAG_RE.match(remainder)
    return (m2.group(1), [m2.group(2) or ""]) if m2 else (None, [])


## @brief Parse all doxygen tag entries from comment text.
#  @version 1.6
#  @req REQ-PARSE-002
def parse_doxygen_tags(block_text: str) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    current_tag: str | None = None
    current_value: list[str] = []
    prefix_re = re.compile(r"^\s*[/*#]+\s?")
    suffix_re = re.compile(r"\s*\*/\s*$")

    for raw_line in block_text.splitlines():
        line = prefix_re.sub("", raw_line)
        line = suffix_re.sub("", line).strip()
        match = _TAG_RE.match(line)
        if match:
            _finalize_tag(tags, current_tag, current_value)
            current_tag, current_value = _handle_tag_match(
                match.group(1),
                match.group(2) or "",
                tags,
            )
        elif not line and current_tag is not None:
            _finalize_tag(tags, current_tag, current_value)
            current_tag = None
            current_value = []
        elif current_tag is not None and line:
            current_value.append(line)

    _finalize_tag(tags, current_tag, current_value)
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
