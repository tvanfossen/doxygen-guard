"""Tests for doxygen_guard.ast_walker module."""

from __future__ import annotations

import tree_sitter_c
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
            emits=["EVENT:RESULT"],
            ext=["comm::Comm_SendStatus"],
        )
        handler = TaggedFunction(
            name="handler",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT:RESULT"],
        )
        ctx = _make_ctx(
            handler_map={"EVENT:RESULT": [handler]},
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
            emits=["EVENT:X"],
            triggers=["STATE_CHANGE"],
        )
        ctx = _make_ctx(
            handler_map={
                "EVENT:X": [
                    TaggedFunction(
                        name="h", file_path="b.c", participant_name="B", handles=["EVENT:X"]
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
            emits=["EVENT:X"],
        )
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT:X"]
        )
        ctx = _make_ctx(handler_map={"EVENT:X": [handler]})
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
            emits=["EVENT:CHUNK"],
        )
        handler = TaggedFunction(
            name="h", file_path="b.c", participant_name="B", handles=["EVENT:CHUNK"]
        )
        ctx = _make_ctx(handler_map={"EVENT:CHUNK": [handler]})
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
            handles=["EVENT:X"],
            ext=["comm::Comm_SendStatus"],
        )
        caller_tf = TaggedFunction(
            name="caller",
            file_path="a.c",
            participant_name="A",
            emits=["EVENT:X"],
        )
        file_cache = {
            "b.c": ParsedFile(tree=handler_tree, func_nodes={"handler": handler_node}),
        }
        ctx = _make_ctx(
            handler_map={"EVENT:X": [handler_tf]},
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
            emits=["EVENT:X"],
        )
        handler = TaggedFunction(
            name="handler",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT:X"],
            emits=["EVENT:Y"],
        )
        ctx = _make_ctx(
            handler_map={"EVENT:X": [handler]},
            max_depth=0,
        )
        edges = walk_function_body(node, tf, ctx, depth=0)
        # Should have emit edge but no chain following (depth=0 means no recursion)
        emit_edges = [e for e in edges if e.kind == "emit"]
        assert len(emit_edges) == 1
