"""Requirement coverage analysis for doxygen-guard.

@brief Cross-reference @req tags against requirements file and report coverage gaps.
@version 1.0
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer import collect_all_tagged_functions

if TYPE_CHECKING:
    from doxygen_guard.tracer_models import TaggedFunction

logger = logging.getLogger(__name__)


## @brief Analyze requirement coverage across all tagged functions.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return Dict with covered, uncovered, supports_only, orphan_refs, unmapped_functions
def analyze_coverage(
    source_dirs: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    full_reqs = load_requirements_full(config)
    all_tagged, _participants, _cache = collect_all_tagged_functions(source_dirs, config, full_reqs)

    all_req_ids = set(full_reqs.keys())
    tagged_reqs = _collect_req_ids(all_tagged)
    supports_only = _collect_supports_only(all_tagged)
    unmapped = _collect_unmapped_functions(all_tagged)

    return {
        "total_requirements": len(all_req_ids),
        "covered": sorted(tagged_reqs & all_req_ids),
        "uncovered": sorted(all_req_ids - tagged_reqs - supports_only),
        "supports_only": sorted(supports_only & all_req_ids),
        "orphan_refs": sorted(tagged_reqs - all_req_ids),
        "unmapped_functions": sorted(unmapped),
    }


## @brief Collect all requirement IDs referenced across tagged functions.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return Set of all unique requirement IDs
def _collect_req_ids(all_tagged: list[TaggedFunction]) -> set[str]:
    result: set[str] = set()
    for tf in all_tagged:
        result.update(tf.reqs)
    return result


## @brief Find requirements referenced only via supports tags, not direct req tags.
#  @version 1.2
#  @req REQ-COVERAGE-001
#  @return Set of requirement IDs with supports-only coverage
def _collect_supports_only(all_tagged: list[TaggedFunction]) -> set[str]:
    req_refs: set[str] = set()
    supports_refs: set[str] = set()
    for tf in all_tagged:
        req_refs.update(tf.reqs)
        supports_refs.update(tf.supports)
    return supports_refs - req_refs


## @brief Find tagged functions with no requirement mapping.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return Set of function names with no requirement mapping
def _collect_unmapped_functions(all_tagged: list[TaggedFunction]) -> set[str]:
    return {tf.name for tf in all_tagged if not tf.reqs}


## @brief Format coverage report as text.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return Formatted text report string
def format_coverage_text(report: dict[str, Any]) -> str:
    lines = [f"Requirements coverage: {len(report['covered'])}/{report['total_requirements']}"]
    if report["uncovered"]:
        lines.append(f"\nUncovered ({len(report['uncovered'])}):")
        for r in report["uncovered"]:
            lines.append(f"  - {r}")
    if report["supports_only"]:
        lines.append(f"\nSupports-only ({len(report['supports_only'])}):")
        for r in report["supports_only"]:
            lines.append(f"  - {r}")
    if report["orphan_refs"]:
        lines.append(f"\nOrphan @req refs ({len(report['orphan_refs'])}):")
        for r in report["orphan_refs"]:
            lines.append(f"  - {r}")
    if report["unmapped_functions"]:
        lines.append(f"\nFunctions without @req ({len(report['unmapped_functions'])}):")
        for f in report["unmapped_functions"]:
            lines.append(f"  - {f}()")
    return "\n".join(lines)


## @brief Format coverage report as JSON.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return JSON string of the coverage report
def format_coverage_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2)


## @brief Format coverage report as markdown.
#  @version 1.1
#  @req REQ-COVERAGE-001
#  @return Markdown formatted coverage report string
def format_coverage_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Requirements Coverage: {len(report['covered'])}/{report['total_requirements']}"]
    if report["uncovered"]:
        lines.append(f"\n## Uncovered ({len(report['uncovered'])})")
        for r in report["uncovered"]:
            lines.append(f"- {r}")
    if report["supports_only"]:
        lines.append(f"\n## Supports-only ({len(report['supports_only'])})")
        for r in report["supports_only"]:
            lines.append(f"- {r}")
    if report["orphan_refs"]:
        lines.append(f"\n## Orphan Refs ({len(report['orphan_refs'])})")
        for r in report["orphan_refs"]:
            lines.append(f"- `{r}`")
    if report["unmapped_functions"]:
        lines.append(f"\n## Unmapped Functions ({len(report['unmapped_functions'])})")
        for f in report["unmapped_functions"]:
            lines.append(f"- `{f}()`")
    return "\n".join(lines)


## @brief Run coverage analysis and return exit code.
#  @version 1.3
#  @req REQ-COVERAGE-001
#  @handles CMD_COVERAGE
#  @return Exit code: 0 if no gaps, 1 if gaps exist
def run_coverage(
    source_dirs: list[str],
    config: dict[str, Any],
    output_format: str = "text",
) -> int:
    report = analyze_coverage(source_dirs, config)

    formatters = {
        "json": format_coverage_json,
        "markdown": format_coverage_markdown,
    }
    formatter = formatters.get(output_format, format_coverage_text)
    print(formatter(report))

    has_gaps = bool(report["uncovered"] or report["orphan_refs"])
    return 1 if has_gaps else 0
