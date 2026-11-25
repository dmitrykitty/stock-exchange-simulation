"""Schemas of the auxiliary data tables that are not part of the data contract (``schemas.py``).

They are read and written through ``marketsim.data.api`` like the contract tables and checked by
``python -m marketsim.data.check``, but no module outside the data pipeline depends on them, so they can change
without the contract procedure.
"""

from __future__ import annotations

from marketsim.data.schemas import Column, TableSpec

OUTLIER_EXPLANATIONS = TableSpec(
    "outlier_explanations", "outlier_explanations.csv", "mapping",
    "Sourced explanations of daily moves above 15%: one row per (date, series).",
    columns=(Column("date", "s", False), Column("series", "s", False), Column("explanation", "s", False),
             Column("source_url", "s", False)),
    owner="D1",
)

OUTLIERS_REPORT = TableSpec(
    "outliers_report", "outliers_report.csv", "processed",
    "Every day with |daily log return| > 0.15, with its explanation ('unexplained' when none is sourced).",
    columns=(Column("date", "s", False), Column("series", "s", False), Column("log_return", "f", False),
             Column("pct_change", "f", False), Column("explanation", "s", False), Column("source_url", "s")),
    min_rows=0, owner="D1",
)

FF49_TO_BEA = TableSpec(
    "ff49_to_bea", "ff49_to_bea.csv", "mapping",
    "Fama-French 49 industries -> BEA industry codes (for the industry-portfolio checks).",
    columns=(Column("ff_code", "s", False), Column("ff_name", "s", False), Column("bea_code", "s", False),
             Column("bea_name", "s", False), Column("notes", "s", False)),
    owner="D2",
)

EXTRA_TABLES = (OUTLIER_EXPLANATIONS, OUTLIERS_REPORT, FF49_TO_BEA)
