"""LME copper: parser of the Westmetall tables and the COMEX tariff premium it is meant to remove."""

import numpy as np
import pandas as pd
import pytest

from marketsim.data import api
from marketsim.data.clean import parse_westmetall_table

TABLE = """
<table>
  <tr><th>date</th><th>LME Copper Cash-Settlement</th><th>LME Copper 3-month</th></tr>
  <tr><td>31. July 2025</td><td>9,569.50</td><td>9,627.00</td></tr>
  <tr><td>30. July 2025</td><td>9,666.00</td><td>9,720.50</td></tr>
  <tr><td>25. December 2024</td><td>-</td><td>-</td></tr>
  <tr><td colspan="3">averages</td></tr>
</table>
"""


def test_westmetall_table_is_parsed_into_dated_prices():
    series = parse_westmetall_table(TABLE)
    assert series.index.tolist() == [pd.Timestamp("2025-07-30"), pd.Timestamp("2025-07-31")]
    assert series.tolist() == [9666.0, 9569.5]


def test_lme_copper_tracks_comex_before_tariffs_and_ignores_the_tariff_jump():
    try:
        daily = api.load_commodities_daily()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    if "copper_lme_usd_per_lb" not in daily.columns:
        pytest.skip("commodities_daily was built without LME copper")
    comex, lme = daily["copper_usd_per_lb"], daily["copper_lme_usd_per_lb"]
    calm = slice("2021-01-01", "2023-12-31")
    premium = comex[calm] / lme[calm] - 1
    # No tariff premium on average; single days differ by ~1% because LME settles at London midday.
    assert abs(premium.mean()) < 0.01 and premium.abs().mean() < 0.02
    assert comex[calm].corr(lme[calm]) > 0.98
    # 2025-07-31: refined copper exempted from the US tariff, the COMEX premium collapsed in one day.
    jump = np.log(comex / comex.shift(1)).loc["2025-07-31"], np.log(lme / lme.shift(1)).loc["2025-07-31"]
    assert jump[0] < -0.15 and abs(jump[1]) < 0.03
