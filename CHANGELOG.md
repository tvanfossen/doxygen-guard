# Changelog

All notable changes to doxygen-guard are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.2.5] - 2026-04-13

### Fixed

- **Box placement** — module-derived participants (from `@participant` tag)
  now render inside the box instead of as standalone entities outside an
  empty box. Internal/external partition simplified: anything without
  `receives_prefix` goes inside the box.
- **Hub backtracking payload extraction** — when entry edges are resolved
  via dispatch hub backtracking, payload context is now scoped to
  conditional branches in the hub that route to the REQ-tagged handler
  (via direct call OR `event_post` of a target event). REQ-0102 now
  shows `cmd/req\n*dock` instead of the full set of all branches.
- **Direct-path payload scoping** — when the receiving function is itself
  a hub (e.g., `handle_cmd_request` with multiple `@sends`), payload
  extraction scopes to events received by other functions in the same
  REQ. REQ-0100 now shows `cmd/req\n*clean` instead of `*clean\n*dock`.

## [1.2.4] - 2026-04-13

### Fixed

Three consumer-reported false positives:

- **@par flagged as unknown** — added to `_KNOWN_TAGS` along with `@throws`,
  `@throw`, `@exception`, `@pre`, `@post`, `@invariant`, `@since`, `@author`,
  `@date`, `@copyright` (all standard Doxygen tags).
- **Constructors required `@return`** — tree-sitter parser now tracks enclosing
  class via `class_specifier`/`struct_specifier` walk for inline definitions and
  `qualified_identifier` scope extraction for out-of-line definitions
  (`Foo::Foo() {}`). `Function.is_constructor()` and `Function.is_destructor()`
  helpers expose the result. `check_return_presence` skips them.
- **Constructors required `@req`** — same detection. `check_req_coverage` skips
  constructors and destructors. They inherit the enclosing class's contract.

### Internal

- New `Function.enclosing_class` field populated by tree-sitter parser
- Hub backtracking through dispatch hubs for entry edge resolution (REQ-0102 etc.
  get full entry chain via `handle_cmd_request`'s `@receives MQTT:cmd/req` even
  though the hub function is tagged with a different REQ)
- Manual label override on `@calls` (`@calls module::func "MQTT: cmd/resp"`)
  for behavioral REQ pattern where intent is on REQ-tagged function but actual
  call happens elsewhere
- Payload extraction wired into entry labels (`*state:clean` from strcmp guards)
- `label_mode: full` deduplication (no more `MQTT:cmd/req\ncmd/req`)
- `.h` files containing C++ constructs (namespace, class, template, access
  specifiers) now route to cpp grammar via content sniff, fixing namespace
  false positives on consumer C++ headers

## [1.2.0] - 2026-04-07

### BREAKING: Behavioral Trace Engine Redesign

The trace engine has been redesigned to produce behavioral sequence diagrams
instead of call graphs. This is a breaking change — all annotation tags,
config keys, and generated output have changed.

### Tag Renames

| Old | New | Purpose |
|-----|-----|---------|
| `@emits` | `@sends` | Dashed arrow to handler's participant |
| `@handles` | `@receives` | Entry arrow from external participant |
| `@ext` | `@calls` | Solid arrow to external participant |
| `@triggers` | `@note` | Note on participant (doxygen built-in) |
| `@assumes` | `@after` | Header precondition |
| `@module` | `@participant` | File-level participant declaration |
| `@emit_source` | `@send_source` | Infrastructure root marker |
| `@handle_source` | `@receive_source` | Infrastructure root marker |

### Config Key Renames

| Old | New |
|-----|-----|
| `infer_emits` | `infer_sends` |
| `infer_ext` | `infer_calls` |
| `event_emit_functions` | `event_send_functions` |
| `event_register_functions` | `event_receive_functions` |

### Removed
- `@supports` tag — use `@utility` for coverage exemption
- `ast_walker.py` — call-graph AST recursion engine (854 lines)
- `edges_ast.py` — call-graph edge builder (524 lines)
- `edges.py` — legacy regex edge builder (251 lines)
- `infrastructure.py` — infrastructure table (depended on @supports)
- Config options: `show_project_calls`, `cross_req_depth`, `show_return_values`

### Added
- `edges_behavioral.py` — annotation-driven behavioral edge builder
- `@loop "label"` tag — wraps handler section in loop block
- `@group "label"` tag — wraps handler section in group block
- `entry_chain` config on external participants — upstream participant chain arrows
- `label_template` config on external participants — protocol-level labels from boundary args
- `box_color` config option
- Boundary-argument extraction from tree-sitter AST
- Payload extraction from conditional patterns (strcmp, ==)

### Changed
- Default `show_returns: false` (was `true`)
- Default `label_mode: "brief"` (was `"full"`)
- Participant resolution priority: requirements CSV > `@participant` (was `@module` > CSV)
- No "External" fallback participant — unresolvable entries omitted with warning

### Migration

Rename annotations before upgrading:
```bash
# In your annotated source files:
sed -i 's/@emits/@sends/g; s/@handles/@receives/g; s/@ext /@calls /g' src/**/*.c
sed -i 's/@triggers/@note/g; s/@assumes/@after/g; s/@module/@participant/g' src/**/*.c
sed -i 's/@supports/@utility/g' src/**/*.c
sed -i 's/@emit_source/@send_source/g; s/@handle_source/@receive_source/g' src/**/*.c

# In .doxygen-guard.yaml:
sed -i 's/infer_emits/infer_sends/; s/infer_ext/infer_calls/' .doxygen-guard.yaml
sed -i 's/event_emit_functions/event_send_functions/' .doxygen-guard.yaml
sed -i 's/event_register_functions/event_receive_functions/' .doxygen-guard.yaml
```

