# Adversarial Analysis Synthesis — doxygen-guard

**Date:** 2026-03-19
**Scope:** Full codebase, 8 focus areas
**Personas:** Explorer, Security, Negative, DRY, KISS, Positive
**Validation:** All tests pass, all 16 PlantUML files pass syntax check, no test writes to docs/generated/

---

## Critical Issues (0)

None.

---

## High Priority (6)

### H1. [Diagrams] 10 of 16 self-dogfood diagrams are note-only or self-edge-only

6 diagrams have zero edges (REQ-CONFIG-002, REQ-PARSE-002, REQ-PARSE-003, REQ-VAL-002,
REQ-VAL-003, REQ-VAL-004). REQ-TRACE-001 has 9+ edges but all are Trace→Trace self-calls.
3 others have only 1-2 self-edges. These diagrams communicate no inter-module sequence flow.

**Root cause:** Functions implementing these requirements lack `@emits`/`@handles`/`@ext`
tags, so the tracer has no edges to build. The tracer correctly produces note-only diagrams
in this case, but the output has near-zero documentation value.

**Fix:** Either add trace tags to these functions (making the diagrams useful) or suppress
diagram generation when no meaningful edges exist.

### H2. [Tracer] Duplicate event handlers silently dropped

`tracer.py:248` — `handler_map[event] = tf` overwrites without warning. If two functions
`@handles` the same event, the last one wins silently.

**Fix:** Log a warning when overwriting, or collect into a list.

### H3. [Tracer] Unresolved `@ext` targets produce no warning

`tracer.py:302-342` — `_resolve_ext_target` returns `None` when resolution fails. The
caller at `_build_ext_edges` uses the raw module string as the participant name without
logging that resolution failed. Users get wrong participant names with no diagnostic.

**Fix:** Log WARNING when resolution falls back to raw module string.

### H4. [Security] `req_id` used unsanitized in file path — path traversal

`tracer.py:637` — `out_path / f"{req_id}.puml"`. A crafted `@req ../../tmp/x` escapes the
output directory. See `.claude/reports/security/20260319-adversarial-security.md` for details.

**Fix:** Validate `req_id` contains no path separators or `..` before use as filename.

### H5. [DRY] Config `.get()` chain drilling — 18 instances across 5 modules

`config.get("validate", {}).get("something", {})` repeated ~18 times. High maintenance
burden if config schema changes; each site must be updated independently.

**Fix:** Typed config accessors or dataclass.

### H6. [Observability] Logging at WARNING default, no debug mode, exc_info=False

`main.py:361` — `logging.basicConfig(level=logging.WARNING)`. Users cannot see INFO/DEBUG
messages. `main.py:123-128` — exceptions logged without `exc_info=True`. When the hook
fails, users have no way to diagnose what happened.

**Fix:** Add `--verbose`/`-v` flag to set INFO/DEBUG level. Log exceptions with traceback.

---

## Medium Priority (9)

### M1. [Parser] Python decorators break doxygen association

`parser.py:105-114` — `_skip_blanks_and_attrs` only recognizes blanks and `__attribute__`
lines. A `@property` or `@staticmethod` decorator between the doxygen block and `def` line
prevents association. Decorated Python functions lose their doxygen.

### M2. [Tracer] `_resolve_ext_target` substring match on module path

`tracer.py:340` — `module in tf.file_path` is substring-based. Module `"comm"` matches
`"recommender"`. Short module names produce incorrect participant resolution.

### M3. [Security] `run_trace` bypasses `validate_output_path`

`main.py:312-316` — trace subcommand doesn't validate output_dir. Defensible for local use
but problematic for CI with untrusted config.

### M4. [KISS] `parse_source_file()` polymorphic return type

`config.py:315-338` — Returns `list[Function] | tuple[list[Function], str] | None`
depending on `return_content` flag. `-> Any` annotation confirms the smell. Only one caller
uses `return_content=True`; it could just read the file itself.

### M5. [KISS] Edge dicts should be dataclasses

Throughout `tracer.py`, edges are `dict[str, Any]` with keys `from`, `to`, `label`, etc.
Stringly-typed, no autocomplete, no type checking. A 5-line dataclass eliminates KeyError risk.

### M6. [DRY] `load_requirements_full()` called up to 4 times per run

`tracer.py:60`, `tracer.py:655`, `impact.py:175-180`, `checks.py:108` — same CSV parsed
from disk repeatedly. Load once, pass through.

### M7. [Negative] `_safe_id` only handles spaces and slashes

