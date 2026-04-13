"""Tests for doxygen_guard.checks module."""

from __future__ import annotations

from doxygen_guard.checks import (
    check_presence,
    check_tags,
    check_version_staleness,
)
from doxygen_guard.config import CONFIG_DEFAULTS
from doxygen_guard.parser import DoxygenBlock, Function


def _make_func(
    name: str = "Func",
    def_line: int = 5,
    body_end: int = 10,
    tags: dict | None = None,
    has_doxygen: bool = True,
) -> Function:
    """Helper to create Function instances for testing."""
    doxygen = None
    if has_doxygen:
        doxygen = DoxygenBlock(
            start_line=def_line - 4,
            end_line=def_line - 1,
            tags={"brief": ["Do something."], "version": ["1.0"]} if tags is None else tags,
            raw="/**\n * @brief Do something.\n * @version 1.0\n */",
        )
    return Function(name=name, def_line=def_line, body_end=body_end, doxygen=doxygen)


class TestCheckPresence:
    """Tests for check_presence."""

    def test_no_violations_when_documented(self):
        funcs = [_make_func()]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert violations == []

    def test_missing_doxygen(self):
        funcs = [_make_func(has_doxygen=False)]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 1
        assert violations[0].check == "presence"
        assert "no doxygen comment" in violations[0].message

    def test_missing_brief(self):
        funcs = [_make_func(tags={"version": ["1.0"]})]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 1
        assert "missing @brief" in violations[0].message

    def test_missing_version(self):
        funcs = [_make_func(tags={"brief": ["Something."]})]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 1
        assert "missing @version" in violations[0].message

    def test_missing_both_tags(self):
        funcs = [_make_func(tags={})]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 2

    def test_presence_disabled(self):
        config = {
            "validate": {
                "presence": {"require_doxygen": False},
                "version": {"require_present": True},
            }
        }
        funcs = [_make_func(has_doxygen=False)]
        violations = check_presence(funcs, "test.c", config)
        assert violations == []

    def test_version_not_required(self):
        config = {
            "validate": {
                "presence": {"require_doxygen": True},
                "version": {"require_present": False},
            }
        }
        funcs = [_make_func(tags={"brief": ["Something."]})]
        violations = check_presence(funcs, "test.c", config)
        assert violations == []

    def test_multiple_functions(self):
        funcs = [
            _make_func(name="Good", def_line=5, body_end=10),
            _make_func(name="Bad", def_line=15, body_end=20, has_doxygen=False),
        ]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 1
        assert "Bad" in violations[0].message

    def test_line_numbers_1_indexed(self):
        funcs = [_make_func(def_line=0, has_doxygen=False)]
        violations = check_presence(funcs, "test.c", CONFIG_DEFAULTS)
        assert violations[0].line == 1  # 0-indexed + 1


