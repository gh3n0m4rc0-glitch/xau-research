"""Terminale macro settimanale dentro il servizio (peso zero: solo informazione).

Ogni fine settimana (e dopo ogni decisione delle banche centrali) l'analisi del terminale scrive
knowledge/terminale.json: verso e convinzione per i sei mercati, report delle banche centrali, COT, calendario.

Questo modulo tiene viva la lettura durante la settimana, senza aspettare il venerdi':
- COT dei fondi su oro (CFTC disaggregato) e su Nasdaq, Dow, euro, sterlina, yen (CFTC TFF, leveraged funds);
- conferma del grafico per ogni banca centrale, ricalcolata con i prezzi di oggi;
- avvisi quando una banca ha deciso dopo la lettura (da rileggere), quando cambia il tasso ufficiale
  e quando la Fed pubblica nuove proiezioni (mediane del dot plot su FRED).
Le chiamate in rete partono al massimo ogni `refresh_hours`; nel mezzo si usa la cache in public/terminale_cache.json.
Nessun testo di errore con indirizzi o chiavi finisce nei file pubblici.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

log = logging.getLogger(__name__)

MERCATI = ["XAUUSD", "US100", "US30", "EURUSD", "GBPUSD", "USDJPY"]
NOMI = {"XAUUSD": "oro", "US100": "Nasdaq", "US30": "Dow", "EURUSD": "euro", "GBPUSD": "sterlina", "USDJPY": "USDJPY"}

# COT: oro dal rapporto disaggregato (managed money), gli altri dal TFF (leveraged funds = hedge fund)
COT_DISAGG = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
COT_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
CODICI_TFF = {"20974+": "US100", "12460+": "US30", "099741": "EURUSD", "096742": "GBPUSD", "097741": "USDJPY"}
# Il future dello yen e' yen contro dollaro: fondi corti sullo yen = scommettono su USDJPY in salita. Si gira il segno.
SEGNO_COT = {"USDJPY": -1}

# Prezzi: Yahoo (giornaliero, anche l'ultimo prezzo), poi FRED (giornaliero, sui cambi arriva una settimana dopo),
# sempre unite alle chiusure del venerdi' scritte nel file; l'ultimo prezzo dal vivo arriva da CNBC.
YAHOO = {"XAUUSD": "GC=F", "US100": "^NDX", "US30": "^DJI", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X"}
FRED_PREZZI = {"US100": "NASDAQ100", "US30": "DJIA", "EURUSD": "DEXUSEU", "GBPUSD": "DEXUSUK", "USDJPY": "DEXJPUS"}
CNBC = {"XAUUSD": "@GC.1", "US100": ".NDX", "US30": ".DJI", "EURUSD": "EUR=", "GBPUSD": "GBP=", "USDJPY": "JPY="}
SCALA_CNBC = 0.2        # un prezzo CNBC a piu' del 20% dall'ultima chiusura della lettura e' un errore di simbolo o di unita'
# Tasso ufficiale su FRED per accorgersi di un cambio: Fed = tetto della forchetta, BCE = tasso sui depositi
FRED_TASSI = {"Fed": "DFEDTARU", "BCE": "ECBDFR"}
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
UA = {"User-Agent": "Mozilla/5.0 (xau-macro-service)"}
TONO = {"falco": 1, "colomba": -1, "neutro": 0}
SOGLIA_CONFERMA = 0.5   # % nel verso atteso
GIORNI_VECCHIO = 8      # dopo 8 giorni la lettura settimanale e' vecchia


# ------------------------------------------------------------------ lettura settimanale
def load_reading(path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("mercati") else None
    except Exception:
        return None


# ------------------------------------------------------------------ COT
def _socrata(url: str, params: dict) -> list[dict]:
    r = requests.get(url, params=params, headers=UA, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"CFTC risponde {r.status_code}")
    return r.json()


def fetch_cot_rows(start: str) -> tuple[list[dict], list[dict]]:
    """(righe oro, righe finanziari) grezze dalla CFTC."""
    oro = _socrata(COT_DISAGG, {
        "$select": "report_date_as_yyyy_mm_dd,open_interest_all,m_money_positions_long_all,m_money_positions_short_all",
        "$where": f"cftc_contract_market_code='088691' AND report_date_as_yyyy_mm_dd >= '{start}T00:00:00'",
        "$order": "report_date_as_yyyy_mm_dd", "$limit": 1000})
    codici = ",".join(f"'{c}'" for c in CODICI_TFF)
    fin = _socrata(COT_TFF, {
        "$select": "report_date_as_yyyy_mm_dd,cftc_contract_market_code,open_interest_all,"
                   "lev_money_positions_long,lev_money_positions_short,asset_mgr_positions_long,asset_mgr_positions_short",
        "$where": f"cftc_contract_market_code in ({codici}) AND report_date_as_yyyy_mm_dd >= '{start}T00:00:00'",
        "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000})
    return oro, fin


def _n(r: dict, k: str) -> int:
    try:
        return int(float(r.get(k) or 0))
    except (TypeError, ValueError):
        return 0


def cot_series(oro_rows: list[dict], fin_rows: list[dict]) -> dict[str, list[list]]:
    """{mercato: [[data, netto_fondi, open_interest, netto_asset_manager|None], ...]} in ordine di data, segno yen gia' girato."""
    out: dict[str, dict] = {m: {} for m in MERCATI}
    for r in oro_rows:
        d = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
        if d:
            out["XAUUSD"][d] = [d, _n(r, "m_money_positions_long_all") - _n(r, "m_money_positions_short_all"),
                                _n(r, "open_interest_all"), None]
    for r in fin_rows:
        m = CODICI_TFF.get(r.get("cftc_contract_market_code"))
        d = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
        if not m or not d:
            continue
        s = SEGNO_COT.get(m, 1)
        out[m][d] = [d, s * (_n(r, "lev_money_positions_long") - _n(r, "lev_money_positions_short")), _n(r, "open_interest_all"),
                     s * (_n(r, "asset_mgr_positions_long") - _n(r, "asset_mgr_positions_short"))]
    return {m: [v[k] for k in sorted(v)] for m, v in out.items() if v}


