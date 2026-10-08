"""Fonti dati per il servizio macro/news dell'oro.

Tutte le fonti usate sono gratuite:
- FRED (St. Louis Fed): rendimenti reali, dollaro, tassi, breakeven, VIX  -> CSV pubblico senza chiave
- CFTC (Commitments of Traders): posizionamento dei Managed Money sull'oro (COMEX, codice 088691)
- Calendario economico settimanale in formato JSON (feed pubblico stile ForexFactory)
- Feed RSS di notizie (configurabili)
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable

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


def fetch_fred(series_id: str, start: str = "2015-01-01") -> pd.Series:
    """Scarica una serie FRED dal CSV pubblico (nessuna API key necessaria)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_fred_csv(r.text, series_id)


def parse_fred_csv(text: str, series_id: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    val_col = series_id if series_id in df.columns else df.columns[1]
    s = pd.to_numeric(df[val_col].replace(".", None), errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    s.name = series_id
    return s.dropna()


def fetch_all_fred(start: str = "2015-01-01") -> dict[str, pd.Series]:
    out = {}
    for name, sid in FRED_SERIES.items():
        try:
            out[name] = fetch_fred(sid, start)
            log.info("FRED %s: %d osservazioni, ultima %s", sid, len(out[name]), out[name].index[-1].date())
        except Exception as e:  # una fonte che manca non deve fermare il servizio
            log.warning("FRED %s non disponibile: %s", sid, e)
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
            items.extend(parse_rss(r.text, url)[:max_per_feed])
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


def parse_rss(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text)
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
