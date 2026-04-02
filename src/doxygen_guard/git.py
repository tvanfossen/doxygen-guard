"""Git diff parsing for change detection.

@brief Parse git diff output to determine which lines changed in staged files.
@version 1.0
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable

logger = logging.getLogger(__name__)

RunCommand = Callable[[list[str]], str]


## @brief Default command runner using subprocess.
#  @version 1.1
#  @internal
def _default_run_command(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout


## @brief Run git diff --cached for a single file.
#  @version 1.0
#  @req REQ-GIT-001
def get_staged_diff(
    file_path: str,
    run_command: RunCommand | None = None,
) -> str:
    runner = run_command or _default_run_command
    return runner(["git", "diff", "--cached", "-U0", "--", file_path])


## @brief Run git diff for a file over a given revision range.
#  @version 1.0
#  @req REQ-GIT-001
def get_diff(
    file_path: str,
    diff_range: str,
    run_command: RunCommand | None = None,
) -> str:
    runner = run_command or _default_run_command
    return runner(["git", "diff", "-U0", diff_range, "--", file_path])


## @brief Extract the set of modified line numbers from a unified diff.
#  @version 1.1
#  @req REQ-GIT-001
#  @return Set of 0-indexed line numbers that were added or modified
#
#  Parses @@ hunk headers to determine which lines in the new file were
#  added or modified. Returns 0-indexed line numbers to match parser conventions.
def parse_changed_lines(diff_output: str) -> set[int]:
    changed: set[int] = set()
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

    for match in hunk_re.finditer(diff_output):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1

        if count == 0:
            # Pure deletion — no new lines added
            continue

        # Convert to 0-indexed
        for line_num in range(start - 1, start - 1 + count):
            changed.add(line_num)

    return changed


## @brief Stage files for the next git commit.
#  @version 1.3
#  @utility
#  @supports REQ-GIT-001
#  @supports REQ-TRACE-001
#  @supports REQ-IMPACT-003
def git_add(
    paths: str | list[str],
    run_command: RunCommand | None = None,
) -> bool:
    try:
        runner = run_command or _default_run_command
        file_list = [paths] if isinstance(paths, str) else [str(p) for p in paths]
        runner(["git", "add", *file_list])
    except (subprocess.CalledProcessError, OSError) as e:
        logger.warning("git add failed: %s", e)
        return False
    return True


## @brief Detect the merge-base between current HEAD and a target branch.
#  @version 1.0
#  @req REQ-GIT-001
def get_merge_base(
    target_branch: str = "origin/main",
    run_command: RunCommand | None = None,
) -> str | None:
    runner = run_command or _default_run_command
    try:
        result = runner(["git", "merge-base", target_branch, "HEAD"])
        return result.strip()
    except (subprocess.CalledProcessError, OSError):
        logger.warning("Could not determine merge-base against %s", target_branch)
        return None


## @brief Build a diff range string from merge-base to HEAD.
#  @details Returns None when on the target branch itself (merge-base == HEAD),
#  signaling callers to fall back to staged diff.
#  @version 1.1
#  @req REQ-GIT-001
def get_branch_diff_range(
    target_branch: str = "origin/main",
    run_command: RunCommand | None = None,
) -> str | None:
    runner = run_command or _default_run_command
    base = get_merge_base(target_branch, run_command)
    if base is None:
        return None
    head = _rev_parse_head(runner)
    if head and base == head:
        return None
    return f"{base}...HEAD"


## @brief Get the current HEAD commit SHA.
#  @version 1.0
#  @internal
def _rev_parse_head(runner: RunCommand) -> str | None:
    try:
        return runner(["git", "rev-parse", "HEAD"]).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


## @brief Convenience function combining staged diff retrieval and parsing.
#  @version 1.0
#  @req REQ-GIT-001
def get_changed_lines_for_file(
    file_path: str,
    run_command: RunCommand | None = None,
) -> set[int]:
    diff_output = get_staged_diff(file_path, run_command)
    return parse_changed_lines(diff_output)
