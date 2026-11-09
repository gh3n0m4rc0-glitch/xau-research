"""Informazioni macro in piu' per l'oro (proposte A3-D3 del 9 nov 2026).

Ogni fonte e' facoltativa: se non risponde finisce in "fonti_mancanti" e il resto continua.
Tutto quello che c'e' qui entra in macro_bias.json sotto "extra" con PESO ZERO nel bias:
prende peso solo dopo la prova sullo storico e la scelta dell'utente.

Fonti (tutte gratuite):
- Yahoo Finance chart API: tassi/dollaro/VIX/oro/argento/petrolio/rame intraday (B1, C4) e riserva del dollaro (A3)
- BLS (ufficio statistica USA) e FRED: dato uscito contro la previsione ForexFactory (B2)
- Fed di Cleveland: nowcast dell'inflazione (B3)
- Fed di New York (EFFR) + Tesoro (T-bill): tagli Fed prezzati (B4)
- TreasuryDirect: aste del Tesoro, risultati e prossime (B5)
- federalreserve.gov: comunicati, discorsi, testimonianze (B6)
- CFTC: flusso settimanale dei fondi (B7, dal COT gia' letto)
- SPDR: once/tonnellate del GLD (C1)
- Google News: notizie su banche centrali e PBoC (C2, informativo)
- Borsa dell'oro di Shanghai: premio Cina (C3)
- GDELT: intensita' delle notizie di conflitto (D3)
- Myfxbook: posizioni dei trader privati (folla, contrarian)
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)
TIMEOUT = 15
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/csv,text/html,application/xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=kw.pop("headers", BROWSER_UA), timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def _r(x, n=3):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


# ------------------------------------------------------------------ B1 / C4 / A3: Yahoo Finance
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
YAHOO_SYMBOLS = {
    "tasso_10y": "^TNX",       # rendimento 10 anni (in %; vecchio formato x10 gestito sotto)
    "dollaro_dxy": "DX-Y.NYB",
    "tips_etf": "TIP",         # proxy dei tassi reali: TIP su = reali giu'
    "vix": "^VIX",
    "oro_futures": "GC=F",
    "argento": "SI=F",
    "petrolio_wti": "CL=F",
    "rame": "HG=F",
}
# segno verso l'oro di una salita della serie (per la lettura intraday)
YAHOO_SIGN = {"tasso_10y": -1, "dollaro_dxy": -1, "tips_etf": +1, "vix": +1, "petrolio_wti": +1}


def parse_yahoo_chart(data: dict) -> pd.Series:
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError(f"Yahoo: nessun risultato ({(data.get('chart') or {}).get('error')})")
    r0 = res[0]
    ts = r0.get("timestamp") or []
    close = ((r0.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    s = pd.Series(close, index=pd.to_datetime(ts, unit="s", utc=True), dtype="float64").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        raise ValueError("Yahoo: serie vuota")
    return s


def fetch_yahoo(sym: str, rng: str = "5d", interval: str = "15m") -> pd.Series:
    r = _get(YAHOO_CHART.format(sym=requests.utils.quote(sym, safe="")), params={"range": rng, "interval": interval})
    return parse_yahoo_chart(r.json())


def change_over(s: pd.Series, hours: float, pct: bool) -> float | None:
    if s.empty:
        return None
    t_end = s.index[-1]
    past = s[s.index <= t_end - pd.Timedelta(hours=hours)]
    if past.empty:
        return None
    a, b = float(past.iloc[-1]), float(s.iloc[-1])
    if pct:
        return (b / a - 1) * 100 if a else None
    return b - a


def intraday_block(fetch: Callable[[str], pd.Series] = fetch_yahoo) -> tuple[dict, list[str]]:
    """Variazioni a 1h/4h/24h e un punteggio intraday -1..+1 (tassi e dollaro giu' = oro su)."""
    out, missing = {}, []
    for name, sym in YAHOO_SYMBOLS.items():
        try:
            s = fetch(sym)
        except Exception as e:
            missing.append(f"yahoo {sym}: {str(e)[:80]}")
            continue
        pct = name not in ("tasso_10y", "vix")
        if name == "tasso_10y" and s.iloc[-1] > 20:  # vecchio formato ^TNX x10
            s = s / 10
        unit = "%" if pct else ("pb" if name == "tasso_10y" else "punti")
        mult = 100 if name == "tasso_10y" else 1     # tassi in punti base
        row = {"ultimo": _r(s.iloc[-1], 4), "ora_utc": s.index[-1].strftime("%Y-%m-%dT%H:%M:%SZ"), "unita_variazioni": unit}
        for h in (1, 4, 24):
            c = change_over(s, h, pct)
            row[f"var_{h}h"] = _r(c * mult if c is not None else None, 3)
        out[name] = row
    # punteggio: 10y in pb (10 pb in 24h = forte), dollaro in % (0,5% = forte), TIP in % (0,3% = forte)
    scale = {"tasso_10y": 10.0, "dollaro_dxy": 0.5, "tips_etf": 0.3, "vix": 3.0, "petrolio_wti": 3.0}
    weights = {"tasso_10y": 0.35, "dollaro_dxy": 0.35, "tips_etf": 0.15, "vix": 0.075, "petrolio_wti": 0.075}
    for h in (4, 24):
        num = den = 0.0
        for k, w in weights.items():
            v = (out.get(k) or {}).get(f"var_{h}h")
            if v is None:
                continue
            num += w * YAHOO_SIGN[k] * math.tanh(v / scale[k] / (1 if h == 24 else 0.6))
            den += w
        out[f"punteggio_{h}h"] = _r(num / den if den else None, 3)
    gold, silver = (out.get("oro_futures") or {}).get("ultimo"), (out.get("argento") or {}).get("ultimo")
    if gold and silver:
        out["rapporto_oro_argento"] = _r(gold / silver, 2)
    return out, missing


def yahoo_dollar_daily(start: str) -> pd.Series:
    """A3: terza riserva del dollaro (indice DXY giornaliero)."""
    s = fetch_yahoo("DX-Y.NYB", rng="5y", interval="1d")
    s.index = s.index.tz_convert(None).normalize()
    s = s[s.index >= pd.Timestamp(start)]
    s = s[~s.index.duplicated(keep="last")]
    s.name = "DXY_yahoo"
    if len(s) < 60:
        raise ValueError("Yahoo DXY: serie troppo corta")
    return s


# ------------------------------------------------------------------ B2: dato vero contro previsione
# serie ufficiali: (fonte, id, trasformazione, scala di una sorpresa "normale", segno per l'oro se il dato esce sopra)
RELEASES = {
    "cpi m/m":            ("bls", "CUSR0000SA0", "mom", 0.1, -1),
    "core cpi m/m":       ("bls", "CUSR0000SA0L1E", "mom", 0.1, -1),
    "cpi y/y":            ("bls", "CUUR0000SA0", "yoy", 0.1, -1),
    "core cpi y/y":       ("bls", "CUUR0000SA0L1E", "yoy", 0.1, -1),
    "non-farm employment change": ("bls", "CES0000000001", "diff", 75.0, -1),
    "unemployment rate":  ("bls", "LNS14000000", "level", 0.1, +1),
    "average hourly earnings m/m": ("bls", "CES0500000003", "mom", 0.1, -1),
    "ppi m/m":            ("bls", "WPSFD4", "mom", 0.2, -1),
    "core ppi m/m":       ("bls", "WPSFD49104", "mom", 0.2, -1),
    "core pce price index m/m": ("fred", "PCEPILFE", "mom", 0.1, -1),
    "pce price index m/m": ("fred", "PCEPI", "mom", 0.1, -1),
    "retail sales m/m":   ("fred", "RSAFS", "mom", 0.4, -1),
    "core retail sales m/m": ("fred", "RSFSXMV", "mom", 0.4, -1),
}
BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_alias = {"cpi m/m": ("ipc m/m",), "core cpi m/m": ("ipc core m/m", "ipc di base m/m")}


def _release_key(title: str) -> str | None:
    t = (title or "").strip().lower()
    for k, al in _alias.items():
        if t in al:
            return k
    return t if t in RELEASES else None


def parse_number(txt) -> float | None:
    """'0.3%' -> 0.3 ; '187K' -> 187 ; '-12.5K' -> -12.5 ; '4.1%' -> 4.1"""
    if txt is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(txt).replace(",", ""))
    if not m:
        return None
    v = float(m.group())
    t = str(txt).upper()
    if t.endswith("M"):
        v *= 1000
    return v


