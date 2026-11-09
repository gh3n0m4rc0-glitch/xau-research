"""Test delle fonti extra (formati presi dalle risposte vere del 9 nov 2026)."""
import json
from datetime import datetime, timezone

import pandas as pd

from xau_macro import extra

NOW = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)


CNBC_SAMPLE = {"FormattedQuoteResult": {"FormattedQuote": [
    {"symbol": "US10YTIP", "code": 0, "last": "2.655%", "previous_day_closing": "2.618%", "last_time": "2026-11-09T16:30:37.000-0500"},
    {"symbol": ".DXY", "code": 0, "last": "101.672", "previous_day_closing": "102.298"},
    {"symbol": "@GC.1", "code": 0, "last": "4,147.30", "previous_day_closing": "4,074.90"},
    {"symbol": "@SI.1", "code": 0, "last": "50.10", "previous_day_closing": "49.00"},
    {"symbol": "GLD", "code": 0, "last": "379.53", "previous_day_closing": "372.52", "sharesout": "364.90M"},
    {"symbol": "CNY=", "code": 0, "last": "6.6727", "previous_day_closing": "6.676"},
    {"symbol": "XXX", "code": 1, "last": ""}]}}


def test_cnbc_parse_and_intraday_history():
    q = extra.parse_cnbc(CNBC_SAMPLE)
    assert q["@GC.1"]["last"] == 4147.30 and q["GLD"]["sharesout"] == 364.9e6 and "XXX" not in q
    hist = {}
    t0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    extra.intraday_from_quotes(q, hist, t0)
    q2 = extra.parse_cnbc(CNBC_SAMPLE)
    q2["US10YTIP"]["last"] = 2.755          # +10 pb in 4 ore
    q2[".DXY"]["last"] = 102.2              # dollaro su
    out = extra.intraday_from_quotes(q2, hist, t0.replace(hour=13))
    assert out["tasso_reale_10y"]["var_4h"] == 10.0
    assert out["tasso_reale_10y"]["var_1h"] is None        # nessun punto di 1 ora fa
    assert out["punteggio_4h"] < -0.3                       # reali e dollaro su = contro oro
    assert out["dollaro_dxy"]["var_24h"] < 0                # contro la chiusura di ieri
    assert len(hist["intraday"]["tasso_reale_10y"]) == 2


def test_etf_from_gld_shares():
    q = extra.parse_cnbc(CNBC_SAMPLE)
    hist = {"gld_tonnellate": [["2026-10-13", 1000.0]]}
    out = extra.etf_from_quotes(q, hist, datetime(2026, 11, 10, 14, tzinfo=timezone.utc))
    t = 364.9e6 * 379.53 / 4147.30 / 32150.7466
    assert abs(out["gld_tonnellate_stimate"] - t) < 0.1
    assert out["flusso_20g_t"] == round(t - 1000.0, 1)
    assert out["flusso_5g_t"] == round(t - 1000.0, 1)


def test_cleveland_month_json():
    data = [
        {"chart": {"subcaption": "2026-10"}, "dataset": [
            {"seriesname": "CPI Inflation", "data": [{"value": "0.31"}, {"value": "0.36"}, {"value": ""}]},
            {"seriesname": "Core CPI Inflation", "data": [{"value": "0.25"}]},
            {"seriesname": "Actual CPI Inflation", "data": [{"value": ""}]}]},
        {"chart": {"subcaption": "2026-11"}, "dataset": [
            {"seriesname": "CPI Inflation", "data": [{"value": "0.19"}]}]}]
    m = extra.parse_cleveland_month_json(data)
    assert m["2026-10"]["cpi_mm"] == 0.36 and m["2026-11"]["cpi_mm"] == 0.19