def cot_summary(series: list[list]) -> dict:
    """Ultimo dato, variazione sulla settimana, percentile a 3 anni (156 settimane) e lettura in parole."""
    if not series:
        return {}
    u = series[-1]
    tre = [x[1] for x in series[-156:]]
    pct = round(100 * sum(1 for v in tre if v <= u[1]) / len(tre))
    out = {"data": u[0], "netto": u[1], "var_settimana": u[1] - series[-2][1] if len(series) > 1 else None,
           "netto_pct_oi": round(100 * u[1] / u[2], 2) if u[2] else None, "percentile_3anni": pct}
    if u[3] is not None:
        a3 = [x[3] for x in series[-156:] if x[3] is not None]
        out["asset_netto"] = u[3]
        out["asset_percentile_3anni"] = round(100 * sum(1 for v in a3 if v <= u[3]) / len(a3)) if a3 else None
    out["lettura"] = ("fondi molto corti: spazio per ricoperture" if pct <= 10 else "fondi leggeri" if pct <= 25 else
                      "fondi molto carichi: rischio prese di profitto" if pct >= 90 else "fondi carichi" if pct >= 75 else "nella norma")
    return out


# ------------------------------------------------------------------ prezzi
def _closes_daily(s: pd.Series) -> pd.Series:
    """Chiusure giornaliere indicizzate per giorno di borsa (Yahoo segna i cambi alle 23:00 UTC del giorno prima)."""
    s = s.dropna().sort_index()
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = (idx.tz_convert("UTC") + pd.Timedelta(hours=3)).tz_localize(None)
    s.index = idx.normalize()
    return s[~s.index.duplicated(keep="last")]


