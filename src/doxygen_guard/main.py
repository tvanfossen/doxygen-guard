"""CLI entry point for doxygen-guard.

@brief Argument parsing, file iteration, and subcommand dispatch.
@version 1.0
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

from doxygen_guard.checks import Violation, check_presence, check_tags, check_version_staleness
from doxygen_guard.config import get_language_config, load_config
from doxygen_guard.git import get_changed_lines_for_file
from doxygen_guard.impact import run_impact
from doxygen_guard.parser import parse_functions
from doxygen_guard.tracer import run_trace

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    @brief Create argparse parser with validate/trace/impact subcommands.
    @version 1.0
    """
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command")

    # validate subcommand (default when called as pre-commit hook)
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate doxygen comments (pre-commit gate)",
    )
    validate_parser.add_argument(
        "files",
        nargs="*",
        help="Files to validate (passed by pre-commit)",
    )
    validate_parser.add_argument(
        "--no-git",
        action="store_true",
        help="Skip git-based staleness checks",
    )

    # trace subcommand (placeholder — implemented in phase 9)
    trace_parser = subparsers.add_parser(
        "trace",
        help="Generate sequence diagrams from doxygen tags",
    )
    trace_parser.add_argument(
        "--req",
        help="Requirement ID to trace (e.g., REQ-0252)",
    )
    trace_parser.add_argument(
        "--all",
        action="store_true",
        dest="trace_all",
        help="Trace all requirements",
    )
    trace_parser.add_argument(
        "source_dirs",
        nargs="*",
        default=["."],
        help="Source directories to scan (default: current directory)",
    )

    # impact subcommand (placeholder — implemented in phase 10)
    impact_parser = subparsers.add_parser(
        "impact",
        help="Change-impact analysis from git diff",
    )
    impact_group = impact_parser.add_mutually_exclusive_group()
    impact_group.add_argument(
        "--staged",
        action="store_true",
        help="Analyze staged changes",
    )
    impact_group.add_argument(
        "--range",
        dest="diff_range",
        help="Git revision range (e.g., HEAD~3..HEAD)",
    )
    impact_parser.add_argument(
        "files",
        nargs="*",
        help="Files to analyze (if empty, uses git to find changed files)",
    )

    return parser


def validate_file(
    file_path: str,
    config: dict[str, Any],
    no_git: bool = False,
) -> list[Violation]:
    """Run all validation checks on a single file.

    @brief Orchestrate presence, staleness, and tag checks for one file.
    @version 1.0
    """
    lang_config = get_language_config(config, file_path)
    if lang_config is None:
        logger.debug("Skipping %s — no matching language config", file_path)
        return []

    # Check exclude patterns
    validate = config.get("validate", {})
    for pattern in validate.get("exclude", []):
        if re.search(pattern, file_path):
            logger.debug("Skipping %s — matches exclude pattern '%s'", file_path, pattern)
            return []

    content = Path(file_path).read_text()
    comment_style = validate.get("comment_style", {})
    comment_start = comment_style.get("start", r"/\*\*(?!\*)")
    comment_end = comment_style.get("end", r"\*/")
    skip_fwd = validate.get("presence", {}).get("skip_forward_declarations", True)

    functions = parse_functions(
        content=content,
        function_pattern=lang_config["function_pattern"],
        exclude_names=lang_config.get("exclude_names", []),
        comment_start=comment_start,
        comment_end=comment_end,
        skip_forward_declarations=skip_fwd,
    )

    violations: list[Violation] = []

    violations.extend(check_presence(functions, file_path, config))
    violations.extend(check_tags(functions, file_path, config))

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


def run_validate(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Execute the validate subcommand.

    @brief Run validation checks on all specified files and report violations.
    @version 1.0
    """
    files = args.files or []
    if not files:
        logger.warning("No files specified for validation")
        return 0

    all_violations: list[Violation] = []

    for file_path in files:
        if not Path(file_path).exists():
            logger.warning("File not found: %s", file_path)
            continue
        violations = validate_file(file_path, config, no_git=args.no_git)
        all_violations.extend(violations)

    for v in all_violations:
        print(v, file=sys.stderr)

    if all_violations:
        print(
            f"\ndoxygen-guard: {len(all_violations)} violation(s) found",
            file=sys.stderr,
        )
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    @brief Parse arguments and dispatch to the appropriate subcommand.
    @version 1.0
    """
    raw_argv = list(argv if argv is not None else sys.argv[1:])

    # Pre-commit passes filenames without a subcommand. Detect this and inject "validate".
    # Find the first arg that looks like a subcommand or a file path.
    subcommands = {"validate", "trace", "impact"}
    needs_inject = True
    # Flags that consume the next argument
    flags_with_value = {"--config"}
    skip_next = False
    for arg in raw_argv:
        if skip_next:
            skip_next = False
            continue
        if arg in flags_with_value:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        # First positional arg found
        if arg in subcommands:
            needs_inject = False
        break

    if needs_inject:
        # Find insertion point: after global flags, before first file arg
        insert_idx = 0
        i = 0
        while i < len(raw_argv):
            if raw_argv[i] in flags_with_value and i + 1 < len(raw_argv):
                i += 2
                insert_idx = i
            elif raw_argv[i].startswith("-"):
                i += 1
                insert_idx = i
            else:
                break
        raw_argv.insert(insert_idx, "validate")

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    config = load_config(args.config)

    if args.command == "validate":
        return run_validate(args, config)

    if args.command == "trace":
        written = run_trace(
            source_dirs=args.source_dirs,
            config=config,
            req_id=args.req,
            trace_all=args.trace_all,
        )
        if written:
            for p in written:
                print(f"Wrote: {p}")
            return 0
        print("No diagrams generated", file=sys.stderr)
        return 1

    if args.command == "impact":
        file_paths = args.files or []
        report = run_impact(
            file_paths=file_paths,
            config=config,
            staged=args.staged,
            diff_range=args.diff_range,
        )
        output_file = config.get("impact", {}).get("output", {}).get("file")
        if output_file:
            Path(output_file).write_text(report)
            print(f"Wrote report: {output_file}")
        else:
            print(report)
        return 0

    parser.print_help()
    return 1


def cli_main() -> None:
    """Entry point wrapper that calls sys.exit.

    @brief Wrapper for setuptools console_scripts entry point.
    @version 1.0
    """
    sys.exit(main())


if __name__ == "__main__":
    cli_main()
