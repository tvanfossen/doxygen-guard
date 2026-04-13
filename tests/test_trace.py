"""Tests for doxygen_guard.tracer module."""

from __future__ import annotations

from doxygen_guard.config import CONFIG_DEFAULTS, deep_merge
from doxygen_guard.tracer import run_trace
from doxygen_guard.tracer.collector import (
    _apply_emit_inference,
    collect_all_tagged_functions,
    detect_phantom_emits,
)
from doxygen_guard.tracer.edges_behavioral import (
    _collect_after,
    _infer_entry_edges,
    _toposort_emitters,
)
from doxygen_guard.tracer.renderer import (
    _sanitize_label,
    generate_plantuml,
    write_diagram,
)
from doxygen_guard.tracer_models import (
    DiagramContext,
    Edge,
    Participant,
    TaggedFunction,
)
from tests.conftest import FIXTURES_DIR

TRACE_CONFIG = deep_merge(
    CONFIG_DEFAULTS,
    {
        "trace": {
            "participant_field": "Subsystem",
            "external": [
                {"External System": {"receives_prefix": ["EXT:"]}},
            ],
            "options": {
                "event_emit_functions": ["event_post"],
            },
        },
        "impact": {
            "requirements": {
                "file": str(FIXTURES_DIR / "trace" / "requirements.csv"),
                "id_column": "Req ID",
                "name_column": "Name",
                "format": "csv",
            },
        },
    },
)


