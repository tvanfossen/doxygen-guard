"""Tests for doxygen_guard.ts_parser module — tree-sitter based function extraction."""

from __future__ import annotations

from doxygen_guard.ts_parser import parse_functions_ts


class TestCFunctionDetection:
    """Tree-sitter C function extraction."""

    def test_basic_c_function(self):
        code = """\
/**
 * @brief Do something.
 * @version 1.0
 */
void my_func(void) {
    return;
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        assert funcs[0].name == "my_func"
        assert funcs[0].doxygen is not None
        assert "brief" in funcs[0].doxygen.tags

    def test_multiple_functions(self):
        code = """\
/**
 * @brief First.
 * @version 1.0
 */
int alpha(void) {
    return 0;
}

/**
 * @brief Second.
 * @version 1.0
 */
void beta(int x) {
    do_something(x);
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 2
        assert funcs[0].name == "alpha"
        assert funcs[1].name == "beta"

    def test_function_without_doxygen(self):
        code = """\
void no_doc(void) {
    return;
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        assert funcs[0].doxygen is None

    def test_forward_declaration_excluded(self):
        """Forward declarations (no body) should be excluded."""
        code = """\
void forward_decl(void);

/**
 * @brief Real function.
 * @version 1.0
 */
void real_func(void) {
    return;
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        assert funcs[0].name == "real_func"

    def test_exclude_names(self):
        code = """\
void if(void) { }
void real(void) { }
"""
        funcs = parse_functions_ts(code, "c", exclude_names=["if"])
        names = [f.name for f in funcs]
        assert "if" not in names

    def test_doxygen_tags_extracted(self):
        code = """\
/**
 * @brief Start pairing.
 * @version 1.0
 * @req REQ-0252
 * @emits EVENT_PAIRING_STARTED
 * @ext wifi_mgr::WiFi_ConnectAfterDelay
 * @triggers CLOUD_DISABLE
 */
void Pairing_Start(void) {
    disable_cloud();
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        tags = funcs[0].doxygen.tags
        assert tags.get("req") == ["REQ-0252"]
        assert tags.get("emits") == ["EVENT_PAIRING_STARTED"]
        assert tags.get("ext") == ["wifi_mgr::WiFi_ConnectAfterDelay"]
        assert tags.get("triggers") == ["CLOUD_DISABLE"]

    def test_body_end_line(self):
        code = """\
void func(void) {
    int x = 1;
    int y = 2;
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        assert funcs[0].def_line == 0
        assert funcs[0].body_end == 3

    def test_static_function(self):
        code = """\
/**
 * @brief Static helper.
 * @version 1.0
 */
static void helper(void) {
    return;
}
"""
        funcs = parse_functions_ts(code, "c")
        assert len(funcs) == 1
        assert funcs[0].name == "helper"


class TestPythonFunctionDetection:
    """Tree-sitter Python function extraction."""

    def test_basic_python_function(self):
        code = """\
## @brief Load config.
#  @version 1.0
#  @req REQ-CONFIG-001
def load_config(path=None):
    data = read_yaml(path)
    return deep_merge(DEFAULTS, data)
"""
        funcs = parse_functions_ts(code, "python", comment_start_pattern=r"^\s*##(?!#)")
        assert len(funcs) == 1
        assert funcs[0].name == "load_config"
        assert funcs[0].doxygen is not None
        assert funcs[0].doxygen.tags.get("req") == ["REQ-CONFIG-001"]

    def test_python_body_end(self):
        code = """\
def func():
    x = 1
    y = 2
    return x + y
"""
        funcs = parse_functions_ts(code, "python", comment_start_pattern=r"^\s*##(?!#)")
        assert len(funcs) == 1
        assert funcs[0].def_line == 0
        assert funcs[0].body_end == 3

    def test_python_no_doxygen(self):
        code = """\
def bare_func():
    pass
"""
        funcs = parse_functions_ts(code, "python", comment_start_pattern=r"^\s*##(?!#)")
        assert len(funcs) == 1
        assert funcs[0].doxygen is None

    def test_decorated_function(self):
        code = """\
## @brief A decorated func.
#  @version 1.0
#  @internal
@dataclass
def decorated():
    pass
"""
        funcs = parse_functions_ts(code, "python", comment_start_pattern=r"^\s*##(?!#)")
        assert len(funcs) == 1
        assert funcs[0].name == "decorated"
        assert funcs[0].doxygen is not None
        assert "internal" in funcs[0].doxygen.tags


class TestParityWithFixtures:
    """Parity checks: tree-sitter vs regex on trace fixtures."""

    def test_pairing_fixture(self):
        from tests.conftest import FIXTURES_DIR

        content = (FIXTURES_DIR / "trace" / "pairing_mgr" / "pairing.c").read_text()
        funcs = parse_functions_ts(content, "c")
        names = [f.name for f in funcs]
        assert "Pairing_Start" in names
        assert "ContinuePairing" in names

        pairing = next(f for f in funcs if f.name == "Pairing_Start")
        assert pairing.doxygen is not None
        assert pairing.doxygen.tags.get("req") == ["REQ-0252"]
        assert pairing.doxygen.tags.get("emits") == ["EVENT_PAIRING_STARTED"]

    def test_wifi_fixture(self):
        from tests.conftest import FIXTURES_DIR

        content = (FIXTURES_DIR / "trace" / "wifi_mgr" / "wifi.c").read_text()
        funcs = parse_functions_ts(content, "c")
        names = [f.name for f in funcs]
        assert "WiFi_ConnectAfterDelay" in names

    def test_cloud_fixture(self):
        from tests.conftest import FIXTURES_DIR

        content = (FIXTURES_DIR / "trace" / "cloud_mgr" / "cloud.c").read_text()
        funcs = parse_functions_ts(content, "c")
        names = [f.name for f in funcs]
        assert "startMqttConnection" in names
