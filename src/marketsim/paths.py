"""Data locations. The data root can be overridden with the MARKETSIM_DATA environment variable."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("MARKETSIM_DATA", PROJECT_ROOT / "data"))


def raw_dir() -> Path:
    """Files exactly as downloaded. Never edited by hand."""
    return data_dir() / "raw"


def processed_dir() -> Path:
    """Cleaned tables that follow marketsim.data.schemas."""
    return data_dir() / "processed"


def mapping_dir() -> Path:
    """Manual work (company-to-industry mapping etc.). Committed to git."""
    return data_dir() / "mapping"


def graph_dir() -> Path:
    """Dependency graphs (contract K2). Committed to git."""
    return data_dir() / "graph"


def fixtures_dir() -> Path:
    """Small synthetic data for tests and for the simulation before real data exists."""
    return data_dir() / "fixtures"


def runs_dir() -> Path:
    """Run logs (contract K3). Not committed."""
    return Path(os.environ.get("MARKETSIM_RUNS", PROJECT_ROOT / "runs"))
