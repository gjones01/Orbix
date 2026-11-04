"""Shared pytest configuration for the Orbix test suite.

The tests never touch the network: HTTP is replaced by the fakes in
``tests/test_celestrak_api.py`` and orbital data comes from the real payload
captured in ``tests/fixtures``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def iss_payload() -> list[dict]:
    """Genuine CelesTrak OMM payload for ISS (ZARYA), captured 2026-11-04T19:54Z."""
    raw = (FIXTURES_DIR / "iss_gp_25544.json").read_text(encoding="utf-8")
    return json.loads(raw)


@pytest.fixture
def iss_payload_bytes() -> bytes:
    """The same payload as the exact bytes CelesTrak returned."""
    return (FIXTURES_DIR / "iss_gp_25544.json").read_bytes()
