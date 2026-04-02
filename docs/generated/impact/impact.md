## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-CONFIG-002 | Per-language parse settings | get_language_config, resolve_parse_settings |
| REQ-PARSE-001 | Function detection | parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | walk_function_body, collect_all_tagged_functions, _infer_entry_edges, build_sequence_edges_ast |
| REQ-TRACE-002 | Participant resolution from requirements | _build_req_participant_map, _resolve_participant_from_reqs |
| REQ-TRACE-003 | External participant prefix routing | _load_external_participants |

**Total: 6 requirement(s) affected, 12 function(s) changed**