`tracer.py:573-574` — Doesn't escape hyphens, dots, parens, or other PlantUML-breaking
characters. Works with current simple participant names but will break with richer names.

### M8. [DRY] Test `parse_functions()` boilerplate — ~25 copy-paste call sites

Same 4-argument call pattern across 4 test files. A conftest helper `parse_c_fixture(name)`
would eliminate this.

### M9. [Negative] Pre-commit trace+impact+git_add pipeline has zero test coverage via main()

`test_main.py` pre-commit tests use `NO_REQ_CONFIG` which lacks `participant_field`, so
trace never triggers through the CLI entry point. The full pipeline is only exercised by the
actual hook, not the test suite.

---

## Low Priority (8)

### L1. [Tracer] `_find_inbound_callers` deduplicates by name only, not (name, file_path)

`tracer.py:385` — Two functions with the same name in different files: second silently dropped.

### L2. [DRY] `exclude_names` lists duplicated across C/C++/Java configs

`config.py:32-44, 56-69, 82` — 3 lists sharing ~30 lines. Extract shared base list.

### L3. [KISS] `_detect_current_version` dispatch dict premature for 2 entries

`main.py:206-213` — Plugin system for `auto:git` and `auto:cmake`. Simple `if/elif` suffices.

### L4. [KISS] `tracer.py:647-693` two near-identical code paths for single-req vs all-reqs

Could be unified: put single `req_id` in a list of one.

### L5. [DRY] JSON and YAML loaders structurally identical

`impact.py:225-242` — 14 lines of near-identical code differing only in `json.load` vs
`yaml.safe_load`.

### L6. [Negative] Test isolation relies on early-return for safety, not explicit output_dir

`test_main.py:161`, `test_trace.py:402` — Tests that exercise trace/impact paths without
`tmp_path` depend on early returns to avoid writing. Fragile.

### L7. [Negative] External participants without `receives_prefix` classified as internal

`tracer.py:469-493` — Explicitly listed external participants placed inside `box` group
if they lack `receives_prefix`.

### L8. [DRY] Hardcoded `"sequences"` subdirectory name in 2 modules

`tracer.py:715`, `main.py:245` — Both construct `Path(base_dir) / "sequences"`.

---

## Strengths Identified (8)

### S1. Clean module decomposition — each module owns exactly one concern

Config, parser, checks, git, tracer, impact, main. No cross-module internal access.
Dependency flow is directional: main orchestrates, others consume config/parser.

### S2. `RunCommand` dependency injection in git.py

Callable type alias with default makes the entire git layer testable without mocking
infrastructure. Zero monkeypatching in git tests.

### S3. Data-driven multi-language support via config, not code branches

Adding a language requires zero code changes — just a new dict entry in `VALIDATE_DEFAULTS`.

### S4. Self-dogfooding — the tool validates its own source and generates its own diagrams

Every function has `@brief`/`@version`. 17 requirements in CSV. 16 generated diagrams.
Version gating infrastructure ready for growth.

### S5. Git commands use list-form subprocess with `--` separator

No shell injection possible. Flag injection prevented.

### S6. `yaml.safe_load` used everywhere

No arbitrary object deserialization.

### S7. Schema validation with `_OPEN_DICT` sentinel

Type-checks known keys while allowing user-defined languages/tags. Catches typos without
blocking extensibility.

### S8. Tests document known limitations explicitly

`test_edge_cases.py:294-324` (unbalanced braces), `test_trace.py:196-214` (string false
positives) — asserted and documented, not papered over.

---

## Recommendations

### Quick Wins (< 1 hour each)

1. Sanitize `req_id` before filename construction (H4)
2. Warn on duplicate event handlers (H2)
3. Warn on unresolved `@ext` targets (H3)
4. Add `--verbose` flag for debug output (H6)
5. Escape more characters in `_safe_id` (M7)

### Medium Effort (1-4 hours each)

6. Add `validate_output_path` to `run_trace` with test escape hatch (M3)
7. Make `_skip_blanks_and_attrs` recognize Python decorators (M1)
8. Replace edge dicts with dataclass (M5)
9. Load requirements once per run and pass through (M6)
10. Add integration test for full pre-commit pipeline via main() (M9)

### Larger Refactors (half-day+)

11. Typed config dataclass to replace `.get()` chain drilling (H5)
12. Decide policy for note-only/self-edge-only diagrams (H1) — suppress, or add trace tags
13. Extract test helpers for parse boilerplate (M8)
