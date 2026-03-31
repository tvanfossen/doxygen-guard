"""Golden file regression tests for sequence diagram generation.

@brief Regenerate example diagrams and diff against committed baselines.
@version 1.0
"""

from __future__ import annotations

from pathlib import Path

from doxygen_guard.config import load_config
from doxygen_guard.tracer import run_trace

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
GOLDEN_DIR = Path(__file__).parent / "golden"


## @brief Load the example project config with resolved paths.
#  @version 1.0
def _load_example_config() -> dict:
    config = load_config(EXAMPLES_DIR / ".doxygen-guard.yaml")
    req_conf = config.get("impact", {}).get("requirements", {})
    if req_conf and req_conf.get("file"):
        req_file = EXAMPLES_DIR / req_conf["file"]
        if req_file.exists():
            req_conf["file"] = str(req_file)
    return config


class TestGoldenFiles:
    """Regenerate diagrams and compare against golden baselines."""

    def test_golden_files_match(self, tmp_path):
        """Regenerated diagrams must match committed golden files."""
        config = _load_example_config()
        config["output_dir"] = str(tmp_path / "docs" / "generated")

        source_dir = str(EXAMPLES_DIR / "src")
        written, _warnings = run_trace([source_dir], config, trace_all=True)

        puml_files = [p for p in written if p.suffix == ".puml"]
        assert len(puml_files) > 0, "No .puml files generated"

        golden_files = sorted(GOLDEN_DIR.glob("*.puml"))
        assert len(golden_files) > 0, "No golden files found"

        golden_names = {f.name for f in golden_files}
        generated_names = {f.name for f in puml_files}

        missing = golden_names - generated_names
        assert not missing, f"Golden files not regenerated: {missing}"

        for golden in golden_files:
            generated = tmp_path / "docs" / "generated" / "sequences" / golden.name
            if not generated.exists():
                continue
            expected = golden.read_text()
            actual = generated.read_text()
            assert actual == expected, (
                f"Golden file mismatch: {golden.name}\nRegenerate with: cp {generated} {golden}"
            )
