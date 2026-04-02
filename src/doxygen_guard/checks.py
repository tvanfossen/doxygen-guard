"""Validation checks for doxygen comments.

@brief Presence, version staleness, and tag validation checks.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doxygen_guard.config import get_impact, get_validate

if TYPE_CHECKING:
    from doxygen_guard.parser import Function

logger = logging.getLogger(__name__)


## @brief Represents a check failure with location and description.
#  @version 1.0
#  @internal
@dataclass
class Violation:
    file: str
    line: int  # 1-indexed for display
    check: str  # "presence" | "version" | "tag"
    message: str

    ## @brief Human-readable violation string.
    #  @version 1.0
    #  @internal
    def __str__(self) -> str:
        return f"{self.file}:{self.line}: [{self.check}] {self.message}"


## @brief Verify every function has a doxygen comment with @brief and @version.
#  @version 1.2
#  @req REQ-VAL-001
def check_presence(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    validate = get_validate(config)
    presence_config = validate.get("presence", {})

    if not presence_config.get("require_doxygen", True):
        return []

    version_config = validate.get("version", {})
    require_version = version_config.get("require_present", True)

    violations: list[Violation] = []

    for func in functions:
        if func.doxygen is None:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.def_line + 1,
                    check="presence",
                    message=(
                        f"Function '{func.name}' has no doxygen comment"
                        " — add '/** @brief <description> @version 1.0 */' before function"
                    ),
                )
            )
            continue

        if "brief" not in func.doxygen.tags:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="presence",
                    message=(
                        f"Function '{func.name}' doxygen missing @brief tag"
                        " — add '@brief <description>' to the doxygen comment"
                    ),
                )
            )

        if require_version and "version" not in func.doxygen.tags:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="presence",
                    message=(
                        f"Function '{func.name}' doxygen missing @version tag"
                        " — add '@version 1.0' to the doxygen comment"
                    ),
                )
            )

    return violations


_VOID_PATTERNS = re.compile(r"\bvoid\b")
_PYTHON_NONE_RETURN = re.compile(r"->\s*None\b")


## @brief Verify non-void functions have @return or @returns tag.
#  @version 1.0
#  @req REQ-VAL-001
def check_return_presence(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
    content: str,
) -> list[Violation]:
    validate = get_validate(config)
    if not validate.get("presence", {}).get("require_return", True):
        return []

    lines = content.splitlines()
    violations: list[Violation] = []
    for func in functions:
        if func.doxygen is None:
            continue
        tags = func.doxygen.tags
        if EXEMPTION_TAGS & set(tags.keys()):
            continue
        if tags.get("return") or tags.get("returns"):
            continue
        if _is_void_function(func, lines):
            continue
        violations.append(
            Violation(
                file=file_path,
                line=func.doxygen.start_line + 1,
                check="presence",
                message=(
                    f"Function '{func.name}' doxygen missing @return tag"
                    " — add '@return <description>' to the doxygen comment"
                ),
            )
        )
    return violations


## @brief Check if a function returns void based on its definition lines.
#  @version 1.1
#  @internal
def _is_void_function(func: Function, lines: list[str]) -> bool:
    for offset in range(3):
        idx = func.def_line + offset
        if idx >= len(lines):
            break
        line = lines[idx]
        name_pos = line.find(func.name)
        if name_pos <= 0:
            continue
        prefix = line[:name_pos]
        is_c_void = bool(_VOID_PATTERNS.search(prefix))
        is_python_none = bool(_PYTHON_NONE_RETURN.search(line))
        is_python_untyped = line.strip().startswith("def ") and "->" not in line
        return is_c_void or is_python_none or is_python_untyped
    return False


EXEMPTION_TAGS = {"utility", "internal", "callback", "supports"}


## @brief Check if version gate is configured.
#  @version 1.2
#  @internal
def _has_version_gate(config: dict[str, Any]) -> bool:
    gate = get_validate(config).get("version_gate", {})
    return bool(gate.get("current_version") and gate.get("version_field"))


## @brief Check if any requirements pass the version gate filter.
#  @version 1.1
#  @internal
def _has_active_requirements(config: dict[str, Any]) -> bool:
    from doxygen_guard.impact import filter_requirements_by_version, load_requirements_full

    full = load_requirements_full(config)
    filtered = filter_requirements_by_version(full, config)
    return bool(filtered)


## @brief Verify functions have requirement or exemption tags when requirements are configured.
#  @version 1.3
#  @req REQ-VAL-004
def check_req_coverage(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    req_config = get_impact(config).get("requirements")
    if not req_config or not req_config.get("file"):
        return []

    # If version gate is configured, check if any active requirements exist
    if _has_version_gate(config) and not _has_active_requirements(config):
        return []

    req_file = req_config["file"]
    violations: list[Violation] = []
    for func in functions:
        if func.doxygen is None:
            continue

        tags = func.doxygen.tags
        has_req = bool(tags.get("req"))
        has_exemption = bool(EXEMPTION_TAGS & set(tags.keys()))

        if not has_req and not has_exemption:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.def_line + 1,
                    check="coverage",
                    message=(
                        f"Function '{func.name}' has no @req tag "
                        f"(see {req_file}) and no exemption "
                        f"(@utility, @internal, @callback, @supports)"
                    ),
                )
            )

    return violations


## @brief Detect stale @version tags when function bodies have been modified.
#  @version 1.1
#  @req REQ-VAL-002
def check_version_staleness(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
    changed_lines: set[int],
) -> list[Violation]:
    validate = get_validate(config)
    version_config = validate.get("version", {})

    if not version_config.get("require_increment_on_change", True):
        return []

    version_tag = version_config.get("tag", "@version")
    # Strip leading @ for tag dict lookup
    tag_key = version_tag.lstrip("@")

    violations: list[Violation] = []

    for func in functions:
        if func.doxygen is None:
            continue

        # Check if any line in the function body was changed
        body_lines = set(range(func.def_line, func.body_end + 1))
        if not body_lines & changed_lines:
            continue

        # Function body was changed — check if the specific @version line was changed.
        # We need the raw source lines to check content, but we only have changed
        # line numbers (0-indexed). Check that at least one changed line in the
        # doxygen range contains the version tag string.
        doxygen_lines_in_diff = (
            set(range(func.doxygen.start_line, func.doxygen.end_line + 1)) & changed_lines
        )
        raw_lines = func.doxygen.raw.splitlines()
        version_line_changed = any(
            version_tag in raw_lines[ln - func.doxygen.start_line]
            for ln in doxygen_lines_in_diff
            if 0 <= ln - func.doxygen.start_line < len(raw_lines)
        )

        if tag_key not in func.doxygen.tags:
            # No version tag at all — already caught by presence check
            continue

        if not version_line_changed:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.def_line + 1,
                    check="version",
                    message=(
                        f"Function '{func.name}' body changed but {version_tag} was not updated"
                    ),
                )
            )
            continue

        # Validate version marker if present (only [reviewed] is valid)
        version_value = func.doxygen.tags.get(tag_key, [""])[0]
        marker_match = re.search(r"\[(\w+)\]$", version_value.strip())
        if marker_match and marker_match.group(1) != "reviewed":
            violations.append(
                Violation(
                    file=file_path,
                    line=func.def_line + 1,
                    check="version",
                    message=(
                        f"Function '{func.name}' has unrecognized version marker "
                        f"'[{marker_match.group(1)}]' (use [reviewed])"
                    ),
                )
            )

    return violations


## @brief Check that doxygen tags match configured patterns, prefixes, and markers.
#  @version 1.1
#  @req REQ-VAL-003
def check_tags(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    validate = get_validate(config)
    tag_rules = validate.get("tags", {})

    if not tag_rules:
        return []

    violations: list[Violation] = []

    for func in functions:
        if func.doxygen is None:
            continue

        for tag_name, rules in tag_rules.items():
            tag_values = func.doxygen.tags.get(tag_name, [])

            for value in tag_values:
                violations.extend(_validate_tag_value(file_path, func, tag_name, value, rules))

    return violations


## @brief Check one tag value against pattern, prefix, and contains rules.
#  @version 1.1
#  @internal
def _validate_tag_value(
    file_path: str,
    func: Function,
    tag_name: str,
    value: str,
    rules: dict[str, Any],
) -> list[Violation]:
    violations: list[Violation] = []
    line = func.doxygen.start_line + 1 if func.doxygen else func.def_line + 1

    # Check pattern match
    pattern = rules.get("pattern")
    if pattern and not re.match(pattern, value):
        violations.append(
            Violation(
                file=file_path,
                line=line,
                check="tag",
                message=(
                    f"Function '{func.name}' @{tag_name} value '{value}' "
                    f"does not match pattern '{pattern}'"
                ),
            )
        )

    # Check required prefix
    require_prefix = rules.get("require_prefix")
    if require_prefix and not any(value.startswith(p) for p in require_prefix):
        violations.append(
            Violation(
                file=file_path,
                line=line,
                check="tag",
                message=(
                    f"Function '{func.name}' @{tag_name} value '{value}' "
                    f"does not start with any required prefix: {require_prefix}"
                ),
            )
        )

    # Check required contains
    require_contains = rules.get("require_contains")
    if require_contains and require_contains not in value:
        violations.append(
            Violation(
                file=file_path,
                line=line,
                check="tag",
                message=(
                    f"Function '{func.name}' @{tag_name} value '{value}' "
                    f"does not contain '{require_contains}'"
                ),
            )
        )

    return violations


_KNOWN_TAGS: frozenset[str] = frozenset(
    {
        "brief",
        "version",
        "req",
        "emits",
        "handles",
        "ext",
        "triggers",
        "supports",
        "assumes",
        "internal",
        "utility",
        "param",
        "return",
        "returns",
        "file",
        "note",
        "details",
        "see",
        "todo",
        "deprecated",
        "warning",
        "emit_source",
        "handle_source",
        "module",
    }
)


## @brief Check for unknown tags with Levenshtein-based suggestions.
#  @version 1.0
#  @req REQ-VAL-001
def check_unknown_tags(
    func: Function,
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    if func.doxygen is None:
        return []

    validate_config = get_validate(config)
    if not validate_config.get("known_tags_warn", True):
        return []

    extra = set(validate_config.get("extra_tags", []))
    known = _KNOWN_TAGS | extra | set(validate_config.get("tags", {}).keys())

    violations: list[Violation] = []
    for tag_name in func.doxygen.tags:
        if tag_name not in known:
            suggestion = _suggest_tag(tag_name, known)
            hint = f" — did you mean @{suggestion}?" if suggestion else ""
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="tag",
                    message=f"Unknown tag @{tag_name} in {func.name}(){hint}",
                )
            )
    return violations


## @brief Find the closest known tag name using edit distance.
#  @version 1.0
#  @internal
def _suggest_tag(unknown: str, known: set[str]) -> str | None:
    best_tag = None
    best_dist = 3
    for tag in known:
        dist = _edit_distance(unknown, tag)
        if dist < best_dist:
            best_dist = dist
            best_tag = tag
    return best_tag


## @brief Compute Levenshtein edit distance between two strings.
#  @version 1.0
#  @internal
def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return _edit_distance(b, a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1] + [0] * len(b)
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr[j + 1] = min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost)
        prev = curr
    return prev[len(b)]


## @brief Cross-validate requirement tag references against the requirements file.
#  @version 1.0
#  @req REQ-VAL-001
def check_req_exists(
    func: Function,
    file_path: str,
    config: dict[str, Any],
    req_ids: set[str] | None = None,
) -> list[Violation]:
    if func.doxygen is None or req_ids is None:
        return []

    validate_config = get_validate(config)
    tag_config = validate_config.get("tags", {}).get("req", {})
    if not tag_config.get("cross_reference", True):
        return []

    violations: list[Violation] = []
    for req_id in func.doxygen.tags.get("req", []):
        if req_id not in req_ids:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="tag",
                    message=(
                        f"@req {req_id} in {func.name}() not found in requirements file"
                        f" — verify the ID or add it to the requirements"
                    ),
                )
            )
    return violations


## @brief Check for file-level doxygen documentation block.
#  @version 1.1
#  @req REQ-VAL-001
def check_file_presence(
    file_path: str,
    content: str,
    config: dict[str, Any],
) -> list[Violation]:
    validate = get_validate(config)
    presence_config = validate.get("presence", {})
    if not presence_config.get("require_file_doxygen", False):
        return []

    lines = content.splitlines()
    block_line = _find_file_doxygen_start(lines)
    if block_line is None:
        first_code = _first_code_line(lines)
        return [
            Violation(
                file=file_path,
                line=first_code,
                check="presence",
                message=(
                    f"File '{file_path}' has no file-level doxygen block"
                    " — add /** @file @brief <desc> @version 1.0 */ before first code"
                ),
            )
        ]

    return _check_file_block_tags(file_path, lines, block_line)


## @brief Check required tags in a file-level doxygen block.
#  @version 1.1
#  @internal
#  @return List of violations for missing required tags
def _check_file_block_tags(file_path: str, lines: list[str], block_start: int) -> list[Violation]:
    block_text = _extract_file_block_text(lines, block_start)
    from doxygen_guard.parser import parse_doxygen_tags

    tags = parse_doxygen_tags(block_text)
    violations: list[Violation] = []
    required = {"file": "@file", "brief": "@brief", "version": "@version"}
    for tag_key, tag_name in required.items():
        if tag_key not in tags:
            violations.append(
                Violation(
                    file=file_path,
                    line=block_start + 1,
                    check="presence",
                    message=f"File-level doxygen missing {tag_name} tag in '{file_path}'",
                )
            )
    return violations


## @brief Extract file-level doxygen block text from lines.
#  @version 1.1
#  @internal
#  @return The raw text of the doxygen block
def _extract_file_block_text(lines: list[str], start: int) -> str:
    block_lines: list[str] = []
    for i in range(start, min(start + 30, len(lines))):
        block_lines.append(lines[i])
        if "*/" in lines[i] or (i > start and not lines[i].strip().startswith(("*", "#", "//"))):
            break
    return "\n".join(block_lines)


## @brief Find the line index of the file-level doxygen block start.
#  @version 1.1
#  @internal
#  @return 0-indexed line number, or None if no block found
def _find_file_doxygen_start(lines: list[str]) -> int | None:
    skip_prefixes = ("#include", "#pragma", "#ifndef", "#define")
    doxygen_starts = ("/**", "///", "## @", '"""')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or any(stripped.startswith(p) for p in skip_prefixes):
            continue
        if any(stripped.startswith(s) for s in doxygen_starts):
            return i
        return None
    return None


## @brief Find the first non-blank, non-preprocessor line number.
#  @version 1.1
#  @internal
#  @return 1-indexed line number for violation reporting
def _first_code_line(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.strip():
            return i + 1
    return 1