class TestCollectAllTaggedFunctions:
    """Tests for collect_all_tagged_functions."""

    def test_collects_from_fixture_dir(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged, participants, _cache = collect_all_tagged_functions([source_dir], TRACE_CONFIG)
        assert len(tagged) > 0

        names = [tf.name for tf in tagged]
        assert "Pairing_Start" in names
        assert "ContinuePairing" in names

    def test_participant_resolved_from_requirements(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged, _participants, _cache = collect_all_tagged_functions([source_dir], TRACE_CONFIG)

        pairing_funcs = [tf for tf in tagged if tf.name == "Pairing_Start"]
        assert len(pairing_funcs) == 1
        assert pairing_funcs[0].participant_name == "Pairing Manager"


class TestToposortEmitters:
    """Tests for _toposort_emitters causal ordering."""

    def test_chain_ordering(self):
        """A emits X, B handles X and emits Y, C handles Y → A, B, C."""
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"])
        b = TaggedFunction(name="B", file_path="b.c", receives=["EVENT_X"], sends=["EVENT_Y"])
        c = TaggedFunction(name="C", file_path="c.c", receives=["EVENT_Y"])
        result = _toposort_emitters([c, b, a])
        names = [tf.name for tf in result]
        assert names == ["A", "B", "C"]

    def test_single_emitter_unchanged(self):
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"])
        result = _toposort_emitters([a])
        assert [tf.name for tf in result] == ["A"]

    def test_independent_emitters_preserve_order(self):
        """Emitters with no causal link stay in original order."""
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"])
        b = TaggedFunction(name="B", file_path="b.c", sends=["EVENT_Y"])
        result = _toposort_emitters([a, b])
        assert [tf.name for tf in result] == ["A", "B"]

    def test_cycle_does_not_hang(self):
        """Mutual emit/handle cycle terminates and includes both."""
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"], receives=["EVENT_Y"])
        b = TaggedFunction(name="B", file_path="b.c", sends=["EVENT_Y"], receives=["EVENT_X"])
        result = _toposort_emitters([a, b])
        names = [tf.name for tf in result]
        assert set(names) == {"A", "B"}
        assert len(names) == 2

    def test_empty_list(self):
        assert _toposort_emitters([]) == []


class TestGeneratePlantuml:
    """Tests for generate_plantuml."""

    def test_basic_output(self):
        participants = [
            Participant(name="Pairing Manager"),
            Participant(name="WiFi Manager"),
        ]
        edges = [Edge("Pairing Manager", "WiFi Manager", "Connect()", "EVENT_START", "-->")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert "@startuml REQ-0001" in result
        assert "@enduml" in result
        assert "autonumber" in result
        assert "Pairing_Manager" in result
        assert "WiFi_Manager" in result
        assert 'box "System"' in result
        assert "end box" in result

    def test_external_rendered_as_entity(self):
        participants = [
            Participant(name="Sender"),
            Participant(name="Cloud", receives_prefix=["MQTT:"]),
        ]
        edges = [Edge("Sender", "Cloud", "publish()", "MQTT:update", "-->")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert 'entity "Cloud"' in result
        assert 'participant "Sender"' in result

    def test_internal_in_box_external_outside(self):
        participants = [
            Participant(name="OTA"),
            Participant(name="Cloud", receives_prefix=["EVENT_CLOUD_"]),
        ]
        edges = [Edge("OTA", "Cloud", "report()", "EVENT_CLOUD_RESULT", "-->")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        lines = result.split("\n")
        entity_line = next(i for i, line in enumerate(lines) if "entity" in line)
        box_line = next(i for i, line in enumerate(lines) if "box" in line)
        assert entity_line < box_line

    def test_custom_box_label(self):
        participants = [Participant(name="A")]
        edges = [Edge("A", "A", "x()")]
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"box_label": "IoT Device"}}})
        result = generate_plantuml("REQ-0001", edges, [], participants, config)
        assert 'box "IoT Device"' in result

    def test_no_box_when_only_externals(self):
        participants = [
            Participant(name="Cloud", receives_prefix=["EVENT_CLOUD_"]),
        ]
        edges = [Edge("Cloud", "Cloud", "self()")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert "box" not in result
        assert 'entity "Cloud"' in result

    def test_with_req_name(self):
        ctx = DiagramContext(req_row={"Name": "BLE Pairing"})
        result = generate_plantuml("REQ-0252", [], [], [], TRACE_CONFIG, context=ctx)
        assert "@startuml REQ-0252_BLE_Pairing" in result

    def test_note_rendering(self):
        participants = [Participant(name="Pairing")]
        edges = [Edge("Pairing", "Pairing", "DISABLE_CLOUD", style="note")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert "note right of Pairing: Disable Cloud" in result

    def test_no_autonumber_when_disabled(self):
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"autonumber": False}}})
        result = generate_plantuml("REQ-0001", [], [], [], config)
        assert "autonumber" not in result

    def test_external_participant_renders_outside_box(self):
        """Participant declared with receives_prefix renders as entity outside box."""
        participants = [
            Participant(name="Handler"),
            Participant(name="External", receives_prefix=["EVENT_"]),
        ]
        edges = [Edge("External", "Handler", "EVENT_BOOT_REQ", style="->")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert 'entity "External"' in result

    def test_undeclared_participant_renders_inside_box(self):
        """Participant used in edges but with no receives_prefix renders inside box."""
        participants = [Participant(name="Handler")]
        edges = [Edge("Internal_Helper", "Handler", "EVENT_X", style="->")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert 'participant "Internal_Helper"' in result
        # Should NOT be rendered as entity (which would put it outside the box)
        assert 'entity "Internal_Helper"' not in result


class TestInferEntryEdges:
    """Tests for _infer_entry_edges (behavioral — no fallback participant)."""

    def test_unresolvable_event_omitted(self):
        """Events with no matching participant are omitted, not fallback."""
        handler = TaggedFunction(
            name="handler", file_path="a.c", participant_name="A", receives=["EVENT_X"]
        )
        entries = _infer_entry_edges([handler], [handler], [], {})
        assert len(entries) == 0

    def test_prefix_match_resolves_source(self):
        handler = TaggedFunction(
            name="handler", file_path="a.c", participant_name="A", receives=["MQTT:UPDATE"]
        )
        cloud = Participant(name="Cloud", receives_prefix=["MQTT:"])
        entries = _infer_entry_edges([handler], [handler], [cloud], {})
        assert len(entries) == 1
        assert entries[0].from_name == "Cloud"

    def test_emitter_participant_used_when_outside_scope(self):
        """When emitter is not in REQ scope, use its participant as source."""
        emitter = TaggedFunction(
            name="sender", file_path="b.c", participant_name="Sender", sends=["EVENT_X"]
        )
        handler = TaggedFunction(
            name="handler", file_path="a.c", participant_name="A", receives=["EVENT_X"]
        )
        entries = _infer_entry_edges([handler], [emitter, handler], [], {})
        assert len(entries) == 1
        assert entries[0].from_name == "Sender"


class TestSecurityHardening:
    """Tests for path traversal prevention and label sanitization."""

    def test_path_traversal_rejected(self):
        import pytest

        puml = "@startuml\n@enduml"
        with pytest.raises(ValueError, match="traversal"):
            write_diagram("REQ-0001", puml, "../../etc/output")

    def test_sanitize_label_semicolon(self):
        assert ";" not in _sanitize_label("foo; bar")

    def test_sanitize_label_backtick(self):
        assert "`" not in _sanitize_label("foo `code` bar")

    def test_sanitize_label_angle_brackets(self):
        result = _sanitize_label("foo <b>bar</b>")
        assert "<" not in result
        assert ">" not in result

    def test_sanitize_label_preserves_normal_text(self):
        assert _sanitize_label("EVENT_BOOT_REQ") == "EVENT_BOOT_REQ"


class TestWriteDiagram:
    """Tests for write_diagram."""

    def test_creates_file(self, tmp_path):
        puml = "@startuml test\n@enduml"
        result = write_diagram("REQ-0001", puml, str(tmp_path / "out"))
        assert result.exists()
        assert result.name == "REQ-0001.puml"
        assert result.read_text() == puml

    def test_creates_directories(self, tmp_path):
        puml = "@startuml\n@enduml"
        out_dir = str(tmp_path / "deep" / "nested" / "dir")
        result = write_diagram("REQ-0001", puml, out_dir)
        assert result.exists()


class TestRunTrace:
    """Integration tests for run_trace."""

    def _trace_config_with_tmp(self, tmp_path):
        return deep_merge(TRACE_CONFIG, {"output_dir": str(tmp_path / "out")})

    def test_single_req(self, tmp_path):
        config = self._trace_config_with_tmp(tmp_path)
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, req_id="REQ-0252")
        assert len(written) == 1
        assert written[0].exists()
        content = written[0].read_text()
        assert "@startuml REQ-0252" in content
        assert "@enduml" in content

    def test_trace_all(self, tmp_path):
        config = self._trace_config_with_tmp(tmp_path)
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, trace_all=True)
        assert len(written) >= 1

    def test_no_req_no_all_returns_empty(self):
        written, _warnings = run_trace([], TRACE_CONFIG)
        assert written == []

    def test_nonexistent_req_returns_empty(self, tmp_path):
        config = self._trace_config_with_tmp(tmp_path)
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, req_id="REQ-9999")
        assert written == []


