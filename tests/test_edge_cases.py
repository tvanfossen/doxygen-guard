"""Edge case tests covering capability analysis findings."""

from __future__ import annotations

from textwrap import dedent

from doxygen_guard.checks import check_presence, check_tags, check_version_staleness
from doxygen_guard.config import CONFIG_DEFAULTS, VALIDATE_DEFAULTS, deep_merge
from doxygen_guard.main import validate_file
from doxygen_guard.parser import ParseSettings, parse_functions
from tests.conftest import (
    C_EXCLUDES,
    C_PATTERN,
    C_SETTINGS,
    COMMENT_END,
    COMMENT_START,
    FIXTURES_DIR,
)

JAVA_PATTERN = VALIDATE_DEFAULTS["languages"]["java"]["function_pattern"]
JAVA_EXCLUDES = VALIDATE_DEFAULTS["languages"]["java"]["exclude_names"]
JAVA_SETTINGS = ParseSettings(comment_start=COMMENT_START, comment_end=COMMENT_END)


class TestDecorativeComments:
    """Verify decorative /*** blocks are not mistaken for doxygen."""

    def test_decorative_not_treated_as_doxygen(self):
        content = (FIXTURES_DIR / "mixed_comments.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}

        # Decorative block should NOT be associated with the function
        assert names["Documented_After_Decorative"].doxygen is not None
        assert "brief" in names["Documented_After_Decorative"].doxygen.tags

        # Functions after decorative/regular/line comments should have no doxygen
        assert names["After_Regular_Comment"].doxygen is None
        assert names["After_Line_Comment"].doxygen is None
        assert names["After_Second_Decorative"].doxygen is None

    def test_decorative_triggers_presence_violation(self):
        violations = validate_file(
            str(FIXTURES_DIR / "mixed_comments.c"),
            CONFIG_DEFAULTS,
            no_git=True,
        )
        # 3 undocumented functions: After_Regular_Comment, After_Line_Comment,
        # After_Second_Decorative
        undoc_violations = [v for v in violations if "no doxygen comment" in v.message]
        assert len(undoc_violations) == 3

    def test_double_star_still_matches(self):
        """Ensure /** (exactly two stars) is still recognized."""
        lines = [
            "/** @brief Test. @version 1.0 */",
            "void Func(void) {",
        ]
        functions = parse_functions(
            "\n".join(lines),
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].doxygen is not None


class TestJavaLanguageConfig:
    """Verify Java function detection with the new language config."""

    def test_java_extension_recognized(self):
        from doxygen_guard.config import get_language_config

        config = CONFIG_DEFAULTS
        result = get_language_config(config, "src/Main.java")
        assert result is not None
        assert ".java" in result["extensions"]

    def test_java_public_method(self):
        content = (FIXTURES_DIR / "java_simple.java").read_text()
        functions = parse_functions(
            content,
            JAVA_PATTERN,
            JAVA_EXCLUDES,
            JAVA_SETTINGS,
        )
        names = [f.name for f in functions]
        assert "initService" in names
        assert "processRequest" in names
        assert "undocumentedHelper" in names
        assert "getWidgets" in names

    def test_java_missing_doxygen_detected(self):
        content = (FIXTURES_DIR / "java_simple.java").read_text()
        functions = parse_functions(
            content,
            JAVA_PATTERN,
            JAVA_EXCLUDES,
            JAVA_SETTINGS,
        )
        violations = check_presence(functions, "Main.java", CONFIG_DEFAULTS)
        undoc = [v for v in violations if "no doxygen comment" in v.message]
        assert len(undoc) == 1
        assert "undocumentedHelper" in undoc[0].message

    def test_java_static_method(self):
        content = dedent("""\
            /**
             * @brief Static helper.
             * @version 1.0
             */
            public static void helper() {
                work();
            }
        """)
        functions = parse_functions(
            content,
            JAVA_PATTERN,
            JAVA_EXCLUDES,
            JAVA_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].name == "helper"
        assert functions[0].doxygen is not None

    def test_java_generic_return_type(self):
        content = dedent("""\
            /**
             * @brief Get items.
             * @version 1.0
             */
            public List<String> getItems() {
                return items;
            }
        """)
        functions = parse_functions(
            content,
            JAVA_PATTERN,
            JAVA_EXCLUDES,
            JAVA_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].name == "getItems"


class TestVersionStaleness:
    """Verify staleness detection with fixture file."""

    def test_stale_version_detected(self):
        content = (FIXTURES_DIR / "stale_version.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        # Simulate: Stale_Func body changed (line 5), version not touched
        # Stale_Func is at def_line=4, body_end=6, doxygen at lines 0-3
        stale = [f for f in functions if f.name == "Stale_Func"][0]
        changed_lines = {stale.def_line + 1}  # A body line changed

        violations = check_version_staleness(
            functions,
            "stale_version.c",
            CONFIG_DEFAULTS,
            changed_lines,
        )
        assert len(violations) == 1
        assert "Stale_Func" in violations[0].message
        assert "not updated" in violations[0].message

    def test_updated_version_no_violation(self):
        content = (FIXTURES_DIR / "stale_version.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        # Simulate: Updated_Func body AND doxygen both changed
        updated = [f for f in functions if f.name == "Updated_Func"][0]
        # Find the actual @version line within the doxygen block
        raw_lines = updated.doxygen.raw.splitlines()
        version_offset = next(i for i, ln in enumerate(raw_lines) if "@version" in ln)
        changed_lines = {
            updated.doxygen.start_line + version_offset,  # @version line changed
            updated.def_line + 1,  # body line changed
        }

        violations = check_version_staleness(
            functions,
            "stale_version.c",
            CONFIG_DEFAULTS,
            changed_lines,
        )
        assert violations == []


class TestTagValidation:
    """Verify tag validation with fixture file."""

    def _make_tag_config(self):
        return deep_merge(
            CONFIG_DEFAULTS,
            {
                "validate": {
                    "tags": {
                        "req": {
                            "pattern": r"^REQ-\w+$",
                        },
                        "emits": {"require_prefix": ["EVENT_", "FSM_"]},
                        "ext": {"require_contains": "::"},
                    },
                },
            },
        )

    def test_bad_tags_all_fail(self):
        content = (FIXTURES_DIR / "bad_tags.c").read_text()
        config = self._make_tag_config()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        violations = check_tags(functions, "bad_tags.c", config)
        bad_func_violations = [v for v in violations if "Bad_Tags" in v.message]

        # @req INVALID-FORMAT → pattern fail + missing confidence marker
        # @emits BADPREFIX_EVENT → prefix fail
        # @ext modfunc → contains fail
        assert len(bad_func_violations) >= 3

        messages = " ".join(v.message for v in bad_func_violations)
        assert "does not match pattern" in messages
        assert "does not start with" in messages
        assert "does not contain" in messages

    def test_good_tags_all_pass(self):
        content = (FIXTURES_DIR / "bad_tags.c").read_text()
        config = self._make_tag_config()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        violations = check_tags(functions, "bad_tags.c", config)
        good_func_violations = [v for v in violations if "Good_Tags" in v.message]
        assert good_func_violations == []


class TestBracesInStrings:
    """Document known limitation: brace counting in strings."""

    def test_braces_in_strings_body_end(self):
        """Brace counting does not account for braces inside string literals.

        This documents a known limitation. The parser counts ALL braces,
        including those in strings. For most real code this works because
        string braces are balanced (e.g., printf("{}")).
        """
        content = (FIXTURES_DIR / "braces_in_strings.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        names = [f.name for f in functions]
        assert "Braces_In_String" in names
        assert "After_String_Braces" in names

        # With balanced string braces, body end detection still works
        braces_func = [f for f in functions if f.name == "Braces_In_String"][0]
        after_func = [f for f in functions if f.name == "After_String_Braces"][0]

        # Braces_In_String body should end before After_String_Braces starts
        assert braces_func.body_end < after_func.def_line

    def test_unbalanced_string_brace_breaks_detection(self):
        """Unbalanced braces in strings confuse body end detection."""
        content = dedent("""\
            /**
             * @brief Problematic function.
             * @version 1.0
             */
            void Unbalanced(void) {
                printf("extra { here");
            }

            /**
             * @brief Should be separate.
             * @version 1.0
             */
            void Next_Func(void) {
                clean();
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )

        unbal = [f for f in functions if f.name == "Unbalanced"][0]
        # The extra { in the string means brace count doesn't reach 0 at the
        # real closing }. Body end extends past where it should.
        # This is a known limitation — documenting, not fixing.
        assert unbal.body_end > 6  # Real end is line 6, parser overshoots


class TestForwardDeclarationMix:
    """Verify forward declarations mixed with definitions."""

    def test_only_definitions_checked(self):
        violations = validate_file(
            str(FIXTURES_DIR / "forward_decl.c"),
            CONFIG_DEFAULTS,
            no_git=True,
        )
        # Only the definition (Module_Init with doxygen) should be checked.
        # No violations expected since it has @brief and @version.
        assert violations == []

    def test_undocumented_definition_after_decl(self):
        content = dedent("""\
            void Helper(int x);

            void Helper(int x) {
                do_stuff(x);
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        # Forward declaration skipped, definition found
        assert len(functions) == 1
        assert functions[0].name == "Helper"
        assert functions[0].doxygen is None

        violations = check_presence(functions, "test.c", CONFIG_DEFAULTS)
        assert len(violations) == 1
        assert "no doxygen comment" in violations[0].message


class TestGccAttributes:
    """Verify __attribute__ between doxygen and function signature is handled."""

    def test_attribute_visibility_hidden(self):
        content = (FIXTURES_DIR / "attribute.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}

        assert "queue_inbound_event" in names
        assert names["queue_inbound_event"].doxygen is not None
        assert "brief" in names["queue_inbound_event"].doxygen.tags

    def test_attribute_unused(self):
        content = (FIXTURES_DIR / "attribute.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}

        assert "unused_callback" in names
        assert names["unused_callback"].doxygen is not None
        assert "version" in names["unused_callback"].doxygen.tags

    def test_no_false_positives_with_attributes(self):
        violations = validate_file(
            str(FIXTURES_DIR / "attribute.c"),
            CONFIG_DEFAULTS,
            no_git=True,
        )
        assert violations == []

    def test_attribute_inline(self):
        """__attribute__ on same line as other content still works."""
        content = dedent("""\
            /**
             * @brief Inlined helper.
             * @version 1.0
             */
            __attribute__((always_inline))
            void inline_helper(void) {
                fast_stuff();
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].doxygen is not None

    def test_multiline_attribute_known_limitation(self):
        """Multi-line __attribute__ breaks doxygen association (known limitation)."""
        content = dedent("""\
            /**
             * @brief Multi-attr function.
             * @version 1.0
             */
            __attribute__((visibility("hidden"),
                           unused))
            void multi_attr(void) {
                work();
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        assert len(functions) == 1
        # Second line of attribute doesn't match attr_re, so backward scan
        # stops and fails to find */. Doxygen not associated.
        assert functions[0].doxygen is None


class TestTypedefReturnsAndMacroQualifiers:
    """Verify function detection with typedef return types and macro qualifiers."""

    def test_lowercase_typedef_return(self):
        content = (FIXTURES_DIR / "typedef_returns.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}
        assert "get_status" in names
        assert names["get_status"].doxygen is not None

    def test_typedef_pointer_return(self):
        content = (FIXTURES_DIR / "typedef_returns.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}
        assert "find_config" in names
        assert names["find_config"].doxygen is not None

    def test_static_macro_qualifier(self):
        content = (FIXTURES_DIR / "typedef_returns.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}
        assert "internal_helper" in names
        assert names["internal_helper"].doxygen is not None

    def test_weak_macro_qualifier(self):
        content = (FIXTURES_DIR / "typedef_returns.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}
        assert "default_handler" in names
        assert names["default_handler"].doxygen is not None

    def test_undocumented_typedef_detected(self):
        content = (FIXTURES_DIR / "typedef_returns.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        names = {f.name: f for f in functions}
        assert "undocumented_func" in names
        assert names["undocumented_func"].doxygen is None

    def test_no_false_positives(self):
        violations = validate_file(
            str(FIXTURES_DIR / "typedef_returns.c"),
            CONFIG_DEFAULTS,
            no_git=True,
        )
        # Only undocumented_func should fail
        assert len(violations) == 1
        assert "undocumented_func" in violations[0].message

    def test_inline_typedef_return(self):
        content = dedent("""\
            /**
             * @brief Convert error code.
             * @version 1.0
             */
            err_code_t convert_error(int raw) {
                return (err_code_t)raw;
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].name == "convert_error"
        assert functions[0].doxygen is not None

    def test_struct_qualified_return(self):
        content = dedent("""\
            /**
             * @brief Create a new node.
             * @version 1.0
             */
            struct node* create_node(int value) {
                return allocate(value);
            }
        """)
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
        )
        assert len(functions) == 1
        assert functions[0].name == "create_node"
