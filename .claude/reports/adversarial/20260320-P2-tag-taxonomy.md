# Adversarial Analysis: P2-20260320-001 — Trace Tag Taxonomy

**Date:** 2026-03-20
**Proposal:** `@supports`, `@assumes` tags and inbound caller scoping fix
**Verdict:** Phase 1 (inbound caller scoping) is sound and should ship immediately. Phase 2 (`@supports`/`@assumes`) has a critical gap around hub functions that needs design work before implementation.

---

## Step 1: What "Good" Looks Like

Analyzed 5 human-authored diagrams from d_linux_bissell_iot_midea:

| Diagram | Participants | Scope | Infrastructure handling |
|---------|-------------|-------|----------------------|
| **OTA** | RobotMCU, FileSystem, BISSELL_IOT, AWS, Http_Server | Download loop + update loop + reboot | DCI dispatch shown as colored call arrow, internals invisible |
| **Pairing** | user, RobotMCU, hostapd, wpa_supplicant, BISSELL_IOT, Mobile, AWS | Full pairing flow: keydance through MQTT ready | DCI events are labeled arrows (`DurableEventCb(EVENT_X, data)`), not expanded |
| **Heartbeat** | RobotMCU, BISSELL_IOT, HEARTBEAT_CHECK, WIFI_EVENTING, UDM_EVENTING, DCI_EVENTING | Timer loop + unhealthy recovery | Internal event chain shown explicitly — this IS the feature |
| **WiFi Startup** | iot_node_bissell, bissellIot_Initialize(), stopgap code, WifiItf_Init(), wpa_supplicant | Boot initialization sequence | Function-level participants (not module-level) |
| **Normal Running** | user, RobotMCU, BISSELL_IOT, AWS | Product status reporting + cycle summary | MQTT abstracted to single arrows |

### Patterns extracted

1. **Feature-scoped**: Each diagram shows ONE feature/flow. No cross-feature bleeding.
2. **Infrastructure invisible**: DCI dispatch, event routing, WiFi management internals are abstracted to labeled arrows. The reader sees `DurableEventCb(EVENT_START_UAP, ssid, pp, interface)` — not the 39-case switch statement that routes it.
3. **Hub functions appear as arrows, not participants**: `DurableEventCb()` is a call FROM BISSELL_IOT TO RobotMCU, not a box with 40 outbound edges.
4. **Preconditions are implicit**: OTA doesn't say "assumes pairing." The reader infers from context that MQTT is available. `@assumes` would make this explicit — an improvement.
5. **Abstraction level varies by diagram purpose**: Heartbeat shows internal event chain (the feature IS the eventing). OTA shows user-visible behavior. The right level of detail is feature-dependent, not formula-driven.
6. **External systems are external participants**: AWS, Mobile are outside the box.

### Quality bar

A generated diagram matches the human standard when:
- It tells a story a new engineer can follow in under 60 seconds
- It excludes everything not needed to understand that story
- Hub/dispatch mechanics are invisible unless they ARE the story

---

## Step 2: Self-Dogfood Pollution

### REQ-CONFIG-001 ("YAML config loading and merging")

**Legitimate edges (2 of 13):**
- `Config -> Config: validate_config_schema()` — direct config validation
- `load_config()` note — the entry point

**Bleed edges (11 of 13):**
- `Validate -> Config: validate_output_path()` — from `run_precommit` (inbound caller), which calls `validate_output_path`
- `Validate -> Git: git_add()` — from `run_precommit` staging generated output
- `Validate -> Impact: collect_changed_functions(), build_impact_report(), format_markdown(), format_json()` — from `run_precommit` impact orchestration
- `Validate -> Trace: run_trace()` — from `run_precommit` trace orchestration
- `Trace -> Config: validate_output_path()` — from `_run_trace_command`
- `Impact -> Config: validate_output_path()` — from `_run_impact_command`
- `Impact -> Impact: run_impact()` — from `_run_impact_command`

