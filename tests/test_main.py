"""Tests for doxygen_guard.main module."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from doxygen_guard.main import main, validate_file
from doxygen_guard.config import CONFIG_DEFAULTS


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestValidateFile:
    """Tests for validate_file."""

    def test_documented_file_no_violations(self):
        # Create a minimal fully-documented C file
        config = CONFIG_DEFAULTS.copy()
        # Use simple.c but only the documented functions will pass
        violations = validate_file(
            str(FIXTURES_DIR / "simple.c"), config, no_git=True,
        )
        # simple.c has one undocumented function
        assert len(violations) == 1
        assert "Undocumented_Function" in violations[0].message

    def test_unknown_extension_skipped(self, tmp_path):
        py_file = tmp_path / "script.py"
        py_file.write_text("def foo(): pass")
        violations = validate_file(str(py_file), CONFIG_DEFAULTS, no_git=True)
        assert violations == []

    def test_excluded_file(self, tmp_path):
        c_file = tmp_path / "gen" / "auto.c"
        c_file.parent.mkdir()
        c_file.write_text("void Func(void) { }")
        config = {
            "validate": {
                **CONFIG_DEFAULTS["validate"],
                "exclude": ["gen/"],
            },
            "trace": CONFIG_DEFAULTS["trace"],
            "impact": CONFIG_DEFAULTS["impact"],
        }
        violations = validate_file(str(c_file), config, no_git=True)
        assert violations == []

    def test_all_checks_run(self, tmp_path):
        c_file = tmp_path / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Good function.
                 * @version 1.0
                 */
                void Good_Func(void) {
                    x();
                }

                void Bad_Func(void) {
                    y();
                }
            """)
        )
        violations = validate_file(str(c_file), CONFIG_DEFAULTS, no_git=True)
        assert len(violations) == 1
        assert "Bad_Func" in violations[0].message


class TestMain:
    """Tests for main CLI entry point."""

    def test_no_files_returns_zero(self):
        result = main(["validate"])
        assert result == 0

    def test_validate_clean_file(self, tmp_path):
        c_file = tmp_path / "clean.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Clean function.
                 * @version 1.0
                 */
                void Clean_Func(void) {
                    do_stuff();
                }
            """)
        )
        result = main(["validate", "--no-git", str(c_file)])
        assert result == 0

    def test_validate_dirty_file(self, tmp_path):
        c_file = tmp_path / "dirty.c"
        c_file.write_text(
            dedent("""\
                void Undoc_Func(void) {
                    do_stuff();
                }
            """)
        )
        result = main(["validate", "--no-git", str(c_file)])
        assert result == 1

    def test_validate_nonexistent_file(self):
        result = main(["validate", "--no-git", "/nonexistent/path.c"])
        assert result == 0  # Warning logged, no violations

    def test_default_subcommand_with_files(self, tmp_path):
        """When no subcommand is given, treat args as files (pre-commit mode)."""
        c_file = tmp_path / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Func.
                 * @version 1.0
                 */
                void Func(void) {
                    x();
                }
            """)
        )
        result = main([str(c_file)])
        # Should default to validate with no_git=False
        # Git will likely fail (not a repo), so staleness check is skipped
        assert result == 0

    def test_verbose_flag(self, tmp_path):
        c_file = tmp_path / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Func.
                 * @version 1.0
                 */
                void Func(void) {
                    x();
                }
            """)
        )
        result = main(["-v", "validate", "--no-git", str(c_file)])
        assert result == 0

    def test_custom_config(self, tmp_path):
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(
            dedent("""\
                validate:
                  presence:
                    require_doxygen: false
            """)
        )
        c_file = tmp_path / "test.c"
        c_file.write_text("void Undoc(void) { x(); }")

        result = main(["--config", str(config_file), "validate", "--no-git", str(c_file)])
        assert result == 0  # Presence check disabled

    def test_trace_no_sources_returns_1(self):
        result = main(["trace", "--req", "REQ-0001"])
        assert result == 1

    def test_impact_no_files_returns_0(self):
        result = main(["impact", "--staged"])
        assert result == 0