class TestCheckVersionStaleness:
    """Tests for check_version_staleness."""

    def test_no_change_no_violation(self):
        funcs = [_make_func(def_line=5, body_end=10)]
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines=set())
        assert violations == []

    def test_body_changed_version_not_updated(self):
        funcs = [_make_func(def_line=5, body_end=10)]
        # Lines 5-10 changed (body), but not lines 1-4 (doxygen)
        changed_lines = {7, 8}
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines)
        assert len(violations) == 1
        assert violations[0].check == "version"
        assert "not updated" in violations[0].message

    def test_body_and_version_both_changed(self):
        funcs = [_make_func(def_line=5, body_end=10)]
        # Both body and doxygen lines changed
        changed_lines = {3, 7}  # doxygen line 3 (in range 1-4), body line 7
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []

    def test_only_doxygen_changed(self):
        funcs = [_make_func(def_line=5, body_end=10)]
        # Only doxygen lines changed, not body
        changed_lines = {2}
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []

    def test_no_doxygen_skipped(self):
        funcs = [_make_func(has_doxygen=False, def_line=5, body_end=10)]
        changed_lines = {7}
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []

    def test_staleness_disabled(self):
        config = {
            "validate": {
                "version": {"require_increment_on_change": False, "tag": "@version"},
            }
        }
        funcs = [_make_func(def_line=5, body_end=10)]
        changed_lines = {7}
        violations = check_version_staleness(funcs, "test.c", config, changed_lines)
        assert violations == []

    def test_no_version_tag_skipped(self):
        """Functions without @version are skipped (caught by presence check)."""
        funcs = [_make_func(def_line=5, body_end=10, tags={"brief": ["Something."]})]
        changed_lines = {7}
        violations = check_version_staleness(funcs, "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []

    def test_reviewed_marker_clears_staleness(self):
        """Adding [reviewed] to @version line counts as updating the version."""
        func = _make_func(
            def_line=5,
            body_end=10,
            tags={"brief": ["Something."], "version": ["1.0 [reviewed]"]},
        )
        # Override raw to include [reviewed] on the version line
        func.doxygen.raw = "/**\n * @brief Something.\n * @version 1.0 [reviewed]\n */"
        # Both body and version line changed
        changed_lines = {3, 7}  # line 3 has @version, line 7 is body
        violations = check_version_staleness([func], "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []

    def test_unknown_version_marker_rejected(self):
        """Unknown markers like [typo] are rejected."""
        func = _make_func(
            def_line=5,
            body_end=10,
            tags={"brief": ["Something."], "version": ["1.0 [typo]"]},
        )
        func.doxygen.raw = "/**\n * @brief Something.\n * @version 1.0 [typo]\n */"
        changed_lines = {3, 7}
        violations = check_version_staleness([func], "test.c", CONFIG_DEFAULTS, changed_lines)
        assert len(violations) == 1
        assert "unrecognized version marker" in violations[0].message

    def test_version_bump_still_works(self):
        """Normal version bump (no marker) still passes."""
        func = _make_func(
            def_line=5,
            body_end=10,
            tags={"brief": ["Something."], "version": ["1.1"]},
        )
        func.doxygen.raw = "/**\n * @brief Something.\n * @version 1.1\n */"
        changed_lines = {3, 7}
        violations = check_version_staleness([func], "test.c", CONFIG_DEFAULTS, changed_lines)
        assert violations == []


class TestCheckTags:
    """Tests for check_tags."""

    def test_no_tag_rules(self):
        funcs = [_make_func()]
        violations = check_tags(funcs, "test.c", CONFIG_DEFAULTS)
        assert violations == []

    def test_valid_pattern(self):
        config = {
            "validate": {
                "tags": {
                    "req": {"pattern": r"^REQ-\w+$"},
                },
            }
        }
        funcs = [
            _make_func(tags={"brief": ["Something."], "version": ["1.0"], "req": ["REQ-0001"]})
        ]
        violations = check_tags(funcs, "test.c", config)
        assert violations == []

    def test_invalid_pattern(self):
        config = {
            "validate": {
                "tags": {
                    "req": {"pattern": r"^REQ-\w+$"},
                },
            }
        }
        funcs = [_make_func(tags={"brief": ["Something."], "version": ["1.0"], "req": ["INVALID"]})]
        violations = check_tags(funcs, "test.c", config)
        assert len(violations) == 1
        assert "does not match pattern" in violations[0].message

    def test_require_prefix(self):
        config = {
            "validate": {
                "tags": {
                    "sends": {"require_prefix": ["EVENT:", "FSM:"]},
                },
            }
        }
        # Valid prefix
        funcs = [_make_func(tags={"brief": ["X."], "version": ["1.0"], "sends": ["EVENT:READY"]})]
        assert check_tags(funcs, "test.c", config) == []

        # Invalid prefix
        funcs = [_make_func(tags={"brief": ["X."], "version": ["1.0"], "sends": ["BADPREFIX"]})]
        violations = check_tags(funcs, "test.c", config)
        assert len(violations) == 1
        assert "does not start with" in violations[0].message

    def test_require_contains(self):
        config = {
            "validate": {
                "tags": {
                    "calls": {"require_contains": "::"},
                },
            }
        }
        # Valid
        funcs = [_make_func(tags={"brief": ["X."], "version": ["1.0"], "calls": ["mod::func"]})]
        assert check_tags(funcs, "test.c", config) == []

        # Invalid
        funcs = [_make_func(tags={"brief": ["X."], "version": ["1.0"], "calls": ["modfunc"]})]
        violations = check_tags(funcs, "test.c", config)
        assert len(violations) == 1
        assert "does not contain" in violations[0].message

    def test_no_doxygen_skipped(self):
        config = {
            "validate": {
                "tags": {
                    "req": {"pattern": r"^REQ-\w+$"},
                },
            }
        }
        funcs = [_make_func(has_doxygen=False)]
        violations = check_tags(funcs, "test.c", config)
        assert violations == []

    def test_tag_not_present_no_violation(self):
        """Tags that aren't present in the doxygen block don't trigger violations."""
        config = {
            "validate": {
                "tags": {
                    "req": {"pattern": r"^REQ-\w+$"},
                },
            }
        }
        funcs = [_make_func(tags={"brief": ["X."], "version": ["1.0"]})]
        violations = check_tags(funcs, "test.c", config)
        assert violations == []

    def test_violation_str(self):
        from doxygen_guard.checks import Violation

        v = Violation(file="test.c", line=10, check="presence", message="missing doxygen")
        assert str(v) == "test.c:10: [presence] missing doxygen"


class TestConstructorDestructorSkipping:
    """Constructors and destructors should be exempt from @return and @req checks."""

    def test_constructor_skipped_for_return_check(self):
        from doxygen_guard.checks import check_return_presence

        ctor = Function(
            name="Foo",
            def_line=5,
            body_end=10,
            doxygen=DoxygenBlock(
                start_line=2, end_line=4, tags={"brief": ["Constructor."], "version": ["1.0"]}
            ),
            enclosing_class="Foo",
        )
        content = "class Foo {\npublic:\nFoo() {}\n};\n"
        violations = check_return_presence([ctor], "test.cpp", CONFIG_DEFAULTS, content)
        assert violations == []

    def test_destructor_skipped_for_return_check(self):
        from doxygen_guard.checks import check_return_presence

        dtor = Function(
            name="~Foo",
            def_line=5,
            body_end=10,
            doxygen=DoxygenBlock(
                start_line=2, end_line=4, tags={"brief": ["Destructor."], "version": ["1.0"]}
            ),
            enclosing_class="Foo",
        )
        content = "class Foo {\npublic:\n~Foo() {}\n};\n"
        violations = check_return_presence([dtor], "test.cpp", CONFIG_DEFAULTS, content)
        assert violations == []

    def test_constructor_skipped_for_req_check(self):
        from doxygen_guard.checks import check_req_coverage

        ctor = Function(
            name="Foo",
            def_line=5,
            body_end=10,
            doxygen=DoxygenBlock(
                start_line=2, end_line=4, tags={"brief": ["Constructor."], "version": ["1.0"]}
            ),
            enclosing_class="Foo",
        )
        config = {
            "impact": {"requirements": {"file": "reqs.csv"}},
            "validate": {},
        }
        violations = check_req_coverage([ctor], "test.cpp", config)
        assert violations == []

    def test_regular_function_still_requires_req(self):
        from doxygen_guard.checks import check_req_coverage

        method = Function(
            name="bar",
            def_line=5,
            body_end=10,
            doxygen=DoxygenBlock(
                start_line=2, end_line=4, tags={"brief": ["Method."], "version": ["1.0"]}
            ),
            enclosing_class="Foo",
        )
        config = {
            "impact": {"requirements": {"file": "reqs.csv"}},
            "validate": {},
        }
        violations = check_req_coverage([method], "test.cpp", config)
        assert len(violations) == 1


class TestStandardDoxygenTags:
    """Standard Doxygen tags should be in the known allowlist."""

    def test_par_not_unknown(self):
        from doxygen_guard.checks import _KNOWN_TAGS

        assert "par" in _KNOWN_TAGS

    def test_throws_not_unknown(self):
        from doxygen_guard.checks import _KNOWN_TAGS

        assert "throws" in _KNOWN_TAGS
        assert "throw" in _KNOWN_TAGS
        assert "exception" in _KNOWN_TAGS