**Root cause:** `validate_output_path` is tagged `@req REQ-CONFIG-001`. It's called by `run_precommit`, `_run_trace_command`, and `_run_impact_command`. Those become inbound callers, and the full edge builder runs on them, pulling their entire call trees in.

### REQ-GIT-001 ("Git diff parsing")

**Legitimate edges (5):** `get_diff()`, `get_staged_diff()`, `parse_changed_lines()`, `get_changed_lines_for_file()`, `git_add()`

**Bleed edges (13):** The entire Validate block — `check_presence`, `check_req_coverage`, `check_version_staleness`, `check_tags`, `parse_source_file` — all pulled in via `run_precommit`/`validate_file` as inbound callers of `get_changed_lines_for_file()` and `git_add()`. Plus all trace/impact edges.

### REQ-VAL-001 ("Doxygen presence check")

**Legitimate edges (9):** `validate_file`, `run_validate`, `run_precommit`, `check_presence`, `check_req_coverage`, `check_version_staleness`, `check_tags`, `parse_source_file`, `get_changed_lines_for_file`

**Bleed edges (6):** `validate_output_path`, `git_add`, `collect_changed_functions`, `build_impact_report`, `format_markdown/format_json`, `run_trace` — all from `run_precommit` which IS tagged `@req REQ-VAL-001` but also orchestrates trace and impact.

**Key insight for REQ-VAL-001:** The bleed comes from `run_precommit` being a DIRECT `@req REQ-VAL-001` function, not an inbound caller. The inbound caller scoping fix alone does NOT solve this case. `run_precommit` calls `run_trace()` and `collect_changed_functions()` which ARE part of its body, and the call edge builder will include them.

---

## Step 3: Self-Dogfood Function Classification

### Functions tagged `@req` — reclassification under proposal

