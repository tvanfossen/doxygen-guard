"""Shared test fixtures for doxygen-guard."""

from pathlib import Path

import pytest

from doxygen_guard.config import VALIDATE_DEFAULTS
from doxygen_guard.parser import ParseSettings

FIXTURES_DIR = Path(__file__).parent / "fixtures"

COMMENT_START = VALIDATE_DEFAULTS["comment_style"]["start"]
COMMENT_END = VALIDATE_DEFAULTS["comment_style"]["end"]
C_PATTERN = VALIDATE_DEFAULTS["languages"]["c"]["function_pattern"]
C_EXCLUDES = VALIDATE_DEFAULTS["languages"]["c"]["exclude_names"]
C_SETTINGS = ParseSettings(comment_start=COMMENT_START, comment_end=COMMENT_END)
CPP_PATTERN = VALIDATE_DEFAULTS["languages"]["cpp"]["function_pattern"]
CPP_EXCLUDES = VALIDATE_DEFAULTS["languages"]["cpp"]["exclude_names"]
CPP_SETTINGS = ParseSettings(comment_start=COMMENT_START, comment_end=COMMENT_END)


@pytest.fixture()
def fixtures_dir():
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR
