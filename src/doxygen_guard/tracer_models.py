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
#  @version 1.2
#  @internal
@dataclass
class Participant:
    name: str
    receives_prefix: list[str] = field(default_factory=list)


## @brief Function metadata needed for diagram generation.
#  @version 1.7
#  @internal
@dataclass
class TaggedFunction:
    name: str
    file_path: str
    participant_name: str | None = None
    emits: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    ext: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    reqs: list[str] = field(default_factory=list)
    supports: list[str] = field(default_factory=list)
    assumes: list[str] = field(default_factory=list)
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


## @brief Context for rendering a diagram header (req metadata + preconditions).
#  @version 1.1
#  @internal
@dataclass
class DiagramContext:
    req_row: dict[str, str] | None = None
    preconditions: list[str] | None = None
    init_only_names: set[str] | None = None


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


## @brief Match ext reference against tagged functions by name or file path.
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


## @brief Resolve an ext reference to a participant via function name, module path, or name match.
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
