## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-TRACE-001 | Sequence diagram generation | run_trace, _build_emit_edges, _build_ext_edges, _build_trigger_edges, _build_call_edges, build_sequence_edges, generate_plantuml, write_diagram, generate_plantuml_ast, collect_all_tagged_functions |
| REQ-TRACE-002 | Participant resolution from requirements | _build_req_participant_map, _resolve_participant_from_reqs |
| REQ-TRACE-003 | External participant prefix routing | _load_external_participants |

**Total: 3 requirement(s) affected, 13 function(s) changed**
