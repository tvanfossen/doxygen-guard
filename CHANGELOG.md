# Changelog

All notable changes to doxygen-guard are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- Single-source version from pyproject.toml (was drifted: __init__ 0.2.0 vs pyproject 0.4.0)
- Encoding guard: non-UTF-8 source files skipped with warning instead of crash
- Tree-sitter parse errors caught and logged instead of crashing
- Subprocess timeouts on all git and plantuml calls
- Grammar dependency upper bounds pinned to prevent silent breakage

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
