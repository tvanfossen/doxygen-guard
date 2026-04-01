"""Tests for doxygen_guard.ast_walker module."""

from __future__ import annotations

import tree_sitter_c
import tree_sitter_cpp
import tree_sitter_python
from tree_sitter import Language, Parser

from doxygen_guard.ast_walker import WalkContext, walk_function_body
from doxygen_guard.tracer import TaggedFunction
from doxygen_guard.ts_languages import get_language_spec

C_LANG = Language(tree_sitter_c.language())
C_PARSER = Parser(C_LANG)
C_SPEC = get_language_spec("c")


def _parse_func_node(code: str):
    """Parse C code and return the first function_definition node."""
    tree = C_PARSER.parse(code.encode("utf-8"))
    for child in tree.root_node.children:
        if child.type == "function_definition":
            return child
    msg = "No function_definition found"
    raise ValueError(msg)


def _make_ctx(
    handler_map=None,
    all_tagged=None,
    externals=None,
    emit_functions=None,
    req_id=None,
    file_cache=None,
    max_depth=3,
):
    return WalkContext(
        handler_map=handler_map or {},
        all_tagged=all_tagged or [],
        externals=externals or [],
        emit_functions=emit_functions or {"event_post"},
        spec=C_SPEC,
        req_id=req_id,
        max_depth=max_depth,
        file_cache=file_cache,
    )


class TestBasicEdgeOrdering:
    """Edges produced in source statement order."""

    def test_ext_before_emit(self):
        """ext call at line 2 should appear before emit at line 3."""
        code = """\
void func(void) {
    Comm_SendStatus(&status);
    event_post(EVENT_RESULT, 0);
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_RESULT"],
            ext=["comm::Comm_SendStatus"],
        )
        handler = TaggedFunction(
            name="handler",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT_RESULT"],
        )
        ctx = _make_ctx(
            handler_map={"EVENT_RESULT": [handler]},
            all_tagged=[tf, handler],
        )
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert kinds.index("ext") < kinds.index("emit")

    def test_call_edge_detected(self):
        """Direct call to a tagged function produces a call edge."""
        code = """\
void caller(void) {
    helper();
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="caller", file_path="a.c", participant_name="A")
        helper = TaggedFunction(
            name="helper", file_path="b.c", participant_name="B", reqs=["REQ-1"]
        )
        ctx = _make_ctx(all_tagged=[tf, helper], req_id="REQ-1")
        edges = walk_function_body(node, tf, ctx)
        assert any(e.kind == "call" and "helper()" in e.edge.label for e in edges)

    def test_trigger_before_edges(self):
        """Triggers appear before call/emit edges."""
        code = """\
void func(void) {
    event_post(EVENT_X, 0);
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_X"],
            triggers=["STATE_CHANGE"],
        )
        ctx = _make_ctx(
            handler_map={
                "EVENT_X": [
                    TaggedFunction(
                        name="h", file_path="b.c", participant_name="B", handles=["EVENT_X"]
                    ),
                ]
            }
        )
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert kinds[0] == "trigger"

    def test_emit_fallback_when_no_emit_call(self):
        """Emits placed at end if no emit function call found in body."""
        code = """\
void func(void) {
    do_something();
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_X"],
        )
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        emit_edges = [e for e in edges if e.kind == "emit"]
        assert len(emit_edges) == 1


class TestControlFlow:
    """Control flow block detection."""

    def test_while_loop_with_emit(self):
        """While loop containing emit call produces loop markers."""
        code = """\
void func(void) {
    while (has_data()) {
        event_post(EVENT_CHUNK, 0);
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_CHUNK"],
        )
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_CHUNK"]
        )
        ctx = _make_ctx(handler_map={"EVENT_CHUNK": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "loop_start" in kinds
        assert "loop_end" in kinds
        assert kinds.index("loop_start") < kinds.index("emit")
        assert kinds.index("emit") < kinds.index("loop_end")

    def test_empty_loop_invisible(self):
        """Loop without tagged calls produces no markers."""
        code = """\
