"""Change-impact analysis from git diff.

@brief Collect @req tags from changed functions, generate impact reports.
@version 1.1
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from doxygen_guard.config import parse_source_file
from doxygen_guard.git import RunCommand, get_diff, get_staged_diff, parse_changed_lines

logger = logging.getLogger(__name__)


## @brief Tracks a changed function with its requirement tags and version info.
#  @version 1.0
#  @internal
@dataclass
class ChangedFunction:
    name: str
    file_path: str
    reqs: list[str] = field(default_factory=list)
    old_version: str | None = None
    new_version: str | None = None


## @brief Groups changed functions by requirement for the impact report.
#  @version 1.1
#  @internal
@dataclass
class ImpactEntry:
    req_id: str
    req_name: str | None = None
    functions: list[ChangedFunction] = field(default_factory=list)


## @brief Get diff output for a single file, handling staged vs range modes.
#  @version 1.1
#  @internal
def _get_file_diff(
    file_path: str,
    staged: bool,
    diff_range: str | None,
    run_command: RunCommand | None,
) -> str | None:
    try:
        if staged:
            return get_staged_diff(file_path, run_command)
        if diff_range:
            return get_diff(file_path, diff_range, run_command)
    except Exception:
        logger.warning("Could not get diff for %s", file_path)
    return None


## @brief Find changed functions in a single file given changed line numbers.
#  @version 1.1
#  @req REQ-IMPACT-001
def _extract_changed_functions(
    file_path: str,
    config: dict[str, Any],
    changed_lines: set[int],
) -> list[ChangedFunction]:
    functions = parse_source_file(file_path, config)
    if functions is None:
        return []

    result: list[ChangedFunction] = []
    for func in functions:
        body_lines = set(range(func.def_line, func.body_end + 1))
        if not body_lines & changed_lines:
            continue

        reqs = func.doxygen.tags.get("req", []) if func.doxygen else []
        version = None
        if func.doxygen and "version" in func.doxygen.tags:
            version = func.doxygen.tags["version"][0]

        result.append(
            ChangedFunction(
                name=func.name,
                file_path=file_path,
                reqs=reqs,
                new_version=version,
            )
        )
    return result


## @brief Parse source files and cross-reference with git diff to find changed functions.
#  @version 1.1
#  @req REQ-IMPACT-001
def collect_changed_functions(
    file_paths: list[str],
    config: dict[str, Any],
    diff_range: str | None = None,
    staged: bool = False,
    run_command: RunCommand | None = None,
) -> list[ChangedFunction]:
    changed_funcs: list[ChangedFunction] = []

    for file_path in file_paths:
        if not Path(file_path).exists():
            logger.warning("File not found: %s", file_path)
            continue

        diff_output = _get_file_diff(file_path, staged, diff_range, run_command)
        if diff_output is None:
            continue

        changed_lines = parse_changed_lines(diff_output)
        if changed_lines:
            changed_funcs.extend(_extract_changed_functions(file_path, config, changed_lines))

    return changed_funcs


## @brief Extract requirements config or return None if not configured.
#  @version 1.1
#  @internal
def _get_requirements_config(
    config: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    impact_config = config.get("impact", {})
    req_config = impact_config.get("requirements")
    if not req_config:
        return None

    req_file = req_config.get("file")
    if not req_file or not Path(req_file).exists():
        logger.warning("Requirements file not found: %s", req_file)
        return None

    return (
        req_file,
        req_config.get("format", "csv"),
        req_config.get("id_column", "Req ID"),
        req_config.get("name_column", "Requirement Name"),
    )


## @brief Load full requirement rows from CSV/JSON/YAML.
#  @version 1.2
#  @req REQ-IMPACT-002
def load_requirements_full(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    req_info = _get_requirements_config(config)
    if req_info is None:
        return {}

    path, fmt, id_col, _name_col = req_info
    loaders = {
        "csv": _load_csv_full,
        "json": _load_json_full,
        "yaml": _load_yaml_full,
    }
    loader = loaders.get(fmt)
    if not loader:
        logger.warning("Unknown requirements format: %s", fmt)
        return {}
    return loader(path, id_col)


## @brief Load requirement id -> name mapping (convenience wrapper).
#  @version 1.2
#  @req REQ-IMPACT-002
def load_requirements(config: dict[str, Any]) -> dict[str, str]:
    req_info = _get_requirements_config(config)
    if req_info is None:
        return {}
    name_col = req_info[3]
    full = load_requirements_full(config)
    return {rid: row.get(name_col, "") for rid, row in full.items()}


## @brief Filter requirements to those active at the configured current version.
#  @version 1.0
#  @req REQ-IMPACT-002
def filter_requirements_by_version(
    reqs: dict[str, dict[str, str]],
    config: dict[str, Any],
) -> dict[str, dict[str, str]]:
    from doxygen_guard.config import compare_versions, parse_version

    version_gate = config.get("validate", {}).get("version_gate", {})
    current_str = version_gate.get("_resolved") or version_gate.get("current_version")
    version_field = version_gate.get("version_field")

    if not current_str or not version_field:
        return reqs

    current = parse_version(current_str)
    return {
        rid: row
        for rid, row in reqs.items()
        if compare_versions(parse_version(row.get(version_field, "v0.0.0")), current) <= 0
    }


## @brief Parse CSV file into req_id -> full row mapping.
#  @version 1.1
#  @internal
def _load_csv_full(path: str, id_col: str) -> dict[str, dict[str, str]]:
    reqs: dict[str, dict[str, str]] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            req_id = row.get(id_col, "").strip()
            if req_id:
                reqs[req_id] = {k: v.strip() for k, v in row.items()}
    return reqs


## @brief Parse JSON array into req_id -> full row mapping.
#  @version 1.1
#  @internal
def _load_json_full(path: str, id_col: str) -> dict[str, dict[str, str]]:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        return {}
    return {row[id_col]: row for row in data if row.get(id_col)}


## @brief Parse YAML list into req_id -> full row mapping.
#  @version 1.1
#  @internal
def _load_yaml_full(path: str, id_col: str) -> dict[str, dict[str, str]]:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        return {}
    return {row[id_col]: row for row in data if row.get(id_col)}


## @brief Group changed functions by requirement for the impact report.
#  @version 1.1
#  @req REQ-IMPACT-003
def build_impact_report(
    changed_functions: list[ChangedFunction],
    config: dict[str, Any],
) -> list[ImpactEntry]:
    req_names = load_requirements(config)

    req_funcs: dict[str, list[ChangedFunction]] = {}
    for cf in changed_functions:
        for req in cf.reqs:
            req_funcs.setdefault(req, []).append(cf)

    return [
        ImpactEntry(req_id=req_id, req_name=req_names.get(req_id), functions=funcs)
        for req_id, funcs in sorted(req_funcs.items())
    ]


## @brief Render impact entries as a markdown table with summary.
#  @version 1.1
#  @req REQ-IMPACT-003
def format_markdown(entries: list[ImpactEntry]) -> str:
    if not entries:
        return "No requirements affected.\n"

    lines = ["## Change Impact Report", ""]
    lines.append("| REQ | Name | Functions Changed |")
    lines.append("|-----|------|-------------------|")

    total_funcs = 0
    for entry in entries:
        name = entry.req_name or "\u2014"
        func_names = ", ".join(f.name for f in entry.functions)
        total_funcs += len(entry.functions)
        lines.append(f"| {entry.req_id} | {name} | {func_names} |")

    lines.append("")
    lines.append(
        f"**Total: {len(entries)} requirement(s) affected, {total_funcs} function(s) changed**"
    )
    return "\n".join(lines) + "\n"


## @brief Render impact entries as a JSON array.
#  @version 1.1
#  @req REQ-IMPACT-003
def format_json(entries: list[ImpactEntry]) -> str:
    data = [
        {
            "req_id": entry.req_id,
            "req_name": entry.req_name,
            "functions": [
                {"name": f.name, "file": f.file_path, "version": f.new_version}
                for f in entry.functions
            ],
        }
        for entry in entries
    ]
    return json.dumps(data, indent=2) + "\n"


## @brief Render impact entries as human-readable text.
#  @version 1.1
#  @req REQ-IMPACT-003
def format_text(entries: list[ImpactEntry]) -> str:
    if not entries:
        return "No requirements affected.\n"
    req_ids = [e.req_id for e in entries]
    return f"REQs affected: {', '.join(req_ids)}\n"


## @brief Dispatch to the appropriate formatter based on config.
#  @version 1.1
#  @internal
def format_report(entries: list[ImpactEntry], config: dict[str, Any]) -> str:
    fmt = config.get("impact", {}).get("output", {}).get("format", "markdown")
    formatters = {"json": format_json, "text": format_text}
    return formatters.get(fmt, format_markdown)(entries)


## @brief Orchestrate diff analysis, requirement mapping, and report generation.
#  @version 1.1
#  @req REQ-IMPACT-003
def run_impact(
    file_paths: list[str],
    config: dict[str, Any],
    staged: bool = False,
    diff_range: str | None = None,
    run_command: RunCommand | None = None,
) -> str:
    changed = collect_changed_functions(
        file_paths,
        config,
        diff_range=diff_range,
        staged=staged,
        run_command=run_command,
    )
    entries = build_impact_report(changed, config)
    return format_report(entries, config)
