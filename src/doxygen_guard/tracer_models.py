"""Shared data models and resolution utilities for sequence diagram generation.

@brief Data classes and resolution functions used by both tracer.py and ast_walker.py.
@version 1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


## @brief A named actor in a sequence diagram, optionally receiving unhandled events by prefix.
#  @version 1.3
#  @internal
@dataclass
class Participant:
    name: str
    receives_prefix: list[str] = field(default_factory=list)
    boundary_functions: list[str] = field(default_factory=list)


## @brief Function metadata needed for diagram generation.
#  @version 1.7
#  @internal
@dataclass
class TaggedFunction:
    name: str
    file_path: str
    participant_name: str | None = None
    sends: list[str] = field(default_factory=list)
    receives: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reqs: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    loop: str | None = None
    group: str | None = None
    body: str = ""
    marker_tags: set[str] = field(default_factory=set)
    return_desc: str | None = None
    is_internal: bool = False

    ## @brief Return the display name for this function in diagrams.
    #  @version 1.0
    #  @internal
    #  @return participant_name if set, otherwise the function name
    @property
    def display_name(self) -> str:
        return self.participant_name or self.name


## @brief Split a calls reference 'module::func_name' into (module, func_name).
#  @version 1.1
#  @internal
#  @return Tuple of (module, func_name); module is empty string if no :: separator
def split_calls_ref(ref: str) -> tuple[str, str]:
    parts = ref.split("::", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", ref)


## @brief Extract just the function name from a calls reference.
#  @version 1.1
#  @internal
#  @return The function name portion after ::, or the full ref if no ::
def calls_func_name(ref: str) -> str:
    return split_calls_ref(ref)[1]


## @brief Context for rendering a diagram header (req metadata + preconditions).
#  @version 1.1
#  @internal
@dataclass
class DiagramContext:
    req_row: dict[str, str] | None = None
    preconditions: list[str] | None = None
    init_only_names: set[str] | None = None


## @brief An edge or control flow marker produced by the AST walk.
#  @version 1.0
#  @internal
@dataclass
class ASTEdge:
    kind: str
    edge: Edge | None = None
    label: str = ""


## @brief A directed edge in a sequence diagram.
#  @version 1.0
#  @internal
@dataclass
class Edge:
    from_name: str
    to_name: str
    label: str
    event: str | None = None
    style: str = "->"


## @brief Shared parameters for diagram generation across requirements.
#  @version 1.0
#  @internal
@dataclass
class DiagramBuildParams:
    all_tagged: list[TaggedFunction]
    participants: list[Participant]
    config: dict[str, Any]
    file_cache: dict | None = None


## @brief Route an unhandled event to an external participant via bus prefix.
#  @version 1.0
#  @req REQ-TRACE-003
def resolve_by_prefix(
    event: str,
    externals: list[Participant],
) -> str | None:
    for p in externals:
        for prefix in p.receives_prefix:
            if event.startswith(prefix):
                return p.name
    return None


## @brief Match calls reference against tagged functions by name or file path.
#  @version 1.0
#  @internal
def _resolve_ext_from_tagged(
    func_name: str,
    module: str,
    all_tagged: list[TaggedFunction],
) -> str | None:
    for tf in all_tagged:
        if tf.name == func_name and tf.participant_name:
            return tf.participant_name
    for tf in all_tagged:
        if tf.participant_name and module in Path(tf.file_path).parts:
            return tf.participant_name
    return None


## @brief Resolve a calls reference to a participant via function name, module path, or name match.
#  @version 1.6
#  @internal
def resolve_ext_target(
    func_name: str,
    module: str,
    all_tagged: list[TaggedFunction],
    participants: list[Participant] | None = None,
) -> str | None:
    result = _resolve_ext_from_tagged(func_name, module, all_tagged)
    if not result and participants:
        module_lower = module.lower()
        result = next((p.name for p in participants if p.name.lower() == module_lower), None)
    if not result and module:
        result = module.replace("_", " ").title()
    return result