void func(void) {
    while (processing()) {
        update_counter();
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A")
        ctx = _make_ctx()
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "loop_start" not in kinds

    def test_if_with_tagged_call(self):
        """If statement containing tagged call produces alt markers."""
        code = """\
void func(void) {
    if (valid) {
        Comm_SendStatus(&status);
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            ext=["comm::Comm_SendStatus"],
        )
        ctx = _make_ctx(all_tagged=[tf])
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "alt_start" in kinds
        assert "alt_end" in kinds


class TestExpandedControlFlow:
    """Tests for try/catch, switch, throw, goto control flow."""

    def test_switch_with_emit_in_case(self):
        """Switch with event_post in a case produces switch markers."""
        code = """\
void func(void) {
    switch (state) {
        case 0:
            event_post(EVENT_A, 0);
            break;
        case 1:
            break;
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A", emits=["EVENT_A"])
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_A"]
        )
        ctx = _make_ctx(handler_map={"EVENT_A": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "switch_start" in kinds
        assert "switch_end" in kinds

    def test_goto_produces_note(self):
        """goto statement produces a goto_note edge."""
        code = """\
void func(void) {
    goto error;
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A")
        ctx = _make_ctx()
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "goto_note" in kinds
        assert any("goto" in e.label for e in edges if e.kind == "goto_note")

    def test_empty_switch_invisible(self):
        """Switch with no tagged content in any case is not rendered."""
        code = """\
void func(void) {
    switch (mode) {
        case 0:
            x = 1;
            break;
        case 1:
            x = 2;
            break;
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A")
        ctx = _make_ctx()
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "switch_start" not in kinds


class TestTryCatchControlFlow:
    """Tests for try/catch/finally control flow handling."""

    def test_try_catch_with_tagged_call(self):
        """C++ try/catch with tagged call produces try/catch markers."""
        code = """\
void func(void) {
    if (ready) {
        event_post(EVENT_X, 0);
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_X"]
        )
        # C doesn't have try — test switch instead since C try isn't available
        ctx = _make_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "alt_start" in kinds

    def test_goto_note_includes_label(self):
        """goto produces note with label name."""
        code = """\
void func(void) {
    goto cleanup;
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A")
        ctx = _make_ctx()
        edges = walk_function_body(node, tf, ctx)
        goto_edges = [e for e in edges if e.kind == "goto_note"]
        assert len(goto_edges) == 1
        assert "goto" in goto_edges[0].label

    def test_empty_switch_no_tagged_content_pruned(self):
        """Switch with no tagged calls in any case is suppressed."""
        code = """\
void func(void) {
    switch (state) {
        case 0: x = 1; break;
        case 1: x = 2; break;
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A")
        ctx = _make_ctx()
        edges = walk_function_body(node, tf, ctx)
        assert not any(e.kind == "switch_start" for e in edges)


class TestAltNoElseWarning:
    """Tests for alt-without-else warning."""

    def test_alt_no_else_warns(self, caplog):
        """Alt block with no else emits warning."""
        import logging

        code = """\
void func(void) {
    if (valid) {
        event_post(EVENT_X, 0);
    }
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_ctx(handler_map={"EVENT_X": [handler]}, req_id="REQ-001")
        with caplog.at_level(logging.WARNING):
            walk_function_body(node, tf, ctx)
        assert any("no else" in r.message for r in caplog.records)
        assert any("REQ-001" in r.message for r in caplog.records)


class TestEmitCallSiteMatching:
    """Tests for set-based emit matching at call sites."""

    def test_emit_placed_at_call_site(self):
        """Emit edge appears at the event_post() call location."""
        code = """\
void func(void) {
    prepare();
    event_post(EVENT_X, 0);
    cleanup();
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.c", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        emit_edges = [e for e in edges if e.kind == "emit"]
        assert len(emit_edges) == 1


class TestHandlerChainFollowing:
    """Handler chain following with depth limiting."""

    def test_follows_handler_body(self):
        """Emit to handler produces handler's edges after the emit."""
        handler_code = """\
void handler(void) {
    Comm_SendStatus(&status);
}
"""
        handler_tree = C_PARSER.parse(handler_code.encode("utf-8"))
        handler_node = handler_tree.root_node.children[0]

        caller_code = """\
void caller(void) {
    event_post(EVENT_X, 0);
}
"""
        caller_node = _parse_func_node(caller_code)

        from doxygen_guard.ts_parser import ParsedFile

        handler_tf = TaggedFunction(
            name="handler",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT_X"],
            ext=["comm::Comm_SendStatus"],
        )
        caller_tf = TaggedFunction(
            name="caller",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_X"],
        )
        file_cache = {
            "b.c": ParsedFile(tree=handler_tree, func_nodes={"handler": handler_node}),
        }
        ctx = _make_ctx(
            handler_map={"EVENT_X": [handler_tf]},
            all_tagged=[caller_tf, handler_tf],
            file_cache=file_cache,
        )
        edges = walk_function_body(caller_node, caller_tf, ctx)
        kinds = [e.kind for e in edges]
        assert "emit" in kinds
        assert "ext" in kinds
        assert kinds.index("emit") < kinds.index("ext")

    def test_depth_limit_stops_recursion(self):
        """Chain following stops at max_depth."""
        code = """\
void func(void) {
    event_post(EVENT_X, 0);
}
"""
        node = _parse_func_node(code)
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT_X"],
        )
        handler = TaggedFunction(
            name="handler",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT_X"],
            emits=["EVENT_Y"],
        )
        ctx = _make_ctx(
            handler_map={"EVENT_X": [handler]},
            max_depth=0,
        )
        edges = walk_function_body(node, tf, ctx, depth=0)
        # Should have emit edge but no chain following (depth=0 means no recursion)
        emit_edges = [e for e in edges if e.kind == "emit"]
        assert len(emit_edges) == 1


# ---- C++/Python parser infrastructure ----

CPP_LANG = Language(tree_sitter_cpp.language())
CPP_PARSER = Parser(CPP_LANG)
CPP_SPEC = get_language_spec("cpp")

PY_LANG = Language(tree_sitter_python.language())
PY_PARSER = Parser(PY_LANG)
PY_SPEC = get_language_spec("python")


def _parse_cpp_func_node(code: str):
    """Parse C++ code and return the first function_definition node."""
    tree = CPP_PARSER.parse(code.encode("utf-8"))
    for child in tree.root_node.children:
        if child.type == "function_definition":
            return child
    msg = "No function_definition found in C++ code"
    raise ValueError(msg)


def _parse_python_func_node(code: str):
    """Parse Python code and return the first function_definition node."""
    tree = PY_PARSER.parse(code.encode("utf-8"))
    for child in tree.root_node.children:
        if child.type == "function_definition":
            return child
    msg = "No function_definition found in Python code"
    raise ValueError(msg)


def _make_cpp_ctx(**kwargs):
    defaults = {
        "handler_map": {},
        "all_tagged": [],
        "externals": [],
        "emit_functions": {"event_post"},
        "spec": CPP_SPEC,
    }
    defaults.update(kwargs)
    return WalkContext(**defaults)


def _make_python_ctx(**kwargs):
    defaults = {
        "handler_map": {},
        "all_tagged": [],
        "externals": [],
        "emit_functions": {"event_post"},
        "spec": PY_SPEC,
    }
    defaults.update(kwargs)
    return WalkContext(**defaults)


class TestCppTryCatch:
    """C++ try/catch control flow via tree-sitter-cpp."""

    def test_try_catch_with_emit(self):
        """C++ try/catch containing emit produces try/catch markers."""
        code = """\
void func() {
    try {
        event_post(EVENT_X, 0);
    } catch (std::exception& e) {
        handle_error();
    }
}
"""
        node = _parse_cpp_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.cpp", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.cpp", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_cpp_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "try_start" in kinds
        assert "try_end" in kinds
        assert "catch_start" in kinds
        assert "catch_end" in kinds

    def test_empty_try_catch_no_tagged_content_still_renders_catch(self):
        """Catch block with no tagged calls STILL renders (error blocks never pruned)."""
        code = """\
void func() {
    try {
        event_post(EVENT_X, 0);
    } catch (int e) {
        log_error();
    }
}
"""
        node = _parse_cpp_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.cpp", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.cpp", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_cpp_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        # catch block rendered even though log_error() is not a tagged call
        assert "catch_start" in kinds

    def test_throw_produces_note(self):
        """C++ throw statement produces a throw_note edge."""
        code = """\
void func() {
    throw std::runtime_error("fail");
}
"""
        node = _parse_cpp_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.cpp", participant_name="A")
        ctx = _make_cpp_ctx()
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "throw_note" in kinds
        throw_edges = [e for e in edges if e.kind == "throw_note"]
        assert any("throws" in e.label for e in throw_edges)


class TestPythonControlFlow:
    """Python control flow via tree-sitter-python."""

    def test_try_except(self):
        """Python try/except produces try/catch markers."""
        code = """\
def func():
    try:
        event_post(EVENT_X, 0)
    except ValueError:
        handle_error()
"""
        node = _parse_python_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.py", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.py", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_python_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "try_start" in kinds
        assert "catch_start" in kinds

    def test_raise_produces_note(self):
        """Python raise produces throw_note edge."""
        code = """\
def func():
    raise ValueError("bad input")
"""
        node = _parse_python_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.py", participant_name="A")
        ctx = _make_python_ctx()
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "throw_note" in kinds

    def test_with_statement_group(self):
        """Python with statement containing tagged call produces group markers."""
        code = """\
def func():
    with open("f") as fh:
        event_post(EVENT_X, 0)
"""
        node = _parse_python_func_node(code)
        tf = TaggedFunction(name="func", file_path="a.py", participant_name="A", emits=["EVENT_X"])
        handler = TaggedFunction(
            name="h", file_path="b.py", participant_name="B", handles=["EVENT_X"]
        )
        ctx = _make_python_ctx(handler_map={"EVENT_X": [handler]})
        edges = walk_function_body(node, tf, ctx)
        kinds = [e.kind for e in edges]
        assert "group_start" in kinds
        assert "group_end" in kinds