| Function | Current tag | Classification | Proposed tag |
|----------|-----------|----------------|-------------|
| **config.py** | | | |
| `load_config` | `@req REQ-CONFIG-001` | **Fulfills** | Keep |
| `validate_config_schema` | `@req REQ-CONFIG-001` | **Fulfills** | Keep |
| `validate_output_path` | `@req REQ-CONFIG-001` | **Supports** | `@utility @supports REQ-CONFIG-001 @supports REQ-TRACE-001 @supports REQ-IMPACT-003` |
| `get_language_config` | `@req REQ-CONFIG-002` | **Fulfills** | Keep |
| `resolve_parse_settings` | `@req REQ-CONFIG-002` | **Fulfills** | Keep |
| `parse_source_file` | `@req REQ-PARSE-001` | **Fulfills** | Keep (but lives in wrong file) |
| `parse_source_file_with_content` | `@req REQ-PARSE-001` | **Fulfills** | Keep |
| **main.py** | | | |
| `validate_file` | `@req REQ-VAL-001` | **Fulfills** | Keep |
| `run_validate` | `@req REQ-VAL-001` | **Fulfills** | Keep |
| `run_precommit` | `@req REQ-VAL-001` | **Hub — partially fulfills** | See analysis below |
| `_run_trace_command` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_run_impact_command` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| **tracer.py** | | | |
| `collect_all_tagged_functions` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_build_emit_edges` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_build_ext_edges` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_build_call_edges` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_build_trigger_edges` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `build_sequence_edges` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `generate_plantuml` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `write_diagram` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `run_trace` | `@req REQ-TRACE-001` | **Fulfills** | Keep |
| `_build_req_participant_map` | `@req REQ-TRACE-002` | **Fulfills** | Keep |
| `_resolve_participant_from_reqs` | `@req REQ-TRACE-002` | **Fulfills** | Keep |
| `_load_external_participants` | `@req REQ-TRACE-003` | **Fulfills** | Keep |
| `_resolve_by_prefix` | `@req REQ-TRACE-003` | **Fulfills** | Keep |
| **checks.py** | | | |
| `check_presence` | `@req REQ-VAL-001` | **Fulfills** | Keep |
| `check_version_staleness` | `@req REQ-VAL-002` | **Fulfills** | Keep |
| `check_tags` | `@req REQ-VAL-003` | **Fulfills** | Keep |
| `check_req_coverage` | `@req REQ-VAL-004` | **Fulfills** | Keep |
| **impact.py** | | | |
| `_extract_changed_functions` | `@req REQ-IMPACT-001` | **Fulfills** | Keep |
| `collect_changed_functions` | `@req REQ-IMPACT-001` | **Fulfills** | Keep |
| `load_requirements_full` | `@req REQ-IMPACT-002` | **Supports** | `@utility @supports REQ-IMPACT-002 @supports REQ-TRACE-001` (also used by tracer) |
| `load_requirements` | `@req REQ-IMPACT-002` | **Fulfills** | Keep |
| `filter_requirements_by_version` | `@req REQ-IMPACT-002` | **Fulfills** | Keep |
| `build_impact_report` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| `format_markdown` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| `format_json` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| `format_text` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| `run_impact` | `@req REQ-IMPACT-003` | **Fulfills** | Keep |
| **git.py** | | | |
| `get_staged_diff` | `@req REQ-GIT-001` | **Fulfills** | Keep |
| `get_diff` | `@req REQ-GIT-001` | **Fulfills** | Keep |
| `parse_changed_lines` | `@req REQ-GIT-001` | **Fulfills** | Keep |
| `git_add` | `@req REQ-GIT-001` | **Supports** | `@utility @supports REQ-GIT-001 @supports REQ-TRACE-001 @supports REQ-IMPACT-003` |
| `get_changed_lines_for_file` | `@req REQ-GIT-001` | **Fulfills** | Keep |
| **parser.py** | | | |
| `parse_doxygen_tags` | `@req REQ-PARSE-002` | **Fulfills** | Keep |
| `find_doxygen_block_before` | `@req REQ-PARSE-002` | **Fulfills** | Keep |
| `find_body_end` | `@req REQ-PARSE-001` | **Fulfills** | Keep |
| `find_body_end_indent` | `@req REQ-PARSE-003` | **Fulfills** | Keep |
| `is_forward_declaration` | `@req REQ-PARSE-001` | **Fulfills** | Keep |
| `parse_functions` | `@req REQ-PARSE-001` | **Fulfills** | Keep |
| `ParseSettings` | `@req REQ-CONFIG-002` | **Fulfills** (dataclass) | Keep |

### The `run_precommit` problem

`run_precommit` is the only true hub function in the self-dogfood codebase. It:
1. Runs validation (`_validate_files`) — fulfills REQ-VAL-001
2. Calls `validate_output_path` — supports multiple REQs
3. Calls `run_trace` — fulfills REQ-TRACE-001
4. Calls `collect_changed_functions` + `build_impact_report` + formatters — fulfills REQ-IMPACT-003
5. Calls `git_add` — supports multiple REQs

Under the proposal, `run_precommit` could be tagged:
```python
## @req REQ-VAL-001
## @supports REQ-TRACE-001
## @supports REQ-IMPACT-003
```

This means it appears in the REQ-VAL-001 diagram. But the call edge builder will STILL scan its body and find calls to `run_trace()`, `collect_changed_functions()`, etc. Those functions are tagged with different REQs, so they are in `all_tagged`. The `_build_call_edges` function will emit edges to them.

**The proposal's "only functions with @req REQ-XXX appear as participants" rule is stated but not implemented.** The current `_build_call_edges` emits edges to ANY tagged function found in the caller's body, regardless of whether the target shares the same @req.

### Simulated REQ-CONFIG-001 diagram after all fixes

After Phase 3 reclassification + Phase 1 inbound caller scoping + call edge filtering:

```
@startuml REQ-CONFIG-001
autonumber

