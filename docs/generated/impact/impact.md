## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-CONFIG-001 | YAML config loading and merging | validate_config_schema, load_config |
| REQ-CONFIG-002 | Per-language parse settings | get_language_config, resolve_parse_settings |
| REQ-PARSE-001 | Function detection | parse_source_file_with_content |
| REQ-TRACE-001 | Sequence diagram generation | walk_function_body, _build_emit_edges, _build_ext_edges, _build_trigger_edges, _build_call_edges, build_sequence_edges, generate_plantuml, write_diagram, generate_plantuml_ast |

**Total: 4 requirement(s) affected, 14 function(s) changed**
