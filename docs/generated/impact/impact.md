## Change Impact Report

| REQ | Name | Functions Changed |
|-----|------|-------------------|
| REQ-GIT-001 | Git diff parsing | parse_changed_lines, get_merge_base, get_branch_diff_range |
| REQ-IMPACT-003 | Impact report generation | _run_impact_command |
| REQ-TRACE-001 | Sequence diagram generation | _run_trace_command, collect_all_tagged_functions |
| REQ-TRACE-002 | Participant resolution from requirements | _build_req_participant_map, _resolve_participant_from_reqs |
| REQ-TRACE-003 | External participant prefix routing | _load_external_participants |
| REQ-VAL-001 | Doxygen presence check | validate_file, run_validate, run_precommit |

**Total: 6 requirement(s) affected, 12 function(s) changed**
