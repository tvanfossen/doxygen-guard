"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.5
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from doxygen_guard.impact import load_requirements_full
from doxygen_guard.tracer_models import (
    DiagramBuildParams,
    DiagramContext,
    Edge,
    Participant,
    TaggedFunction,
    resolve_by_prefix,
    resolve_ext_target,
)

from .collector import (
    _apply_emit_inference,
    collect_all_tagged_functions,
    detect_phantom_emits,
)
from .edges import (
    _build_call_edges,
    _build_ext_edges,
    _build_inbound_edges,
    _is_req_relevant_target,
    build_sequence_edges,
)
from .edges_ast import (
    _collect_assumes,
    _detect_dominant_spec,
    _infer_entry_edges,
    _toposort_emitters,
    build_sequence_edges_ast,
)
from .infrastructure import (
    generate_infrastructure_table,
    write_infrastructure_table,
)
from .renderer import (
    _find_undeclared_participants,
    _safe_id,
    _sanitize_label,
    _select_label_text,
    _write_diagrams_for_reqs,
    generate_plantuml,
    generate_plantuml_ast,
    write_diagram,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
_resolve_by_prefix = resolve_by_prefix
_resolve_ext_target = resolve_ext_target

__all__ = [
    "DiagramBuildParams",
    "DiagramContext",
    "Edge",
    "Participant",
    "TaggedFunction",
    "_apply_emit_inference",
    "_build_call_edges",
    "_build_ext_edges",
    "_build_inbound_edges",
    "_collect_assumes",
    "_detect_dominant_spec",
    "_find_undeclared_participants",
    "_infer_entry_edges",
    "_is_req_relevant_target",
    "_resolve_by_prefix",
    "_resolve_ext_target",
    "_safe_id",
    "_sanitize_label",
    "_select_label_text",
    "_toposort_emitters",
    "build_sequence_edges",
    "build_sequence_edges_ast",
    "collect_all_tagged_functions",
    "detect_phantom_emits",
    "generate_infrastructure_table",
    "generate_plantuml",
    "generate_plantuml_ast",
    "run_trace",
    "write_diagram",
    "write_infrastructure_table",
]


## @brief Orchestrate scanning, edge building, and diagram generation.
#  @version 1.8
#  @req REQ-TRACE-001
#  @return Tuple of (written file paths, warning messages)
def run_trace(
    source_dirs: list[str],
    config: dict[str, Any],
    req_id: str | None = None,
    trace_all: bool = False,
) -> tuple[list[Path], list[str]]:
    if not trace_all and not req_id:
        logger.error("Must specify --req or --all for trace command")
        return [], []

    all_tagged, participants, file_cache, full_reqs = _collect_and_validate(
        source_dirs, config, req_id
    )
    if not all_tagged:
        return [], []

    return _generate_or_cache(all_tagged, participants, file_cache, config, req_id, full_reqs)


## @brief Generate diagrams or return cached results if manifest is current.
#  @version 1.0
#  @internal
#  @return Tuple of (written file paths, warning messages)
def _generate_or_cache(
    all_tagged: list[TaggedFunction],
    participants: list[Participant],
    file_cache: dict | None,
    config: dict[str, Any],
    req_id: str | None,
    full_reqs: dict,
) -> tuple[list[Path], list[str]]:
    base_dir = config.get("output_dir", "docs/generated/")
    seq_dir = str(Path(base_dir) / "sequences")

    current_hash = _compute_trace_hash(all_tagged, config)
    manifest_path = Path(seq_dir) / ".trace-manifest.json"
    if _is_manifest_current(manifest_path, current_hash, req_id):
        existing = list(Path(seq_dir).glob("*.puml"))
        logger.info("Trace manifest current, skipping regeneration (%d files)", len(existing))
        return existing, []

    params = DiagramBuildParams(all_tagged, participants, config, file_cache)
    written, warnings = _write_diagrams_for_reqs(params, seq_dir, req_id, full_reqs)
    _write_manifest(manifest_path, current_hash)
    return written, warnings


## @brief Collect tagged functions and run phantom detection.
#  @version 1.0
#  @internal
def _collect_and_validate(
    source_dirs: list[str], config: dict[str, Any], req_id: str | None
) -> tuple[list[TaggedFunction], list[Participant], dict | None, dict]:
    full_reqs = load_requirements_full(config)
    all_tagged, participants, file_cache = collect_all_tagged_functions(
        source_dirs, config, full_reqs
    )
    if not all_tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return [], [], None, full_reqs
    for tf in all_tagged:
        detect_phantom_emits(tf, config)
    return all_tagged, participants, file_cache, full_reqs


## @brief Compute a hash of all tagged function data for change detection.
#  @version 1.0
#  @internal
def _compute_trace_hash(tagged: list[TaggedFunction], config: dict[str, Any]) -> str:
    h = hashlib.sha256()
    for tf in sorted(tagged, key=lambda t: (t.file_path, t.name)):
        h.update(f"{tf.name}:{tf.file_path}:{tf.body}".encode("utf-8", errors="replace"))
    h.update(json.dumps(config.get("trace", {}), sort_keys=True).encode())
    return h.hexdigest()


## @brief Check if the stored manifest matches the current hash.
#  @version 1.0
#  @internal
def _is_manifest_current(manifest_path: Path, current_hash: str, req_id: str | None) -> bool:
    if req_id or not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return data.get("hash") == current_hash


## @brief Write the trace manifest with the current hash.
#  @version 1.0
#  @internal
def _write_manifest(manifest_path: Path, current_hash: str) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"hash": current_hash}, indent=2))