class TestSupportsAndAssumes:
    """Tests for Phase 2: @supports and @assumes tag handling."""

    def test_tagged_function_has_after_field(self):
        """TaggedFunction stores after (precondition) field."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            after=["REQ-003"],
        )
        assert tf.after == ["REQ-003"]

    def test_collect_after_deduplicates(self):
        """_collect_after returns unique values preserving order."""
        funcs = [
            TaggedFunction(name="a", file_path="a.c", after=["REQ-001", "REQ-002"]),
            TaggedFunction(name="b", file_path="b.c", after=["REQ-002", "REQ-003"]),
        ]
        result = _collect_after(funcs)
        assert result == ["REQ-001", "REQ-002", "REQ-003"]

    def test_assumes_rendered_in_header(self):
        """@assumes produces Preconditions line in diagram header."""
        participants = [Participant(name="OTA")]
        edges = [Edge("OTA", "OTA", "check()")]
        ctx = DiagramContext(
            req_row={"Name": "OTA Updates", "Description": "Firmware OTA"},
            preconditions=["REQ-PAIR-001 (Device Pairing)"],
        )
        result = generate_plantuml(
            "REQ-OTA-001", edges, [], participants, TRACE_CONFIG, context=ctx
        )
        assert "Preconditions" in result
        assert "REQ-PAIR-001" in result
        assert "Device Pairing" in result

    def test_assumes_without_name(self):
        """@assumes renders REQ ID even without name lookup."""
        ctx = DiagramContext(
            req_row={"Name": "OTA", "Description": "FW updates"},
            preconditions=["REQ-PAIR-001"],
        )
        result = generate_plantuml("REQ-OTA-001", [], [], [], TRACE_CONFIG, context=ctx)
        assert "Preconditions" in result
        assert "REQ-PAIR-001" in result


class TestToposortEdgeOrder:
    """Verify causal ordering for product diagrams."""

    def test_chain_order_matches_causal(self):
        """REQ-PROD style: A emits X, B handles X — A edges come first."""
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"])
        b = TaggedFunction(name="B", file_path="b.c", receives=["EVENT_X"], sends=["EVENT_Y"])
        c = TaggedFunction(name="C", file_path="c.c", receives=["EVENT_Y"])
        result = _toposort_emitters([c, b, a])
        assert [tf.name for tf in result] == ["A", "B", "C"]

    def test_single_emitter_unchanged(self):
        """Single emitter is not reordered."""
        a = TaggedFunction(name="A", file_path="a.c", sends=["EVENT_X"])
        result = _toposort_emitters([a])
        assert result == [a]


class TestEmitInference:
    """Tests for @emits inference from function body."""

    def test_infer_emits_from_event_post(self):
        """event_post(EVENT_FOO) with no @emits → inferred."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            body="void func() { event_post(EVENT_SENSOR_READY, 0); }",
        )
        _apply_emit_inference(tf, tf.body, TRACE_CONFIG)
        assert "EVENT_SENSOR_READY" in tf.sends

    def test_declared_emits_not_duplicated(self):
        """Both @emits and event_post() → no duplicate."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            sends=["EVENT_SENSOR_READY"],
            body="void func() { event_post(EVENT_SENSOR_READY, 0); }",
        )
        _apply_emit_inference(tf, tf.body, TRACE_CONFIG)
        assert tf.sends.count("EVENT_SENSOR_READY") == 1

    def test_variable_arg_no_inference(self):
        """event_post(variable) where variable doesn't match pattern → no inference."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            body="void func() { event_post(my_event, 0); }",
        )
        _apply_emit_inference(tf, tf.body, TRACE_CONFIG)
        assert len(tf.sends) == 0

    def test_event_name_pattern_rejects_invalid(self, caplog):
        """Constant failing event_name_pattern is rejected."""
        import logging

        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            body="void func() { event_post(event_lower, 0); }",
        )
        with caplog.at_level(logging.WARNING):
            _apply_emit_inference(tf, tf.body, TRACE_CONFIG)
        assert len(tf.sends) == 0

    def test_infer_emits_disabled(self):
        """infer_emits: false prevents inference."""
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"infer_sends": False}}})
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            body="void func() { event_post(EVENT_X, 0); }",
        )
        _apply_emit_inference(tf, tf.body, config)
        assert len(tf.sends) == 0


class TestPhantomEmits:
    """Tests for phantom @emits detection."""

    def test_phantom_emits_warns(self, caplog):
        """@emits EVENT:X with no matching event_post → warning."""
        import logging

        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            sends=["EVENT_PHANTOM"],
            body="void func() { do_something(); }",
        )
        with caplog.at_level(logging.WARNING):
            phantoms = detect_phantom_emits(tf, TRACE_CONFIG)
        assert "EVENT_PHANTOM" in phantoms
        assert any("phantom" in r.message.lower() for r in caplog.records)

    def test_no_phantom_when_call_exists(self):
        """@emits EVENT:X with matching event_post → no phantom."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            sends=["EVENT_SENSOR_READY"],
            body="void func() { event_post(EVENT_SENSOR_READY, 0); }",
        )
        phantoms = detect_phantom_emits(tf, TRACE_CONFIG)
        assert len(phantoms) == 0
