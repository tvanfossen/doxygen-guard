"""CLI entry point for doxygen-guard.

@brief Argument parsing, file iteration, and subcommand dispatch.
@version 1.1
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from doxygen_guard.checks import (
    Violation,
    check_presence,
    check_req_coverage,
    check_tags,
    check_version_staleness,
)
from doxygen_guard.config import get_language_config, load_config, resolve_parse_settings
from doxygen_guard.git import get_changed_lines_for_file
from doxygen_guard.impact import run_impact
from doxygen_guard.parser import parse_functions
from doxygen_guard.tracer import run_trace

logger = logging.getLogger(__name__)

_SUBCOMMANDS = {"validate", "trace", "impact"}
_FLAGS_WITH_VALUE = {"--config"}


## @brief Create argparse parser with validate/trace/impact subcommands.
#  @version 1.1
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doxygen-guard",
        description="Validate doxygen comments for presence, version staleness, and custom tags",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to .doxygen-guard.yaml (default: .doxygen-guard.yaml in cwd)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")

    subparsers = parser.add_subparsers(dest="command")
    _add_validate_parser(subparsers)
    _add_trace_parser(subparsers)
    _add_impact_parser(subparsers)
    return parser


## @brief Add the validate subcommand to the parser.
#  @version 1.0
def _add_validate_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("validate", help="Validate doxygen comments (pre-commit gate)")
    p.add_argument("files", nargs="*", help="Files to validate (passed by pre-commit)")
    p.add_argument("--no-git", action="store_true", help="Skip git-based staleness checks")


## @brief Add the trace subcommand to the parser.
#  @version 1.0
def _add_trace_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("trace", help="Generate sequence diagrams from doxygen tags")
    p.add_argument("--req", help="Requirement ID to trace (e.g., REQ-0252)")
    p.add_argument("--all", action="store_true", dest="trace_all", help="Trace all requirements")
    p.add_argument("source_dirs", nargs="*", default=["."], help="Source directories to scan")


## @brief Add the impact subcommand to the parser.
#  @version 1.1
def _add_impact_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("impact", help="Change-impact analysis from git diff")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="Analyze staged changes")
    group.add_argument("--range", dest="diff_range", help="Git revision range (e.g., HEAD~3..HEAD)")
    p.add_argument("files", nargs="*", help="Files to analyze")


## @brief Orchestrate presence, staleness, and tag checks for one file.
#  @version 1.1
def validate_file(
    file_path: str,
    config: dict[str, Any],
    no_git: bool = False,
) -> list[Violation]:
    lang_config = get_language_config(config, file_path)
    if lang_config is None:
        logger.debug("Skipping %s — no matching language config", file_path)
        return []

    validate = config.get("validate", {})
    for pattern in validate.get("exclude", []):
        if re.search(pattern, file_path):
            logger.debug("Skipping %s — matches exclude pattern '%s'", file_path, pattern)
            return []

    content = Path(file_path).read_text()
    settings = resolve_parse_settings(config, lang_config)
    skip_fwd = validate.get("presence", {}).get("skip_forward_declarations", True)

    functions = parse_functions(
        content=content,
        function_pattern=lang_config["function_pattern"],
        exclude_names=lang_config.get("exclude_names", []),
        settings=settings,
        skip_forward_declarations=skip_fwd,
    )

    violations: list[Violation] = []
    violations.extend(check_presence(functions, file_path, config))
    violations.extend(check_tags(functions, file_path, config))
    violations.extend(check_req_coverage(functions, file_path, config))

    if not no_git:
        try:
            changed_lines = get_changed_lines_for_file(file_path)
            violations.extend(check_version_staleness(functions, file_path, config, changed_lines))
        except Exception:
            logger.warning(
                "Could not get git diff for %s — skipping staleness check",
                file_path,
                exc_info=True,
            )

    return violations


## @brief Validate a list of files and report violations.
#  @version 1.1
def _validate_files(
    file_paths: list[str],
    config: dict[str, Any],
    no_git: bool = False,
) -> list[Violation]:
    violations: list[Violation] = []
    for file_path in file_paths:
        if not Path(file_path).exists():
            logger.warning("File not found: %s", file_path)
            continue
        violations.extend(validate_file(file_path, config, no_git=no_git))
    return violations


## @brief Print violations to stderr and return exit code.
#  @version 1.0
def _report_violations(violations: list[Violation]) -> int:
    for v in violations:
        print(v, file=sys.stderr)
    if violations:
        print(f"\ndoxygen-guard: {len(violations)} violation(s) found", file=sys.stderr)
        return 1
    return 0


## @brief Run validation checks on all specified files and report violations.
#  @version 1.1
def run_validate(args: argparse.Namespace, config: dict[str, Any]) -> int:
    files = args.files or []
    if not files:
        logger.warning("No files specified for validation")
        return 0
    return _report_violations(_validate_files(files, config, no_git=args.no_git))


## @brief Derive unique source directories from a list of file paths.
#  @version 1.1
def _source_dirs_from_files(file_paths: list[str]) -> list[str]:
    return sorted({str(Path(f).parent) for f in file_paths})


## @brief Run all configured checks in pre-commit mode (no subcommand).
#  @version 1.3
def run_precommit(file_paths: list[str], config: dict[str, Any]) -> int:
    rc = _report_violations(_validate_files(file_paths, config))

    trace_config = config.get("trace", {})
    if trace_config.get("participants"):
        source_dirs = _source_dirs_from_files(file_paths) or ["."]
        written, trace_warnings = run_trace(
            source_dirs=source_dirs,
            config=config,
            trace_all=True,
        )
        for w in trace_warnings:
            print(f"doxygen-guard: [trace] {w}", file=sys.stderr)
        if written:
            output_dir = trace_config.get("output_dir", "docs/generated/sequences/")
            subprocess.run(["git", "add", output_dir], capture_output=True, check=False)
            print(
                f"doxygen-guard: {len(written)} diagram(s) written to {output_dir}",
                file=sys.stderr,
            )

    impact_config = config.get("impact", {})
    if impact_config.get("requirements"):
        report = run_impact(file_paths=file_paths, config=config, staged=True)
        if report.strip() and "No requirements affected" not in report:
            print(report, file=sys.stderr)

    return rc


## @brief Detect whether the first positional arg is a known subcommand.
#  @version 1.0
def _has_subcommand(raw_argv: list[str]) -> bool:
    skip_next = False
    for arg in raw_argv:
        if skip_next:
            skip_next = False
            continue
        if arg in _FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg in _SUBCOMMANDS
    return False


## @brief Parse pre-commit mode args (no subcommand) into components.
#  @version 1.0
def _parse_precommit_args(
    raw_argv: list[str],
) -> tuple[Path | None, list[str], bool]:
    config_path = None
    file_paths: list[str] = []
    verbose = False
    i = 0
    while i < len(raw_argv):
        if raw_argv[i] == "--config" and i + 1 < len(raw_argv):
            config_path = Path(raw_argv[i + 1])
            i += 2
        elif raw_argv[i] in ("-v", "--verbose"):
            verbose = True
            i += 1
        elif raw_argv[i].startswith("-"):
            i += 1
        else:
            file_paths.append(raw_argv[i])
            i += 1
    return config_path, file_paths, verbose


## @brief Execute the trace subcommand.
#  @version 1.1
def _run_trace_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    written, warnings = run_trace(
        source_dirs=args.source_dirs,
        config=config,
        req_id=args.req,
        trace_all=args.trace_all,
    )
    for w in warnings:
        print(f"[trace] {w}", file=sys.stderr)
    if not written:
        print("No diagrams generated", file=sys.stderr)
        return 1
    for p in written:
        print(f"Wrote: {p}")
    return 0


## @brief Execute the impact subcommand.
#  @version 1.0
def _run_impact_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    file_paths = args.files or []
    report = run_impact(
        file_paths=file_paths,
        config=config,
        staged=args.staged,
        diff_range=args.diff_range,
    )
    output_file = config.get("impact", {}).get("output", {}).get("file")
    if output_file:
        out_path = Path(output_file).resolve()
        if ".." in Path(output_file).parts:
            print(f"Error: output path '{output_file}' contains traversal", file=sys.stderr)
            return 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        print(f"Wrote report: {out_path}")
    else:
        print(report)
    return 0


## @brief Configure logging based on verbosity flag.
#  @version 1.0
def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


## @brief Dispatch an explicit subcommand to its handler.
#  @version 1.0
def _dispatch_subcommand(args: argparse.Namespace, config: dict[str, Any]) -> int:
    handlers = {
        "validate": lambda: run_validate(args, config),
        "trace": lambda: _run_trace_command(args, config),
        "impact": lambda: _run_impact_command(args, config),
    }
    handler = handlers.get(args.command)
    if handler:
        return handler()
    return 1


## @brief Parse arguments and dispatch to the appropriate subcommand.
#  @version 1.2
def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv if argv is not None else sys.argv[1:])

    if not _has_subcommand(raw_argv):
        config_path, file_paths, verbose = _parse_precommit_args(raw_argv)
        _setup_logging(verbose)
        return run_precommit(file_paths, load_config(config_path))

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    _setup_logging(args.verbose)
    return _dispatch_subcommand(args, load_config(args.config))


## @brief Wrapper for setuptools console_scripts entry point.
#  @version 1.0
def cli_main() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli_main()