def compact(s: pd.Series, weeks: int = 60) -> list[list]:
    """Chiusura di ogni venerdi' (ultima seduta della settimana) per `weeks` settimane piu' l'ultimo prezzo."""
    s = _closes_daily(s)
    if s.empty:
        return []
    wk = s.groupby(s.index.to_period("W-FRI")).last()
    pts = [[(p.end_time.normalize()).strftime("%Y-%m-%d"), round(float(v), 6)] for p, v in wk.items()][-weeks:]
    last_day = s.index[-1].strftime("%Y-%m-%d")
    if pts and pts[-1][0] > last_day:       # settimana in corso: l'ultimo punto e' il prezzo di oggi, non un venerdi'
        pts[-1] = [last_day, round(float(s.iloc[-1]), 6)]
    return pts


def fetch_prices(yahoo_fetch: Callable | None, fred_fetch: Callable | None, start: str) -> tuple[dict, dict, list[str]]:
    """({mercato: [[data, chiusura], ...]}, {mercato: fonte}, mancanti)."""
    out, fonte, missing = {}, {}, []
    for m in MERCATI:
        if yahoo_fetch:
            try:
                pts = compact(yahoo_fetch(YAHOO[m], rng="2y", interval="1d"))
                if len(pts) >= 20:
                    out[m], fonte[m] = pts, "Yahoo"
                    continue
            except Exception:
                pass
        if fred_fetch and m in FRED_PREZZI:
            try:
                pts = compact(fred_fetch(FRED_PREZZI[m], start))
                if len(pts) >= 20:
                    out[m], fonte[m] = pts, "FRED"
                    continue
            except Exception:
                pass
        missing.append(f"terminale prezzi {m}: uso le chiusure del venerdi'")
    return out, fonte, missing


