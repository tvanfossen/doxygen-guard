"""Tests for doxygen_guard.tracer module."""

from __future__ import annotations

from pathlib import Path

from doxygen_guard.config import CONFIG_DEFAULTS, deep_merge
from doxygen_guard.tracer import (
    Participant,
    TaggedFunction,
    build_sequence_edges,
    collect_all_tagged_functions,
    generate_plantuml,
    run_trace,
    write_diagram,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TRACE_CONFIG = deep_merge(
    CONFIG_DEFAULTS,
    {
        "trace": {
            "participant_field": "Subsystem",
            "external": [
                {"External System": {"receives_prefix": ["EXT:"]}},
            ],
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
        tagged, participants = collect_all_tagged_functions([source_dir], TRACE_CONFIG)
        assert len(tagged) > 0

        names = [tf.name for tf in tagged]
        assert "Pairing_Start" in names
        assert "ContinuePairing" in names

    def test_participant_resolved_from_requirements(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged, _participants = collect_all_tagged_functions([source_dir], TRACE_CONFIG)

        pairing_funcs = [tf for tf in tagged if tf.name == "Pairing_Start"]
        assert len(pairing_funcs) == 1
        assert pairing_funcs[0].participant_name == "Pairing Manager"


class TestBuildSequenceEdges:
    """Tests for build_sequence_edges."""

    def _make_participants(self):
        return [
            Participant(name="Pairing"),
            Participant(name="WiFi"),
            Participant(name="Cloud"),
        ]

    def _make_tagged(self):
        return [
            TaggedFunction(
                name="Pairing_Start",
                file_path="pairing_mgr/pairing.c",
                participant_name="Pairing",
                emits=["EVENT:PAIRING_STARTED"],
                triggers=["CLOUDMGR_DISABLE"],
            ),
            TaggedFunction(
                name="WIFIMGR_Connect",
                file_path="wifi_mgr/wifi.c",
                participant_name="WiFi",
                handles=["EVENT:PAIRING_STARTED"],
                emits=["EVENT:WIFI_CONNECTED"],
            ),
            TaggedFunction(
                name="ContinuePairing",
                file_path="pairing_mgr/pairing.c",
                participant_name="Pairing",
                handles=["EVENT:WIFI_CONNECTED"],
                emits=["EVENT:MQTT_START"],
            ),
            TaggedFunction(
                name="startMqtt",
                file_path="cloud_mgr/cloud.c",
                participant_name="Cloud",
                handles=["EVENT:MQTT_START"],
            ),
        ]

    def test_emits_creates_arrow_to_handler(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, _warnings = build_sequence_edges(tagged, tagged, participants)

        emit_edges = [e for e in edges if e["style"] == "-->"]
        assert len(emit_edges) > 0

        pairing_to_wifi = [e for e in emit_edges if e["from"] == "Pairing" and e["to"] == "WiFi"]
        assert len(pairing_to_wifi) == 1

    def test_triggers_creates_note(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, _warnings = build_sequence_edges(tagged, tagged, participants)

        notes = [e for e in edges if e["style"] == "note"]
        assert len(notes) == 1
        assert "CLOUDMGR_DISABLE" in notes[0]["label"]

    def test_full_chain_no_unknowns(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, warnings = build_sequence_edges(tagged, tagged, participants)

        arrow_edges = [e for e in edges if e["style"] != "note"]
        assert len(arrow_edges) == 3
        assert warnings == []

    def test_unresolved_event_produces_warning(self):
        tagged = [
            TaggedFunction(
                name="Emitter",
                file_path="src/emitter.c",
                participant_name="Emitter",
                emits=["EVENT:NOBODY_HANDLES_THIS"],
            ),
        ]
        participants = [Participant(name="Emitter")]
        edges, warnings = build_sequence_edges(tagged, tagged, participants)

        assert len(edges) == 0
        assert len(warnings) == 1
        assert "NOBODY_HANDLES_THIS" in warnings[0]

    def test_prefix_routing_to_external(self):
        tagged = [
            TaggedFunction(
                name="Sender",
                file_path="src/sender.c",
                participant_name="Sender",
                emits=["MQTT:shadow_update"],
            ),
        ]
        participants = [
            Participant(name="Sender"),
            Participant(name="Cloud", receives_prefix=["MQTT:"]),
        ]
        edges, warnings = build_sequence_edges(tagged, tagged, participants)

        assert len(edges) == 1
        assert edges[0]["to"] == "Cloud"
        assert warnings == []

    def test_edge_labels_include_function_names(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, _warnings = build_sequence_edges(tagged, tagged, participants)

        emit_edges = [e for e in edges if e["style"] == "-->"]
        first = emit_edges[0]
        assert "Pairing_Start()" in first["label"]
        assert "WIFIMGR_Connect()" in first["label"]


class TestGeneratePlantuml:
    """Tests for generate_plantuml."""

    def test_basic_output(self):
        participants = [
            Participant(name="Pairing Manager"),
            Participant(name="WiFi Manager"),
        ]
        edges = [
            {
                "from": "Pairing Manager",
                "to": "WiFi Manager",
                "label": "Connect()",
                "event": "EVENT:START",
                "style": "-->",
            },
        ]
        result = generate_plantuml("REQ-0001", edges, participants, TRACE_CONFIG)
        assert "@startuml REQ-0001" in result
        assert "@enduml" in result
        assert "autonumber" in result
        assert "Pairing_Manager" in result
        assert "WiFi_Manager" in result

    def test_with_req_name(self):
        result = generate_plantuml(
            "REQ-0252",
            [],
            [],
            TRACE_CONFIG,
            req_name="BLE Pairing",
        )
        assert "@startuml REQ-0252 BLE Pairing" in result

    def test_note_rendering(self):
        participants = [Participant(name="Pairing")]
        edges = [
            {
                "from": "Pairing",
                "to": "Pairing",
                "label": "DISABLE_CLOUD",
                "event": None,
                "style": "note",
            },
        ]
        result = generate_plantuml("REQ-0001", edges, participants, TRACE_CONFIG)
        assert "note right of Pairing: DISABLE_CLOUD" in result

    def test_no_autonumber_when_disabled(self):
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"autonumber": False}}})
        result = generate_plantuml("REQ-0001", [], [], config)
        assert "autonumber" not in result


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

    def test_single_req(self, tmp_path):
        config = deep_merge(
            TRACE_CONFIG,
            {"trace": {"output_dir": str(tmp_path / "out")}},
        )
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, req_id="REQ-0252")
        assert len(written) == 1
        assert written[0].exists()
        content = written[0].read_text()
        assert "@startuml REQ-0252" in content
        assert "@enduml" in content

    def test_trace_all(self, tmp_path):
        config = deep_merge(
            TRACE_CONFIG,
            {"trace": {"output_dir": str(tmp_path / "out")}},
        )
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, trace_all=True)
        assert len(written) >= 1

    def test_no_req_no_all_returns_empty(self):
        written, _warnings = run_trace([], TRACE_CONFIG)
        assert written == []

    def test_nonexistent_req_returns_empty(self, tmp_path):
        config = deep_merge(
            TRACE_CONFIG,
            {"trace": {"output_dir": str(tmp_path / "out")}},
        )
        source_dir = str(FIXTURES_DIR / "trace")
        written, _warnings = run_trace([source_dir], config, req_id="REQ-9999")
        assert written == []
