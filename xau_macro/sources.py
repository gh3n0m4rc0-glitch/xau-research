"""Fonti dati per il servizio macro/news dell'oro.

Tutte le fonti usate sono gratuite:
- FRED (St. Louis Fed): rendimenti reali, dollaro, tassi, breakeven, VIX  -> API (FRED_API_KEY) o CSV pubblico
- Riserve senza chiave: Tesoro USA (rendimenti), BCE (dollaro sintetico), CBOE (VIX)
- CFTC (Commitments of Traders): posizionamento dei Managed Money sull'oro (COMEX, codice 088691)
- Calendario economico settimanale in formato JSON (feed pubblico stile ForexFactory)
- Feed RSS di notizie (configurabili)
"""
from __future__ import annotations

import io
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (xau-macro-service)"}
TIMEOUT = 30

# Serie FRED usate dal motore macro
FRED_SERIES = {
    "real_yield_10y": "DFII10",    # rendimento reale 10 anni (TIPS): driver n.1 dell'oro
    "dollar_broad": "DTWEXBGS",    # indice dollaro ampio (trade weighted)
    "nominal_2y": "DGS2",          # 2 anni: proxy del percorso dei tassi Fed
    "breakeven_10y": "T10YIE",     # inflazione attesa 10 anni
    "vix": "VIXCLS",               # avversione al rischio
}