def _cnbc_day(t) -> str | None:
    """Giorno di mercato di un'ora CNBC ("2026-11-17T10:42:50.000-0500", ora di New York).
    Dalle 18 di New York si apre gia' la seduta del giorno dopo (cambi e future)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}))?", str(t or ""))
    if not m:
        return None
    g = date.fromisoformat(m.group(1))
    if m.group(2) and int(m.group(2)) >= 18:
        g += timedelta(days=1)
    return g.isoformat()


def fetch_quotes(cnbc_fetch: Callable | None, reading: dict, now: datetime) -> tuple[dict, list[str]]:
    """Ultimo prezzo di ogni mercato da CNBC: ({mercato: [giorno, prezzo]}, mancanti)."""
    if not cnbc_fetch:
        return {}, []
    try:
        q = cnbc_fetch(list(CNBC.values()))
    except Exception as e:   # solo il tipo di errore, mai indirizzi
        return {}, [f"terminale CNBC: non raggiunto ({type(e).__name__})"]
    out, missing = {}, []
    domani = (now + timedelta(days=1)).date().isoformat()
    for m, sym in CNBC.items():
        x = q.get(sym) or {}
        day, last = _cnbc_day(x.get("time")), x.get("last")
        if not day or not last or day > domani:
            continue
        ref = ((reading.get("prezzi_settimanali") or {}).get(m) or [[None, None]])[-1][1]
        if ref and abs(float(last) / ref - 1) > SCALA_CNBC:
            missing.append(f"terminale CNBC {m}: prezzo fuori scala, non usato")
            continue
        out[m] = [day, round(float(last), 6)]
    return out, missing


def merge_prices(lettura: list | None, rete: list | None, quota: list | None) -> tuple[list[list], list[str]]:
    """Serie per i confronti e da dove arriva. Le chiusure della lettura vincono a parita' di giorno (bot e pagina danno
    gli stessi numeri); dalla rete (Yahoo o FRED) si prendono i giorni che la lettura non ha, e l'ultimo prezzo CNBC
    se e' piu' recente della lettura. Cosi' un FRED in ritardo non toglie mai le chiusure piu' nuove."""
    d = {p[0]: p[1] for p in rete or []}
    ultimo_rete = max(d) if d else ""
    let = {p[0]: p[1] for p in lettura or []}
    d.update(let)
    parti = ["rete"] if rete else []
    ultimo_let = max(let) if let else ""
    if let and ultimo_let > ultimo_rete:
        parti.append("lettura")
    if quota and quota[0] > ultimo_let:
        d[quota[0]] = quota[1]
        parti.append("CNBC")
    return [[k, d[k]] for k in sorted(d)], parti


def price_at(points: list[list], day: str) -> float | None:
    """Ultima chiusura con data <= day."""
    v = None
    for d, c in points:
        if d <= day:
            v = c
        else:
            break
    return v


def friday_before(day: str) -> str:
    g = date.fromisoformat(day[:10]) - timedelta(days=1)
    while g.weekday() != 4:
        g -= timedelta(days=1)
    return g.isoformat()


def pct(a, b):
    return round(100 * (b / a - 1), 2) if a and b else None


# ------------------------------------------------------------------ FRED: proiezioni e tassi
def fred_api(series_id: str, start: str) -> list[tuple[str, float]]:
    """Osservazioni FRED con la chiave del servizio. Gli errori non riportano mai l'indirizzo (che contiene la chiave)."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("manca FRED_API_KEY")
    try:
        r = requests.get(FRED_API, params={"series_id": series_id, "api_key": key, "file_type": "json",
                                           "observation_start": start}, headers=UA, timeout=30)
    except Exception as e:
        raise RuntimeError(f"FRED {series_id}: rete ({type(e).__name__})") from None
    if r.status_code != 200:
        raise RuntimeError(f"FRED {series_id}: risponde {r.status_code}")
    out = []
    for o in r.json().get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fed_projections(fred_obs: Callable) -> dict:
    """{"data_sep": data dell'ultimo dot plot, "mediana": {"2026": 4.1, ..., "lungo": 3.2}} da FEDTARMD e FEDTARMDLR."""
    lr = fred_obs("FEDTARMDLR", "2020-01-01")
    md = fred_obs("FEDTARMD", "2020-01-01")
    med = {d[:4]: v for d, v in md}
    if lr:
        med["lungo"] = lr[-1][1]
    return {"data_sep": lr[-1][0] if lr else None, "mediana": med}


def official_rates(fred_obs: Callable, start: str) -> dict:
    out = {}
    for banca, sid in FRED_TASSI.items():
        obs = fred_obs(sid, start)
        if obs:
            out[banca] = {"valore": obs[-1][1], "data": obs[-1][0]}
    return out


# ------------------------------------------------------------------ calcolo del blocco
def confirmations(reading: dict, prezzi: dict) -> list[dict]:
    """Conferma del grafico per l'ultima uscita di ogni banca, con l'ultimo prezzo disponibile."""
    eff = reading.get("effetto_falco") or {}
    out = []
    for b in reading.get("banche") or []:
        t = TONO.get(b.get("tono"), 0)
        dal = friday_before(b["ultima"])
        for m, e in (eff.get(b["banca"]) or {}).items():
            pts = prezzi.get(m) or []
            a, z = price_at(pts, dal), (pts[-1][1] if pts else None)
            tot = pct(a, z)
            atteso = t * e
            if not atteso or tot is None:
                esito = "neutro"
            elif tot * atteso >= SOGLIA_CONFERMA:
                esito = "conferma"
            elif tot * atteso <= -SOGLIA_CONFERMA:
                esito = "smentisce"
            else:
                esito = "neutro"
            out.append({"banca": b["banca"], "mercato": m, "atteso": "su" if atteso > 0 else "giu" if atteso < 0 else "-",
                        "dal": dal, "fino_a": pts[-1][0] if pts else None, "da_allora_pct": tot, "esito": esito})
    return out