participant "Config" as Config

note over Config: load_config()

Config -> Config: validate_config_schema()

@enduml
```

Two functions, one edge. Clean, focused, tells the story. Matches the quality bar.

But this ONLY works if `_build_call_edges` is modified to filter targets by shared @req tag. Without that change, `load_config()` calling `deep_merge()` (@utility — invisible, good) and `validate_config_schema()` (@req REQ-CONFIG-001 — visible, good) would work. But `validate_config_schema` calling `_validate_node` (@internal — invisible, good) would also work. So for CONFIG-001 specifically, it works even without the call edge filter because the only cross-REQ call is from run_precommit which would be scoped by inbound caller fix.

The problem manifests for REQ-VAL-001 where `run_precommit` IS a direct @req participant.

---

## Step 4: Simulation on d_linux_bissell_iot_midea

### durableInterface.c (4217 lines)

**Structure:**
- 39 `DURABLEIOT_IN_EVENT_*` cases across multiple dispatch functions
- 9 `DURABLEIOT_OUT_EVENT_*` cases in `do_eventOutCallback`
- Hub pattern: `dci_in_task` → `do_eventInCallback` → `invoke_event_callback` → `populate_event_data` → sub-dispatchers (`populate_event_data_settings`, `populate_event_data_cloud`, `populate_event_data_nav`, etc.)
- Already has some doxygen: `@handles DURABLE:dci_in_msg_queue`, `@emits FSM:EVENT_NTP_TIME_UPDATE`, `@callback`, `@internal`

**Under the proposal:**

The dispatch chain (`do_eventInCallback` → `populate_event_data` → sub-functions) would be tagged `@internal` or `@utility`. The hub doesn't fulfill any single feature — it routes ALL features. Currently there's no `@req` on these functions, so they wouldn't pollute feature diagrams.

The individual `bissellIot_Set*` API functions (28 of them in bissell_iot.cpp) are the feature interface. Each would get `@req REQ-XXX` for its feature. The DCI dispatch connecting them is infrastructure.

**Example doxygen block under proposal:**
```c
/**
 * @brief Dispatch dequeued durable in-event to appropriate handler
 * @handles DURABLE:dci_in_msg_queue
 * @supports REQ-OTA-001
 * @supports REQ-PAIR-001
 * @supports REQ-HEARTBEAT-001
 * @supports REQ-SHADOW-001
 * ... (39 more)
 * @internal
 * @version 1.0
 */
