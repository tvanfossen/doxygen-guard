"""Tests for doxygen_guard.trace module."""

from __future__ import annotations

from pathlib import Path

from doxygen_guard.config import CONFIG_DEFAULTS, deep_merge
from doxygen_guard.trace import (
    Participant,
    TaggedFunction,
    build_sequence_edges,
    collect_tagged_functions,
    generate_plantuml,
    resolve_participant,
    run_trace,
    write_diagram,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

TRACE_CONFIG = deep_merge(
    CONFIG_DEFAULTS,
    {
        "trace": {
            "participants": [
                {"id": "pairing", "label": "Pairing Manager", "match": "pairing_mgr/"},
                {"id": "wifi", "label": "WiFi Manager", "match": "wifi_mgr/"},
                {"id": "cloud", "label": "Cloud Manager", "match": "cloud_mgr/"},
            ],
        },
    },
)


class TestResolveParticipant:
    """Tests for resolve_participant."""

    def test_matches_path_prefix(self):
        participants = [
            Participant(id="pairing", label="Pairing", match="pairing_mgr/"),
            Participant(id="wifi", label="WiFi", match="wifi_mgr/"),
        ]
        result = resolve_participant("src/pairing_mgr/pairing.c", participants)
        assert result is not None
        assert result.id == "pairing"

    def test_no_match_returns_none(self):
        participants = [
            Participant(id="pairing", label="Pairing", match="pairing_mgr/"),
        ]
        result = resolve_participant("src/unknown/file.c", participants)
        assert result is None

    def test_first_match_wins(self):
        participants = [
            Participant(id="specific", label="Specific", match="pairing_mgr/sub"),
            Participant(id="general", label="General", match="pairing_mgr/"),
        ]
        result = resolve_participant("src/pairing_mgr/sub/file.c", participants)
        assert result is not None
        assert result.id == "specific"


class TestCollectTaggedFunctions:
    """Tests for collect_tagged_functions."""

    def test_collects_from_fixture_dir(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged = collect_tagged_functions([source_dir], TRACE_CONFIG)
        assert len(tagged) > 0

        names = [tf.name for tf in tagged]
        assert "Pairing_Start" in names
        assert "ContinuePairing" in names
        assert "WIFIMGR_STACONNECTAFTERDELAY" in names

    def test_filter_by_req(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged = collect_tagged_functions([source_dir], TRACE_CONFIG, req_filter="REQ-0252")
        assert len(tagged) > 0
        for tf in tagged:
            assert "REQ-0252" in tf.reqs

    def test_filter_nonexistent_req(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged = collect_tagged_functions([source_dir], TRACE_CONFIG, req_filter="REQ-9999")
        assert tagged == []

    def test_participant_resolved(self):
        source_dir = str(FIXTURES_DIR / "trace")
        tagged = collect_tagged_functions([source_dir], TRACE_CONFIG)

        pairing_funcs = [tf for tf in tagged if tf.name == "Pairing_Start"]
        assert len(pairing_funcs) == 1
        assert pairing_funcs[0].participant is not None
        assert pairing_funcs[0].participant.id == "pairing"


class TestBuildSequenceEdges:
    """Tests for build_sequence_edges."""

    def _make_tagged(self):
        pairing = Participant(id="pairing", label="Pairing", match="pairing_mgr/")
        wifi = Participant(id="wifi", label="WiFi", match="wifi_mgr/")
        cloud = Participant(id="cloud", label="Cloud", match="cloud_mgr/")

        return [
            TaggedFunction(
                name="Pairing_Start",
                file_path="pairing_mgr/pairing.c",
                participant=pairing,
                emits=["EVENT:PAIRING_STARTED"],
                triggers=["CLOUDMGR_DISABLE"],
            ),
            TaggedFunction(
                name="WIFIMGR_Connect",
                file_path="wifi_mgr/wifi.c",
                participant=wifi,
                handles=["EVENT:PAIRING_STARTED"],
                emits=["EVENT:WIFI_CONNECTED"],
            ),
            TaggedFunction(
                name="ContinuePairing",
                file_path="pairing_mgr/pairing.c",
                participant=pairing,
                handles=["EVENT:WIFI_CONNECTED"],
                emits=["EVENT:MQTT_START"],
            ),
            TaggedFunction(
                name="startMqtt",
                file_path="cloud_mgr/cloud.c",
                participant=cloud,
                handles=["EVENT:MQTT_START"],
            ),
        ]

    def test_emits_creates_arrow_to_handler(self):
        tagged = self._make_tagged()
        edges = build_sequence_edges(tagged)

        emit_edges = [e for e in edges if e["style"] == "-->"]
        assert len(emit_edges) > 0

        # Pairing_Start emits PAIRING_STARTED → wifi handles it
        pairing_to_wifi = [
            e for e in emit_edges
            if e["from_id"] == "pairing" and e["to_id"] == "wifi"
        ]
        assert len(pairing_to_wifi) == 1

    def test_triggers_creates_note(self):
        tagged = self._make_tagged()
        edges = build_sequence_edges(tagged)

        notes = [e for e in edges if e["style"] == "note"]
        assert len(notes) == 1
        assert "CLOUDMGR_DISABLE" in notes[0]["label"]

    def test_full_chain(self):
        tagged = self._make_tagged()
        edges = build_sequence_edges(tagged)

        # Should have: pairing→wifi, wifi→pairing, pairing→cloud, plus 1 note
        arrow_edges = [e for e in edges if e["style"] != "note"]
        assert len(arrow_edges) == 3


class TestGeneratePlantuml:
    """Tests for generate_plantuml."""

    def test_basic_output(self):
        edges = [
            {
                "from_id": "pairing",
                "to_id": "wifi",
                "label": "Connect()",
                "event": "EVENT:START",
                "style": "-->",
            },
        ]
        result = generate_plantuml("REQ-0001", edges, TRACE_CONFIG)
        assert "@startuml REQ-0001" in result
        assert "@enduml" in result
        assert "autonumber" in result
        assert 'participant "Pairing Manager" as pairing' in result
        assert 'participant "WiFi Manager" as wifi' in result
        assert "pairing --> wifi: EVENT:START" in result

    def test_with_req_name(self):
        result = generate_plantuml("REQ-0252", [], TRACE_CONFIG, req_name="BLE Pairing")
        assert "@startuml REQ-0252 BLE Pairing" in result

    def test_note_rendering(self):
        edges = [
            {
                "from_id": "pairing",
                "to_id": "pairing",
                "label": "DISABLE_CLOUD",
                "event": None,
                "style": "note",
            },
        ]
        result = generate_plantuml("REQ-0001", edges, TRACE_CONFIG)
        assert "note right of pairing: DISABLE_CLOUD" in result

    def test_no_autonumber_when_disabled(self):
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"autonumber": False}}})
        result = generate_plantuml("REQ-0001", [], config)
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
        result = run_trace([source_dir], config, req_id="REQ-0252")
        assert len(result) == 1
        assert result[0].exists()
        content = result[0].read_text()
        assert "@startuml REQ-0252" in content
        assert "@enduml" in content

    def test_trace_all(self, tmp_path):
        config = deep_merge(
            TRACE_CONFIG,
            {"trace": {"output_dir": str(tmp_path / "out")}},
        )
        source_dir = str(FIXTURES_DIR / "trace")
        result = run_trace([source_dir], config, trace_all=True)
        assert len(result) >= 1

    def test_no_req_no_all_returns_empty(self):
        result = run_trace([], TRACE_CONFIG)
        assert result == []

    def test_nonexistent_req_returns_empty(self, tmp_path):
        config = deep_merge(
            TRACE_CONFIG,
            {"trace": {"output_dir": str(tmp_path / "out")}},
        )
        source_dir = str(FIXTURES_DIR / "trace")
        result = run_trace([source_dir], config, req_id="REQ-9999")
        assert result == []
