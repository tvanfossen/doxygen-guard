## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-CONFIG-002 | Per-language parse settings | get_language_config, resolve_parse_settings |
| REQ-IMPACT-003 | Impact report generation | _run_impact_command |
| REQ-PARSE-001 | Function detection | parse_source_file, parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | _infer_entry_edges, build_sequence_edges_ast, _run_trace_command |
| REQ-TRACE-003 | External participant prefix routing | resolve_by_prefix |
| REQ-VAL-001 | Doxygen presence check | validate_file, run_validate, run_precommit |

**Total: 7 requirement(s) affected, 14 function(s) changed**
