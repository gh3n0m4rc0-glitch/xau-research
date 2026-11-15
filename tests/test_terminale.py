"""Test del terminale macro nel servizio (dati finti, nessuna chiamata in rete)."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from xau_macro import terminale

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

READING = {
    "versione": 2, "settimana": "16-20 novembre 2026", "scritto_il": "2026-11-15", "venerdi": "2026-11-13",
    "resoconto": ["Questa settimana comanda la Fed."],
    "mercati": [{"id": m, "verso": "ribassista" if m == "XAUUSD" else "neutro", "convinzione": 55 if m == "XAUUSD" else 35,
                 "frase": "x", "cosa_lo_smentisce": "y"} for m in terminale.MERCATI],
    "banche": [
        {"banca": "Fed", "report": "dot plot", "ultima": "2026-09-16", "prossima": "2026-12-09", "tasso": "3,75-4,00%", "tono": "falco",
         "in_parole_semplici": "z", "prossima_ora_utc": "2026-12-09T19:00:00Z", "prossima_decisione_utc": "2026-12-09T19:00:00Z", "tasso_num": 4.0},
        {"banca": "BCE", "report": "proiezioni", "ultima": "2026-09-10", "prossima": "2026-12-17", "tasso": "2,50%", "tono": "falco",
         "in_parole_semplici": "z", "prossima_ora_utc": "2026-12-17T13:15:00Z", "prossima_decisione_utc": "2026-12-17T13:15:00Z", "tasso_num": 2.5},
        {"banca": "BoE", "report": "MPR", "ultima": "2026-11-05", "prossima": "2027-02-04", "tasso": "4,00%", "tono": "falco",
         "in_parole_semplici": "z", "prossima_ora_utc": "2027-02-04T12:00:00Z", "prossima_decisione_utc": "2026-12-17T12:00:00Z", "tasso_num": 4.0},
    ],
    "fed_proiezioni": {"data_sep": "2026-09-16", "mediana": {"2026": 4.1, "2027": 4.1, "2028": 3.9, "lungo": 3.2}},
    "effetto_falco": {"Fed": {"XAUUSD": -1, "USDJPY": 1}, "BCE": {"EURUSD": 1}, "BoE": {"GBPUSD": 1}},
    "cot": [{"mercato": "XAUUSD", "data": "2026-11-03", "netto": 103614, "var_settimana": -1948, "netto_pct_oi": 25.96, "percentile_3anni": 22}],
    "prezzi_settimanali": {"XAUUSD": [["2026-09-11", 4350.0], ["2026-11-13", 3887.6]],
                           "EURUSD": [["2026-09-04", 1.16], ["2026-11-13", 1.1021]],
                           "GBPUSD": [["2026-10-30", 1.34], ["2026-11-13", 1.3215]],
                           "USDJPY": [["2026-09-11", 153.7], ["2026-11-13", 159.66]]},
    "calendario": [{"quando": "2026-11-17T13:30:00Z", "evento": "vendite"}, {"quando": "2026-11-18T19:00:00Z", "evento": "verbali Fed"}],
}


def _cot_rows():
    oro, fin = [], []
    d0 = pd.Timestamp("2023-10-03")
    for i in range(160):
        d = (d0 + pd.Timedelta(weeks=i)).strftime("%Y-%m-%dT00:00:00.000")
        oro.append({"report_date_as_yyyy_mm_dd": d, "open_interest_all": "400000",
                    "m_money_positions_long_all": str(150000 + 300 * i), "m_money_positions_short_all": "40000"})
        fin.append({"report_date_as_yyyy_mm_dd": d, "cftc_contract_market_code": "097741", "open_interest_all": "300000",
                    "lev_money_positions_long": "20000", "lev_money_positions_short": str(10000 + 200 * i),
                    "asset_mgr_positions_long": "5000", "asset_mgr_positions_short": "1000"})
        fin.append({"report_date_as_yyyy_mm_dd": d, "cftc_contract_market_code": "099741", "open_interest_all": "800000",
                    "lev_money_positions_long": "50000", "lev_money_positions_short": "60000",
                    "asset_mgr_positions_long": "300000", "asset_mgr_positions_short": "100000"})
    return oro, fin


def test_cot_series_flips_yen_and_reads_percentiles():
    s = terminale.cot_series(*_cot_rows())
    assert set(s) == {"XAUUSD", "USDJPY", "EURUSD"}
    # yen: fondi sempre piu' corti sullo yen = sempre piu' lunghi su USDJPY -> netto positivo e in salita
    j = terminale.cot_summary(s["USDJPY"])
    assert j["netto"] == 200 * 159 + 10000 - 20000 and j["percentile_3anni"] == 100 and j["var_settimana"] == 200
    assert j["asset_netto"] == -4000 and "carichi" in j["lettura"]
    o = terminale.cot_summary(s["XAUUSD"])
    assert o["data"] == "2026-10-20" and o["netto"] == 110000 + 300 * 159 and "asset_netto" not in o
    assert o["netto_pct_oi"] == round(100 * (110000 + 300 * 159) / 400000, 2)


def test_compact_weekly_with_yahoo_fx_timestamps():
    # Yahoo segna la barra del cambio di venerdi' 13 alle 23:00 UTC di giovedi' 12
    idx = pd.to_datetime(["2026-11-05 23:00", "2026-11-12 23:00", "2026-11-15 23:00", "2026-11-16 23:00"], utc=True)
    s = pd.Series([1.10, 1.1021, 1.1030, 1.1050], index=idx)
    pts = terminale.compact(s)
    assert pts == [["2026-11-06", 1.10], ["2026-11-13", 1.1021], ["2026-11-17", 1.105]]
    assert terminale.price_at(pts, "2026-11-13") == 1.1021 and terminale.price_at(pts, "2026-11-01") is None


def test_confirmations_follow_the_rule():
    conf = {(c["banca"], c["mercato"]): c for c in terminale.confirmations(READING, READING["prezzi_settimanali"])}
    assert conf[("Fed", "XAUUSD")]["esito"] == "conferma" and conf[("Fed", "XAUUSD")]["dal"] == "2026-09-11"
    assert conf[("Fed", "USDJPY")]["esito"] == "conferma"
    assert conf[("BCE", "EURUSD")]["esito"] == "smentisce" and conf[("BCE", "EURUSD")]["dal"] == "2026-09-04"
    assert conf[("BoE", "GBPUSD")]["esito"] == "smentisce"


def test_changes_decision_without_report_projections_and_rates():
    now = datetime(2026, 12, 18, 9, 0, tzinfo=timezone.utc)
    prezzi = {"GBPUSD": [["2026-12-11", 1.30], ["2026-12-16", 1.31], ["2026-12-18", 1.33]]}
    fed = {"data_sep": "2026-12-09", "mediana": {"2026": 4.4, "2027": 4.4, "2028": 3.9, "lungo": 3.2}}
    camb = terminale.changes(READING, now, prezzi, fed, {"Fed": {"valore": 4.25, "data": "2026-12-10"}, "BCE": {"valore": 2.5, "data": "2026-12-17"}})
    tipi = {(c["tipo"], c["banca"]) for c in camb}
    assert ("decisione", "Fed") in tipi and ("decisione", "BCE") in tipi and ("decisione", "BoE") in tipi
    boe = next(c for c in camb if c["tipo"] == "decisione" and c["banca"] == "BoE")
    assert boe["con_report"] is False and boe["mosse"] == [{"mercato": "GBPUSD", "dalla_decisione_pct": 1.53}]
    p = next(c for c in camb if c["tipo"] == "proiezioni_fed")
    assert p["verso"] == "piu' falco" and p["differenze"] == {"2026": 0.3, "2027": 0.3}
    t = next(c for c in camb if c["tipo"] == "tasso")
    assert t["banca"] == "Fed" and t["ora"] == 4.25 and "alza" in t["breve"]
    assert not [c for c in camb if c["tipo"] == "tasso" and c["banca"] == "BCE"]
    # prima della decisione e con tutto uguale: nessun avviso
    assert terminale.changes(READING, NOW, prezzi, {"data_sep": "2026-09-16", "mediana": dict(READING["fed_proiezioni"]["mediana"])},
                             {"Fed": {"valore": 4.0, "data": "2026-11-17"}}) == []


def test_run_uses_cache_and_never_leaks_errors(tmp_path):
    kdir, out = tmp_path / "knowledge", tmp_path / "public"
    kdir.mkdir()
    (kdir / "terminale.json").write_text(json.dumps(READING), encoding="utf-8")
    calls = {"cot": 0, "yahoo": 0}

    def cot_fetch(start):
        calls["cot"] += 1
        return _cot_rows()

    def yahoo(sym, rng, interval):
        calls["yahoo"] += 1
        if sym == "GC=F":
            raise RuntimeError("401 for url: https://query1.finance.yahoo.com/?crumb=SEGRETO")
        idx = pd.date_range("2026-03-02 14:30", periods=180, freq="D", tz="UTC")
        return pd.Series([100 + i * 0.1 for i in range(180)], index=idx)

    def fred_obs(sid, start):
        raise RuntimeError(f"FRED {sid}: risponde 400")

    block, missing = terminale.run(kdir, out, NOW, yahoo, None, 2, cot_fetch, fred_obs)
    assert calls == {"cot": 1, "yahoo": 6}
    assert block["mercati"]["XAUUSD"]["ultimo_prezzo"] == 3887.6          # Yahoo assente: chiusure del venerdi'
    assert block["fonti_prezzi"].get("US100") == "Yahoo" and block["fonti_prezzi"]["XAUUSD"] == "lettura"
    assert block["mercati"]["USDJPY"]["cot"]["percentile_3anni"] == 100
    assert block["vecchio"] is False and block["eta_giorni"] == 3
    assert [e["evento"] for e in block["prossimi_eventi"]] == ["verbali Fed"]
    assert "SEGRETO" not in json.dumps(block) + json.dumps(missing)
    assert any("XAUUSD" in m for m in missing)
    assert (out / "terminale_cache.json").exists()
    # un'ora dopo: niente rete, stessi numeri dalla cache
    block2, missing2 = terminale.run(kdir, out, NOW + timedelta(hours=1), yahoo, None, 2, cot_fetch, fred_obs)
    assert calls == {"cot": 1, "yahoo": 6} and missing2 == []
    assert block2["mercati"]["US100"]["ultimo_prezzo"] == block["mercati"]["US100"]["ultimo_prezzo"]
    # tre ore dopo: si riprende tutto
    terminale.run(kdir, out, NOW + timedelta(hours=3), yahoo, None, 2, cot_fetch, fred_obs)
    assert calls == {"cot": 2, "yahoo": 12}


def test_cnbc_day_uses_new_york_session():
    assert terminale._cnbc_day("2026-11-13T17:00:00.000-0500") == "2026-11-13"     # chiusura del venerdi'
    assert terminale._cnbc_day("2026-11-15T18:05:00.000-0500") == "2026-11-16"     # domenica sera = seduta di lunedi'
    assert terminale._cnbc_day("2026-11-13") == "2026-11-13" and terminale._cnbc_day(None) is None


def test_stale_fred_never_hides_newer_closes_and_cnbc_gives_today(tmp_path):
    """Su GitHub Yahoo non risponde e FRED sui cambi arriva una settimana dopo: restano le chiusure della lettura
    e l'ultimo prezzo arriva da CNBC (cosi' la conferma del grafico e' quella di oggi)."""
    kdir, out = tmp_path / "knowledge", tmp_path / "public"
    kdir.mkdir()
    (kdir / "terminale.json").write_text(json.dumps(READING), encoding="utf-8")

    def yahoo(sym, rng, interval):
        raise RuntimeError("Yahoo 429")

    def fred(sid, start):                     # DEXUSEU fermo al 6 novembre, piu' vecchio della lettura (13 novembre)
        idx = pd.date_range("2026-01-02", "2026-11-06", freq="B")
        return pd.Series([1.1154] * len(idx), index=idx)

    def cnbc(symbols):
        assert set(symbols) == set(terminale.CNBC.values())
        return {"EUR=": {"last": 1.0950, "time": "2026-11-18T06:40:00.000-0500"},
                "@GC.1": {"last": 3950.0, "time": "2026-11-18T06:41:00.000-0500"},
                "JPY=": {"last": 0.00627, "time": "2026-11-18T06:41:00.000-0500"},     # unita' sbagliata: scartato
                "GBP=": {"last": 1.32, "time": "2026-11-20T06:41:00.000-0500"}}        # nel futuro: scartato

    block, missing = terminale.run(kdir, out, NOW, yahoo, fred, 2, lambda s: _cot_rows(), lambda sid, d: [], cnbc)
    eu = block["mercati"]["EURUSD"]
    assert (eu["ultimo_prezzo"], eu["ultimo_prezzo_data"]) == (1.095, "2026-11-18")
    assert eu["da_venerdi_pct"] == terminale.pct(1.1021, 1.095)               # dal venerdi' della lettura, non da FRED
    assert block["fonti_prezzi"]["EURUSD"] == "FRED+lettura+CNBC"
    bce = [c for c in block["conferma_grafico"] if c["banca"] == "BCE"][0]
    assert bce["da_allora_pct"] == terminale.pct(1.16, 1.095) and bce["fino_a"] == "2026-11-18"
    assert block["mercati"]["XAUUSD"]["ultimo_prezzo"] == 3950.0 and block["fonti_prezzi"]["XAUUSD"] == "lettura+CNBC"
    assert block["mercati"]["USDJPY"]["ultimo_prezzo"] == 159.66 and block["fonti_prezzi"]["USDJPY"] == "FRED+lettura"
    assert block["mercati"]["GBPUSD"]["ultimo_prezzo_data"] == "2026-11-13"
    assert not any("XAUUSD" in m for m in missing)                            # oro coperto da CNBC
    assert any("CNBC USDJPY: prezzo fuori scala" in m for m in missing)
    # CNBC fermo: niente errori con indirizzi, si torna alle chiusure
    def cnbc_down(symbols):
        raise ConnectionError("HTTPSConnectionPool(host='quote.cnbc.com') SEGRETO")
    block2, missing2 = terminale.run(kdir, out, NOW + timedelta(hours=3), yahoo, fred, 2, lambda s: _cot_rows(), lambda sid, d: [], cnbc_down)
    assert block2["mercati"]["EURUSD"]["ultimo_prezzo"] == 1.1021
    assert "terminale CNBC: non raggiunto (ConnectionError)" in missing2 and "SEGRETO" not in json.dumps(missing2)