def parse_bls(data: dict) -> dict[str, pd.Series]:
    out = {}
    for s in ((data.get("Results") or {}).get("series") or []):
        pts = {}
        for d in s.get("data", []):
            p = d.get("period", "")
            if not p.startswith("M") or p == "M13":
                continue
            try:
                pts[pd.Timestamp(int(d["year"]), int(p[1:]), 1)] = float(d["value"])
            except (ValueError, KeyError):
                continue
        if pts:
            out[s.get("seriesID")] = pd.Series(pts).sort_index()
    return out


def fetch_bls(ids: list[str], api_key: str = "") -> dict[str, pd.Series]:
    y = datetime.now(timezone.utc).year
    body = {"seriesid": ids, "startyear": str(y - 2), "endyear": str(y)}
    url = BLS_API
    if api_key:
        body["registrationkey"] = api_key
    else:
        url = BLS_API.replace("/v2/", "/v1/")
    r = requests.post(url, json=body, headers={**BROWSER_UA, "Content-Type": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS: {d.get('status')} {d.get('message')}")
    return parse_bls(d)


def transform(s: pd.Series, how: str) -> tuple[pd.Timestamp, float] | None:
    s = s.dropna().sort_index()
    if len(s) < 2:
        return None
    last = s.index[-1]
    if how == "mom":
        return last, (s.iloc[-1] / s.iloc[-2] - 1) * 100
    if how == "diff":
        return last, s.iloc[-1] - s.iloc[-2]
    if how == "yoy":
        prev = s[s.index == last - pd.DateOffset(years=1)]
        return (last, (s.iloc[-1] / prev.iloc[0] - 1) * 100) if len(prev) else None
    return last, s.iloc[-1]


def surprises(calendar_raw: list[dict], now: datetime, data: dict[str, pd.Series], hours_back: float = 72) -> list[dict]:
    """Per ogni dato USD uscito nelle ultime ore: dato vero, previsione, sorpresa normalizzata e verso per l'oro."""
    out = []
    for e in calendar_raw:
        if (e.get("country") or e.get("currency") or "").upper() != "USD":
            continue
        key = _release_key(e.get("title", ""))
        if not key:
            continue
        try:
            t = datetime.fromisoformat(str(e.get("date") or e.get("time_utc")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if not (now - timedelta(hours=hours_back) <= t <= now):
            continue
        src, sid, how, scale, sign = RELEASES[key]
        s = data.get(sid)
        fc = parse_number(e.get("forecast"))
        row = {"evento": e.get("title"), "ora_utc": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "previsione": e.get("forecast"), "fonte": f"{src} {sid}"}
        tr = transform(s, how) if s is not None else None
        # il dato deve riferirsi al mese prima dell'uscita (CPI di ottobre esce a novembre)
        ref = pd.Timestamp(t.year, t.month, 1) - pd.DateOffset(months=1)
        if tr is None or tr[0] < ref:
            row["stato"] = "dato non ancora disponibile alla fonte"
            out.append(row)
            continue
        actual = round(tr[1], 1) if how != "diff" else round(tr[1], 0)
        row.update({"dato": actual, "mese_riferimento": tr[0].strftime("%Y-%m")})
        if fc is None:
            row["stato"] = "manca la previsione"
        else:
            z = (actual - fc) / scale
            row.update({"sorpresa": _r(actual - fc, 3), "sorpresa_norm": _r(z, 2),
                        "verso_oro": int(np.sign(sign * z)) if abs(z) >= 1 else 0,
                        "forza": _r(min(1.0, abs(z) / 3), 2), "stato": "ok"})
        out.append(row)
    return out


def surprise_block(calendar_raw: list[dict], now: datetime, fred_fetch: Callable[[str, str], pd.Series],
                   bls_key: str = "") -> tuple[list[dict], list[str]]:
    missing = []
    def _recent(e) -> bool:
        try:
            t = datetime.fromisoformat(str(e.get("date") or e.get("time_utc")).replace("Z", "+00:00"))
        except ValueError:
            return False
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return now - timedelta(hours=72) <= t <= now
    # si interroga la fonte solo se un dato e' uscito nelle ultime 72 ore (BLS senza chiave: 25 richieste al giorno)
    wanted = {RELEASES[k] for e in calendar_raw if (k := _release_key(e.get("title", ""))) and _recent(e)}
    if not wanted:
        return [], missing
    data: dict[str, pd.Series] = {}
    bls_ids = sorted({w[1] for w in wanted if w[0] == "bls"})
    if bls_ids:
        try:
            data.update(fetch_bls(bls_ids, bls_key))
        except Exception as e:
            missing.append(f"BLS: {str(e)[:80]}")
    start = (now - timedelta(days=800)).strftime("%Y-%m-%d")
    for w in wanted:
        if w[0] == "fred" or w[1] not in data:
            fid = {"CUSR0000SA0": "CPIAUCSL", "CUSR0000SA0L1E": "CPILFESL", "CES0000000001": "PAYEMS",
                   "LNS14000000": "UNRATE", "CUUR0000SA0": "CPIAUCNS", "CUUR0000SA0L1E": "CPILFENS",
                   "CES0500000003": "CES0500000003", "WPSFD4": "PPIFIS", "WPSFD49104": "PPICOR"}.get(w[1], w[1])
            try:
                data[w[1]] = fred_fetch(fid, start)
            except Exception as e:
                missing.append(f"FRED {fid}: {str(e)[:60]}")
    return surprises(calendar_raw, now, data), missing


# ------------------------------------------------------------------ B3: nowcast Fed di Cleveland
CLEVELAND_JSON = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_month.json"
CLEVELAND_PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"


def parse_cleveland_month_json(data) -> dict:
    """Formato dei grafici Highcharts del sito: lista di blocchi con 'chart' e 'dataset' (serie per indice)."""
    blocks = data if isinstance(data, list) else [data]
    last = blocks[-1]
    out = {"mese": (last.get("chart") or {}).get("subcaption") or (last.get("chart") or {}).get("caption")}
    for ds in last.get("dataset", []):
        for s in ds.get("series", []) if isinstance(ds, dict) else []:
            name = (s.get("seriesname") or s.get("name") or "").lower()
            vals = [p.get("value") for p in s.get("data", []) if p.get("value") not in (None, "")]
            if not vals:
                continue
            v = float(vals[-1])
            if name.startswith("core cpi"):
                out["core_cpi_mm"] = v
            elif name.startswith("cpi"):
                out["cpi_mm"] = v
            elif name.startswith("core pce"):
                out["core_pce_mm"] = v
            elif name.startswith("pce"):
                out["pce_mm"] = v
    if not any(k in out for k in ("cpi_mm", "core_cpi_mm")):
        raise ValueError("Cleveland: serie CPI non trovate nel JSON")
    return out


def parse_cleveland_page(html: str) -> dict:
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt)
    out = {}
    m = re.search(r"Core CPI\s+(-?\d+\.\d+)", txt)
    if m:
        out["core_cpi_mm"] = float(m.group(1))
    m = re.search(r"(?<!Core )CPI\s+(-?\d+\.\d+)", txt)
    if m:
        out["cpi_mm"] = float(m.group(1))
    if not out:
        raise ValueError("Cleveland: numeri non trovati nella pagina")
    return out


def nowcast_block(calendar_raw: list[dict], now: datetime) -> tuple[dict, list[str]]:
    try:
        nc = parse_cleveland_month_json(_get(CLEVELAND_JSON).json())
        nc["fonte"] = "Cleveland Fed (json)"
    except Exception as e1:
        try:
            nc = parse_cleveland_page(_get(CLEVELAND_PAGE).text)
            nc["fonte"] = "Cleveland Fed (pagina)"
        except Exception as e2:
            return {}, [f"Cleveland nowcast: {str(e1)[:50]} / {str(e2)[:50]}"]
    # confronto con la previsione del prossimo CPI in calendario: nowcast sopra il consenso = rischio dato caldo
    for e in calendar_raw:
        k = _release_key(e.get("title", ""))
        if k not in ("cpi m/m", "core cpi m/m"):
            continue
        try:
            t = datetime.fromisoformat(str(e.get("date")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        fc = parse_number(e.get("forecast"))
        v = nc.get("cpi_mm" if k == "cpi m/m" else "core_cpi_mm")
        if t > now and fc is not None and v is not None:
            gap = v - fc
            nc.setdefault("prossimi_cpi", []).append({
                "evento": e.get("title"), "ora_utc": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "previsione": fc, "nowcast": v, "scarto": _r(gap, 2),
                "rischio": "dato caldo (oro giu')" if gap >= 0.1 else "dato freddo (oro su)" if gap <= -0.1 else "in linea"})
    return nc, []


# ------------------------------------------------------------------ B4: tagli Fed prezzati
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"


def parse_effr(data: dict) -> tuple[float, str, float, float]:
    r0 = (data.get("refRates") or [])[0]
    return float(r0["percentRate"]), r0["effectiveDate"], float(r0.get("targetRateFrom", "nan")), float(r0.get("targetRateTo", "nan"))


def fed_path_block(bills: dict[str, pd.Series], effr_hist: pd.Series | None = None) -> tuple[dict, list[str]]:
    """bills: {'3m','6m','1y'} serie giornaliere (Tesoro). Tagli prezzati ~ (EFFR - rendimento) / 0,25."""
    missing = []
    try:
        effr, d, lo, hi = parse_effr(_get(NYFED_EFFR).json())
    except Exception as e:
        missing.append(f"Fed NY EFFR: {str(e)[:80]}")
        if effr_hist is None or effr_hist.empty:
            return {}, missing
        effr, d, lo, hi = float(effr_hist.iloc[-1]), effr_hist.index[-1].strftime("%Y-%m-%d"), float("nan"), float("nan")
    out = {"effr": effr, "data_effr": d, "forchetta_fed": [lo, hi] if not math.isnan(lo) else None}
    for k, s in bills.items():
        if s is None or s.empty:
            continue
        # i T-bill sono quotati su base sconto/bond-equivalent: approssimazione sufficiente per il verso
        out[f"tagli_prezzati_{k}"] = _r((effr - float(s.iloc[-1])) / 0.25, 2)
        if len(s) > 21:
            out[f"variazione_20g_{k}"] = _r((float(s.iloc[-21]) - float(s.iloc[-1])) / 0.25, 2)
    ch = out.get("variazione_20g_1y")
    if ch is not None:
        # piu' tagli prezzati rispetto a un mese fa = oro su
        out["punteggio"] = _r(math.tanh(ch / 1.5), 3)
    return out, missing


# ------------------------------------------------------------------ B5: aste del Tesoro
TD_AUCTIONED = "https://www.treasurydirect.gov/TA_WS/securities/auctioned"
TD_UPCOMING = "https://www.treasurydirect.gov/TA_WS/securities/upcoming"


def auction_quality(rows: list[dict]) -> list[dict]:
    """Bid-to-cover e quota dei primary dealer contro le ultime 6 aste della stessa durata."""
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    df["term"] = df.get("originalSecurityTerm", df.get("securityTerm"))
    df["date"] = pd.to_datetime(df["auctionDate"] if "auctionDate" in df else df["competitiveAuctionCloseDate"], errors="coerce")
    df["btc"] = pd.to_numeric(df["bidToCoverRatio"], errors="coerce")
    tot = pd.to_numeric(df.get("totalParAwardedInsideOfOfferingAmount"), errors="coerce")
    tot = tot.fillna(pd.to_numeric(df.get("offeringAmount"), errors="coerce"))
    df["dealer"] = pd.to_numeric(df.get("primaryDealersCompetitiveParAwarded"), errors="coerce") / tot
    df["tail_proxy"] = pd.to_numeric(df.get("highYield"), errors="coerce") - pd.to_numeric(df.get("medianYield"), errors="coerce")
    out = []
    for term, g in df.dropna(subset=["date", "btc"]).sort_values("date").groupby("term"):
        if not re.search(r"(10|20|30)-Year", str(term)):
            continue
        last = g.iloc[-1]
        prev = g.iloc[-7:-1]
        z_btc = (last["btc"] - prev["btc"].mean()) / (prev["btc"].std() or np.nan) if len(prev) >= 3 else np.nan
        z_dlr = (last["dealer"] - prev["dealer"].mean()) / (prev["dealer"].std() or np.nan) if len(prev) >= 3 else np.nan
        weak = np.nanmean([-z_btc, z_dlr]) if not (np.isnan(z_btc) and np.isnan(z_dlr)) else np.nan
        out.append({"titolo": term, "data": last["date"].strftime("%Y-%m-%d"), "bid_to_cover": _r(last["btc"], 2),
                    "quota_dealer": _r(last["dealer"], 3), "high_meno_mediano": _r(last["tail_proxy"], 3),
                    "debolezza_z": _r(weak, 2),
                    "giudizio": "debole (rendimenti su, oro giu')" if weak is not None and not np.isnan(weak) and weak > 1
                    else "forte" if weak is not None and not np.isnan(weak) and weak < -1 else "normale"})
    return out


def parse_upcoming(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for r in rows:
        term = r.get("securityTerm") or r.get("originalSecurityTerm") or ""
        if r.get("securityType") not in ("Note", "Bond") or not re.search(r"(10|20|30)-Year", term):
            continue
        d = pd.to_datetime(r.get("auctionDate"), errors="coerce")
        if pd.isna(d):
            continue
        # le aste di note e bond chiudono alle 13:00 di New York
        t = (d.tz_localize("America/New_York") + pd.Timedelta(hours=13)).tz_convert("UTC")
        if t.to_pydatetime() >= now:
            out.append({"titolo": f"Asta {term}", "ora_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "importo_mld": _r(pd.to_numeric(r.get("offeringAmount"), errors="coerce") / 1e9, 1)})
    return sorted(out, key=lambda x: x["ora_utc"])


def auctions_block(now: datetime) -> tuple[dict, list[str]]:
    out, missing = {}, []
    try:
        rows = []
        for typ in ("Note", "Bond"):
            rows += _get(TD_AUCTIONED, params={"format": "json", "type": typ, "days": 400}).json()
        out["ultime"] = auction_quality(rows)
    except Exception as e:
        missing.append(f"TreasuryDirect aste: {str(e)[:80]}")
    try:
        out["prossime"] = parse_upcoming(_get(TD_UPCOMING, params={"format": "json"}).json(), now)
    except Exception as e:
        missing.append(f"TreasuryDirect prossime aste: {str(e)[:80]}")
    return out, missing


# ------------------------------------------------------------------ B6: Fed (comunicati, discorsi)
FED_FEEDS = {
    "comunicati": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "discorsi": "https://www.federalreserve.gov/feeds/speeches.xml",
    "testimonianze": "https://www.federalreserve.gov/feeds/testimony.xml",
}
FED_CALENDAR = "https://www.federalreserve.gov/json/calendar.json"


def parse_rss_items(xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text.lstrip("﻿").strip())
    out = []
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        d = {}
        for ch in it:
            t = ch.tag.split("}")[-1]
            if t == "title":
                d["titolo"] = (ch.text or "").strip()
            elif t in ("pubDate", "published", "updated", "date") and "data" not in d:
                d["data"] = (ch.text or "").strip()
            elif t == "link":
                d["link"] = (ch.text or ch.get("href") or "").strip()
        if d.get("titolo"):
            out.append(d)
    return out


def parse_fed_calendar(data, now: datetime, days: int = 7) -> list[dict]:
    events = data.get("events", data) if isinstance(data, dict) else data
    out = []
    for e in events or []:
        title = e.get("title") or e.get("description") or ""
        typ = (e.get("type") or "").lower()
        if not any(k in (title + " " + typ).lower() for k in ("speech", "testimony", "fomc", "discussion", "remarks", "press conference")):
            continue
        d = e.get("days") or e.get("date") or ""
        tm = e.get("time") or ""
        try:
            dt = pd.to_datetime(f"{e.get('month', '')} {d} {tm}".strip() if e.get("month") else f"{d} {tm}", errors="raise")
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.tz_localize("America/New_York")
        dt = dt.tz_convert("UTC")
        if now - timedelta(hours=2) <= dt.to_pydatetime() <= now + timedelta(days=days):
            out.append({"evento": title.strip(), "ora_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "chi": e.get("speaker", "")})
    return sorted(out, key=lambda x: x["ora_utc"])[:20]


def fed_block(now: datetime) -> tuple[dict, list[str]]:
    out, missing = {}, []
    for k, url in FED_FEEDS.items():
        try:
            out[k] = parse_rss_items(_get(url).text)[:5]
        except Exception as e:
            missing.append(f"Fed feed {k}: {str(e)[:60]}")
    try:
        out["agenda_7g"] = parse_fed_calendar(_get(FED_CALENDAR).json(), now)
    except Exception as e:
        missing.append(f"Fed agenda: {str(e)[:60]}")
    return out, missing


# ------------------------------------------------------------------ B7: COT continuo
def cot_flow_block(cot: pd.DataFrame | None) -> dict:
    if cot is None or cot.empty:
        return {}
    s = cot.set_index("date")["mm_net_pct_oi"].sort_index()
    ch4 = s.diff(4)
    z = (ch4.iloc[-1] - ch4.iloc[-156:].mean()) / (ch4.iloc[-156:].std() or np.nan)
    pct = (s.iloc[-156:] <= s.iloc[-1]).mean()
    return {"data": s.index[-1].strftime("%Y-%m-%d"), "netto_fondi_pct_oi": _r(s.iloc[-1] * 100, 1),
            "percentile_3anni": _r(pct, 2), "variazione_4sett_pct_oi": _r(ch4.iloc[-1] * 100, 1),
            "flusso_z": _r(z, 2), "punteggio_flusso": _r(math.tanh(z / 1.5) if not np.isnan(z) else None, 3)}


# ------------------------------------------------------------------ C1: ETF (GLD)
GLD_CSV = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"


def parse_gld_csv(text: str) -> pd.Series:
    lines = text.splitlines()
    hdr = next(i for i, l in enumerate(lines) if "Date" in l and ("Tonnes" in l or "Ounces" in l))
    df = pd.read_csv(io.StringIO("\n".join(lines[hdr:])), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    col = next(c for c in df.columns if "Tonnes" in c)
    s = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
    s.index = pd.to_datetime(df["Date"], errors="coerce", format="mixed", dayfirst=True)
    s = s[s.index.notna()].dropna().sort_index()
    s = s[s > 0]
    if len(s) < 30:
        raise ValueError("GLD: serie troppo corta")
    return s


def etf_block() -> tuple[dict, list[str]]:
    try:
        s = parse_gld_csv(_get(GLD_CSV, timeout=25).text)
    except Exception as e:
        return {}, [f"SPDR GLD: {str(e)[:80]}"]
    s = s[~s.index.duplicated(keep="last")]
    ch5, ch20 = s.diff(5), s.diff(20)
    z = (ch20.iloc[-1] - ch20.iloc[-500:].mean()) / (ch20.iloc[-500:].std() or np.nan)
    return {"gld_tonnellate": _r(s.iloc[-1], 2), "data": s.index[-1].strftime("%Y-%m-%d"),
            "flusso_5g_t": _r(ch5.iloc[-1], 2), "flusso_20g_t": _r(ch20.iloc[-1], 2),
            "flusso_z": _r(z, 2), "punteggio": _r(math.tanh(z / 1.5) if not np.isnan(z) else None, 3)}, []


# ------------------------------------------------------------------ C2: banche centrali (informativo)
CB_NEWS = ("https://news.google.com/rss/search?q=%22central+bank%22+gold+(tonnes+OR+reserves)+"
           "(%22World+Gold+Council%22+OR+PBOC+OR+%22People%27s+Bank%22)&hl=en-US&gl=US&ceid=US:en")


def central_banks_block(now: datetime) -> tuple[dict, list[str]]:
    try:
        items = parse_rss_items(_get(CB_NEWS).text)
    except Exception as e:
        return {}, [f"notizie banche centrali: {str(e)[:60]}"]
    keep = []
    for it in items:
        try:
            d = pd.to_datetime(it.get("data"), utc=True)
        except Exception:
            continue
        if d.to_pydatetime() >= now - timedelta(days=45):
            keep.append({"titolo": it["titolo"], "data": d.strftime("%Y-%m-%d")})
    pboc_months = None
    for it in keep:
        m = re.search(r"(\d+)(?:th|st|nd|rd)?\s+(?:straight|consecutive)\s+month", it["titolo"], re.I)
        if m and re.search(r"china|pboc|people", it["titolo"], re.I):
            pboc_months = int(m.group(1))
            break
    nxt = now.replace(day=7, hour=1, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt = (nxt + timedelta(days=32)).replace(day=7)
    return {"titoli_45g": keep[:8], "pboc_mesi_di_acquisti": pboc_months,
            "prossimo_dato_pboc_utc": nxt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nota": "regime di fondo, mensile: informativo"}, []


# ------------------------------------------------------------------ C3: premio Shanghai
SGE_URLS = ["https://en.sge.com.cn/data_BenchmarkPrice", "https://www.sge.com.cn/sjzx/jzj"]
GRAMS_PER_OZ = 31.1034768


def parse_sge_benchmark(html: str) -> float:
    """Prezzo di riferimento SGE in CNY/grammo (valori plausibili 300-2000)."""
    txt = re.sub(r"<[^>]+>", " ", html)
    nums = [float(x) for x in re.findall(r"\b(\d{3,4}\.\d{1,2})\b", txt)]
    nums = [x for x in nums if 300 <= x <= 2000]
    if not nums:
        raise ValueError("SGE: prezzo non trovato")
    return nums[0]


def shanghai_block(gold_usd_oz: float | None) -> tuple[dict, list[str]]:
    if not gold_usd_oz:
        return {}, ["premio Shanghai: manca il prezzo oro in dollari"]
    try:
        usdcnh = float(fetch_yahoo("CNY=X", rng="5d", interval="1h").iloc[-1])
    except Exception as e:
        return {}, [f"cambio USDCNY: {str(e)[:60]}"]
    errs = []
    for url in SGE_URLS:
        try:
            cny_g = parse_sge_benchmark(_get(url).text)
            usd_oz = cny_g * GRAMS_PER_OZ / usdcnh
            prem = usd_oz - gold_usd_oz
            return {"sge_cny_g": cny_g, "usdcny": _r(usdcnh, 4), "sge_usd_oz": _r(usd_oz, 1),
                    "premio_usd_oz": _r(prem, 1), "premio_pct": _r(prem / gold_usd_oz * 100, 2),
                    "nota": "premio alto = Cina compra forte; il riferimento SGE e' di oggi, l'oro e' l'ultimo prezzo"}, []
        except Exception as e:
            errs.append(f"{url.split('/')[2]}: {str(e)[:40]}")
    return {}, ["SGE: " + "; ".join(errs)]


# ------------------------------------------------------------------ D3: geopolitica (GDELT)
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = ('(war OR missile OR airstrike OR invasion OR "military escalation" OR sanctions OR "nuclear threat") '
               "sourcelang:english")


def parse_gdelt_timeline(data: dict) -> pd.Series:
    tl = (data.get("timeline") or [{}])[0].get("data") or []
    s = pd.Series({pd.to_datetime(p["date"], format="%Y%m%dT%H%M%SZ", utc=True): float(p["value"]) for p in tl if "date" in p})
    if s.empty:
        raise ValueError("GDELT: serie vuota")
    return s.sort_index()


def geopolitics_block() -> tuple[dict, list[str]]:
    try:
        r = _get(GDELT, params={"query": GDELT_QUERY, "mode": "timelinevol", "timespan": "30d", "format": "json"}, timeout=25)
        s = parse_gdelt_timeline(r.json())
    except Exception as e:
        return {}, [f"GDELT: {str(e)[:80]}"]
    daily = s.resample("1D").mean().dropna()
    last24 = s[s.index >= s.index[-1] - pd.Timedelta(hours=24)].mean()
    base = daily.iloc[:-1]
    z = (last24 - base.mean()) / (base.std() or np.nan) if len(base) > 7 else np.nan
    return {"intensita_24h": _r(last24, 4), "media_30g": _r(base.mean(), 4), "z": _r(z, 2),
            "allarme": bool(not np.isnan(z) and z >= 2),
            "punteggio": _r(math.tanh(max(0.0, z) / 2) if not np.isnan(z) else None, 3)}, []


# ------------------------------------------------------------------ folla: Myfxbook (contrarian)
MYFXBOOK = "https://www.myfxbook.com/community/outlook/XAUUSD"


def parse_myfxbook(html: str) -> dict:
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt)
    m_s = re.search(r"(\d{1,3})\s*%\s*of the (?:forex )?traders are (?:currently )?going short", txt, re.I)
    m_l = re.search(r"(\d{1,3})\s*%\s*of the (?:forex )?traders are (?:currently )?going long", txt, re.I)
    if not (m_s and m_l):
        m_s = re.search(r"Short\s+(\d{1,3})\s*%", txt)
        m_l = re.search(r"Long\s+(\d{1,3})\s*%", txt)
    if not (m_s and m_l):
        raise ValueError("Myfxbook: percentuali non trovate")
    short, long_ = int(m_s.group(1)), int(m_l.group(1))
    return {"privati_long_pct": long_, "privati_short_pct": short}


def crowd_block() -> tuple[dict, list[str]]:
    try:
        d = parse_myfxbook(_get(MYFXBOOK).text)
    except Exception as e:
        return {}, [f"Myfxbook: {str(e)[:80]}"]
    lg = d["privati_long_pct"]
    # contrarian solo agli estremi (oltre 75% da una parte)
    score = -(lg - 75) / 25 if lg > 75 else (25 - lg) / 25 if lg < 25 else 0.0
    d["punteggio_contrarian"] = _r(_clip(score), 3)
    d["nota"] = "folla: conta solo oltre il 75% da una parte, in senso contrario"
    return d, []


# ------------------------------------------------------------------ raccolta
def collect(calendar_raw: list[dict], now: datetime, cot: pd.DataFrame | None, bills: dict[str, pd.Series],
            fred_fetch: Callable[[str, str], pd.Series], effr_hist: pd.Series | None = None,
            bls_key: str = "", budget_s: float = 300) -> tuple[dict, list[str]]:
    """Esegue tutti i blocchi entro un tempo massimo; restituisce (extra, fonti_mancanti)."""
    t0 = time.monotonic()
    extra: dict = {"peso_nel_bias": 0, "nota": "informazioni in prova: non cambiano il bias finche' non le confermi"}
    missing: list[str] = []
    steps = [
        ("intraday", lambda: intraday_block()),
        ("dati_usciti", lambda: surprise_block(calendar_raw, now, fred_fetch, bls_key)),
        ("nowcast_inflazione", lambda: nowcast_block(calendar_raw, now)),
        ("aspettative_fed", lambda: fed_path_block(bills, effr_hist)),
        ("aste_tesoro", lambda: auctions_block(now)),
        ("fed", lambda: fed_block(now)),
        ("cot_flusso", lambda: (cot_flow_block(cot), [])),
        ("etf", lambda: etf_block()),
        ("banche_centrali", lambda: central_banks_block(now)),
        ("geopolitica", lambda: geopolitics_block()),
        ("folla", lambda: crowd_block()),
    ]
    for name, fn in steps:
        if time.monotonic() - t0 > budget_s:
            missing.append(f"{name}: saltato per tempo")
            continue
        try:
            val, miss = fn()
            if val:
                extra[name] = val
            missing += miss
        except Exception as e:
            log.warning("extra %s: %s", name, e)
            missing.append(f"{name}: errore {str(e)[:80]}")
    gold = ((extra.get("intraday") or {}).get("oro_futures") or {}).get("ultimo")
    if time.monotonic() - t0 <= budget_s:
        try:
            val, miss = shanghai_block(gold)
            if val:
                extra["premio_shanghai"] = val
            missing += miss
        except Exception as e:
            missing.append(f"premio_shanghai: errore {str(e)[:80]}")
    extra["sintesi"] = summary_it(extra)
    return extra, missing


def summary_it(x: dict) -> str:
    parts = []
    p = (x.get("intraday") or {}).get("punteggio_4h")
    if p is not None and abs(p) >= 0.2:
        parts.append(f"tassi/dollaro ultime 4h {'pro' if p > 0 else 'contro'} oro ({p:+.2f})")
    for s in x.get("dati_usciti") or []:
        if s.get("stato") == "ok" and s.get("verso_oro"):
            parts.append(f"{s['evento']} {s['dato']} vs {s['previsione']}: oro {'su' if s['verso_oro'] > 0 else 'giu'}")
    for c in (x.get("nowcast_inflazione") or {}).get("prossimi_cpi", []):
        if c["rischio"] != "in linea":
            parts.append(f"{c['evento']}: nowcast {c['nowcast']} vs previsione {c['previsione']} ({c['rischio']})")
    f = (x.get("aspettative_fed") or {}).get("punteggio")
    if f is not None and abs(f) >= 0.3:
        parts.append(f"tagli Fed prezzati {'in aumento' if f > 0 else 'in calo'}")
    e = (x.get("etf") or {}).get("flusso_20g_t")
    if e is not None and abs(e) >= 10:
        parts.append(f"ETF GLD {e:+.0f} t in 20 giorni")
    g = x.get("geopolitica") or {}
    if g.get("allarme"):
        parts.append("picco di notizie di conflitto")
    for a in (x.get("aste_tesoro") or {}).get("ultime", []):
        if a["giudizio"].startswith("debole"):
            parts.append(f"asta {a['titolo']} del {a['data']} debole")
    return "; ".join(parts) or "nessun segnale extra forte"
