# Security Analysis — doxygen-guard

**Date:** 2026-03-19
**Scope:** Full codebase, all source modules

## Findings

### Medium Severity

#### 1. `req_id` used unsanitized in file path — path traversal via `@req` tags

**Location:** `tracer.py:637`

```python
puml_file = out_path / f"{req_id}.puml"
```

`req_id` comes from `@req` tags in source files. A crafted `@req ../../tmp/payload` would
write `../../tmp/payload.puml` relative to the output directory. `Path` join with `..`
components works on Linux.

**Remediation:** Sanitize `req_id` before filename construction:
```python
if ".." in req_id or "/" in req_id or "\\" in req_id:
    raise ValueError(f"Invalid requirement ID: {req_id}")
```

#### 2. `run_trace` subcommand bypasses `validate_output_path`

**Location:** `main.py:312-316` (no validation), vs `main.py:229` (pre-commit validates)

The `trace` CLI subcommand reads `output_dir` from config and passes it through to
`write_diagram` without calling `validate_output_path`. A malicious `.doxygen-guard.yaml`
with `output_dir: /etc/cron.d` would write `.puml` files to an arbitrary location.

**Boundary assessment:** Defensible for local developer use (the developer controls the
config). Problematic if the trace subcommand is ever invoked in CI with untrusted config.

**Remediation:** Add `validate_output_path(base_dir)` in `run_trace` or
`_run_trace_command`, with an `--allow-absolute` flag for tests.

#### 3. User-supplied regex compiled without backtracking protection

**Location:** `parser.py:257` (function_pattern), `tracer.py:199` (exclude patterns)

Config-supplied regex patterns are compiled directly via `re.compile()`. A malicious
config could supply `(a+)+$` causing ReDoS. Limited to supply-chain attack scenario
(malicious PR modifying config).

### Low Severity

#### 4. PlantUML injection via requirement names

**Location:** `tracer.py:506-528` (`_render_req_header`)

Requirement names/descriptions from CSV flow into PlantUML header text. A crafted
requirement name could inject PlantUML directives. Cosmetic impact only.

#### 5. Unhandled `re.error` on exclude patterns

**Location:** `tracer.py:199`, `main.py:104`

Malformed exclude patterns in config crash with unhandled `re.error`. Not exploitable
but poor robustness.

#### 6. No file size limit on `read_text()`

**Location:** `config.py:327`

Entire source file read into memory. Self-defeating in pre-commit context (attacker
must stage the file).

## Positive Findings

- **YAML uses `safe_load` everywhere** — `config.py:270`, `impact.py:238`
- **Git commands use list-form `subprocess.run`** — no shell injection possible via `git.py`
- **`--` separator used in git commands** — prevents flag injection via filenames
- **`validate_output_path` correctly rejects `..` and absolute paths** — `config.py:288-296`
