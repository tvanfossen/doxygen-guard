"""Regenerate golden test files from current examples.

Usage: .venv/bin/python scripts/regen_golden.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from doxygen_guard.config import load_config
from doxygen_guard.tracer import run_trace

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
GOLDEN_DIR = Path(__file__).parent.parent / "tests" / "golden"


def main() -> None:
    config = load_config(EXAMPLES_DIR / ".doxygen-guard.yaml")
    req_conf = config.get("impact", {}).get("requirements", {})
    if req_conf and req_conf.get("file"):
        req_file = EXAMPLES_DIR / req_conf["file"]
        if req_file.exists():
            req_conf["file"] = str(req_file)

    tmp_out = Path("/tmp/dg-golden-regen")
    config["output_dir"] = str(tmp_out)

    written, warnings = run_trace([str(EXAMPLES_DIR / "src")], config, trace_all=True)

    updated = 0
    added = 0
    for f in written:
        if f.suffix != ".puml":
            continue
        dest = GOLDEN_DIR / f.name
        if dest.exists():
            old = dest.read_text()
            new = f.read_text()
            if old != new:
                shutil.copy(f, dest)
                print(f"  updated: {f.name}")
                updated += 1
        else:
            shutil.copy(f, dest)
            print(f"  added:   {f.name}")
            added += 1

    print(f"\n{updated} updated, {added} added, {len(written)} total generated")
    for w in warnings:
        print(f"  WARNING: {w}")


if __name__ == "__main__":
    main()
