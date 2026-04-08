"""Tests for doxygen_guard.main module."""

from __future__ import annotations

from textwrap import dedent

from doxygen_guard.config import CONFIG_DEFAULTS, parse_source_file
from doxygen_guard.main import main, validate_file
from tests.conftest import FIXTURES_DIR

NO_REQ_CONFIG = str(FIXTURES_DIR / "no_requirements_config.yaml")


class TestValidateFile:
    """Tests for validate_file."""

    def test_documented_file_no_violations(self):
        # Create a minimal fully-documented C file
        config = CONFIG_DEFAULTS.copy()
        # Use simple.c but only the documented functions will pass
        violations = validate_file(
            str(FIXTURES_DIR / "simple.c"),
            config,
            no_git=True,
        )
        # simple.c has one undocumented function
        assert len(violations) == 1
        assert "Undocumented_Function" in violations[0].message

    def test_unknown_extension_skipped(self, tmp_path):
        py_file = tmp_path / "script.rs"
        py_file.write_text("fn foo() {}")
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
        result = main(["--config", NO_REQ_CONFIG, "validate", "--no-git", str(c_file)])
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
        result = main(["--config", NO_REQ_CONFIG, str(c_file)])
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
        result = main(["--config", NO_REQ_CONFIG, "-v", "validate", "--no-git", str(c_file)])
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

    def test_trace_nonexistent_req_returns_1(self):
        result = main(["trace", "--req", "REQ-NONEXISTENT-9999"])
        assert result == 1

    def test_impact_no_files_returns_0(self):
        result = main(["impact", "--staged"])
        assert result == 0


class TestPrecommitPipeline:
    """Integration tests for run_precommit with trace+impact."""

    def test_precommit_with_trace_and_impact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        c_file = src / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Process data.
                 * @version 1.0
                 * @req REQ-001
                 * @sends EVENT:DATA_READY
                 */
                void Process(void) {
                    event_post(EVENT_DATA_READY);
                }

                /**
                 * @brief Handle data event.
                 * @version 1.0
                 * @req REQ-001
                 * @receives EVENT:DATA_READY
                 */
                void OnDataReady(void) {
                    consume();
                }
            """)
        )
        req_file = tmp_path / "reqs.csv"
        req_file.write_text("Req ID,Name,Subsystem\nREQ-001,Data Processing,DataSvc\n")
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                output_dir: out/
                trace:
                  participant_field: "Subsystem"
                  options:
                    autonumber: true
                    min_edges: 0
                impact:
                  requirements:
                    file: reqs.csv
                    id_column: "Req ID"
                    name_column: "Name"
                    format: csv
            """)
        )
        result = main(["--config", str(config_file), str(c_file)])
        assert result == 0
        seq_dir = tmp_path / "out" / "sequences"
        assert seq_dir.exists()
        puml_files = list(seq_dir.glob("*.puml"))
        assert len(puml_files) >= 1
        content = puml_files[0].read_text()
        assert "Process()" in content or "OnDataReady()" in content


class TestParseSourceFile:
    """Tests for parse_source_file."""

    def test_unsupported_extension_returns_none(self, tmp_path):
        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("fn main() {}")
        result = parse_source_file(str(rs_file), CONFIG_DEFAULTS)
        assert result is None
