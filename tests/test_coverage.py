"""Tests for doxygen_guard.coverage module."""

from doxygen_guard.coverage import (
    _collect_req_ids,
    _collect_supports_only,
    _collect_unmapped_functions,
    format_coverage_json,
    format_coverage_markdown,
    format_coverage_text,
)
from doxygen_guard.tracer_models import TaggedFunction


class TestCollectReqIds:
    def test_basic(self):
        funcs = [
            TaggedFunction(name="a", file_path="a.c", reqs=["REQ-001", "REQ-002"]),
            TaggedFunction(name="b", file_path="b.c", reqs=["REQ-002", "REQ-003"]),
        ]
        assert _collect_req_ids(funcs) == {"REQ-001", "REQ-002", "REQ-003"}

    def test_empty(self):
        assert _collect_req_ids([]) == set()


class TestCollectSupportsOnly:
    def test_supports_without_req(self):
        funcs = [
            TaggedFunction(name="a", file_path="a.c", reqs=["REQ-001"]),
            TaggedFunction(name="b", file_path="b.c", supports=["REQ-002"]),
        ]
        assert _collect_supports_only(funcs) == {"REQ-002"}

    def test_supports_with_req(self):
        funcs = [
            TaggedFunction(name="a", file_path="a.c", reqs=["REQ-001"], supports=["REQ-001"]),
        ]
        assert _collect_supports_only(funcs) == set()


class TestCollectUnmapped:
    def test_no_reqs(self):
        funcs = [
            TaggedFunction(name="a", file_path="a.c", reqs=["REQ-001"]),
            TaggedFunction(name="b", file_path="b.c"),
        ]
        assert _collect_unmapped_functions(funcs) == {"b"}


class TestFormatters:
    REPORT = {
        "total_requirements": 3,
        "covered": ["REQ-001"],
        "uncovered": ["REQ-002"],
        "supports_only": [],
        "orphan_refs": ["REQ-999"],
        "unmapped_functions": ["helper"],
    }

    def test_text_format(self):
        result = format_coverage_text(self.REPORT)
        assert "1/3" in result
        assert "REQ-002" in result
        assert "REQ-999" in result

    def test_json_format(self):
        result = format_coverage_json(self.REPORT)
        assert '"total_requirements": 3' in result

    def test_markdown_format(self):
        result = format_coverage_markdown(self.REPORT)
        assert "# Requirements Coverage" in result
        assert "REQ-002" in result
