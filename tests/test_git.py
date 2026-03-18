"""Tests for doxygen_guard.git module."""

from __future__ import annotations

from doxygen_guard.git import (
    get_changed_lines_for_file,
    get_diff,
    get_staged_diff,
    parse_changed_lines,
)


class TestParseChangedLines:
    """Tests for parse_changed_lines."""

    def test_single_line_addition(self):
        diff = """\
@@ -0,0 +1 @@
+new line
"""
        result = parse_changed_lines(diff)
        assert result == {0}  # line 1 → 0-indexed

    def test_multi_line_addition(self):
        diff = """\
@@ -0,0 +1,3 @@
+line one
+line two
+line three
"""
        result = parse_changed_lines(diff)
        assert result == {0, 1, 2}

    def test_modification_in_middle(self):
        diff = """\
@@ -10,2 +10,2 @@
-old line A
-old line B
+new line A
+new line B
"""
        result = parse_changed_lines(diff)
        assert result == {9, 10}  # lines 10-11 → 0-indexed

    def test_multiple_hunks(self):
        diff = """\
@@ -5,1 +5,1 @@
-old
+new
@@ -20,1 +20,1 @@
-old
+new
"""
        result = parse_changed_lines(diff)
        assert result == {4, 19}

    def test_pure_deletion(self):
        diff = """\
@@ -5,2 +5,0 @@
-deleted line 1
-deleted line 2
"""
        result = parse_changed_lines(diff)
        assert result == set()

    def test_empty_diff(self):
        result = parse_changed_lines("")
        assert result == set()

    def test_real_world_diff(self):
        diff = """\
diff --git a/src/main.c b/src/main.c
index abc123..def456 100644
--- a/src/main.c
+++ b/src/main.c
@@ -10,3 +10,5 @@ void Module_Init(void) {
-    old_setup();
+    new_setup();
+    configure();
+    validate();
@@ -25,1 +27,1 @@ int Module_Process(const char *data) {
-    return process(data);
+    return process_v2(data);
"""
        result = parse_changed_lines(diff)
        # First hunk: lines 10-14 (5 lines starting at 10)
        assert {9, 10, 11, 12, 13}.issubset(result)
        # Second hunk: line 27
        assert 26 in result


class TestGetStagedDiff:
    """Tests for get_staged_diff with injected command runner."""

    def test_calls_git_diff_cached(self):
        calls = []

        def mock_runner(cmd):
            calls.append(cmd)
            return "mock diff output"

        result = get_staged_diff("src/main.c", run_command=mock_runner)
        assert result == "mock diff output"
        assert calls == [["git", "diff", "--cached", "-U0", "--", "src/main.c"]]


class TestGetDiff:
    """Tests for get_diff with injected command runner."""

    def test_calls_git_diff_with_range(self):
        calls = []

        def mock_runner(cmd):
            calls.append(cmd)
            return "mock diff output"

        result = get_diff("src/main.c", "HEAD~3..HEAD", run_command=mock_runner)
        assert result == "mock diff output"
        assert calls == [["git", "diff", "-U0", "HEAD~3..HEAD", "--", "src/main.c"]]


class TestGetChangedLinesForFile:
    """Tests for get_changed_lines_for_file integration."""

    def test_combines_diff_and_parse(self):
        def mock_runner(cmd):
            return """\
@@ -5,1 +5,1 @@
-old
+new
"""

        result = get_changed_lines_for_file("test.c", run_command=mock_runner)
        assert result == {4}
