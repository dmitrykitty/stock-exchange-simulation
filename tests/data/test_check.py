"""marketsim.data.check: the checks the schemas cannot express."""

import pandas as pd

from marketsim.data.check import cross_checks

DATES = pd.date_range("2024-01-02", periods=3, name="date")


def _daily(**extra) -> pd.DataFrame:
    return pd.DataFrame({"wti": [70.0, 71.0, 72.0], **extra}, index=DATES)


def test_lme_copper_must_be_a_positive_float_price_without_gaps():
    assert cross_checks({"commodities_daily": _daily(copper_lme_usd_per_lb=[4.0, 4.1, 4.2])}) == []
    for bad in ([4.0, None, 4.2], [4.0, -1.0, 4.2]):
        assert any("copper_lme_usd_per_lb" in p
                   for p in cross_checks({"commodities_daily": _daily(copper_lme_usd_per_lb=bad)}))
    assert any("missing" in p for p in cross_checks({"commodities_daily": _daily()}))


def test_external_scenario_levels_must_be_positive():
    good = pd.DataFrame({"date": DATES, "wti": [70.0, 71.0, 72.0]})
    assert cross_checks({"external_x": good}) == []
    assert cross_checks({"external_x": good.assign(wti=[70.0, 0.0, 72.0])}) == ["external_x: signal levels must be > 0"]