## [1.1.3] - 2026-04-02

### Added
- **v1.1.0**: Project-defined function call visibility (root repo 7→13 diagrams)
- Test coverage for `coverage.py` and `infrastructure.py`

### Changed
- `min_edges` default lowered from 2 to 1
- `tracer/__init__.py` trimmed from 28 to 3 re-exports
- ASTEdge and WalkContext moved to `tracer_models.py` (eliminates circular imports)
- `git_add` scoped to specific written files instead of directories
- WalkContext copy uses `dataclasses.replace()` instead of manual 16-field construction
- README fully rewritten for all v0.4.0+ features

### Fixed
- Dead code removed: `clear_cache()`, unused backward-compat aliases
- 12 `tf.participant_name or tf.name` sites → `TaggedFunction.display_name` property
- 7 `get_trace(config).get("options", {})` sites → `get_trace_options()` accessor
- 7 `ref.split("::", 1)` sites → `split_ext_ref()` / `ext_func_name()` utilities
- REQ-relevance logic deduplicated → `is_req_relevant()` in `tracer_models.py`
- 5 `except Exception` blocks narrowed to specific types
- `trace.options` values validated at config load time
- PlantUML `!include` directive sanitization
- PyYAML capped to `<8`
- LICENSE name corrected to Tristan VanFossen
- CI runs pre-commit (all hooks) and triggers on develop branch
- Release workflow adds PyPI publish via `pypa/gh-action-pypi-publish`

## [1.0.0] - 2026-04-02

### Breaking Changes
- Return arrows use `<--` style instead of `-->`, visually distinct from async event dispatch.
- Legend remains opt-in (`trace.options.legend: false` default). Enable with `legend: true`.

### Added
- **v0.5.0**: Return type labels on arrows (AST-derived, `@return` tag override)
- **v0.5.0**: `@return` presence enforcement for non-void functions (default on)
- **v0.5.0**: Internal call arguments shown (same as ext calls)
- **v0.5.0**: `@internal` functions invisible to trace; `@utility` visible as call targets
- **v0.6.0**: Cross-REQ emitter lookup before "External" fallback
- **v0.6.0**: Module-stem fallback for unresolved ext targets
- **v0.7.0**: Catch body recovery summaries (callee extraction, empty handler notation)
- **v0.8.0**: Configurable condition truncation at clause boundaries (max 80 chars)
- **v0.8.0**: For-loop condition extraction (test expression only)
- **v0.8.0**: Incremental trace via SHA-256 manifest (493x speedup on cache hit)
- **v0.9.0**: Legacy `build_sequence_edges` path emits DeprecationWarning

### Fixed
- Single-source version from pyproject.toml via importlib.metadata
- Encoding guard: non-UTF-8 source files skipped with warning
- Tree-sitter parse errors caught and logged
- Subprocess timeouts on all git (30s) and plantuml (60s) calls
- Grammar dependency upper bounds pinned (`<0.23.5`)
- Stale diagram cleanup (files not regenerated by current min_edges removed)
- Duplicate function notes suppressed (section separator dedup)

## [0.4.0] - 2026-03-27

### Added
- Tree-sitter AST pipeline for execution-ordered sequence diagrams
- Control flow blocks: if/else, loops, try/catch, switch/case
- Handler chain following with depth limits
- Infrastructure inference: @emit_source, @handle_source markers
- @module file-level tag for participant resolution
- Emit/handle/ext inference from AST (zero manual behavioral tags needed)
- Cross-REQ chain depth limiting (trace.options.cross_req_depth)
- Init function exclusion from behavioral diagrams
- Entry edge alt grouping for multi-event handlers
- Empty section separator pruning
- Orphaned function note suppression
- Activation bar auto-close at diagram end
- Event_register labels show arguments
- git ls-files for file discovery (replaces rglob)
- Golden file regression testing (14 diagrams)

### Fixed
- Alt branch inversion in emit ordering (direct constant lookup)
- Golden test non-determinism from set iteration
- False presence violations from } in doxygen comment text
- Impact report diffs against branch point instead of staged only

### Removed
- Deprecated EVENT: prefix naming (replaced by raw EVENT_ constants)
- Manual @emits/@handles/@ext tags in examples (fully inferred)

## [0.2.0] - 2026-03-20

### Added
- Config schema validation with unknown key rejection
- Version-gated @req enforcement (version_gate config)
- [reviewed] marker for version tags
- auto:git version gate source

### Fixed
- Tag parser: only split on known doxygen tags
- False presence violations from backward scan

## [0.1.0] - 2026-03-15

### Added
- Initial release
- Doxygen presence check (@brief, @version required)
- Version staleness detection (body changed, version not bumped)
- Tag syntax validation (pattern, prefix, contains rules)
- @req coverage enforcement with exemption tags
- Unknown tag detection with Levenshtein suggestions
- PlantUML sequence diagram generation from @emits/@handles/@ext
- Change impact report (markdown, JSON, text)
- Git diff parsing for changed function detection
- Multi-language support: C, C++, Python, Java
- YAML configuration with deep-merge defaults
- Pre-commit hook integration