def test_nowcast_uses_month_before_release(monkeypatch):
    data = [{"chart": {"subcaption": "2026-10"}, "dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.45"}]}]},
            {"chart": {"subcaption": "2026-11"}, "dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.10"}]}]}]
    import json as _j
    resp = type("R", (), {"content": _j.dumps(data).encode()})()
    monkeypatch.setattr(extra, "_get", lambda *a, **k: resp)
    cal = [{"title": "CPI m/m", "country": "USD", "date": "2026-11-10T08:30:00-05:00", "forecast": "0.3%"}]
    nc, miss = extra.nowcast_block(cal, datetime(2026, 11, 9, 21, tzinfo=timezone.utc))
    c = nc["prossimi_cpi"][0]
    assert c["mese_dato"] == "2026-10" and c["nowcast"] == 0.45 and c["rischio"].startswith("dato caldo")


def test_fed_rss_with_bom_and_calendar():
    xml = b"\xef\xbb\xbf<?xml version='1.0' encoding='utf-8'?><rss><channel><item><title>Speech by Governor X</title><pubDate>Mon, 09 Nov 2026 15:00:00 GMT</pubDate></item></channel></rss>"
    assert extra.parse_rss_items(xml)[0]["titolo"] == "Speech by Governor X"
    assert extra.parse_rss_items(xml.decode("latin-1"))[0]["titolo"] == "Speech by Governor X"
    cal = {"events": [
        {"title": "Testimony - Chairman Kevin Warsh", "time": "10:00 a.m.", "month": "2026-11", "days": "12", "type": "Testimony"},
        {"title": "Speech - Governor Y", "time": "", "month": "2026-11", "days": "11", "type": "Speech"},
        {"title": "Beige Book", "time": "2:00 p.m.", "month": "2026-11", "days": "11", "type": "Beige Book"}]}
    ev = extra.parse_fed_calendar(cal, datetime(2026, 11, 9, 21, tzinfo=timezone.utc))
    assert [e["evento"] for e in ev] == ["Speech - Governor Y", "Testimony - Chairman Kevin Warsh"]
    assert ev[1]["ora_utc"] == "2026-11-12T15:00:00Z" and ev[0]["orario_indicativo"]


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
    monkeypatch.setattr(extra, "_get", lambda *a, **k: type("R", (), {"content": json.dumps(d).encode()})())
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


def test_cot_flow():
    df = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=120, freq="7D"),
                       "mm_net_pct_oi": [0.1 + 0.001 * i for i in range(120)]})
    out = extra.cot_flow_block(df)
    assert out["percentile_3anni"] == 1.0


def test_collect_survives_everything_down(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("rete giu'")
    monkeypatch.setattr(extra, "_get", boom)
    monkeypatch.setattr(extra, "fetch_cnbc", boom)
    monkeypatch.setattr(extra.requests, "post", boom)
    x, miss = extra.collect([], NOW, None, {}, boom, budget_s=60, history_path=str(tmp_path / "h.json"))
    assert x["peso_nel_bias"] == 0
    assert miss and "sintesi" in x


def test_upcoming_auctions_use_close_date():
    rows = [{"securityType": "Note", "securityTerm": "10-Year", "competitiveAuctionCloseDate": "2026-11-12T00:00:00",
             "competitiveAuctionCloseTime": "01:00 PM", "offeringAmount": "42000000000"},
            {"securityType": "Bill", "securityTerm": "4-Week", "competitiveAuctionCloseDate": "2026-11-12T00:00:00"}]
    up = extra.parse_upcoming(rows, NOW)
    assert up == [{"titolo": "Asta 10-Year", "ora_utc": "2026-11-12T18:00:00Z", "importo_mld": 42.0}]


def test_bls_error_never_shows_key(monkeypatch):
    key = "0123456789abcdef0123456789abcdef"     # chiave finta
    calls = []

    def fake(ids, k):
        calls.append(k)
        return {"status": "REQUEST_NOT_PROCESSED", "message": [f"The key:{key}. provided by the User is invalid."]}
    monkeypatch.setattr(extra, "_bls_post", fake)
    try:
        extra.fetch_bls(["CUSR0000SA0"], key + "\n")
        assert False
    except ValueError as e:
        assert key not in str(e)
    assert calls == [key, ""]          # chiave pulita, poi riprova senza chiave
