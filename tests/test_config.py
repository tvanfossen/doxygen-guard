"""Tests for doxygen_guard.config module."""

from __future__ import annotations

from textwrap import dedent

import pytest

from doxygen_guard.config import (
    CONFIG_DEFAULTS,
    deep_merge,
    get_language_config,
    load_config,
    parse_version,
    validate_config_schema,
    validate_output_path,
)


class TestDeepMerge:
    """Tests for deep_merge function."""

    def test_empty_override(self):
        base = {"a": 1, "b": {"c": 2}}
        assert deep_merge(base, {}) == base

    def test_empty_base(self):
        override = {"a": 1}
        assert deep_merge({}, override) == override

    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"c": 99}}
        result = deep_merge(base, override)
        assert result == {"a": {"b": 1, "c": 99}, "d": 3}

    def test_override_adds_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_override_replaces_non_dict_with_dict(self):
        base = {"a": 1}
        override = {"a": {"nested": True}}
        assert deep_merge(base, override) == {"a": {"nested": True}}

    def test_override_replaces_dict_with_non_dict(self):
        base = {"a": {"nested": True}}
        override = {"a": "flat"}
        assert deep_merge(base, override) == {"a": "flat"}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        deep_merge(base, override)
        assert base == {"a": {"b": 1}}


