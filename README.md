# doxygen-guard

Pre-commit hook that validates doxygen comments, generates PlantUML sequence diagrams, and produces change-impact reports. Architecture-agnostic — works with event-driven, sequential, async IPC, and any other pattern.

## Quick Start

### 1. Add the hook to `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/tvanfossen/doxygen-guard
    rev: main
    hooks:
      - id: doxygen-guard
        types_or: [c, c++, java, python]
```

### 2. Create `.doxygen-guard.yaml` in your repo root

```yaml
output_dir: docs/generated/

validate:
  exclude:
    - "^tests/"
    - "^\\.venv/"

trace:
  format: plantuml
  participant_field: "Subsystem"

impact:
  requirements:
    file: docs/requirements.csv
    id_column: "Req ID"
    name_column: "Name"
    format: csv
```

### 3. Add doxygen to your functions

```c
/** @module Sensor Driver */

/**
 * @brief Read temperature from sensor hardware.
 * @version 1.0
 * @req REQ-0010
 * @return Raw ADC value
 */
int Sensor_ReadTemperature(void) {
    int raw = hw_read_adc(TEMP_CHANNEL);
    event_post(EVENT_SENSOR_DATA_READY, raw);
    return raw;
}
```

Run `pre-commit run --all-files` — diagrams appear in `docs/generated/sequences/`.

## What It Does

### Validation (pre-commit gate)

Every function in staged files is checked for:

- **Presence** — must have `@brief`, `@version`, and `@return` (non-void functions)
- **Version staleness** — if function body changed (git diff), `@version` must be updated
- **Tag syntax** — tag values validated against configured patterns
- **Requirement coverage** — functions must have `@req` or an exemption tag

### Sequence Diagrams (auto-generated)

Diagrams are generated from the AST — most behavioral tags are **inferred**, not manually written:

| Tag | Purpose | Manual? |
|-----|---------|---------|
| `@req` | Requirement mapping | Yes — per function |
| `@module` | Participant identity | Yes — once per file |
| `@triggers` | State annotations | Yes — per function |
| `@return` | Return value documentation | Yes — per function |
| `@emit_source` | Infrastructure root (e.g., on `Event_post`) | Yes — once |
| `@handle_source` | Registration root (e.g., on `Event_register`) | Yes — once |
| `@emits` | Events emitted | **Inferred from AST** |
| `@handles` | Events handled | **Inferred from AST** |
| `@ext` | Cross-module calls | **Inferred from AST** |

The tool scans function bodies via tree-sitter to detect `event_post()` calls, `Event_register()` patterns, and cross-participant function calls automatically.

**Project-defined function calls** are also shown in diagrams — any standalone call to a function defined in your scanned source files produces an edge, even without trace tags. Method calls (`.get()`, `.strip()`) are excluded via AST node type, not heuristics.

### Diagram Features

- **Return types** on arrows (derived from AST or `@return` tag override)
- **Control flow** blocks (if/else, loops, try/catch, switch)
- **Error recovery** notes in catch blocks (callee extraction)
- **Activation bars** with auto-close at diagram end
- **Incremental generation** — SHA-256 manifest skips unchanged diagrams (493x speedup)
- **Cross-REQ handler chain** following with configurable depth

### Exemption Tags

| Tag | Validation | Trace |
|-----|-----------|-------|
| `@internal` | Exempt from `@req` | **Invisible** — never in any diagram |
| `@utility` | Exempt from `@req` | **Visible** — appears as call target |
| `@callback` | Exempt from `@req` | Normal visibility |

### Change-Impact Reports

Cross-references git diff with parsed functions to show which requirements are affected by staged changes. Reports in markdown and JSON at `<output_dir>/impact/`.

## Configuration Reference

### `validate` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `languages` | dict | C, C++, Java, Python | Per-language function patterns and comment styles |
| `presence.require_doxygen` | bool | `true` | Require doxygen on every function |
| `presence.require_return` | bool | `true` | Require `@return` on non-void functions |
| `presence.skip_forward_declarations` | bool | `true` | Skip C/C++ forward declarations |
| `version.require_present` | bool | `true` | Require `@version` tag |
| `version.require_increment_on_change` | bool | `true` | Require version bump when body changes |
| `exclude` | list | `[]` | Regex patterns for files to skip |
| `version_gate.current_version` | string | — | `auto:git`, `auto:cmake`, or explicit version |
| `version_gate.version_field` | string | — | Column in requirements file for version gating |

### `trace` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `format` | string | `plantuml` | Diagram output format |
| `participant_field` | string | — | Requirements file column for participant resolution |
| `external` | list | `[]` | External participants with `receives_prefix` |
| `external_fallback` | string | `External` | Default name for unresolved external sources |

### `trace.options`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `autonumber` | bool | `true` | Number sequence arrows |
| `box_label` | string | `System` | Internal participant box label |
| `min_edges` | int | `1` | Minimum behavioral edges to generate a diagram |
| `show_returns` | bool | `true` | Show return arrows on ext calls |
| `show_return_values` | bool | `true` | Label return arrows with type info |
| `show_project_calls` | bool | `true` | Show calls to project-defined functions |
| `show_recovery_notes` | bool | `true` | Show callee names in empty catch blocks |
| `cross_req_depth` | int | `1` | Handler chain hops across REQ boundaries (-1=unlimited) |
| `max_condition_length` | int | `80` | Truncation limit for alt/loop conditions |
| `infer_emits` | bool | `true` | Infer @emits from emit function calls |
| `infer_ext` | bool | `true` | Infer @ext from cross-module calls |
| `legend` | bool | `false` | Render arrow style legend |
| `label_mode` | string | `full` | Label style: `full`, `brief`, `label-only` |

### `impact` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `requirements.file` | string | — | Path to requirements file |
| `requirements.format` | string | `csv` | Format: `csv`, `json`, or `yaml` |
| `requirements.id_column` | string | `Req ID` | Requirement ID column/field |
| `requirements.name_column` | string | `Requirement Name` | Requirement name column/field |

### External participants

Route unhandled events to named external actors by event prefix. Entries can be a name with config, or a plain string:

```yaml
trace:
  external:
    - Cloud:
        receives_prefix: ["EVENT_CLOUD_"]
    - Hardware:
        receives_prefix: ["EVENT_HW_"]
    - "User Action"             # plain string, no prefix matching
  external_fallback: "External" # default name for unresolved sources
