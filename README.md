# doxygen-guard

Pre-commit hook that validates doxygen comments, generates sequence diagrams from trace tags, and produces change-impact reports.

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
  options:
    autonumber: true

impact:
  requirements:
    file: docs/requirements.csv
    id_column: "Req ID"
    name_column: "Name"
    format: csv
```

### 3. Create your requirements file

CSV format:

```csv
Req ID,Name,Subsystem,Description,Acceptance Criteria
REQ-001,User Authentication,Auth Service,Authenticate users via OAuth2,Login flow completes within 5s
REQ-002,Data Persistence,Storage,Persist records to database,Records survive restart
```

YAML format:

```yaml
- id: REQ-001
  name: User Authentication
  module: Auth Service
  description: Authenticate users via OAuth2
  acceptance_criteria: Login flow completes within 5s
```

Set `format: yaml` and adjust `id_column`/`name_column` to match your field names.

## What It Does

### Validation (pre-commit gate)

Every function in staged files is checked for:

- **Presence** — must have a doxygen comment with `@brief` and `@version`
- **Version staleness** — if the function body changed (git diff), `@version` must be updated
- **Tag syntax** — tag values validated against configured patterns and prefixes
- **Requirement coverage** — functions must have `@req` or an exemption tag (`@utility`, `@internal`, `@callback`)

### Sequence Diagrams (auto-generated)

Functions tagged with `@emits`, `@handles`, `@ext`, and `@triggers` produce PlantUML sequence diagrams grouped by requirement. The tracer also scans function bodies for calls to other tagged functions.

```c
/**
 * @brief Handle incoming sensor data and decide on control action.
 * @version 1.0
 * @req REQ-030
 * @handles EVENT:SENSOR_DATA_READY
 * @emits EVENT:CONTROL_ACTION
 * @triggers THRESHOLD_CHECK
 */
void Controller_OnSensorData(int sensor_value) {
```

Diagrams are written to `<output_dir>/sequences/` with PNGs auto-generated if `plantuml` is on PATH.

### Change-Impact Reports (auto-generated)

Cross-references git diff with parsed functions to show which requirements are affected by staged changes. Reports written to `<output_dir>/impact/` in markdown and JSON.

## Configuration Reference

### `validate` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `languages` | dict | C, C++, Java, Python | Per-language function patterns and comment styles |
| `comment_style.start` | regex | `/\*\*(?!\*)` | Doxygen comment start pattern |
| `comment_style.end` | regex | `\*/` | Doxygen comment end pattern |
| `presence.require_doxygen` | bool | `true` | Require doxygen on every function |
| `presence.skip_forward_declarations` | bool | `true` | Skip C/C++ forward declarations |
| `version.tag` | string | `@version` | Version tag name |
| `version.require_present` | bool | `true` | Require version tag |
| `version.require_increment_on_change` | bool | `true` | Require version bump when body changes |
| `tags` | dict | `{}` | Per-tag validation rules (pattern, prefix, confidence markers) |
| `exclude` | list | `[]` | Regex patterns for files to skip |
| `version_gate` | dict | — | Gate requirement enforcement by project version |

### `trace` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `format` | string | `plantuml` | Diagram format |
| `participant_field` | string | — | Column/field in requirements file that maps REQs to diagram participants |
| `external` | list | `[]` | External participants with `receives_prefix` for event routing |
| `options.autonumber` | bool | `true` | Number sequence arrows |
| `options.box_label` | string | `System` | Label for the internal participant box |

### `impact` section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `requirements.file` | string | — | Path to requirements file |
| `requirements.format` | string | `csv` | Format: `csv`, `json`, or `yaml` |
| `requirements.id_column` | string | `Req ID` | Column/field containing requirement IDs |
| `requirements.name_column` | string | `Requirement Name` | Column/field containing requirement names |

### Tag validation rules

```yaml
validate:
  tags:
    req:
      pattern: "^REQ-\\w+$"
      confidence_markers: [verified, inferred]
    emits:
      require_prefix: ["EVENT:"]
    handles:
      require_prefix: ["EVENT:"]
    ext:
      require_contains: "::"
```

### External participants

Route unhandled events to named external actors by prefix:

```yaml
trace:
  external:
    - Cloud:
        receives_prefix: ["EVENT:CLOUD_"]
    - Hardware:
        receives_prefix: ["EVENT:HW_"]
```

### Version gate

Gate requirement enforcement by project version (useful during incremental adoption):

```yaml
validate:
  version_gate:
    current_version: "auto:git"  # or "auto:cmake" or "v1.2.0"
    version_field: "Min Version"  # column in requirements file
```

## Requirements Models

doxygen-guard supports both product-level and software-level requirements:

**Product-level** — feature-oriented, spans multiple modules. One requirement traces to many functions across subsystems.

```csv
Req ID,Name,Subsystem
REQ-PROD-001,Device Environmental Monitoring,Product
```

**Software-level** — module-oriented, maps tightly to code. One requirement per module boundary.

```csv
Req ID,Name,Subsystem
REQ-SENSOR-001,ADC Read Abstraction,Sensor Driver
```

Functions can carry both levels simultaneously:

```c
/**
 * @brief Read temperature from sensor hardware.
 * @req REQ-SENSOR-001
 * @req REQ-PROD-001
 */
```

The `participant_field` config key determines which column resolves diagram participants. Switch configs to see different diagram perspectives of the same codebase.

## Supported Languages

| Language | Extensions | Comment Style | Body Detection |
|----------|-----------|---------------|----------------|
| C | `.c`, `.h` | `/** ... */` | Brace matching |
| C++ | `.cpp`, `.hpp`, `.cc`, `.cxx` | `/** ... */` | Brace matching |
| Java | `.java` | `/** ... */` | Brace matching |
| Python | `.py` | `## ... / # ...` | Indentation |

Custom languages can be added in config under `validate.languages`.

## CLI Usage

```bash
# Pre-commit mode (default — called by pre-commit)
doxygen-guard [--config path] [files...]

# Explicit subcommands
doxygen-guard validate --no-git src/*.c
doxygen-guard trace --all src/
doxygen-guard trace --req REQ-001 src/
doxygen-guard impact --staged src/*.c

# Verbose mode
doxygen-guard -v validate --no-git src/*.c
```
