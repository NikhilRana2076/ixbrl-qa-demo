"""Integration tests run against the REAL `src/` package (no stubs, no API calls).

Kept apart from tests/ because tests/conftest.py puts the stub package first on
sys.path; mixing the two in one pytest process would import the wrong `src`.
Run with:  python -m pytest -q tests_integration
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = ROOT / "samples"
VODAFONE = SAMPLES / "Vodafone_Group_plc_FY2026.html"


@pytest.fixture(scope="session")
def edge_filing():
    from src.ingestion.parser import parse_filing

    return parse_filing(FIXTURES / "edge_cases.xhtml")


@pytest.fixture(scope="session")
def facts(edge_filing):
    """Numeric facts keyed by (concept, context_id)."""
    return {(f.concept, f.context_id): f for f in edge_filing.numeric_facts}