class TestLoadConfig:
    """Tests for load_config function."""

    def test_no_config_file_returns_defaults(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config == CONFIG_DEFAULTS

    def test_empty_config_file_returns_defaults(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text("")
        config = load_config(config_file)
        assert config == CONFIG_DEFAULTS

    def test_partial_validate_override(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                validate:
                  presence:
                    require_doxygen: false
            """)
        )
        config = load_config(config_file)
        assert config["validate"]["presence"]["require_doxygen"] is False
        # Other defaults preserved
        assert config["validate"]["presence"]["skip_forward_declarations"] is True
        assert "c" in config["validate"]["languages"]

    def test_custom_tags(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                validate:
                  tags:
                    req:
                      pattern: "^REQ-\\\\w+$"
            """)
        )
        config = load_config(config_file)
        assert "req" in config["validate"]["tags"]
        assert config["validate"]["tags"]["req"]["pattern"] == r"^REQ-\w+$"

    def test_trace_section_override(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                trace:
                  format: mermaid
            """)
        )
        config = load_config(config_file)
        assert config["trace"]["format"] == "mermaid"
        assert config["trace"]["options"]["autonumber"] is True

    def test_impact_section_override(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                impact:
                  requirements:
                    file: reqs.csv
                    format: csv
            """)
        )
        config = load_config(config_file)
        assert config["impact"]["requirements"]["file"] == "reqs.csv"

    def test_full_config(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                output_dir: docs/out/
                validate:
                  languages:
                    java:
                      extensions: [.java]
                      function_pattern: "public\\\\s+\\\\w+"
                      exclude_names: []
                  version:
                    tag: "@ver"
                  exclude:
                    - "^gen/"
                trace:
                  format: plantuml
                  participant_field: "Subsystem"
                impact:
                  requirements:
                    file: reqs.csv
                    format: csv
            """)
        )
        config = load_config(config_file)
        assert "java" in config["validate"]["languages"]
        assert config["validate"]["version"]["tag"] == "@ver"
        assert config["validate"]["exclude"] == ["^gen/"]
        assert config["output_dir"] == "docs/out/"

    def test_non_mapping_config_returns_defaults(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text("just a string")
        config = load_config(config_file)
        assert config == CONFIG_DEFAULTS

    def test_exclude_patterns(self, tmp_path):
        config_file = tmp_path / ".doxygen-guard.yaml"
        config_file.write_text(
            dedent("""\
                validate:
                  exclude:
                    - "^gen/"
                    - "vendor/"
            """)
        )
        config = load_config(config_file)
        assert config["validate"]["exclude"] == ["^gen/", "vendor/"]


class TestGetLanguageConfig:
    """Tests for get_language_config function."""

    def test_c_file(self):
        config = CONFIG_DEFAULTS
        result = get_language_config(config, "src/main.c")
        assert result is not None
        assert ".c" in result["extensions"]

    def test_header_file(self):
        config = CONFIG_DEFAULTS
        result = get_language_config(config, "include/driver.h")
        assert result is not None
        assert ".h" in result["extensions"]

    def test_cpp_file(self):
        config = CONFIG_DEFAULTS
        result = get_language_config(config, "src/app.cpp")
        assert result is not None
        assert ".cpp" in result["extensions"]

    def test_unknown_extension(self):
        config = CONFIG_DEFAULTS
        result = get_language_config(config, "script.rs")
        assert result is None

    def test_no_extension(self):
        config = CONFIG_DEFAULTS
        result = get_language_config(config, "Makefile")
        assert result is None


class TestValidateConfigSchema:
    """Tests for validate_config_schema."""

    def test_empty_config_passes(self):
        assert validate_config_schema({}) == []

    def test_valid_output_dir(self):
        assert validate_config_schema({"output_dir": "docs/"}) == []

    def test_unknown_top_level_key(self):
        errors = validate_config_schema({"bogus": 1})
        assert len(errors) == 1
        assert "bogus" in errors[0]

    def test_unknown_nested_key(self):
        errors = validate_config_schema({"validate": {"bogus": 1}})
        assert len(errors) == 1
        assert "validate.bogus" in errors[0]

    def test_wrong_type_string(self):
        errors = validate_config_schema({"output_dir": 123})
        assert len(errors) == 1
        assert "expected str" in errors[0]

    def test_wrong_type_bool(self):
        errors = validate_config_schema({"validate": {"presence": {"require_doxygen": "yes"}}})
        assert len(errors) == 1
        assert "expected bool" in errors[0]

    def test_open_dict_allows_custom_languages(self):
        errors = validate_config_schema(
            {"validate": {"languages": {"rust": {"extensions": [".rs"]}}}}
        )
        assert errors == []

    def test_open_dict_allows_custom_tags(self):
        errors = validate_config_schema({"validate": {"tags": {"custom_tag": {"pattern": ".*"}}}})
        assert errors == []

    def test_stale_participants_rejected(self):
        errors = validate_config_schema({"trace": {"participants": [{"id": "x"}]}})
        assert len(errors) == 1
        assert "participants" in errors[0]

    def test_stale_test_mapping_rejected(self):
        errors = validate_config_schema({"impact": {"test_mapping": []}})
        assert len(errors) == 1
        assert "test_mapping" in errors[0]

    def test_version_gate_accepted(self):
        errors = validate_config_schema({"validate": {"version_gate": {"current_version": "v1.0"}}})
        assert errors == []

    def test_non_dict_where_dict_expected(self):
        errors = validate_config_schema({"validate": "garbage"})
        assert len(errors) == 1
        assert "expected dict" in errors[0]

    def test_multiple_errors(self):
        errors = validate_config_schema({"bogus1": 1, "bogus2": 2})
        assert len(errors) == 2


class TestParseVersion:
    """Tests for parse_version."""

    def test_basic(self):
        assert parse_version("v1.8.2") == (1, 8, 2)

    def test_no_prefix(self):
        assert parse_version("1.8.2") == (1, 8, 2)

    def test_two_parts(self):
        assert parse_version("v1.0") == (1, 0)

    def test_single_part(self):
        assert parse_version("v3") == (3,)

    def test_prerelease_version(self):
        assert parse_version("v1.0.0-rc1") == (1, 0, 0)

    def test_build_metadata(self):
        assert parse_version("v2.1.0+build123") == (2, 1, 0)

    def test_invalid(self):
        assert parse_version("not-a-version") == (0,)


class TestValidateOutputPath:
    """Tests for validate_output_path."""

    def test_relative_path_passes(self):
        result = validate_output_path("docs/generated/")
        assert result.parts[0] == "docs"

    def test_nested_relative_passes(self):
        result = validate_output_path("docs/generated/sequences")
        assert len(result.parts) == 3

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="must be relative"):
            validate_output_path("/etc/cron.d")

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="directory traversal"):
            validate_output_path("../outside")

    def test_mid_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="directory traversal"):
            validate_output_path("docs/../../etc")