static void do_eventInCallback(durable_iot_in_event_t event)
```

That's a 42+ line doxygen block on a single function. This is the accepted cost per the proposal.

### shadow.c (1080 lines)

**Structure:**
- `evHandler` with `@handles` tags for 5 FSM events (WiFi disconnect/connect, MQTT connect/close, STA failure)
- `Shadow_initialize` registers the handler via `Event_register`
- JSON builders for shadow publish (config, status, firmware sections)

**Under the proposal:**
- `evHandler` already has `@handles` tags — works as-is
- Shadow module functions would get `@req REQ-SHADOW-001` or similar
- `@assumes REQ-PAIR-001` (MQTT can't work without pairing) — this cross-reference IS genuinely useful

**Would generated diagrams match human-authored?** For shadow specifically, yes. The `@handles` tags already scope correctly. The `@assumes REQ-PAIR-001` would add a header cross-reference that's currently missing from the human diagrams but would improve them.

### bissell_iot.cpp (1153 lines)

**Structure:**
- `bissellIot_Initialize` — the hub that sets up everything (DCI, WiFi, tasks)
- 28 `bissellIot_Set*` / `bissellIot_Get*` API functions — each queues a DCI out event
- Each API function is ~15 lines: validate input, set data model, queue event

**Under the proposal:**
- `bissellIot_Initialize` would be `@supports` for ALL features (everything depends on init)
- Each `bissellIot_Set*` function would get `@req REQ-XXX @emits DURABLE:EVENT_XXX`
- Generated diagrams would show the API call emitting a DCI event, which the `@handles` on durableInterface.c picks up

**Match with human-authored?** The human diagrams show `bissellIot_SetProductStatus()` as a colored arrow from RobotMCU to BISSELL_IOT. Under the proposal, the generated diagram would show the same function as an edge. The DCI dispatch internals remain invisible. This matches.

### Overall BISSELL assessment

The proposal works well for the BISSELL codebase BECAUSE:
1. Hub functions (`do_eventInCallback`, `bissellIot_Initialize`) are already `@internal` — they don't appear in feature diagrams
2. Feature functions have clear `@req` alignment
3. The `@emits`/`@handles` pattern already scopes correctly

The one exception: `bissellIot_Initialize` would need a massive `@supports` block. But it's already tagged `@internal`, so adding `@supports` is optional and informational.

---

## Step 5: Attack the Proposal

### Attack 1: Does `@supports` actually prevent bleed?

**Partially.** `@supports` prevents the tagged function from appearing as a PARTICIPANT in feature diagrams. But it does NOT prevent the function from appearing as an EDGE TARGET via `_build_call_edges`.

If function A is `@req REQ-X` and calls function B which is `@utility @supports REQ-X`, the call edge builder currently finds B in `all_tagged` (because B has `@supports`) and emits an edge from A to B. The proposal says B should NOT appear in the feature diagram, but the edge builder doesn't know this.

**Fix required:** `_build_call_edges` must filter out targets that are `@utility` or only have `@supports` for the current REQ. This is implied by "Only functions with @req REQ-XXX appear as sequence participants" but needs explicit implementation guidance.

**Severity: Medium.** The fix is straightforward once identified.

### Attack 2: Can `@assumes` be inferred from `@handles` chains?

**No, not reliably.** Consider: shadow's `evHandler` `@handles FSM:EVENT_MQTT_CONNACK`. MQTT CONNACK requires pairing (cloud certs). But there's no `@emits FSM:EVENT_MQTT_CONNACK` on any pairing function — MQTT connection is managed by a third module (cloud manager). The `@assumes` relationship between shadow and pairing is semantic/architectural, not traceable through emit/handle chains.

You could infer SOME assumptions: if function A handles events that are only emitted after function B completes, then A assumes B. But this requires global dataflow analysis across the event bus, and it's fragile — one missing `@emits` tag breaks the inference.

**Recommendation:** Keep `@assumes` as a manual tag. It's lightweight (one line per precondition), architecturally meaningful, and captures domain knowledge that code analysis can't.

### Attack 3: Hub functions with 40+ `@handles` — maintainable?

**The doxygen block concern is overstated.** Looking at real hub functions:

- `do_eventInCallback` has ONE `@handles DURABLE:dci_in_msg_queue` tag — it handles one queue, not 39 individual events. The sub-dispatch functions (`populate_event_data_settings`, etc.) each have their own subset.
- `evHandler` in shadow.c has 5 `@handles` tags. Manageable.
- `run_precommit` would have 1 `@req` + 2 `@supports`. Manageable.

The 40+ tag scenario is a straw man. Real hub functions either:
1. Handle a single queue/dispatch point (one `@handles` tag), or
2. Are decomposed into sub-dispatchers that each handle a subset

The doxygen-guard hook enforces freshness on every commit. If a new event type is added to a switch case, the version check catches the body change. The `@handles` tag may not capture the new event, but that's a separate validation issue (and could be a future check: "switch case targets vs @handles alignment").

**Where it IS a real concern:** The `@supports` block. If `do_eventInCallback` genuinely supports 20+ requirements, listing them all is noisy. But this is informational — `@supports` is optional on `@internal` functions per the proposal. Only list the major features.

### Attack 4: Phase 1 inbound caller scoping — regression risk?

**Low risk, one edge case.**

Phase 1 changes `build_sequence_edges` so inbound callers only emit the edge TO the target function, not their full edge set. Currently:

```python
inbound = _find_inbound_callers(emitters, all_tagged)
all_emitters = list(emitters) + inbound
# Then ALL emitters get full edge building
```

After Phase 1:
```python
inbound = _find_inbound_callers(emitters, all_tagged)
# Inbound callers get ONLY the edge to the target function
for caller in inbound:
    # Emit edge from caller to whichever target it calls
    # Do NOT run _build_emit_edges, _build_ext_edges, _build_call_edges on caller
