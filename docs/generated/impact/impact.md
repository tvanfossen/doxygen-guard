## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-CONFIG-002 | Per-language parse settings | get_language_config, resolve_parse_settings |
| REQ-PARSE-001 | Function detection | parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | walk_function_body, run_trace, _infer_entry_edges, build_sequence_edges_ast |

**Total: 4 requirement(s) affected, 9 function(s) changed**
