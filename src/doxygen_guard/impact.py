"""Change-impact analysis from git diff.

@brief Collect @req tags from changed functions, map to test suites, generate reports.
@version 1.0
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from doxygen_guard.config import get_language_config
from doxygen_guard.git import RunCommand, get_diff, get_staged_diff, parse_changed_lines
from doxygen_guard.parser import parse_functions

logger = logging.getLogger(__name__)


@dataclass
class ChangedFunction:
    """A function whose body was modified in a diff.

    @brief Tracks a changed function with its requirement tags and version info.
    @version 1.0
    """

    name: str
    file_path: str
    reqs: list[str] = field(default_factory=list)
    old_version: str | None = None
    new_version: str | None = None


@dataclass
class ImpactEntry:
    """A requirement affected by changes.

    @brief Groups changed functions by requirement for the impact report.
    @version 1.0
    """

    req_id: str
    req_name: str | None = None
    functions: list[ChangedFunction] = field(default_factory=list)
    test_suites: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)


def collect_changed_functions(
    file_paths: list[str],
    config: dict[str, Any],
    diff_range: str | None = None,
    staged: bool = False,
    run_command: RunCommand | None = None,
) -> list[ChangedFunction]:
    """Find functions whose bodies changed in the given diff scope.

    @brief Parse source files and cross-reference with git diff to find changed functions.
    @version 1.0
    """
    validate_config = config.get("validate", {})
    comment_style = validate_config.get("comment_style", {})
    comment_start = comment_style.get("start", r"/\*\*(?!\*)")
    comment_end = comment_style.get("end", r"\*/")

    changed_funcs: list[ChangedFunction] = []

    for file_path in file_paths:
        lang_config = get_language_config(config, file_path)
        if lang_config is None:
            continue

        if not Path(file_path).exists():
            logger.warning("File not found: %s", file_path)
            continue

        # Get changed lines for this file
        try:
            if staged:
                diff_output = get_staged_diff(file_path, run_command)
            elif diff_range:
                diff_output = get_diff(file_path, diff_range, run_command)
            else:
                continue
        except Exception:
            logger.warning("Could not get diff for %s", file_path)
            continue

        changed_lines = parse_changed_lines(diff_output)
        if not changed_lines:
            continue

        content = Path(file_path).read_text()
        functions = parse_functions(
            content=content,
            function_pattern=lang_config["function_pattern"],
            exclude_names=lang_config.get("exclude_names", []),
            comment_start=comment_start,
            comment_end=comment_end,
        )

        for func in functions:
            body_lines = set(range(func.def_line, func.body_end + 1))
            if not body_lines & changed_lines:
                continue

            reqs = func.doxygen.tags.get("req", []) if func.doxygen else []
            version = None
            if func.doxygen and "version" in func.doxygen.tags:
                version = func.doxygen.tags["version"][0]

            changed_funcs.append(
                ChangedFunction(
                    name=func.name,
                    file_path=file_path,
                    reqs=reqs,
                    new_version=version,
                )
            )

    return changed_funcs


def load_requirements(config: dict[str, Any]) -> dict[str, str]:
    """Load requirement ID → name mapping from configured file.

    @brief Read requirements from CSV/JSON/YAML as specified in impact config.
    @version 1.0
    """
    impact_config = config.get("impact", {})
    req_config = impact_config.get("requirements")
    if not req_config:
        return {}

    req_file = req_config.get("file")
    if not req_file or not Path(req_file).exists():
        logger.warning("Requirements file not found: %s", req_file)
        return {}

    fmt = req_config.get("format", "csv")
    id_col = req_config.get("id_column", "Req ID")
    name_col = req_config.get("name_column", "Requirement Name")

    if fmt == "csv":
        return _load_csv_requirements(req_file, id_col, name_col)
    if fmt == "json":
        return _load_json_requirements(req_file, id_col, name_col)
    if fmt == "yaml":
        return _load_yaml_requirements(req_file, id_col, name_col)

    logger.warning("Unknown requirements format: %s", fmt)
    return {}


def _load_csv_requirements(path: str, id_col: str, name_col: str) -> dict[str, str]:
    """Load requirements from CSV.

    @brief Parse CSV file into req_id → req_name mapping.
    @version 1.0
    """
    reqs: dict[str, str] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            req_id = row.get(id_col, "").strip()
            req_name = row.get(name_col, "").strip()
            if req_id:
                reqs[req_id] = req_name
    return reqs


def _load_json_requirements(path: str, id_col: str, name_col: str) -> dict[str, str]:
    """Load requirements from JSON array.

    @brief Parse JSON array of objects into req_id → req_name mapping.
    @version 1.0
    """
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        return {}
    return {row.get(id_col, ""): row.get(name_col, "") for row in data if row.get(id_col)}


def _load_yaml_requirements(path: str, id_col: str, name_col: str) -> dict[str, str]:
    """Load requirements from YAML list.

    @brief Parse YAML list of mappings into req_id → req_name mapping.
    @version 1.0
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        return {}
    return {row.get(id_col, ""): row.get(name_col, "") for row in data if row.get(id_col)}


