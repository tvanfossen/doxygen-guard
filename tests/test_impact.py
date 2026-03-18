"""Tests for doxygen_guard.impact module."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from doxygen_guard.config import CONFIG_DEFAULTS, deep_merge
from doxygen_guard.impact import (
    ChangedFunction,
    ImpactEntry,
    build_impact_report,
    collect_changed_functions,
    format_json,
    format_markdown,
    format_text,
    load_requirements,
    map_to_test_suites,
    run_impact,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_impact_config(tmp_path=None, req_file=None):
    """Build a config with impact section for testing."""
    impact = {
        "test_mapping": [
            {"match": "REQ-025[0-9]", "suite": "Pairing", "command": "run_pairing"},
            {"match": "REQ-055[0-9]", "suite": "OTA", "command": "run_ota"},
            {"match": ".*", "suite": "Full Regression", "command": "run_all"},
        ],
        "output": {"format": "markdown", "file": None},
    }
    if req_file:
        impact["requirements"] = {
            "file": str(req_file),
            "id_column": "Req ID",
            "name_column": "Requirement Name",
            "format": "csv",
        }
    return deep_merge(CONFIG_DEFAULTS, {"impact": impact})


class TestCollectChangedFunctions:
    """Tests for collect_changed_functions."""

    def test_finds_changed_function(self, tmp_path):
        c_file = tmp_path / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Do stuff.
                 * @version 1.1
                 * @req REQ-0252
                 */
                void Do_Stuff(void) {
                    new_impl();
                }
            """)
        )

        def mock_runner(cmd):
            return "@@ -6,1 +6,1 @@\n-old\n+new\n"

        result = collect_changed_functions(
            [str(c_file)],
            CONFIG_DEFAULTS,
            staged=True,
            run_command=mock_runner,
        )
        assert len(result) == 1
        assert result[0].name == "Do_Stuff"
        assert "REQ-0252" in result[0].reqs
        assert result[0].new_version == "1.1"

    def test_no_change_no_result(self, tmp_path):
        c_file = tmp_path / "test.c"
        c_file.write_text("void Func(void) { x(); }")

        def mock_runner(cmd):
            return ""

        result = collect_changed_functions(
            [str(c_file)],
            CONFIG_DEFAULTS,
            staged=True,
            run_command=mock_runner,
        )
        assert result == []

    def test_unknown_extension_skipped(self, tmp_path):
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo(): pass")

        def mock_runner(cmd):
            return "@@ -1,1 +1,1 @@\n-old\n+new\n"

        result = collect_changed_functions(
            [str(py_file)],
            CONFIG_DEFAULTS,
            staged=True,
            run_command=mock_runner,
        )
        assert result == []


class TestLoadRequirements:
    """Tests for load_requirements."""

    def test_load_csv(self):
        config = _make_impact_config(req_file=FIXTURES_DIR / "impact" / "req.csv")
        reqs = load_requirements(config)
        assert reqs["REQ-0252"] == "BLE-First Pairing"
        assert reqs["REQ-0555"] == "OTA Download"

    def test_load_json(self, tmp_path):
        req_file = tmp_path / "req.json"
        req_file.write_text(
            json.dumps(
                [
                    {"Req ID": "REQ-0001", "Requirement Name": "Test Req"},
                ]
            )
        )
        config = deep_merge(
            CONFIG_DEFAULTS,
            {
                "impact": {
                    "requirements": {
                        "file": str(req_file),
                        "id_column": "Req ID",
                        "name_column": "Requirement Name",
                        "format": "json",
                    },
                },
            },
        )
        reqs = load_requirements(config)
        assert reqs["REQ-0001"] == "Test Req"

    def test_load_yaml(self, tmp_path):
        req_file = tmp_path / "req.yaml"
        req_file.write_text(
            dedent("""\
                - Req ID: REQ-0001
                  Requirement Name: YAML Req
            """)
        )
        config = deep_merge(
            CONFIG_DEFAULTS,
            {
                "impact": {
                    "requirements": {
                        "file": str(req_file),
                        "id_column": "Req ID",
                        "name_column": "Requirement Name",
                        "format": "yaml",
                    },
                },
            },
        )
        reqs = load_requirements(config)
        assert reqs["REQ-0001"] == "YAML Req"

    def test_no_requirements_config(self):
        reqs = load_requirements(CONFIG_DEFAULTS)
        assert reqs == {}

    def test_missing_file(self):
        config = deep_merge(
            CONFIG_DEFAULTS,
            {
                "impact": {
                    "requirements": {"file": "/nonexistent/req.csv", "format": "csv"},
                },
            },
        )
        reqs = load_requirements(config)
        assert reqs == {}


