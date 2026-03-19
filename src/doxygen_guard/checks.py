"""Validation checks for doxygen comments.

@brief Presence, version staleness, and tag validation checks.
@version 1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
#  @version 1.0
#  @req REQ-VAL-001
def check_presence(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    validate = config.get("validate", {})
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
                    message=f"Function '{func.name}' has no doxygen comment",
                )
            )
            continue

        if "brief" not in func.doxygen.tags:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="presence",
                    message=f"Function '{func.name}' doxygen missing @brief tag",
                )
            )

        if require_version and "version" not in func.doxygen.tags:
            violations.append(
                Violation(
                    file=file_path,
                    line=func.doxygen.start_line + 1,
                    check="presence",
                    message=f"Function '{func.name}' doxygen missing @version tag",
                )
            )

    return violations


EXEMPTION_TAGS = {"utility", "internal", "callback"}


## @brief Check if version gate is configured.
#  @version 1.1
#  @internal
def _has_version_gate(config: dict[str, Any]) -> bool:
    gate = config.get("validate", {}).get("version_gate", {})
    return bool(gate.get("current_version") and gate.get("version_field"))


## @brief Check if any requirements pass the version gate filter.
#  @version 1.1
#  @internal
def _has_active_requirements(config: dict[str, Any]) -> bool:
    from doxygen_guard.impact import filter_requirements_by_version, load_requirements_full

    full = load_requirements_full(config)
    filtered = filter_requirements_by_version(full, config)
    return bool(filtered)


## @brief Verify functions have @req or an exemption tag when requirements are configured.
#  @version 1.1
#  @req REQ-VAL-004
def check_req_coverage(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    req_config = config.get("impact", {}).get("requirements")
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
                        f"(@utility, @internal, @callback)"
                    ),
                )
            )

    return violations


## @brief Detect stale @version tags when function bodies have been modified.
#  @version 1.0
#  @req REQ-VAL-002
def check_version_staleness(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
    changed_lines: set[int],
) -> list[Violation]:
    validate = config.get("validate", {})
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
#  @version 1.0
#  @req REQ-VAL-003
def check_tags(
    functions: list[Function],
    file_path: str,
    config: dict[str, Any],
) -> list[Violation]:
    validate = config.get("validate", {})
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


## @brief Check one tag value against pattern, prefix, contains, and confidence rules.
#  @version 1.0
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
    if pattern:
        # Strip confidence markers before pattern check
        check_value = _strip_confidence_marker(value, rules)
        if not re.match(pattern, check_value):
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

    # Check confidence markers
    markers = rules.get("confidence_markers")
    if markers:
        violations.extend(_check_confidence_marker(file_path, func, tag_name, value, markers, line))

    return violations


## @brief Strip [marker] suffix from tag value.
#  @version 1.0
#  @internal
def _strip_confidence_marker(value: str, rules: dict[str, Any]) -> str:
    markers = rules.get("confidence_markers", [])
    if not markers:
        return value

    for marker in markers:
        suffix = f" [{marker}]"
        if value.endswith(suffix):
            return value[: -len(suffix)]

    return value


## @brief Verify confidence marker syntax [marker] is present and valid.
#  @version 1.0
#  @internal
def _check_confidence_marker(
    file_path: str,
    func: Function,
    tag_name: str,
    value: str,
    markers: list[str],
    line: int,
) -> list[Violation]:
    marker_pattern = re.compile(r"\[(\w+)\]$")
    match = marker_pattern.search(value)

    if not match:
        return [
            Violation(
                file=file_path,
                line=line,
                check="tag",
                message=(
                    f"Function '{func.name}' @{tag_name} value '{value}' "
                    f"missing confidence marker (expected one of: {markers})"
                ),
            )
        ]

    marker = match.group(1)
    if marker not in markers:
        return [
            Violation(
                file=file_path,
                line=line,
                check="tag",
                message=(
                    f"Function '{func.name}' @{tag_name} value '{value}' "
                    f"has invalid confidence marker '{marker}' "
                    f"(expected one of: {markers})"
                ),
            )
        ]

    return []
