"""Tests for doxygen_guard.config module."""

from __future__ import annotations

from textwrap import dedent

from doxygen_guard.config import (
    CONFIG_DEFAULTS,
    deep_merge,
    get_language_config,
    load_config,
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
                      confidence_markers: [verified, inferred]
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
                  output_dir: diagrams/
            """)
        )
        config = load_config(config_file)
        assert config["trace"]["format"] == "mermaid"
        assert config["trace"]["output_dir"] == "diagrams/"
        # Defaults preserved
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
                  participants:
                    - id: app
                      label: "App"
                      match: "src/app/"
                impact:
                  test_mapping:
                    - match: "REQ-.*"
                      suite: "All"
                      command: "pytest"
            """)
        )
        config = load_config(config_file)
        assert "java" in config["validate"]["languages"]
        assert config["validate"]["version"]["tag"] == "@ver"
        assert config["validate"]["exclude"] == ["^gen/"]
        assert len(config["trace"]["participants"]) == 1
        assert len(config["impact"]["test_mapping"]) == 1

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
