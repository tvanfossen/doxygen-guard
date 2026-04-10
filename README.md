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
  static_participants:
    - name: User
      type: actor
  external:
    - Cloud:
        receives_prefix: ["MQTT:"]
        boundary_functions: ["CloudMgr_Publish"]
        label_template: "MQTT: {arg0}"
        entry_chain:
          - { from: User, to: Mobile, label: "{req_name}" }
          - { from: Mobile, to: Cloud, label: "{req_name}" }
    - MCU:
        receives_prefix: ["DURABLE:"]
        boundary_functions: ["DurableEventCb"]
  options:
    show_returns: false
    label_mode: brief

impact:
  requirements:
    file: docs/requirements.csv
    id_column: "Req ID"
    name_column: "Name"
    format: csv
```

### 3. Add doxygen to your functions

```c
/** @participant Sensor Driver */

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

Behavioral sequence diagrams are generated from annotations. Most tags are **inferred** from AST:

| Tag | Purpose | Manual? |
|-----|---------|---------|
| `@req` | Requirement mapping | Yes — per function |
| `@participant` | Participant identity | Yes — once per file |
| `@note` | Diagram notes (doxygen built-in) | Yes — per function |
| `@after` | Precondition cross-reference | Yes — per function |
| `@loop` | Wrap handler in loop block | Yes — per function |
| `@group` | Wrap handler in group block | Yes — per function |
| `@return` | Return value documentation | Yes — per function |
| `@send_source` | Infrastructure root (e.g., on `Event_post`) | Yes — once |
| `@receive_source` | Registration root (e.g., on `Event_register`) | Yes — once |
| `@sends` | Events emitted | **Inferred from AST** |
| `@receives` | Events handled | **Inferred from AST** |
| `@calls` | Cross-module boundary calls | **Inferred from AST** |

The tool scans function bodies via tree-sitter to detect `event_post()` calls, `Event_register()` patterns, and cross-participant function calls automatically.

### Diagram Features

- **Entry chains** — upstream participant arrows (User→Mobile→Cloud) prepended from config
- **Label templates** — protocol labels from boundary function args (`MQTT: {arg0}`)
- **Hub backtracking** — REQs routed through dispatch hubs get entry chains automatically
- **Control flow** blocks (if/else, loops) from AST
- **Loop/group wrappers** — `@loop` and `@group` tags for repeated/grouped handlers
- **Boundary argument extraction** — function args rendered on arrows via tree-sitter
- **Incremental generation** — SHA-256 manifest skips unchanged diagrams

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
| `exclude` | list | `[]` | Regex patterns for files to skip (applies to both validation and trace) |
| `tags.req.cross_reference` | bool | `true` | Validate @req IDs exist in requirements file |
| `version_gate.current_version` | string | — | `auto:git`, `auto:cmake`, or explicit version |
| `version_gate.version_field` | string | — | Column in requirements file for version gating |

### `trace` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `format` | string | `plantuml` | Diagram output format |
| `participant_field` | string | — | Requirements file column for participant resolution |
| `static_participants` | list | `[]` | Actors/entities always shown (e.g., `{name: User, type: actor}`) |
| `external` | list | `[]` | External participants (see below) |
| `external_fallback` | string | `External` | Fallback name for unresolved sources (warns and omits in behavioral mode) |

### External participants

Route events to named external actors by prefix. Boundary functions produce protocol-labeled arrows via `label_template`. Entry chains prepend upstream participant arrows.

```yaml
trace:
  external:
    - Cloud:
        receives_prefix: ["MQTT:"]
        boundary_functions: ["CloudMgr_Publish"]
        label_template: "MQTT: {arg0}"
        entry_chain:
          - { from: User, to: Mobile, label: "{req_name}" }
          - { from: Mobile, to: Cloud, label: "{req_name}" }
    - MCU:
        receives_prefix: ["DURABLE:"]
        boundary_functions: ["DurableEventCb"]
    - Hardware:
        receives_prefix: ["EVENT_HW_"]
```

| Sub-key | Purpose |
|---------|---------|
| `receives_prefix` | Route `@receives` events matching these prefixes to this participant |
| `boundary_functions` | Function names that cross this system boundary (for `@calls` edges) |
| `label_template` | Format boundary call labels — `{arg0}`, `{arg1}` substituted from call args |
| `entry_chain` | Upstream participant arrows prepended before entry edge |

