"""Tests for Python ## doxygen comment support."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from doxygen_guard.config import CONFIG_DEFAULTS, VALIDATE_DEFAULTS
from doxygen_guard.main import validate_file
from doxygen_guard.parser import ParseSettings, find_body_end_indent, parse_functions

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PY_CONFIG = VALIDATE_DEFAULTS["languages"]["python"]
PY_PATTERN = PY_CONFIG["function_pattern"]
PY_EXCLUDES = PY_CONFIG["exclude_names"]
PY_START = PY_CONFIG["comment_style"]["start"]
PY_END = PY_CONFIG["comment_style"]["end"]
PY_SETTINGS = ParseSettings(comment_start=PY_START, comment_end=PY_END, body_style="indent")


class TestPythonFunctionDetection:
    """Verify Python def/async def detection."""

    def test_finds_all_functions(self):
        content = (FIXTURES_DIR / "python_simple.py").read_text()
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        names = [f.name for f in functions]
        assert "init_system" in names
        assert "process_data" in names
        assert "undocumented_function" in names
        assert "handle_event" in names
        assert "__init__" in names
        assert "get_value" in names
        assert "undocumented_method" in names

    def test_async_def_detected(self):
        content = dedent("""\
            ## @brief Async handler.
            #  @version 1.0
            async def handler(event):
                await process(event)
        """)
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].name == "handler"
        assert functions[0].doxygen is not None


class TestPythonDoxygenBlocks:
    """Verify ## doxygen block detection for Python."""

    def test_doxygen_associated_correctly(self):
        content = (FIXTURES_DIR / "python_simple.py").read_text()
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        by_name = {f.name: f for f in functions}

        assert by_name["init_system"].doxygen is not None
        assert "brief" in by_name["init_system"].doxygen.tags
        assert "version" in by_name["init_system"].doxygen.tags

        assert by_name["process_data"].doxygen is not None
        assert by_name["handle_event"].doxygen is not None
        assert "req" in by_name["handle_event"].doxygen.tags

    def test_undocumented_detected(self):
        content = (FIXTURES_DIR / "python_simple.py").read_text()
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        by_name = {f.name: f for f in functions}
        assert by_name["undocumented_function"].doxygen is None
        assert by_name["undocumented_method"].doxygen is None

    def test_regular_comment_not_doxygen(self):
        content = dedent("""\
            # just a regular comment
            def func():
                pass
        """)
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].doxygen is None

    def test_triple_hash_not_doxygen(self):
        content = dedent("""\
            ### Section header ###
            def func():
                pass
        """)
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].doxygen is None

    def test_multiline_tags(self):
        content = dedent("""\
            ## @brief Process data from the sensor.
            #  @version 2.0
            #  @param data Raw sensor reading.
            #  @return Calibrated value.
            def process_sensor(data):
                return calibrate(data)
        """)
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        assert len(functions) == 1
        tags = functions[0].doxygen.tags
        assert tags["brief"] == ["Process data from the sensor."]
        assert tags["version"] == ["2.0"]
        assert "param" in tags
        assert "return" in tags

    def test_single_line_doxygen(self):
        content = dedent("""\
            ## @brief Quick helper. @version 1.0
            def helper():
                pass
        """)
        functions = parse_functions(
            content,
            PY_PATTERN,
            PY_EXCLUDES,
            PY_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].doxygen is not None
        assert "brief" in functions[0].doxygen.tags


class TestPythonBodyEnd:
    """Verify indentation-based body end detection."""

    def test_simple_function(self):
        lines = [
            "def func():",
            "    x = 1",
            "    return x",
            "",
            "def other():",
        ]
        assert find_body_end_indent(lines, 0) == 2

    def test_nested_blocks(self):
        lines = [
            "def func():",
            "    if True:",
            "        x = 1",
            "    else:",
            "        x = 2",
            "    return x",
            "",
            "def other():",
        ]
        assert find_body_end_indent(lines, 0) == 5

    def test_blank_lines_within_body(self):
        lines = [
            "def func():",
            "    x = 1",
            "",
            "    y = 2",
            "    return x + y",
            "",
        ]
        assert find_body_end_indent(lines, 0) == 4

    def test_class_method(self):
        lines = [
            "class Foo:",
            "    def method(self):",
            "        return self.x",
            "",
            "    def other(self):",
            "        pass",
        ]
        # method body ends at line 2
        assert find_body_end_indent(lines, 1) == 2

    def test_last_function_in_file(self):
        lines = [
            "def func():",
            "    return 42",
        ]
        assert find_body_end_indent(lines, 0) == 1


class TestPythonPresenceCheck:
    """Verify presence checks work end-to-end with Python files."""

    def test_validate_file_python(self):
        violations = validate_file(
            str(FIXTURES_DIR / "python_simple.py"),
            CONFIG_DEFAULTS,
            no_git=True,
        )
        undoc = [v for v in violations if "no doxygen comment" in v.message]
        names_flagged = [v.message for v in undoc]
        assert any("undocumented_function" in m for m in names_flagged)
        assert any("undocumented_method" in m for m in names_flagged)

    def test_fully_documented_python(self, tmp_path):
        py_file = tmp_path / "clean.py"
        py_file.write_text(
            dedent("""\
                ## @brief Do work.
                #  @version 1.0
                def do_work():
                    pass
            """)
        )
        violations = validate_file(str(py_file), CONFIG_DEFAULTS, no_git=True)
        assert violations == []