class TestMapToTestSuites:
    """Tests for map_to_test_suites."""

    def test_matches_specific_pattern(self):
        config = _make_impact_config()
        result = map_to_test_suites({"REQ-0252"}, config)
        suites = [r["suite"] for r in result]
        assert "Pairing" in suites

    def test_matches_wildcard(self):
        config = _make_impact_config()
        result = map_to_test_suites({"REQ-9999"}, config)
        suites = [r["suite"] for r in result]
        assert "Full Regression" in suites

    def test_no_mapping_config(self):
        result = map_to_test_suites({"REQ-0001"}, CONFIG_DEFAULTS)
        assert result == []

    def test_deduplicates_suites(self):
        config = _make_impact_config()
        result = map_to_test_suites({"REQ-0251", "REQ-0252"}, config)
        suite_names = [r["suite"] for r in result]
        assert suite_names.count("Pairing") == 1


class TestBuildImpactReport:
    """Tests for build_impact_report."""

    def test_groups_by_requirement(self):
        config = _make_impact_config(req_file=FIXTURES_DIR / "impact" / "req.csv")
        changed = [
            ChangedFunction(name="FuncA", file_path="a.c", reqs=["REQ-0252"]),
            ChangedFunction(name="FuncB", file_path="b.c", reqs=["REQ-0252"]),
            ChangedFunction(name="FuncC", file_path="c.c", reqs=["REQ-0555"]),
        ]
        entries = build_impact_report(changed, config)
        assert len(entries) == 2

        req252 = next(e for e in entries if e.req_id == "REQ-0252")
        assert len(req252.functions) == 2
        assert req252.req_name == "BLE-First Pairing"
        assert "Pairing" in req252.test_suites

    def test_no_changes(self):
        entries = build_impact_report([], CONFIG_DEFAULTS)
        assert entries == []


class TestFormatMarkdown:
    """Tests for format_markdown."""

    def test_renders_table(self):
        entries = [
            ImpactEntry(
                req_id="REQ-0252",
                req_name="Pairing",
                functions=[ChangedFunction(name="Func", file_path="a.c", reqs=["REQ-0252"])],
                test_suites=["Pairing"],
                test_commands=["run_pairing"],
            ),
        ]
        result = format_markdown(entries)
        assert "## Change Impact Report" in result
        assert "REQ-0252" in result
        assert "Func" in result
        assert "`run_pairing`" in result
        assert "1 requirement(s)" in result

    def test_empty_report(self):
        result = format_markdown([])
        assert "No requirements affected" in result


class TestFormatJson:
    """Tests for format_json."""

    def test_valid_json(self):
        entries = [
            ImpactEntry(
                req_id="REQ-0001",
                functions=[ChangedFunction(name="F", file_path="a.c", new_version="1.0")],
            ),
        ]
        result = format_json(entries)
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["req_id"] == "REQ-0001"


class TestFormatText:
    """Tests for format_text."""

    def test_lists_reqs(self):
        entries = [
            ImpactEntry(req_id="REQ-0252", test_suites=["Pairing"]),
            ImpactEntry(req_id="REQ-0555", test_suites=["OTA"]),
        ]
        result = format_text(entries)
        assert "REQ-0252" in result
        assert "REQ-0555" in result
        assert "Pairing" in result

    def test_empty_report(self):
        result = format_text([])
        assert "No requirements affected" in result


class TestRunImpact:
    """Integration tests for run_impact."""

    def test_full_pipeline(self, tmp_path):
        c_file = tmp_path / "test.c"
        c_file.write_text(
            dedent("""\
                /**
                 * @brief Func.
                 * @version 1.0
                 * @req REQ-0252
                 */
                void Func(void) {
                    impl();
                }
            """)
        )

        def mock_runner(cmd):
            return "@@ -6,1 +6,1 @@\n-old\n+new\n"

        config = _make_impact_config(req_file=FIXTURES_DIR / "impact" / "req.csv")
        result = run_impact(
            [str(c_file)],
            config,
            staged=True,
            run_command=mock_runner,
        )
        assert "REQ-0252" in result
        assert "Func" in result
