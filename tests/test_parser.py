"""Tests for doxygen_guard.parser module."""

from __future__ import annotations

from doxygen_guard.parser import (
    find_body_end,
    find_doxygen_block_before,
    is_forward_declaration,
    parse_doxygen_tags,
    parse_functions,
)
from tests.conftest import (
    C_EXCLUDES,
    C_PATTERN,
    C_SETTINGS,
    COMMENT_END,
    COMMENT_START,
    CPP_EXCLUDES,
    CPP_PATTERN,
    CPP_SETTINGS,
)


class TestParseTags:
    """Tests for parse_doxygen_tags."""

    def test_single_tag(self):
        block = "/** @brief Initialize the module. */"
        tags = parse_doxygen_tags(block)
        assert "brief" in tags
        assert tags["brief"] == ["Initialize the module."]

    def test_multiple_tags(self):
        block = """\
/**
 * @brief Do something.
 * @version 1.0
 * @req REQ-0001
 */"""
        tags = parse_doxygen_tags(block)
        assert tags["brief"] == ["Do something."]
        assert tags["version"] == ["1.0"]
        assert tags["req"] == ["REQ-0001"]

    def test_duplicate_tags(self):
        block = """\
/**
 * @brief Something.
 * @req REQ-0001
 * @req REQ-0002
 */"""
        tags = parse_doxygen_tags(block)
        assert len(tags["req"]) == 2
        assert "REQ-0001" in tags["req"]
        assert "REQ-0002" in tags["req"]

    def test_empty_block(self):
        block = "/** */"
        tags = parse_doxygen_tags(block)
        assert tags == {}


class TestFindDoxygenBlockBefore:
    """Tests for find_doxygen_block_before."""

    def test_finds_block_immediately_before(self):
        lines = [
            "/**",
            " * @brief Init.",
            " * @version 1.0",
            " */",
            "void Init(void) {",
        ]
        block = find_doxygen_block_before(lines, 4, COMMENT_START, COMMENT_END)
        assert block is not None
        assert block.start_line == 0
        assert block.end_line == 3

    def test_finds_block_with_blank_line_gap(self):
        lines = [
            "/**",
            " * @brief Init.",
            " */",
            "",
            "void Init(void) {",
        ]
        block = find_doxygen_block_before(lines, 4, COMMENT_START, COMMENT_END)
        assert block is not None
        assert block.start_line == 0

    def test_no_block_returns_none(self):
        lines = [
            "",
            "void Init(void) {",
        ]
        block = find_doxygen_block_before(lines, 1, COMMENT_START, COMMENT_END)
        assert block is None

    def test_regular_comment_not_doxygen(self):
        lines = [
            "/* regular comment */",
            "void Init(void) {",
        ]
        block = find_doxygen_block_before(lines, 1, COMMENT_START, COMMENT_END)
        assert block is None

    def test_decorative_comment_not_matched(self):
        """Default pattern /\\*\\*(?!\\*) rejects /*** decorative blocks."""
        lines = [
            "/***********************************************************",
            " * This is decorative.",
            " ***********************************************************/",
            "void Func(void) {",
        ]
        block = find_doxygen_block_before(lines, 3, COMMENT_START, COMMENT_END)
        assert block is None


class TestFindBodyEnd:
    """Tests for find_body_end."""

    def test_simple_function(self):
        lines = [
            "void Func(void) {",
            "    do_stuff();",
            "}",
        ]
        assert find_body_end(lines, 0) == 2

    def test_nested_braces(self):
        lines = [
            "void Func(void) {",
            "    if (x) {",
            "        y();",
            "    }",
            "}",
        ]
        assert find_body_end(lines, 0) == 4

    def test_brace_on_next_line(self):
        lines = [
            "void Func(void)",
            "{",
            "    do_stuff();",
            "}",
        ]
        assert find_body_end(lines, 0) == 3


class TestIsForwardDeclaration:
    """Tests for is_forward_declaration."""

    def test_forward_declaration(self):
        lines = ["void Module_Init(void);"]
        assert is_forward_declaration(lines, 0) is True

    def test_function_definition(self):
        lines = ["void Module_Init(void) {", "    setup();", "}"]
        assert is_forward_declaration(lines, 0) is False

    def test_multiline_declaration(self):
        lines = [
            "int Module_Process(const char *data,",
            "                   size_t len);",
        ]
        assert is_forward_declaration(lines, 0) is True

    def test_multiline_definition(self):
        lines = [
            "int Module_Process(const char *data,",
            "                   size_t len)",
            "{",
        ]
        assert is_forward_declaration(lines, 0) is False