FRED_API = "https://api.stlouisfed.org/fred/series/observations"
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/json,text/plain,*/*",
}


_fred_csv_blocked = False


def parse_fred_json(data: dict, series_id: str) -> pd.Series:
    obs = data.get("observations", [])
    idx = pd.to_datetime([o["date"] for o in obs])
    s = pd.to_numeric(pd.Series([o.get("value") for o in obs], index=idx).replace(".", None), errors="coerce")
    s.name = series_id
    return s.dropna()


def fetch_fred(series_id: str, start: str = "2015-01-01") -> pd.Series:
    """Serie FRED: prima con la API key (se c'e' FRED_API_KEY), poi dal CSV pubblico con 3 tentativi."""
    errors = []
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        try:
            r = requests.get(FRED_API, params={"series_id": series_id, "api_key": key, "file_type": "json",
                                               "observation_start": start}, headers=BROWSER_UA, timeout=TIMEOUT)
            r.raise_for_status()
            s = parse_fred_json(r.json(), series_id)
            if len(s) >= 10:
                return s
            errors.append("API: serie vuota")
        except Exception as e:
            errors.append(f"API: {e}")
    global _fred_csv_blocked
    if _fred_csv_blocked:
        errors.append("CSV FRED gia' bloccato in questo giro")
        raise RuntimeError("; ".join(errors))
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    for attempt in range(2):
        try:
            r = requests.get(url, headers=BROWSER_UA, timeout=20)
            r.raise_for_status()
            if "<html" in r.text[:800].lower():
                raise ValueError("FRED ha risposto con una pagina web invece del CSV (probabile blocco)")
            s = parse_fred_csv(r.text, series_id)
            if len(s) >= 10:
                return s
            raise ValueError("serie vuota")
        except Exception as e:
            errors.append(f"CSV tentativo {attempt + 1}: {e}")
            if attempt < 1:
                time.sleep(2)
    _fred_csv_blocked = True   # se FRED blocca il runner, le altre serie vanno subito alle riserve
    raise RuntimeError("; ".join(errors))


def parse_fred_csv(text: str, series_id: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    val_col = series_id if series_id in df.columns else df.columns[1]
    s = pd.to_numeric(df[val_col].replace(".", None), errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    s.name = series_id
    return s.dropna()


# ---------------------------------------------------------------- fonti di riserva (senza chiavi)
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type={kind}&field_tdr_date_value={year}&page&_format=csv")
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY+GBP+CAD+SEK+CHF.EUR.SP00.A"
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
DXY_EXP = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091, "USDSEK": 0.042, "USDCHF": 0.036}


def _norm(c: str) -> str:
    return str(c).replace(" ", "").replace("_", "").lower()


def parse_treasury_csv(text: str, column: str) -> pd.Series:
    """CSV del Tesoro USA (Date,5 YR,7 YR,10 YR,... oppure Date,1 Mo,...,2 Yr,...,10 Yr)."""
    df = pd.read_csv(io.StringIO(text))
    cols = {_norm(c): c for c in df.columns}
    key = _norm(column)
    if key not in cols:
        raise ValueError(f"colonna {column} assente: {list(df.columns)[:8]}")
    date_col = cols.get("date", df.columns[0])
    s = pd.to_numeric(df[cols[key]], errors="coerce")
    s.index = pd.to_datetime(df[date_col], errors="coerce")
    s = s[s.index.notna()].dropna().sort_index()
    s.name = column
    return s


_treasury_cache: dict[tuple[str, str, str], pd.Series] = {}
_treasury_text: dict[tuple[str, int], str] = {}


def fetch_treasury(kind: str, column: str, start: str) -> pd.Series:
    ck = (kind, column, start)
    if ck in _treasury_cache:
        return _treasury_cache[ck]
    parts = []
    for y in range(int(start[:4]), datetime.now(timezone.utc).year + 1):
        if (kind, y) not in _treasury_text:
            r = requests.get(TREASURY_URL.format(year=y, kind=kind), headers=BROWSER_UA, timeout=TIMEOUT)
            r.raise_for_status()
            _treasury_text[(kind, y)] = r.text
        text = _treasury_text[(kind, y)]
        if not text.strip():
            continue
        parts.append(parse_treasury_csv(text, column))
    if not parts:
        raise ValueError("Tesoro: nessun dato")
    s = pd.concat(parts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    s = s[s.index >= pd.Timestamp(start)]
    if len(s) < 10:
        raise ValueError("Tesoro: serie troppo corta")
    _treasury_cache[ck] = s
    return s


def parse_ecb_dollar_index(text: str) -> pd.Series:
    """Indice del dollaro sintetico (pesi DXY) dai cambi di riferimento BCE (valute per 1 EUR)."""
    df = pd.read_csv(io.StringIO(text))
    cols = {_norm(c): c for c in df.columns}
    cur, per, val = cols["currency"], cols["timeperiod"], cols["obsvalue"]
    piv = df.pivot_table(index=per, columns=cur, values=val, aggfunc="last")
    piv.index = pd.to_datetime(piv.index, format="%Y-%m-%d", errors="coerce")
    piv = piv[piv.index.notna()]
    piv = piv.sort_index().ffill().dropna(subset=["USD"])
    eurusd = piv["USD"]
    pairs = {"EURUSD": eurusd}
    if "JPY" in piv:
        pairs["USDJPY"] = piv["JPY"] / eurusd
    if "GBP" in piv:
        pairs["GBPUSD"] = eurusd / piv["GBP"]
    if "CAD" in piv:
        pairs["USDCAD"] = piv["CAD"] / eurusd
    if "SEK" in piv:
        pairs["USDSEK"] = piv["SEK"] / eurusd
    if "CHF" in piv:
        pairs["USDCHF"] = piv["CHF"] / eurusd
    logs = sum(DXY_EXP[k] * np.log(v) for k, v in pairs.items())
    s = (50.14348112 * np.exp(logs)).dropna()
    s.name = "DXY_sintetico"
    return s


def fetch_ecb_dollar(start: str) -> pd.Series:
    r = requests.get(ECB_URL, params={"format": "csvdata", "startPeriod": start}, headers=BROWSER_UA, timeout=20)
    r.raise_for_status()
    s = parse_ecb_dollar_index(r.text)
    if len(s) < 10:
        raise ValueError("BCE: serie troppo corta")
    return s


def parse_cboe_vix(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    cols = {_norm(c): c for c in df.columns}
    s = pd.to_numeric(df[cols["close"]], errors="coerce")
    s.index = pd.to_datetime(df[cols["date"]], errors="coerce")
    s = s[s.index.notna()].dropna().sort_index()
    s.name = "VIX"
    return s


def fetch_cboe_vix(start: str) -> pd.Series:
    r = requests.get(CBOE_VIX_URL, headers=BROWSER_UA, timeout=TIMEOUT)
    r.raise_for_status()
    s = parse_cboe_vix(r.text)
    return s[s.index >= pd.Timestamp(start)]


def fallback_series(name: str, start: str) -> tuple[pd.Series, str]:
    if name == "real_yield_10y":
        return fetch_treasury("daily_treasury_real_yield_curve", "10 YR", start), "Tesoro USA"
    if name == "nominal_2y":
        return fetch_treasury("daily_treasury_yield_curve", "2 Yr", start), "Tesoro USA"
    if name == "breakeven_10y":
        nom = fetch_treasury("daily_treasury_yield_curve", "10 Yr", start)
        real = fetch_treasury("daily_treasury_real_yield_curve", "10 YR", start)
        be = (nom - real).dropna()
        be.name = "breakeven_10y"
        return be, "Tesoro USA (10y nominale - reale)"
    if name == "dollar_broad":
        try:
            return fetch_ecb_dollar(start), "BCE (dollaro sintetico)"
        except Exception as e:
            log.warning("BCE non risponde (%s): provo Yahoo DXY", str(e)[:120])
            from . import extra
            return extra.yahoo_dollar_daily(start), "Yahoo (indice DXY)"
    if name == "vix":
        return fetch_cboe_vix(start), "CBOE"
    raise KeyError(name)


SOURCE_LOG: dict[str, str] = {}   # nome serie -> fonte usata o "MANCA: motivo" (per macro_bias.json)


def fetch_bills(start: str) -> dict[str, pd.Series]:
    """T-bill 3 mesi, 6 mesi, 1 anno dal Tesoro (per i tagli Fed prezzati)."""
    out = {}
    for k, col in (("3m", "3 Mo"), ("6m", "6 Mo"), ("1y", "1 Yr")):
        try:
            out[k] = fetch_treasury("daily_treasury_yield_curve", col, start)
        except Exception as e:
            log.warning("T-bill %s non disponibile: %s", col, str(e)[:120])
    return out


def fetch_all_fred(start: str = "2015-01-01") -> dict[str, pd.Series]:
    """Serie macro: FRED se risponde, altrimenti fonti ufficiali di riserva. Una fonte che manca non ferma nulla."""
    global _fred_csv_blocked
    out = {}
    SOURCE_LOG.clear()
    _treasury_cache.clear()
    _treasury_text.clear()
    _fred_csv_blocked = False
    for name, sid in FRED_SERIES.items():
        try:
            out[name] = fetch_fred(sid, start)
            SOURCE_LOG[name] = "FRED"
            log.info("FRED %s: %d osservazioni, ultima %s", sid, len(out[name]), out[name].index[-1].date())
            continue
        except Exception as e:
            log.warning("FRED %s non disponibile (%s): provo la fonte di riserva", sid, str(e)[:200])
        try:
            s, src = fallback_series(name, start)
            out[name] = s
            SOURCE_LOG[name] = src
            log.info("%s da %s: %d osservazioni, ultima %s", name, src, len(s), s.index[-1].date())
        except Exception as e:
            log.warning("%s: anche la fonte di riserva non risponde: %s", name, str(e)[:200])
            SOURCE_LOG[name] = "MANCA: " + str(e)[:100]
    if not out:
        log.error("Nessun dato macro disponibile: controlla la connessione del runner")
    return out


def fetch_cot_gold(start: str = "2015-01-01") -> pd.DataFrame:
    """COT disaggregato (futures only) per l'oro COMEX: posizioni Managed Money."""
    url = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
    params = {
        "$select": "report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all,open_interest_all",
        "$where": f"cftc_contract_market_code='088691' AND report_date_as_yyyy_mm_dd >= '{start}T00:00:00'",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": 5000,
    }
    r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_cot_json(r.json())


def parse_cot_json(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None)
    for c in ["m_money_positions_long_all", "m_money_positions_short_all", "open_interest_all"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["mm_net_pct_oi"] = (df["m_money_positions_long_all"] - df["m_money_positions_short_all"]) / df["open_interest_all"]
    # Il COT è riferito al martedì ma pubblicato il venerdì: disponibile solo da venerdì (no look-ahead)
    df["available_from"] = df["date"] + pd.Timedelta(days=3)
    return df[["date", "available_from", "mm_net_pct_oi"]].dropna().sort_values("date").reset_index(drop=True)


def fetch_calendar(url: str) -> list[dict]:
    """Calendario economico settimanale (JSON con title, country, date, impact, forecast, previous)."""
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_headlines(feeds: Iterable[str], max_per_feed: int = 25) -> list[dict]:
    """Legge feed RSS/Atom e restituisce titoli recenti (titolo, data, fonte)."""
    items: list[dict] = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            items.extend(parse_rss(r.content, url)[:max_per_feed])
        except Exception as e:
            log.warning("Feed %s non disponibile: %s", url, e)
    # dedup per titolo
    seen, out = set(), []
    for it in items:
        k = it["title"].strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def parse_rss(xml_text, source: str) -> list[dict]:
    if isinstance(xml_text, bytes):   # dai byte: niente codifica indovinata, BOM tolto (feed della Fed)
        xml_text = xml_text.decode("utf-8-sig", errors="replace")
    xml_text = xml_text.lstrip(chr(0xFEFF)).replace(chr(0xEF) + chr(0xBB) + chr(0xBF), "", 1).strip()
    root = ET.fromstring(xml_text.encode("utf-8"))
    out = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title, date = "", ""
        for ch in item:
            t = ch.tag.split("}")[-1]
            if t == "title":
                title = (ch.text or "").strip()
            elif t in ("pubDate", "published", "updated") and not date:
                date = (ch.text or "").strip()
        if title:
            out.append({"title": title, "published": date, "source": source})
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
