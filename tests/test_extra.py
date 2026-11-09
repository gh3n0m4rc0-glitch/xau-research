"""Test delle fonti extra (formati presi dalle risposte vere del 9 nov 2026)."""
from datetime import datetime, timezone

import pandas as pd

from xau_macro import extra

NOW = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)


def test_yahoo_parse_and_intraday():
    ts = [1794178800 + 900 * i for i in range(120)]
    data = {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"close": [100 + i * 0.01 for i in range(119)] + [None]}]}}], "error": None}}
    s = extra.parse_yahoo_chart(data)
    assert len(s) == 119
    assert extra.change_over(s, 1, True) > 0

    def fake(sym):
        if sym == "^TNX":
            return pd.Series([4.10, 4.20], index=pd.to_datetime([NOW.timestamp() - 86400, NOW.timestamp()], unit="s", utc=True))
        if sym == "DX-Y.NYB":
            return pd.Series([100.0, 100.6], index=pd.to_datetime([NOW.timestamp() - 86400, NOW.timestamp()], unit="s", utc=True))
        raise RuntimeError("non disponibile")
    out, miss = extra.intraday_block(fake)
    assert out["tasso_10y"]["var_24h"] == 10.0          # +10 pb
    assert out["punteggio_24h"] < -0.5                  # tassi e dollaro su = contro oro
    assert len(miss) == len(extra.YAHOO_SYMBOLS) - 2


def test_bls_and_surprise_cpi_hot():
    bls = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": "CUSR0000SA0", "data": [
            {"year": "2026", "period": "M10", "value": "330.0"},
            {"year": "2026", "period": "M09", "value": "328.68"}]}]}}
    data = extra.parse_bls(bls)
    cal = [{"title": "CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.6%"}]
    res = extra.surprises(cal, NOW, data)
    assert res[0]["stato"] == "ok"
    assert res[0]["dato"] == 0.4
    assert res[0]["verso_oro"] == -1          # CPI caldo = oro giu'


def test_surprise_waits_for_new_month():
    data = {"CUSR0000SA0": pd.Series([328.0, 329.0], index=[pd.Timestamp(2026, 8, 1), pd.Timestamp(2026, 9, 1)])}
    cal = [{"title": "CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "forecast": "0.3%"}]
    assert extra.surprises(cal, NOW, data)[0]["stato"].startswith("dato non")


def test_nfp_number_parse():
    assert extra.parse_number("187K") == 187
    assert extra.parse_number("-12.5K") == -12.5
    assert extra.parse_number("1.2M") == 1200
    assert extra.parse_number("") is None


def test_effr_and_fed_path(monkeypatch):
    d = {"refRates": [{"effectiveDate": "2026-11-06", "type": "EFFR", "percentRate": 3.88, "targetRateFrom": 3.75, "targetRateTo": 4.00}]}
    assert extra.parse_effr(d)[0] == 3.88
    idx = pd.bdate_range("2026-09-01", periods=30)
    one_y = pd.Series([3.70] * 10 + [3.40] * 20, index=idx)
    monkeypatch.setattr(extra, "_get", lambda *a, **k: type("R", (), {"json": lambda self: d})())
    out, miss = extra.fed_path_block({"1y": one_y})
    assert out["tagli_prezzati_1y"] == round((3.88 - 3.40) / 0.25, 2)
    assert out["punteggio"] > 0               # piu' tagli prezzati di un mese fa = oro su


def test_auction_quality():
    rows = []
    for i, btc in enumerate([2.5, 2.6, 2.55, 2.45, 2.5, 2.52, 2.1]):
        rows.append({"originalSecurityTerm": "10-Year", "competitiveAuctionCloseDate": f"2026-0{i + 2}-10T00:00:00",
                     "bidToCoverRatio": str(btc), "primaryDealersCompetitiveParAwarded": str(4e9 if i < 6 else 12e9),
                     "totalParAwardedInsideOfOfferingAmount": "39000000000", "highYield": "4.5", "medianYield": "4.45"})
    q = extra.auction_quality(rows)
    assert q[0]["giudizio"].startswith("debole")


def test_myfxbook_parse():
    html = "<p>48% of the forex traders are currently going short with XAU/USD, meanwhile 52% of the forex traders are going long</p>"
    assert extra.parse_myfxbook(html) == {"privati_long_pct": 52, "privati_short_pct": 48}


def test_gdelt_parse():
    data = {"timeline": [{"series": "Volume Intensity", "data": [{"date": "20261108T000000Z", "value": 0.5}, {"date": "20261109T000000Z", "value": 0.9}]}]}
    assert extra.parse_gdelt_timeline(data).iloc[-1] == 0.9


def test_gld_csv_parse():
    rows = "\n".join(f"{d:%d-%b-%Y},400.0,{870 + i * 0.5:.2f}" for i, d in enumerate(pd.bdate_range("2026-08-01", periods=60)))
    text = "SPDR Gold\n\nDate,GLD Close,Tonnes of Gold\n" + rows
    s = extra.parse_gld_csv(text)
    assert len(s) == 60 and s.iloc[-1] > s.iloc[0]


def test_cot_flow():
    df = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=120, freq="7D"),
                       "mm_net_pct_oi": [0.1 + 0.001 * i for i in range(120)]})
    out = extra.cot_flow_block(df)
    assert out["percentile_3anni"] == 1.0


def test_collect_survives_everything_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rete giu'")
    monkeypatch.setattr(extra, "_get", boom)
    monkeypatch.setattr(extra, "fetch_yahoo", boom)
    monkeypatch.setattr(extra.requests, "post", boom)
    x, miss = extra.collect([], NOW, None, {}, boom, budget_s=60)
    assert x["peso_nel_bias"] == 0
    assert miss and "sintesi" in x