class TestParseFunctions:
    """Integration tests for parse_functions."""

    def test_simple_c_file(self, fixtures_dir):
        content = (fixtures_dir / "simple.c").read_text()
        functions = parse_functions(content, C_PATTERN, C_EXCLUDES, C_SETTINGS)

        assert len(functions) == 3
        assert functions[0].name == "Module_Init"
        assert functions[0].doxygen is not None
        assert "brief" in functions[0].doxygen.tags

        assert functions[1].name == "Module_Process"
        assert functions[1].doxygen is not None
        assert "version" in functions[1].doxygen.tags

        assert functions[2].name == "Undocumented_Function"
        assert functions[2].doxygen is None

    def test_forward_declarations_skipped(self, fixtures_dir):
        content = (fixtures_dir / "forward_decl.c").read_text()
        functions = parse_functions(content, C_PATTERN, C_EXCLUDES, C_SETTINGS)

        # Only the definition should be found, not the forward declarations
        assert len(functions) == 1
        assert functions[0].name == "Module_Init"
        assert functions[0].doxygen is not None

    def test_forward_declarations_not_skipped(self, fixtures_dir):
        content = (fixtures_dir / "forward_decl.c").read_text()
        functions = parse_functions(
            content,
            C_PATTERN,
            C_EXCLUDES,
            C_SETTINGS,
            skip_forward_declarations=False,
        )

        # Forward declarations + definition
        assert len(functions) == 3

    def test_exclude_names(self):
        content = """\
/**
 * @brief Real function.
 * @version 1.0
 */
void Real_Func(void) {
    if (x) {
        return;
    }
}
"""
        functions = parse_functions(content, C_PATTERN, C_EXCLUDES, C_SETTINGS)
        names = [f.name for f in functions]
        assert "Real_Func" in names
        # 'if' and 'return' should not appear even though they might match the pattern
        assert "if" not in names
        assert "return" not in names

    def test_body_end_tracking(self):
        content = """\
/**
 * @brief Func A.
 * @version 1.0
 */
void Func_A(void) {
    if (x) {
        y();
    }
}

/**
 * @brief Func B.
 * @version 1.0
 */
void Func_B(void) {
    z();
}
"""
        functions = parse_functions(content, C_PATTERN, C_EXCLUDES, C_SETTINGS)
        assert len(functions) == 2
        assert functions[0].name == "Func_A"
        assert functions[0].body_end == 8  # closing brace of Func_A
        assert functions[1].name == "Func_B"
        assert functions[1].body_end == 16  # closing brace of Func_B

    def test_tags_parsed_correctly(self, fixtures_dir):
        content = (fixtures_dir / "simple.c").read_text()
        functions = parse_functions(content, C_PATTERN, C_EXCLUDES, C_SETTINGS)

        process_func = functions[1]
        assert process_func.name == "Module_Process"
        assert "emits" in process_func.doxygen.tags
        assert "handles" in process_func.doxygen.tags
        assert process_func.doxygen.tags["emits"] == ["EVENT:DATA_READY"]
        assert process_func.doxygen.tags["handles"] == ["EVENT:DATA_RECEIVED"]


class TestParseFunctionsCpp:
    """Tests for C++ function pattern matching."""

    def test_cpp_fixture_file(self, fixtures_dir):
        content = (fixtures_dir / "cpp_methods.cpp").read_text()
        functions = parse_functions(content, CPP_PATTERN, CPP_EXCLUDES, CPP_SETTINGS)
        names = [f.name for f in functions]
        assert "parse_name" in names
        assert "entropic_free" in names
        assert "contains" in names
        assert "getData" in names
        assert "simple_func" in names
        assert "Undocumented_Method" in names

    def test_namespaced_return_type(self, fixtures_dir):
        content = (fixtures_dir / "cpp_methods.cpp").read_text()
        functions = parse_functions(content, CPP_PATTERN, CPP_EXCLUDES, CPP_SETTINGS)
        by_name = {f.name: f for f in functions}
        assert by_name["parse_name"].doxygen is not None
        assert "brief" in by_name["parse_name"].doxygen.tags

    def test_extern_c_linkage(self, fixtures_dir):
        content = (fixtures_dir / "cpp_methods.cpp").read_text()
        functions = parse_functions(content, CPP_PATTERN, CPP_EXCLUDES, CPP_SETTINGS)
        by_name = {f.name: f for f in functions}
        assert "entropic_free" in by_name
        assert by_name["entropic_free"].doxygen is not None

    def test_class_qualified_method(self, fixtures_dir):
        content = (fixtures_dir / "cpp_methods.cpp").read_text()
        functions = parse_functions(content, CPP_PATTERN, CPP_EXCLUDES, CPP_SETTINGS)
        by_name = {f.name: f for f in functions}
        assert "contains" in by_name
        assert "getData" in by_name

    def test_undocumented_detected(self, fixtures_dir):
        content = (fixtures_dir / "cpp_methods.cpp").read_text()
        functions = parse_functions(content, CPP_PATTERN, CPP_EXCLUDES, CPP_SETTINGS)
        by_name = {f.name: f for f in functions}
        assert by_name["Undocumented_Method"].doxygen is None
