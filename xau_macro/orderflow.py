"""Order flow REALE dei futures oro COMEX (GC) tramite Databento (opzionale, a pagamento a consumo).

Perché: sull'oro spot CFD il cBot vede solo il "tick volume". I futures GC sono il mercato dove passa il
volume vero dell'oro, con il lato dell'aggressore per ogni trade (B = compratore aggressivo, A = venditore).
Da qui calcoliamo: volume profile reale (POC/VAH/VAL/HVN/LVN), delta e CVD, bilancio dei grandi ordini.

Il cBot converte i livelli futures in prezzi spot sottraendo la "base" (GC - spot) al momento del dato.
Richiede: pip install databento  +  variabile DATABENTO_API_KEY.
Nota: i nuovi account Databento ricevono crediti gratuiti per lo storico; il live richiede un piano mensile.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
DATASET = "GLBX.MDP3"
SYMBOL = "GC.v.0"          # contratto continuo: front month per volume


# ---------------------------------------------------------------------------------------------
#  Calcoli puri (testabili senza rete)
# ---------------------------------------------------------------------------------------------
def volume_profile(bars: pd.DataFrame, bin_size: float = 0.5, va_pct: float = 0.70) -> dict:
    """Profilo volumetrico da barre OHLCV (colonne high, low, volume): volume distribuito uniformemente nel range."""
    if bars.empty:
        return {}
    lo, hi = float(bars["low"].min()), float(bars["high"].max())
    while (hi - lo) / bin_size > 4000:
        bin_size *= 2
    nb = int(np.ceil((hi - lo) / bin_size)) + 1
    hist = np.zeros(nb)
    for h, l, v in zip(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["volume"].to_numpy()):
        a, b = int((l - lo) / bin_size), int((h - lo) / bin_size)
        hist[a:b + 1] += v / (b - a + 1)
    total = hist.sum()
    poc = int(hist.argmax())
    up = dn = poc
    acc = hist[poc]
    while acc < total * va_pct and (up < nb - 1 or dn > 0):
        nu = hist[up + 1] if up < nb - 1 else -1
        nd = hist[dn - 1] if dn > 0 else -1
        if nu >= nd:
            up += 1; acc += nu
        else:
            dn -= 1; acc += nd
    sm = np.convolve(hist, np.ones(3) / 3, mode="same")
    mean = total / nb
    hvn = [(lo + (k + 0.5) * bin_size, sm[k]) for k in range(2, nb - 2) if sm[k] > sm[k - 1] and sm[k] >= sm[k + 1] and sm[k] > 1.3 * mean]
    lvn = [lo + (k + 0.5) * bin_size for k in range(max(2, dn + 1), min(nb - 2, up)) if sm[k] < sm[k - 1] and sm[k] <= sm[k + 1] and sm[k] < 0.5 * mean]
    return {
        "poc": round(lo + (poc + 0.5) * bin_size, 2),
        "vah": round(lo + (up + 1) * bin_size, 2),
        "val": round(lo + dn * bin_size, 2),
        "hvn": [round(p, 2) for p, _ in sorted(hvn, key=lambda x: -x[1])[:6]],
        "lvn": [round(p, 2) for p in lvn[:6]],
        "total_volume": float(total),
    }


def delta_stats(trades: pd.DataFrame, big_size: int = 20, bucket: str = "1h") -> dict:
    """Delta/CVD dai trade con lato aggressore (colonne: ts (datetime), price, size, side in {'A','B','N'})."""
    if trades.empty:
        return {}
    t = trades.copy()
    sign = np.where(t["side"] == "B", 1, np.where(t["side"] == "A", -1, 0))
    t["d"] = sign * t["size"]
    t = t.set_index("ts").sort_index()
    per = t["d"].resample(bucket).sum()
    px = t["price"].resample(bucket).last().ffill()
    cvd = per.cumsum()
    vol = t["size"].sum()
    session_delta = float(t["d"].sum())
    # trend del delta: ultime 6 ore normalizzate sul volume medio orario
    recent = per.tail(6)
    avg_abs = per.abs().mean() or 1.0
    delta_trend = float(np.tanh(recent.sum() / (6 * avg_abs)))
    big = t[t["size"] >= big_size]
    big_bias = float(np.tanh(big["d"].sum() / max(1.0, big["size"].sum()) * 2)) if len(big) else 0.0
    # divergenza: prezzo su nuovi massimi ma CVD no (o viceversa) nelle ultime 24 barre
    div = "none"
    if len(px) >= 12:
        half = len(px) // 2
        p1, p2 = px.iloc[:half], px.iloc[half:]
        c1, c2 = cvd.iloc[:half], cvd.iloc[half:]
        if p2.max() > p1.max() and c2.max() < c1.max():
            div = "bearish"
        elif p2.min() < p1.min() and c2.min() > c1.min():
            div = "bullish"
    return {
        "session_delta": round(session_delta, 1),
        "delta_pct_volume": round(session_delta / vol, 4) if vol else 0.0,
        "delta_trend": round(delta_trend, 3),
        "big_trades_bias": round(big_bias, 3),
        "big_trades_count": int(len(big)),
        "cvd_divergence": div,
    }


def book_imbalance(book: pd.DataFrame, levels: int = 10) -> float | None:
    """DOM reale CME (schema mbp-10): sbilanciamento medio tra size in bid e in ask sui primi N livelli (-1..+1)."""
    bcols = [f"bid_sz_{i:02d}" for i in range(levels) if f"bid_sz_{i:02d}" in book.columns]
    acols = [f"ask_sz_{i:02d}" for i in range(levels) if f"ask_sz_{i:02d}" in book.columns]
    if book.empty or not bcols or not acols:
        return None
    b, a = book[bcols].sum(axis=1), book[acols].sum(axis=1)
    tot = (b + a).replace(0, np.nan)
    return round(float(((b - a) / tot).dropna().mean()), 3)


def build_levels(bars_composite: pd.DataFrame, bars_prev: pd.DataFrame, trades: pd.DataFrame, gc_last: float, ts: datetime) -> dict:
    out = {
        "timestamp_utc": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "CME GC futures (Databento)",
        "gc_last": round(float(gc_last), 2),
        "composite": volume_profile(bars_composite),
        "prev_session": volume_profile(bars_prev),
    }
    out.update(delta_stats(trades))
    return out


# ---------------------------------------------------------------------------------------------
#  Download da Databento
# ---------------------------------------------------------------------------------------------
def fetch_levels(days: int = 5, trade_hours: int = 12) -> dict | None:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        return None
    try:
        import databento as db
    except ImportError:
        log.warning("pacchetto 'databento' non installato: pip install databento")
        return None
    try:
        client = db.Historical(key)
        rng = client.metadata.get_dataset_range(dataset=DATASET)
        raw_end = rng["end"] if isinstance(rng, dict) else getattr(rng, "end", None)
        end = pd.Timestamp(raw_end) if raw_end is not None else pd.Timestamp.now("UTC")
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
        start = end - pd.Timedelta(days=days + 2)
        bars = client.timeseries.get_range(dataset=DATASET, schema="ohlcv-1m", symbols=SYMBOL, stype_in="continuous",
                                           start=start.isoformat(), end=end.isoformat()).to_df()
        if bars.empty:
            return None
        bars = bars.reset_index().rename(columns={"ts_event": "ts"})
        last_day = bars["ts"].dt.date.max()
        prev = bars[bars["ts"].dt.date < last_day]
        prev = prev[prev["ts"].dt.date == prev["ts"].dt.date.max()] if not prev.empty else prev
        trades = client.timeseries.get_range(dataset=DATASET, schema="trades", symbols=SYMBOL, stype_in="continuous",
                                             start=(end - pd.Timedelta(hours=trade_hours)).isoformat(), end=end.isoformat()).to_df()
        trades = trades.reset_index().rename(columns={"ts_event": "ts"})[["ts", "price", "size", "side"]] if not trades.empty else pd.DataFrame(columns=["ts", "price", "size", "side"])
        gc_last = float(bars["close"].iloc[-1])
        book_imb = None
        try:   # DOM reale CME: ultimi 10 minuti del book a 10 livelli
            book = client.timeseries.get_range(dataset=DATASET, schema="mbp-10", symbols=SYMBOL, stype_in="continuous",
                                               start=(end - pd.Timedelta(minutes=10)).isoformat(), end=end.isoformat()).to_df()
            book_imb = book_imbalance(book)
        except Exception as e:
            log.info("DOM CME (mbp-10) non disponibile: %s", e)
        last_ts = pd.Timestamp(bars["ts"].iloc[-1])
        last_ts = last_ts.tz_localize("UTC") if last_ts.tzinfo is None else last_ts
        out = build_levels(bars, prev, trades, gc_last, last_ts.to_pydatetime())
        out["book_imbalance"] = book_imb
        return out
    except Exception as e:
        log.warning("Databento non disponibile: %s", e)
        return None