def test_run_without_reading(tmp_path):
    block, missing = terminale.run(tmp_path, tmp_path, NOW, cot_fetch=lambda s: ([], []), fred_obs=lambda s, d: [])
    assert block == {} and missing == ["terminale: manca knowledge/terminale.json"]


def test_short_text_is_ascii_and_short():
    b = terminale.build_block(READING, NOW, {})
    assert b["sintesi"] == "Terminale: oro ribassista 55/100, fondi leggeri (22/100)"
    b2 = terminale.build_block(READING, datetime(2026, 12, 18, 9, 0, tzinfo=timezone.utc), {})
    assert b2["vecchio"] and b2["sintesi"].startswith("NUOVO BoE ha deciso, da rileggere | terminale vecchio")
    assert all(ord(ch) < 128 for ch in b2["sintesi"]) and len(b2["sintesi"]) <= 90
    assert "NUOVO: Fed ha deciso" in b2["testo_analisti"]


def test_fred_api_hides_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="manca FRED_API_KEY"):
        terminale.fred_api("FEDTARMD", "2020-01-01")
    monkeypatch.setenv("FRED_API_KEY", "chiave-finta-123")

    class R:
        status_code = 400

    monkeypatch.setattr(terminale.requests, "get", lambda *a, **k: R())
    with pytest.raises(RuntimeError) as e:
        terminale.fred_api("FEDTARMD", "2020-01-01")
    assert "chiave-finta-123" not in str(e.value) and "400" in str(e.value)

    def boom(*a, **k):
        raise ConnectionError("for url https://api.stlouisfed.org/?api_key=chiave-finta-123")

    monkeypatch.setattr(terminale.requests, "get", boom)
    with pytest.raises(RuntimeError) as e:
        terminale.fred_api("FEDTARMD", "2020-01-01")
    assert "chiave-finta-123" not in str(e.value) and e.value.__cause__ is None


def test_committee_sees_terminal_only_when_switched_on():
    from xau_macro import committee
    term = terminale.build_block(READING, NOW, {})
    spento = committee.build_briefing(None, {"terminale": dict(term, agli_analisti=False)}, [], [], [], None)
    acceso = committee.build_briefing(None, {"terminale": dict(term, agli_analisti=True)}, [], [], [], None)
    assert "Terminale settimanale" not in spento and "Terminale settimanale" in acceso