def changes(reading: dict, now: datetime, prezzi: dict, fed_now: dict | None, tassi_now: dict | None) -> list[dict]:
    """Cose successe dopo la lettura settimanale: il bot le deve sapere subito."""
    out = []
    scritto = reading.get("scritto_il", "")
    for b in reading.get("banche") or []:
        # prossima decisione sui tassi (anche senza report); se manca, la prossima uscita del report
        ora = b.get("prossima_decisione_utc") or b.get("prossima_ora_utc") or (b.get("prossima", "") + "T23:59:00Z")
        try:
            t = datetime.strptime(ora, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        giorno = ora[:10]
        if now >= t and giorno > scritto:
            con_report = giorno == b.get("prossima")
            mosse = []
            for m in (reading.get("effetto_falco") or {}).get(b["banca"], {}):
                pts = prezzi.get(m) or []
                v = pct(price_at(pts, (date.fromisoformat(giorno) - timedelta(days=1)).isoformat()), pts[-1][1] if pts else None)
                if v is not None and pts[-1][0] >= giorno:
                    mosse.append({"mercato": m, "dalla_decisione_pct": v})
            out.append({"tipo": "decisione", "banca": b["banca"], "data": giorno, "con_report": con_report,
                        "testo": f"{b['banca']} ha deciso il {giorno} ({'con' if con_report else 'senza'} report trimestrale): "
                                 "la lettura del terminale va aggiornata",
                        "breve": f"{b['banca']} ha deciso, da rileggere", "mosse": mosse})
    base = reading.get("fed_proiezioni") or {}
    if fed_now and fed_now.get("mediana") and base.get("mediana"):
        nuovo_sep = bool(fed_now.get("data_sep") and base.get("data_sep") and fed_now["data_sep"] > base["data_sep"])
        diff = {k: round(fed_now["mediana"][k] - v, 2) for k, v in base["mediana"].items()
                if k in fed_now["mediana"] and abs(fed_now["mediana"][k] - v) >= 0.05}
        if nuovo_sep or diff:
            s = sum(diff.values())
            verso = "piu' falco" if s > 0 else "piu' colomba" if s < 0 else "mediane invariate"
            dett = ", ".join(f"{k} {base['mediana'][k]}->{fed_now['mediana'][k]}" for k in diff)
            out.append({"tipo": "proiezioni_fed", "banca": "Fed", "data": fed_now.get("data_sep"), "verso": verso, "differenze": diff,
                        "testo": f"Nuove proiezioni Fed ({verso}){': ' + dett if dett else ''}",
                        "breve": f"nuovo dot plot Fed, {verso}"})
    for b in reading.get("banche") or []:
        tn, base_t = (tassi_now or {}).get(b["banca"]), b.get("tasso_num")
        if tn and base_t is not None and abs(tn["valore"] - base_t) >= 0.01:
            su = tn["valore"] > base_t
            out.append({"tipo": "tasso", "banca": b["banca"], "data": tn["data"], "prima": base_t, "ora": tn["valore"],
                        "testo": f"{b['banca']}: tasso {'alzato' if su else 'tagliato'} da {base_t} a {tn['valore']}",
                        "breve": f"{b['banca']} {'alza' if su else 'taglia'} a {tn['valore']}"})
    return out


def build_block(reading: dict, now: datetime, remote: dict) -> dict:
    """Blocco "terminale" per macro_bias.json, dalla lettura settimanale e dai dati presi in rete (o dalla cache)."""
    lettura, rete, quote = reading.get("prezzi_settimanali") or {}, remote.get("prezzi") or {}, remote.get("quote") or {}
    prezzi, fonti = {}, {}
    for m in MERCATI:
        pts, parti = merge_prices(lettura.get(m), rete.get(m), quote.get(m))
        if pts:
            prezzi[m] = pts
            nomi = {"rete": (remote.get("fonti_prezzi") or {}).get(m, "rete"), "lettura": "lettura", "CNBC": "CNBC"}
            fonti[m] = "+".join(nomi[x] for x in parti)
    cot = remote.get("cot") or {}
    cot_out = {m: cot_summary(cot[m]) for m in cot if cot.get(m)}
    for c in reading.get("cot") or []:                       # COT non raggiunto: resta quello della lettura
        cot_out.setdefault(c["mercato"], {k: c.get(k) for k in ("data", "netto", "var_settimana", "netto_pct_oi", "percentile_3anni")})
    try:
        eta = (now.date() - date.fromisoformat(reading.get("scritto_il", "")[:10])).days
    except ValueError:
        eta = None
    mercati = {}
    for r in reading.get("mercati") or []:
        m = r["id"]
        pts = prezzi.get(m) or []
        ven = reading.get("venerdi") or ""
        mercati[m] = {"verso": r.get("verso"), "convinzione": r.get("convinzione"), "frase": r.get("frase"),
                      "cosa_lo_smentisce": r.get("cosa_lo_smentisce"),
                      "ultimo_prezzo": pts[-1][1] if pts else None, "ultimo_prezzo_data": pts[-1][0] if pts else None,
                      "da_venerdi_pct": pct(price_at(pts, ven), pts[-1][1]) if pts and ven and pts[-1][0] > ven else None,
                      "cot": cot_out.get(m)}
    conf = confirmations(reading, prezzi)
    camb = changes(reading, now, prezzi, remote.get("fed_proiezioni"), remote.get("tassi"))
    banche = []
    for b in reading.get("banche") or []:
        cc = [c for c in conf if c["banca"] == b["banca"]]
        banche.append({k: b.get(k) for k in ("banca", "report", "tono", "tasso", "ultima", "prossima", "in_parole_semplici")}
                      | {"conferma_grafico": {c["mercato"]: c["esito"] for c in cc},
                         "da_rileggere": any(x["banca"] == b["banca"] for x in camb)})
    block = {"peso_nel_bias": 0, "settimana": reading.get("settimana"), "scritto_il": reading.get("scritto_il"),
             "eta_giorni": eta, "vecchio": eta is None or eta > GIORNI_VECCHIO,
             "mercati": mercati, "banche": banche, "conferma_grafico": conf, "cambiamenti": camb,
             "resoconto": reading.get("resoconto") or [],
             "prossimi_eventi": [c for c in reading.get("calendario") or [] if c.get("quando", "") >= now.strftime("%Y-%m-%dT%H:%M:%SZ")][:8],
             "fonti_prezzi": fonti, "dati_presi_utc": remote.get("preso_utc")}
    block["sintesi"] = short_text(block)
    block["testo_analisti"] = long_text(block)
    return block


def short_text(b: dict) -> str:
    """Al massimo 90 caratteri e solo ASCII, per il pannello dell'EA (sta davanti alla sintesi extra; l'EA ne mostra 160)."""
    if b.get("vecchio"):
        base = f"terminale vecchio di {b.get('eta_giorni')} giorni"
    else:
        o = (b.get("mercati") or {}).get("XAUUSD") or {}
        base = f"Terminale: oro {o.get('verso', '?')} {o.get('convinzione', '?')}/100"
        p = (o.get("cot") or {}).get("percentile_3anni")
        if p is not None and (p <= 25 or p >= 75):
            base += f", fondi {'leggeri' if p <= 25 else 'carichi'} ({p}/100)"
    camb = b.get("cambiamenti") or []
    if camb:
        base = "NUOVO " + camb[-1]["breve"] + " | " + base
    return base.encode("ascii", "ignore").decode()[:90]


def long_text(b: dict) -> str:
    """Testo per gli analisti AI del servizio (llm e comitato)."""
    parti = [f"Terminale settimanale {b.get('settimana')} (scritto il {b.get('scritto_il')}"
             + (", VECCHIO" if b.get("vecchio") else "") + ", peso zero)."]
    for m, r in (b.get("mercati") or {}).items():
        c = r.get("cot") or {}
        parti.append(f"{m}: {r.get('verso')} {r.get('convinzione')}/100"
                     + (f", da venerdi' {r['da_venerdi_pct']:+.2f}%" if r.get("da_venerdi_pct") is not None else "")
                     + (f", COT fondi {c.get('percentile_3anni')}° pct" if c.get("percentile_3anni") is not None else "") + ".")
    for x in b.get("banche") or []:
        cg = x.get("conferma_grafico") or {}
        parti.append(f"{x['banca']} {x.get('tono')} (ultima {x.get('ultima')}, prossima {x.get('prossima')}): grafico "
                     + ", ".join(f"{k} {v}" for k, v in cg.items()) + ".")
    for c in b.get("cambiamenti") or []:
        parti.append("NUOVO: " + c["testo"] + ".")
    return " ".join(parti)


# ------------------------------------------------------------------ giro del servizio
def run(knowledge_dir, out_dir, now: datetime, yahoo_fetch: Callable | None = None, fred_fetch: Callable | None = None,
        refresh_hours: float = 2.0, cot_fetch: Callable | None = None, fred_obs: Callable | None = None,
        cnbc_fetch: Callable | None = None) -> tuple[dict, list[str]]:
    """Restituisce (blocco terminale, fonti mancanti). Le chiamate in rete partono solo se la cache e' piu' vecchia di refresh_hours."""
    cot_fetch, fred_obs = cot_fetch or fetch_cot_rows, fred_obs or fred_api
    reading = load_reading(Path(knowledge_dir) / "terminale.json")
    if not reading:
        return {}, ["terminale: manca knowledge/terminale.json"]
    cache_path = Path(out_dir) / "terminale_cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    remote = cache.get("remote") or {}
    missing: list[str] = []
    try:
        age_h = (now - datetime.strptime(remote.get("preso_utc", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
    except ValueError:
        age_h = 1e9
    if age_h >= refresh_hours:
        start = (now - timedelta(days=3 * 365 + 60)).strftime("%Y-%m-%d")
        new = dict(remote)
        try:
            oro, fin = cot_fetch(start)
            new["cot"] = cot_series(oro, fin)
            for m in MERCATI:
                if not new["cot"].get(m):
                    missing.append(f"terminale COT {m}: assente")
        except Exception as e:   # solo il codice HTTP o il tipo di errore, mai indirizzi
            missing.append(f"terminale COT: {str(e)[:40] if isinstance(e, RuntimeError) else 'non raggiunto (' + type(e).__name__ + ')'}")
        prezzi, fonti, miss = fetch_prices(yahoo_fetch, fred_fetch, (now - timedelta(days=500)).strftime("%Y-%m-%d"))
        new["prezzi"] = {**(remote.get("prezzi") or {}), **prezzi}
        new["fonti_prezzi"] = {**(remote.get("fonti_prezzi") or {}), **fonti}
        quote, miss_q = fetch_quotes(cnbc_fetch, reading, now)
        new["quote"] = quote                      # niente quote vecchie: se CNBC non risponde restano le chiusure
        # un mercato senza serie in rete ma con il prezzo CNBC e' a posto: chiusure della lettura + prezzo di oggi
        missing += [x for x in miss if not any(f" {m}:" in x for m in quote)] + miss_q
        try:
            new["fed_proiezioni"] = fed_projections(fred_obs)
        except Exception as e:
            missing.append(f"terminale proiezioni Fed: {str(e)[:60]}")
        try:
            new["tassi"] = official_rates(fred_obs, (now - timedelta(days=60)).strftime("%Y-%m-%d"))
        except Exception as e:
            missing.append(f"terminale tassi ufficiali: {str(e)[:60]}")
        new["preso_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        remote = new
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"remote": remote}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, cache_path)
        except Exception as e:
            missing.append(f"terminale cache non salvata: {type(e).__name__}")
    return build_block(reading, now, remote), missing