```

Events matching `receives_prefix` are routed to that participant. Events not matching any prefix use `external_fallback`.

## File-Level Doxygen

Each source file should have a file-level doxygen block. Use `@module` to set the participant name for all functions in the file:

```c
/**
 * @file
 * @brief Sensor hardware abstraction layer.
 * @version 1.0
 * @module Sensor Driver
 */
```

The `@module` tag overrides the requirements-file `participant_field` for participant resolution. Enable `validate.presence.require_file_doxygen: true` to enforce file-level blocks.

For Python:

```python
## @file
## @brief Configuration loading and validation.
## @version 1.0
## @module Config
```

## Infrastructure Roots

Mark event bus functions once to enable automatic inference:

```c
/**
 * @brief Post an event to all registered handlers.
 * @version 1.0
 * @req REQ-0050
 * @emit_source
 */
void Event_post(uint64_t event, void *data) { ... }

/**
 * @brief Register a handler for events matching a bitmask.
 * @version 1.0
 * @req REQ-0050
 * @handle_source
 */
void Event_register(uint64_t mask, event_handler_fn handler) { ... }
```

With these in place, `@emits` and `@handles` are derived from the AST for all other functions — zero manual behavioral tags needed.

## Generated Code

For code generators that rebuild files on every build (UDM, protobuf, HAL generators), annotations on generated files are destroyed. Use `event_emit_functions` and `event_register_functions` as config-level overrides:

```yaml
trace:
  options:
    event_emit_functions: ["dm_event_callback"]
    event_register_functions: ["register_fsm_handler"]
```

These tell the tool to treat calls to these functions as emit/registration points, equivalent to `@emit_source` / `@handle_source` but without requiring tags on the generated source. **Use this only for generated code you cannot annotate.** For code you own, use `@emit_source` / `@handle_source` tags instead.

Both default to empty lists — `@emit_source` / `@handle_source` tags are the primary mechanism.

## Config Validation

The config file is validated at load time against a built-in schema. Unknown keys are rejected with an error message:

```
doxygen-guard config error: Unknown key 'trace.optoins' — did you mean 'trace.options'?
```

`trace.options` values are type-checked after merge with defaults. Invalid types produce warnings:

```
WARNING: trace.options.min_edges must be int >= 0, got 'banana'
```

## Adopting on an Existing Codebase

For repos with existing code that has no doxygen, adopt incrementally:

1. **Start with validation only** — add `@brief` and `@version` to functions as you touch them. Use `version_gate` to only enforce `@req` on functions added after a specific version:

```yaml
validate:
  version_gate:
    current_version: "auto:git"
    version_field: "Min Version"
```

2. **Add `@module` to each file** — one line per file sets the participant name for diagrams.

3. **Add `@return` to non-void functions** — required by default. Disable with `presence.require_return: false` during migration.

4. **Mark infrastructure roots** — if you have an event bus or message dispatcher, add `@emit_source`/`@handle_source` once to enable automatic inference.

5. **Exclude paths you're not ready to cover**:

```yaml
validate:
  exclude:
    - "^vendor/"
    - "^legacy/"
```

The tool generates useful diagrams from day one — `show_project_calls` shows all project function calls even before any trace tags are added.

## Supported Languages

| Language | Extensions | Comment Style | Body Detection |
|----------|-----------|---------------|----------------|
| C | `.c`, `.h` | `/** ... */` | Brace matching |
| C++ | `.cpp`, `.hpp`, `.cc`, `.cxx` | `/** ... */` | Brace matching |
| Java | `.java` | `/** ... */` | Brace matching |
| Python | `.py` | `## ...` | Indentation |

## CLI Usage

```bash
# Pre-commit mode (default — called by pre-commit)
doxygen-guard [--config path] [files...]

# Explicit subcommands
doxygen-guard validate --no-git src/*.c
doxygen-guard trace --all src/
doxygen-guard trace --req REQ-001 src/
doxygen-guard impact --staged src/*.c
doxygen-guard coverage src/

# Verbose mode
doxygen-guard -v trace --all src/
```

## License

MIT — see [LICENSE](LICENSE).
