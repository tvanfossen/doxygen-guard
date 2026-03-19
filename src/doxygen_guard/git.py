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
#  @version 1.0
#  @internal
def _default_run_command(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
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
#  @version 1.0
#  @req REQ-GIT-001
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


## @brief Stage a file or directory for the next git commit.
#  @version 1.1
#  @req REQ-GIT-001
def git_add(
    path: str,
    run_command: RunCommand | None = None,
) -> bool:
    try:
        runner = run_command or _default_run_command
        runner(["git", "add", path])
    except (subprocess.CalledProcessError, OSError) as e:
        logger.warning("git add %s failed: %s", path, e)
        return False
    return True


## @brief Convenience function combining staged diff retrieval and parsing.
#  @version 1.0
#  @req REQ-GIT-001
def get_changed_lines_for_file(
    file_path: str,
    run_command: RunCommand | None = None,
) -> set[int]:
    diff_output = get_staged_diff(file_path, run_command)
    return parse_changed_lines(diff_output)
