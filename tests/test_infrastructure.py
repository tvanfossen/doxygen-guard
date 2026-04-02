"""Tests for doxygen_guard.tracer.infrastructure module."""

from doxygen_guard.tracer.infrastructure import (
    generate_infrastructure_table,
    write_infrastructure_table,
)
from doxygen_guard.tracer_models import TaggedFunction


class TestGenerateInfrastructureTable:
    def test_basic(self):
        funcs = [
            TaggedFunction(name="helper_a", file_path="module_a.c", supports=["REQ-001"]),
            TaggedFunction(
                name="helper_b", file_path="module_b.c", supports=["REQ-001", "REQ-002"]
            ),
        ]
        result = generate_infrastructure_table(funcs)
        assert "## Infrastructure Overview" in result
        assert "helper_a" in result
        assert "helper_b" in result
        assert "REQ-001, REQ-002" in result

    def test_empty_when_no_supports(self):
        funcs = [TaggedFunction(name="a", file_path="a.c", reqs=["REQ-001"])]
        assert generate_infrastructure_table(funcs) == ""

    def test_sorted_by_module_then_name(self):
        funcs = [
            TaggedFunction(name="z_func", file_path="b_mod.c", supports=["REQ-001"]),
            TaggedFunction(name="a_func", file_path="a_mod.c", supports=["REQ-002"]),
        ]
        result = generate_infrastructure_table(funcs)
        lines = result.strip().splitlines()
        data_lines = [
            row
            for row in lines
            if row.startswith("| ") and "Function" not in row and "---" not in row
        ]
        assert "a_func" in data_lines[0]
        assert "z_func" in data_lines[1]


class TestWriteInfrastructureTable:
    def test_writes_file(self, tmp_path):
        funcs = [TaggedFunction(name="h", file_path="m.c", supports=["REQ-001"])]
        result = write_infrastructure_table(funcs, str(tmp_path))
        assert result is not None
        assert result.exists()
        assert "## Infrastructure Overview" in result.read_text()

    def test_returns_none_when_empty(self, tmp_path):
        funcs = [TaggedFunction(name="a", file_path="a.c")]
        assert write_infrastructure_table(funcs, str(tmp_path)) is None
