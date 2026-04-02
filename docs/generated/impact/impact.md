## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-PARSE-001 | Function detection | parse_source_file, parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | run_trace, walk_function_body, _build_emit_edges, _build_ext_edges, _build_trigger_edges, _build_call_edges, build_sequence_edges |

**Total: 3 requirement(s) affected, 11 function(s) changed**
02 | Participant resolution from requirements | _build_req_participant_map, _resolve_participant_from_reqs |
| REQ-TRACE-003 | External participant prefix routing | _load_external_participants |

**Total: 6 requirement(s) affected, 13 function(s) changed**
