"""Sequence diagram generation from doxygen tags.

@brief Scan source files for @emits/@handles/@ext/@triggers tags and generate PlantUML diagrams.
@version 1.4
"""

from __future__ import annotations

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
#  @version 1.7
#  @req REQ-TRACE-001
def run_trace(
    source_dirs: list[str],
    config: dict[str, Any],
    req_id: str | None = None,
    trace_all: bool = False,
) -> tuple[list[Path], list[str]]:
    if not trace_all and not req_id:
        logger.error("Must specify --req or --all for trace command")
        return [], []

    full_reqs = load_requirements_full(config)
    all_tagged, participants, file_cache = collect_all_tagged_functions(
        source_dirs, config, full_reqs
    )
    if not all_tagged:
        logger.warning("No tagged functions found%s", f" for {req_id}" if req_id else "")
        return [], []

    for tf in all_tagged:
        detect_phantom_emits(tf, config)

    base_dir = config.get("output_dir", "docs/generated/")
    seq_dir = str(Path(base_dir) / "sequences")
    params = DiagramBuildParams(all_tagged, participants, config, file_cache)
    written, warnings = _write_diagrams_for_reqs(params, seq_dir, req_id, full_reqs)

    return written, warnings
