## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-PARSE-001 | Function detection | parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | walk_function_body, collect_all_tagged_functions |
| REQ-TRACE-002 | Participant resolution from requirements | _build_req_participant_map, _resolve_participant_from_reqs |
| REQ-TRACE-003 | External participant prefix routing | _load_external_participants, resolve_by_prefix |

**Total: 5 requirement(s) affected, 9 function(s) changed**
