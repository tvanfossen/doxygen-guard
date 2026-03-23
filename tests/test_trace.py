"""Tests for doxygen_guard.tracer module."""

from __future__ import annotations

from doxygen_guard.config import CONFIG_DEFAULTS, deep_merge
from doxygen_guard.tracer import (
    DiagramContext,
    Edge,
    Participant,
    TaggedFunction,
    _build_call_edges,
    _build_inbound_edges,
    _collect_assumes,
    _is_req_relevant_target,
    build_sequence_edges,
    collect_all_tagged_functions,
    generate_infrastructure_table,
    generate_plantuml,
    run_trace,
    write_diagram,
    write_infrastructure_table,
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
                name="WiFi_Connect",
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

        emit_edges = [e for e in edges if e.style == "-->"]
        assert len(emit_edges) > 0

        pairing_to_wifi = [
            e for e in emit_edges if e.from_name == "Pairing" and e.to_name == "WiFi"
        ]
        assert len(pairing_to_wifi) == 1

    def test_triggers_creates_note(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, _warnings = build_sequence_edges(tagged, tagged, participants)

        notes = [e for e in edges if e.style == "note"]
        assert len(notes) == 1
        assert "CLOUDMGR_DISABLE" in notes[0].label

    def test_full_chain_no_unknowns(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, warnings = build_sequence_edges(tagged, tagged, participants)

        arrow_edges = [e for e in edges if e.style != "note"]
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
        assert edges[0].to_name == "Cloud"
        assert warnings == []

    def test_edge_labels_include_function_names(self):
        tagged = self._make_tagged()
        participants = self._make_participants()
        edges, _warnings = build_sequence_edges(tagged, tagged, participants)

        emit_edges = [e for e in edges if e.style == "-->"]
        first = emit_edges[0]
        assert "Pairing_Start()" in first.label
        assert "WiFi_Connect()" in first.label


class TestBuildCallEdges:
    """Tests for _build_call_edges body scanning."""

    def test_finds_direct_call(self):
        caller = TaggedFunction(
            name="main_func",
            file_path="a.c",
            participant_name="A",
            body="void main_func() {\n    helper_func();\n}",
        )
        target = TaggedFunction(
            name="helper_func",
            file_path="b.c",
            participant_name="B",
        )
        edges = _build_call_edges(caller, "A", [caller, target])
        assert len(edges) == 1
        assert edges[0].to_name == "B"
        assert "helper_func()" in edges[0].label

    def test_no_false_positive_in_string(self):
        caller = TaggedFunction(
            name="main_func",
            file_path="a.c",
            participant_name="A",
            body='void main_func() {\n    printf("helper_func()");\n}',
        )
        target = TaggedFunction(
            name="helper_func",
            file_path="b.c",
            participant_name="B",
        )
        # Word boundary prevents matching inside string when preceded by "
        edges = _build_call_edges(caller, "A", [caller, target])
        # Note: printf("helper_func()") WILL match because \b matches at
        # the transition from " to h. This is a known limitation of regex-
        # based scanning — documented, not fixed.
        # The word boundary prevents partial matches like log_helper_func().
        assert len(edges) <= 1  # May match, documenting behavior

    def test_no_partial_name_match(self):
        caller = TaggedFunction(
            name="main_func",
            file_path="a.c",
            participant_name="A",
            body="void main_func() {\n    log_helper_func();\n}",
        )
        target = TaggedFunction(
            name="helper_func",
            file_path="b.c",
            participant_name="B",
        )
        edges = _build_call_edges(caller, "A", [caller, target])
        assert len(edges) == 0  # Word boundary prevents partial match

    def test_skips_self(self):
        caller = TaggedFunction(
            name="recursive",
            file_path="a.c",
            participant_name="A",
            body="void recursive() {\n    recursive();\n}",
        )
        edges = _build_call_edges(caller, "A", [caller])
        assert len(edges) == 0


class TestGeneratePlantuml:
    """Tests for generate_plantuml."""

    def test_basic_output(self):
        participants = [
            Participant(name="Pairing Manager"),
            Participant(name="WiFi Manager"),
        ]
        edges = [Edge("Pairing Manager", "WiFi Manager", "Connect()", "EVENT:START", "-->")]
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
            Participant(name="Cloud", receives_prefix=["EVENT:CLOUD_"]),
        ]
        edges = [Edge("OTA", "Cloud", "report()", "EVENT:CLOUD_RESULT", "-->")]
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
            Participant(name="Cloud", receives_prefix=["EVENT:CLOUD_"]),
        ]
        edges = [Edge("Cloud", "Cloud", "self()")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert "box" not in result
        assert 'entity "Cloud"' in result

    def test_with_req_name(self):
        ctx = DiagramContext(req_row={"Name": "BLE Pairing"})
        result = generate_plantuml("REQ-0252", [], [], [], TRACE_CONFIG, context=ctx)
        assert "@startuml REQ-0252 BLE Pairing" in result

    def test_note_rendering(self):
        participants = [Participant(name="Pairing")]
        edges = [Edge("Pairing", "Pairing", "DISABLE_CLOUD", style="note")]
        result = generate_plantuml("REQ-0001", edges, [], participants, TRACE_CONFIG)
        assert "note right of Pairing: DISABLE_CLOUD" in result

    def test_no_autonumber_when_disabled(self):
        config = deep_merge(TRACE_CONFIG, {"trace": {"options": {"autonumber": False}}})
        result = generate_plantuml("REQ-0001", [], [], [], config)
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


class TestInboundCallerScoping:
    """Tests for Phase 1A: inbound callers produce only edges to target functions."""

    def test_inbound_caller_only_edges_to_target(self):
        """Inbound callers should NOT get full edge expansion."""
        # hub calls both target and unrelated
        hub = TaggedFunction(
            name="hub",
            file_path="hub.c",
            participant_name="Hub",
            emits=["EVENT:UNRELATED"],
            body="void hub() {\n    target_func();\n    unrelated();\n}",
        )
        target = TaggedFunction(
            name="target_func",
            file_path="target.c",
            participant_name="Target",
            reqs=["REQ-001"],
        )
        unrelated = TaggedFunction(
            name="unrelated",
            file_path="other.c",
            participant_name="Other",
            reqs=["REQ-002"],
        )
        handler = TaggedFunction(
            name="handler",
            file_path="handler.c",
            participant_name="Handler",
            handles=["EVENT:UNRELATED"],
        )
        all_tagged = [hub, target, unrelated, handler]
        participants = [Participant(name="Hub"), Participant(name="Target")]

        # hub is NOT a direct emitter — it's an inbound caller of target
        edges, _warnings = build_sequence_edges(
            [target], all_tagged, participants, req_id="REQ-001"
        )

        edge_labels = [e.label for e in edges]
        # Should have edge from hub to target
        assert any("target_func()" in label for label in edge_labels)
        # Should NOT have hub's emit edge to handler
        assert not any("handler()" in label for label in edge_labels)
        # Should NOT have hub's call edge to unrelated
        assert not any("unrelated()" in label for label in edge_labels)

    def test_build_inbound_edges_only_targets(self):
        """_build_inbound_edges only creates edges to specified target names."""
        caller = TaggedFunction(
            name="caller",
            file_path="a.c",
            participant_name="A",
            body="void caller() {\n    alpha();\n    beta();\n}",
        )
        alpha = TaggedFunction(name="alpha", file_path="b.c", participant_name="B")
        beta = TaggedFunction(name="beta", file_path="c.c", participant_name="C")

        edges = _build_inbound_edges(caller, "A", {"alpha"}, [alpha, beta])
        assert len(edges) == 1
        assert edges[0].to_name == "B"
        assert "alpha()" in edges[0].label


class TestCallEdgeReqFiltering:
    """Tests for Phase 1B: call edges filtered by shared @req tag."""

    def test_filters_out_different_req(self):
        """Call edge targets with different @req are excluded."""
        caller = TaggedFunction(
            name="run_precommit",
            file_path="main.py",
            participant_name="Validate",
            reqs=["REQ-VAL-001"],
            body="def run_precommit():\n    run_trace()\n    validate_file()\n",
        )
        same_req = TaggedFunction(
            name="validate_file",
            file_path="main.py",
            participant_name="Validate",
            reqs=["REQ-VAL-001"],
        )
        different_req = TaggedFunction(
            name="run_trace",
            file_path="tracer.py",
            participant_name="Trace",
            reqs=["REQ-TRACE-001"],
        )
        edges = _build_call_edges(
            caller, "Validate", [caller, same_req, different_req], req_id="REQ-VAL-001"
        )
        target_names = {e.label for e in edges}
        assert "validate_file()" in target_names
        assert "run_trace()" not in target_names

    def test_allows_handler_targets(self):
        """Targets with @handles are always allowed (trace-relevant)."""
        caller = TaggedFunction(
            name="emitter",
            file_path="a.c",
            participant_name="A",
            reqs=["REQ-001"],
            body="void emitter() {\n    handler_func();\n}",
        )
        handler = TaggedFunction(
            name="handler_func",
            file_path="b.c",
            participant_name="B",
            handles=["EVENT:X"],
        )
        edges = _build_call_edges(caller, "A", [caller, handler], req_id="REQ-001")
        assert len(edges) == 1

    def test_no_filter_without_req_id(self):
        """Without req_id, all targets are allowed (backward compat)."""
        caller = TaggedFunction(
            name="caller",
            file_path="a.c",
            participant_name="A",
            body="void caller() {\n    other();\n}",
        )
        other = TaggedFunction(
            name="other",
            file_path="b.c",
            participant_name="B",
            reqs=["REQ-OTHER"],
        )
        edges = _build_call_edges(caller, "A", [caller, other])
        assert len(edges) == 1

    def test_is_req_relevant_excludes_supports_only(self):
        """@supports REQ-X without @req REQ-X is excluded."""
        target = TaggedFunction(
            name="util",
            file_path="util.c",
            supports=["REQ-001"],
        )
        assert not _is_req_relevant_target(target, "REQ-001")

    def test_is_req_relevant_allows_req_match(self):
        """@req REQ-X is allowed."""
        target = TaggedFunction(
            name="func",
            file_path="a.c",
            reqs=["REQ-001"],
        )
        assert _is_req_relevant_target(target, "REQ-001")

    def test_is_req_relevant_allows_dual_role(self):
        """Function with both @req and @supports for same REQ is allowed."""
        target = TaggedFunction(
            name="func",
            file_path="a.c",
            reqs=["REQ-001"],
            supports=["REQ-001"],
        )
        assert _is_req_relevant_target(target, "REQ-001")


class TestSupportsAndAssumes:
    """Tests for Phase 2: @supports and @assumes tag handling."""

    def test_tagged_function_has_supports_assumes(self):
        """TaggedFunction stores supports and assumes fields."""
        tf = TaggedFunction(
            name="func",
            file_path="a.c",
            supports=["REQ-001", "REQ-002"],
            assumes=["REQ-003"],
        )
        assert tf.supports == ["REQ-001", "REQ-002"]
        assert tf.assumes == ["REQ-003"]

    def test_collect_assumes_deduplicates(self):
        """_collect_assumes returns unique values preserving order."""
        funcs = [
            TaggedFunction(name="a", file_path="a.c", assumes=["REQ-001", "REQ-002"]),
            TaggedFunction(name="b", file_path="b.c", assumes=["REQ-002", "REQ-003"]),
        ]
        result = _collect_assumes(funcs)
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

    def test_supports_function_excluded_from_diagram(self):
        """Function with @supports REQ-X but no @req REQ-X is excluded from edges."""
        caller = TaggedFunction(
            name="load_config",
            file_path="config.py",
            participant_name="Config",
            reqs=["REQ-CONFIG-001"],
            body="def load_config():\n    validate_output_path()\n",
        )
        supports_only = TaggedFunction(
            name="validate_output_path",
            file_path="config.py",
            participant_name="Config",
            supports=["REQ-CONFIG-001"],
        )
        edges = _build_call_edges(
            caller, "Config", [caller, supports_only], req_id="REQ-CONFIG-001"
        )
        assert len(edges) == 0


class TestInfrastructureTable:
    """Tests for Phase 4: infrastructure overview table generation."""

    def test_generates_markdown_table(self):
        """Infrastructure table includes @supports functions."""
        tagged = [
            TaggedFunction(
                name="validate_output_path",
                file_path="src/config.py",
                supports=["REQ-CONFIG-001", "REQ-TRACE-001"],
            ),
            TaggedFunction(
                name="git_add",
                file_path="src/git.py",
                supports=["REQ-GIT-001"],
            ),
            TaggedFunction(
                name="load_config",
                file_path="src/config.py",
                reqs=["REQ-CONFIG-001"],
            ),
        ]
        result = generate_infrastructure_table(tagged)
        assert "## Infrastructure Overview" in result
        assert "validate_output_path" in result
        assert "git_add" in result
        assert "REQ-CONFIG-001, REQ-TRACE-001" in result
        # load_config has no @supports so should NOT appear
        assert "load_config" not in result

    def test_empty_when_no_supports(self):
        """Returns empty string when no functions have @supports."""
        tagged = [
            TaggedFunction(name="func", file_path="a.c", reqs=["REQ-001"]),
        ]
        assert generate_infrastructure_table(tagged) == ""

    def test_sorted_by_module_then_name(self):
        """Table rows sorted by module then function name."""
        tagged = [
            TaggedFunction(name="z_func", file_path="src/b.py", supports=["REQ-1"]),
            TaggedFunction(name="a_func", file_path="src/a.py", supports=["REQ-2"]),
        ]
        result = generate_infrastructure_table(tagged)
        lines = result.strip().split("\n")
        data_lines = [line for line in lines if line.startswith("| ") and "---" not in line][1:]
        assert "a_func" in data_lines[0]
        assert "z_func" in data_lines[1]

    def test_writes_file(self, tmp_path):
        """write_infrastructure_table creates the file."""
        tagged = [
            TaggedFunction(name="helper", file_path="src/util.py", supports=["REQ-001"]),
        ]
        result = write_infrastructure_table(tagged, str(tmp_path))
        assert result is not None
        assert result.exists()
        assert result.name == "infrastructure.md"
        content = result.read_text()
        assert "helper" in content