### `trace.options`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `autonumber` | bool | `true` | Number sequence arrows |
| `box_label` | string | `System` | Internal participant box label |
| `box_color` | string | `#LightBlue` | Internal participant box color |
| `min_edges` | int | `1` | Minimum behavioral edges to generate a diagram |
| `show_returns` | bool | `false` | Show return arrows and activation bars on @calls edges |
| `show_recovery_notes` | bool | `true` | Show callee names in empty catch blocks |
| `max_condition_length` | int | `80` | Truncation limit for alt/loop conditions |
| `infer_sends` | bool | `true` | Infer @sends from event post function calls |
| `infer_calls` | bool | `true` | Infer @calls from cross-module calls |
| `event_send_functions` | list | `[]` | Event post function names for generated code (escape hatch) |
| `event_receive_functions` | list | `[]` | Registration function names for generated code (escape hatch) |
| `legend` | bool | `false` | Render arrow style legend |
| `label_mode` | string | `brief` | Label style: `full` (event+function), `brief` (event name only) |

### `impact` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `requirements.file` | string | — | Path to requirements file |
| `requirements.format` | string | `csv` | Format: `csv`, `json`, or `yaml` |
| `requirements.id_column` | string | `Req ID` | Requirement ID column/field |
| `requirements.name_column` | string | `Requirement Name` | Requirement name column/field |

## File-Level Doxygen

Each source file should have a file-level doxygen block. Use `@participant` to set the participant name for all functions in the file:

```c
/**
 * @file
 * @brief Sensor hardware abstraction layer.
 * @version 1.0
 * @participant Sensor Driver
 */
```

The requirements-file `participant_field` takes precedence over `@participant` for participant resolution. `@participant` is the fallback when no requirements mapping exists. Enable `validate.presence.require_file_doxygen: true` to enforce file-level blocks.

For Python:

```python
## @file
## @brief Configuration loading and validation.
## @version 1.0
## @participant Config
```

## Infrastructure Roots

Mark event bus functions once to enable automatic inference:

```c
/**
 * @brief Post an event to all registered handlers.
 * @version 1.0
 * @req REQ-0050
 * @send_source
 */
void Event_post(uint64_t event, void *data) { ... }

/**
 * @brief Register a handler for events matching a bitmask.
 * @version 1.0
 * @req REQ-0050
 * @receive_source
 */
void Event_register(uint64_t mask, event_handler_fn handler) { ... }
```

With these in place, `@sends` and `@receives` are derived from the AST for all other functions — zero manual behavioral tags needed.

## Generated Code

For code generators that rebuild files on every build (UDM, protobuf, HAL generators), annotations on generated files are destroyed. Use `event_send_functions` and `event_receive_functions` as config-level overrides:

```yaml
trace:
  options:
    event_send_functions: ["dm_event_callback"]
    event_receive_functions: ["register_fsm_handler"]
```

These tell the tool to treat calls to these functions as send/registration points, equivalent to `@send_source` / `@receive_source` but without requiring tags on the generated source. **Use this only for generated code you cannot annotate.** For code you own, use `@send_source` / `@receive_source` tags instead.

Both default to empty lists — `@send_source` / `@receive_source` tags are the primary mechanism.

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

2. **Add `@participant` to each file** — one line per file sets the participant name for diagrams.

3. **Add `@return` to non-void functions** — required by default. Disable with `presence.require_return: false` during migration.

4. **Mark infrastructure roots** — if you have an event bus or message dispatcher, add `@send_source`/`@receive_source` once to enable automatic inference.

5. **Exclude paths you're not ready to cover**:

```yaml
validate:
  exclude:
    - "^vendor/"
    - "^legacy/"
```

## Supported Languages

| Language | Extensions | Comment Style | Body Detection |
|----------|-----------|---------------|----------------|
| C | `.c`, `.h` | `/** ... */` | Brace matching |
| C++ | `.cpp`, `.hpp`, `.cc`, `.cxx` | `/** ... */` | Brace matching |
| Java | `.java` | `/** ... */` | Brace matching |
| Python | `.py` | `## ...` | Indentation |

C++ template functions (`template<typename T> void func(...)`) are fully supported — doxygen comments are associated via tree-sitter AST sibling detection, handling `template_declaration` wrappers correctly.

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