def map_to_test_suites(
    req_ids: set[str],
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Map requirement IDs to test suites via configured regex patterns.

    @brief Match each REQ to test_mapping entries and return matched suites.
    @version 1.0
    """
    impact_config = config.get("impact", {})
    test_mapping = impact_config.get("test_mapping", [])
    if not test_mapping:
        return []

    matched: list[dict[str, str]] = []
    seen_suites: set[str] = set()

    for entry in test_mapping:
        pattern = entry.get("match", "")
        suite = entry.get("suite", "")
        command = entry.get("command", "")

        for req_id in req_ids:
            if re.match(pattern, req_id) and suite not in seen_suites:
                matched.append({"suite": suite, "command": command})
                seen_suites.add(suite)
                break

    return matched


def build_impact_report(
    changed_functions: list[ChangedFunction],
    config: dict[str, Any],
) -> list[ImpactEntry]:
    """Build structured impact report from changed functions.

    @brief Group changed functions by requirement and map to test suites.
    @version 1.0
    """
    req_names = load_requirements(config)

    # Group by requirement
    req_funcs: dict[str, list[ChangedFunction]] = {}
    for cf in changed_functions:
        for req in cf.reqs:
            req_funcs.setdefault(req, []).append(cf)

    entries: list[ImpactEntry] = []
    for req_id, funcs in sorted(req_funcs.items()):
        req_suites = map_to_test_suites({req_id}, config)
        entries.append(
            ImpactEntry(
                req_id=req_id,
                req_name=req_names.get(req_id),
                functions=funcs,
                test_suites=[s["suite"] for s in req_suites],
                test_commands=[s["command"] for s in req_suites],
            )
        )

    return entries


def format_markdown(entries: list[ImpactEntry]) -> str:
    """Format impact report as markdown.

    @brief Render impact entries as a markdown table with summary.
    @version 1.0
    """
    if not entries:
        return "No requirements affected.\n"

    lines = ["## Change Impact Report", ""]
    lines.append("| REQ | Name | Functions Changed | Smoke Test |")
    lines.append("|-----|------|-------------------|------------|")

    total_funcs = 0
    all_suites: set[str] = set()

    for entry in entries:
        name = entry.req_name or "—"
        func_names = ", ".join(f.name for f in entry.functions)
        total_funcs += len(entry.functions)
        suite_str = ", ".join(entry.test_suites) or "—"
        all_suites.update(entry.test_suites)
        cmd_str = " / ".join(f"`{c}`" for c in entry.test_commands) if entry.test_commands else ""
        lines.append(f"| {entry.req_id} | {name} | {func_names} | {cmd_str or suite_str} |")

    lines.append("")
    lines.append(
        f"**Total: {len(entries)} requirement(s) affected, {total_funcs} function(s) changed**"
    )
    if all_suites:
        lines.append(f"**Recommended smoke tests: {', '.join(sorted(all_suites))}**")

    return "\n".join(lines) + "\n"


def format_json(entries: list[ImpactEntry]) -> str:
    """Format impact report as JSON.

    @brief Render impact entries as a JSON array.
    @version 1.0
    """
    data = []
    for entry in entries:
        data.append(
            {
                "req_id": entry.req_id,
                "req_name": entry.req_name,
                "functions": [
                    {"name": f.name, "file": f.file_path, "version": f.new_version}
                    for f in entry.functions
                ],
                "test_suites": entry.test_suites,
                "test_commands": entry.test_commands,
            }
        )
    return json.dumps(data, indent=2) + "\n"


def format_text(entries: list[ImpactEntry]) -> str:
    """Format impact report as plain text.

    @brief Render impact entries as human-readable text.
    @version 1.0
    """
    if not entries:
        return "No requirements affected.\n"

    lines = []
    req_ids = [e.req_id for e in entries]
    lines.append(f"REQs affected: {', '.join(req_ids)}")

    all_suites: set[str] = set()
    for entry in entries:
        all_suites.update(entry.test_suites)

    if all_suites:
        lines.append(f"Smoke tests: {', '.join(sorted(all_suites))}")

    return "\n".join(lines) + "\n"


def format_report(entries: list[ImpactEntry], config: dict[str, Any]) -> str:
    """Format impact report in the configured output format.

    @brief Dispatch to the appropriate formatter based on config.
    @version 1.0
    """
    impact_config = config.get("impact", {})
    output_config = impact_config.get("output", {})
    fmt = output_config.get("format", "markdown")

    if fmt == "json":
        return format_json(entries)
    if fmt == "text":
        return format_text(entries)
    return format_markdown(entries)


def run_impact(
    file_paths: list[str],
    config: dict[str, Any],
    staged: bool = False,
    diff_range: str | None = None,
    run_command: RunCommand | None = None,
) -> str:
    """Execute the impact command.

    @brief Orchestrate diff analysis, requirement mapping, and report generation.
    @version 1.0
    """
    changed = collect_changed_functions(
        file_paths,
        config,
        diff_range=diff_range,
        staged=staged,
        run_command=run_command,
    )

    entries = build_impact_report(changed, config)
    return format_report(entries, config)
