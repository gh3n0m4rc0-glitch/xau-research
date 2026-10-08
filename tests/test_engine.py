import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from xau_macro import engine, llm, news, service, sources


def synth(trend, start=1.0, n=600, noise=0.02, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(start + np.cumsum(trend + rng.normal(0, noise, n)), index=idx)


def series_gold_bullish():
    s = {k: synth(0.0, 1.0, seed=i) for i, k in enumerate(engine.SIGN)}
    # ultimi 20 giorni: reali e dollaro scendono forte, breakeven sale
    for k, d in [("real_yield_10y", -0.03), ("dollar_broad", -0.3), ("nominal_2y", -0.02), ("breakeven_10y", 0.02)]:
        x = s[k].copy(); x.iloc[-20:] += np.cumsum(np.full(20, d)); s[k] = x
    s["dollar_broad"] += 120; s["vix"] = s["vix"].abs() + 15
    return s


def test_bullish_macro_gives_positive_bias():
    r = engine.latest(series_gold_bullish(), None, today=pd.Timestamp("2025-04-21"))
    assert r.bias > 0.4, r
    assert 0 < r.confidence <= 1
    assert "rialzista" in r.summary


def test_bearish_is_symmetric():
    s = series_gold_bullish()
    s = {k: (2 * v.iloc[0] - v) if k != "vix" else v for k, v in s.items()}
    r = engine.latest(s, None, today=pd.Timestamp("2025-04-21"))
    assert r.bias < -0.3, r


def test_history_no_nan_and_range():
    h = engine.build_history(series_gold_bullish())
    assert len(h) > 300
    assert h["bias"].between(-1, 1).all() and h["confidence"].between(0, 1).all()


def test_cot_contrarian():
    idx = pd.date_range("2020-01-07", periods=200, freq="7D")
    df = pd.DataFrame({"date": idx, "available_from": idx + pd.Timedelta(days=3), "mm_net_pct_oi": np.linspace(0.0, 0.5, 200)})
    sc = engine.cot_score_series(df, pd.DatetimeIndex([idx[-1] + pd.Timedelta(days=5)]))
    assert sc.iloc[-1] < -0.5  # massimo storico di long speculativi -> contrarian ribassista


def test_fred_and_cot_parsers():
    s = sources.parse_fred_csv("observation_date,DFII10\n2025-01-02,2.1\n2025-01-03,.\n2025-01-06,2.2\n", "DFII10")
    assert list(s.values) == [2.1, 2.2]
    cot = sources.parse_cot_json([{"report_date_as_yyyy_mm_dd": "2025-01-07T00:00:00.000", "m_money_positions_long_all": "200000",
                                   "m_money_positions_short_all": "50000", "open_interest_all": "500000"}])
    assert abs(cot["mm_net_pct_oi"].iloc[0] - 0.3) < 1e-9


def test_calendar_tiers_and_utc():
    raw = [{"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-02T08:30:00-04:00", "impact": "High"},
           {"title": "German ZEW", "country": "EUR", "date": "2026-10-02T05:00:00-04:00", "impact": "High"},
           {"title": "ISM Services PMI", "country": "USD", "date": "2026-10-05T10:00:00-04:00", "impact": "High"}]
    ev = news.normalize_calendar(raw)
    assert [e["tier"] for e in ev] == [1, 2]
    assert ev[0]["time_utc"] == "2026-10-02T12:30:00Z"
    up = news.upcoming(ev, datetime(2026, 10, 2, tzinfo=timezone.utc), 48)
    assert len(up) == 1


def test_rss_parser():
    xml = "<rss><channel><item><title>Gold hits record</title><pubDate>Mon, 05 Oct 2026</pubDate></item></channel></rss>"
    assert sources.parse_rss(xml, "x")[0]["title"] == "Gold hits record"


def test_llm_json_parsing_and_blend():
    v = llm.parse_llm_json('Ecco: {"news_bias": 1.7, "confidence": 0.8, "halt_trading": false, "risk_flags": ["Medio Oriente"], "summary_it": "ok"}')
    assert v["news_bias"] == 1.0
    m = engine.MacroResult(bias=0.5, confidence=0.6, summary="s", risk_multiplier=1.0)
    out = service.blend(m, v, 0.25)
    assert out["bias"] > 0.5 and out["confidence"] > 0.6 and out["risk_multiplier"] == 0.8
    json.dumps(out)  # serializzabile