```

**Edge case:** An inbound caller that ALSO happens to emit an event that a target function handles. Currently this produces both a call edge (from body scan) and an emit edge (from @emits). After Phase 1, only the call edge remains. The emit edge is lost.

**Is this a real scenario?** In the self-dogfood codebase, no. In BISSELL, potentially — a function could call a DCI API function AND emit an event that the same feature handles. But this would be unusual and arguably a design smell.

**Recommendation:** Phase 1 is safe to ship. Document the edge case.

### Attack 5: Dual-role functions — fulfills one REQ, supports another

The proposal handles this: "@supports without @utility = function has its own @req for one feature but also supports others."

```python
## @req REQ-VAL-001
## @supports REQ-TRACE-001
## @supports REQ-IMPACT-003
def run_precommit(...):
```

`run_precommit` appears in REQ-VAL-001's diagram (via `@req`), but NOT in REQ-TRACE-001 or REQ-IMPACT-003 diagrams (via `@supports`).

**The problem:** In the REQ-VAL-001 diagram, `run_precommit`'s call edges to `run_trace()` and `collect_changed_functions()` still bleed in. The `@supports` declaration on `run_precommit` only affects OTHER diagrams, not the one it participates in.

**This is the proposal's biggest gap.** The call edge builder needs a second filter: when building edges for a function tagged `@req REQ-X`, only emit call edges to functions that are ALSO `@req REQ-X`. Edges to functions with different `@req` tags or `@supports` tags are excluded.

Without this filter, `run_precommit` in REQ-VAL-001 still shows edges to trace and impact functions, just like today.

**Severity: High.** This is the core "waiter's full shift" problem, and the proposal doesn't fully solve it for hub functions that are direct @req participants.

### Attack 6: `@supports` function that SHOULD appear in a feature diagram

**Yes, this exists.** Consider `validate_output_path` — it's shared infrastructure, but if a feature's acceptance criteria includes "output path must be validated," then the function IS part of the feature's story.

The proposal's binary classification (fulfills vs. supports) doesn't handle this. A function can be:
- Infrastructure for most features (supports)
- Part of the critical path for one specific feature (fulfills)

Currently `validate_output_path` is infrastructure for trace/impact but arguably fulfills REQ-CONFIG-001's "invalid keys rejected" criterion.

**Mitigation:** A function can have BOTH `@req REQ-CONFIG-001` AND `@supports REQ-TRACE-001 @supports REQ-IMPACT-003`. The proposal already allows this. It appears in CONFIG-001's diagram but not TRACE-001 or IMPACT-003.

---

## Step 6: Open Questions — Evidence-Based Recommendations

### Q1: Should `@supports` be required on `@utility` functions?

**Recommendation: Optional, with a lint warning for functions called by `@req`-tagged functions.**

Evidence:
- `deep_merge` in config.py is `@utility`. It's called by `load_config` (`@req REQ-CONFIG-001`). Adding `@supports REQ-CONFIG-001` to `deep_merge` provides marginal value — it's a pure dict merge utility.
- `_safe_id` in tracer.py is `@utility`. Adding `@supports REQ-TRACE-001` adds noise to what's clearly a string sanitizer.
- `validate_output_path` is currently `@req` but should be `@utility @supports`. Here the `@supports` IS valuable — it documents which features depend on path validation.

**Rule of thumb:** If the function name makes its purpose obvious and it's a pure utility (string formatting, data structure manipulation), `@supports` is noise. If the function implements domain logic that happens to be shared, `@supports` is valuable.

**Implementation:** Add a configurable lint check: "functions tagged @utility that are called by @req functions SHOULD have @supports." Warning, not error. Default off.

### Q2: Infrastructure overview format?

**Recommendation: Markdown dependency table, not PlantUML.**

Evidence from the human-authored diagrams: all 5 are sequence diagrams showing temporal flow. A `@supports` dependency graph is structural, not temporal. PlantUML component diagrams for structural relationships are:
1. Harder to read than a table for large dependency sets
2. Harder to diff in git
3. Harder to render in GitHub markdown preview

A dependency matrix is simpler and more useful:

```markdown
| Utility Function | Supports |
|-----------------|----------|
| validate_output_path | REQ-CONFIG-001, REQ-TRACE-001, REQ-IMPACT-003 |
| git_add | REQ-GIT-001, REQ-TRACE-001, REQ-IMPACT-003 |
| load_requirements_full | REQ-IMPACT-002, REQ-TRACE-001 |
```

The `@assumes` cross-reference table is also better as markdown:

```markdown
| Requirement | Assumes |
|------------|---------|
| REQ-OTA-001 | REQ-PAIR-001 (device paired, MQTT available) |
| REQ-SHADOW-001 | REQ-PAIR-001 (cloud certs provisioned) |
```

**Phase 4 can start simple (markdown table) and add PlantUML component diagram later if there's demand.**

### Q3: Hub functions with 40+ `@handles` — grouping syntax?

**Recommendation: No wildcard syntax. Decompose into sub-dispatchers instead.**

Evidence from durableInterface.c: the codebase ALREADY decomposes dispatch:
- `do_eventInCallback` handles 3 categories + callback
- `populate_event_data` chains 4 sub-dispatchers
- `populate_event_data_settings` handles 9 event types
- `populate_event_data_cloud` handles 3 event types
- `populate_event_data_nav` handles 5 event types

Each sub-dispatcher has a focused `@handles` block with 3-9 entries. The 40+ scenario doesn't actually exist in practice because good code decomposes it.

A wildcard like `@handles EVENT:DCI_*` is:
1. **Fragile**: Adding `DURABLEIOT_IN_EVENT_DOORBELL` shouldn't silently become part of the DCI handler contract
2. **Imprecise**: Wildcards hide which specific events are handled
3. **Unnecessary**: Real codebases already decompose dispatch

**If a function genuinely has 40+ `@handles` lines, that's a code smell — the function should be decomposed.** The doxygen block size is a useful pressure signal.

---

## Critical Findings Summary

| Finding | Severity | Phase affected |
|---------|----------|---------------|
| Call edge builder needs to filter targets by shared `@req` tag | **High** | Phase 1-2 |
| Inbound caller scoping alone doesn't fix hub functions that are direct `@req` participants | **High** | Phase 1 |
| `@supports` prevents participation but not edge targeting — implementation gap | **Medium** | Phase 2 |
| `@assumes` cannot be auto-inferred from `@handles` chains | **Low** | Phase 2 (confirms manual tagging is correct) |
| Phase 1 has minor regression risk for emit-and-call edge case | **Low** | Phase 1 |

### Recommended Phase 1 scope adjustment

Phase 1 should include TWO fixes, not one:

1. **Inbound caller scoping** (as proposed): Inbound callers emit only the edge to the target function
2. **Call edge REQ filtering** (new): When building call edges for a function tagged `@req REQ-X`, only emit edges to functions that are also `@req REQ-X` (or have `@handles`/`@emits` tags — trace-relevant targets)

Without fix #2, `run_precommit` in REQ-VAL-001 still bleeds trace/impact edges. With both fixes, REQ-VAL-001 shows only validation-related edges.
