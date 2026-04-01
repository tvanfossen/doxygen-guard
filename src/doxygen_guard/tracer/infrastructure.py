"""Infrastructure overview table generation.

@brief Generate Markdown infrastructure table from @supports tags.
@version 1.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doxygen_guard.tracer_models import TaggedFunction

logger = logging.getLogger(__name__)


## @brief Generate infrastructure overview table from supports tags.
#  @version 1.0
#  @req REQ-TRACE-001
def generate_infrastructure_table(
    all_tagged: list[TaggedFunction],
) -> str:
    rows: list[tuple[str, str, str]] = []
    for tf in all_tagged:
        if not tf.supports:
            continue
        module = Path(tf.file_path).stem
        supports_str = ", ".join(tf.supports)
        rows.append((tf.name, module, supports_str))

    if not rows:
        return ""

    rows.sort(key=lambda r: (r[1], r[0]))
    lines = ["## Infrastructure Overview", ""]
    lines.append("| Function | Module | Supports |")
    lines.append("|----------|--------|----------|")
    for name, module, supports in rows:
        lines.append(f"| {name} | {module} | {supports} |")
    return "\n".join(lines) + "\n"


## @brief Write infrastructure table to output directory.
#  @version 1.0
#  @req REQ-TRACE-001
def write_infrastructure_table(
    all_tagged: list[TaggedFunction],
    output_dir: str,
) -> Path | None:
    content = generate_infrastructure_table(all_tagged)
    if not content:
        return None
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    infra_file = out_path / "infrastructure.md"
    infra_file.write_text(content)
    logger.info("Wrote infrastructure table: %s", infra_file)
    return infra_file
