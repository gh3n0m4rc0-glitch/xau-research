"""Motore macro: trasforma i dati in un bias per l'oro (-1 ribassista .. +1 rialzista) e una confidenza 0..1.

Logica (relazioni macro classiche dell'oro):
- Rendimenti reali in calo  -> positivo (costo opportunità più basso)         peso 0.35
- Dollaro in indebolimento  -> positivo                                        peso 0.25
- 2 anni in calo (Fed più accomodante) -> positivo                             peso 0.15
- Inflazione attesa in salita -> positivo                                      peso 0.10
- Posizionamento COT Managed Money estremo -> contrarian                        peso 0.10
- VIX in salita (risk-off) -> leggermente positivo                             peso 0.05

Ogni componente è uno z-score della variazione a 20 giorni rispetto all'ultimo anno, compresso con tanh.
La stessa funzione viene usata sia live sia per costruire lo storico del backtest (coerenza).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

WEIGHTS = {
    "real_yield_10y": 0.35,
    "dollar_broad": 0.25,
    "nominal_2y": 0.15,
    "breakeven_10y": 0.10,
    "cot": 0.10,
    "vix": 0.05,
}
# segno della relazione con l'oro: -1 = se la serie sale l'oro tende a scendere
SIGN = {"real_yield_10y": -1, "dollar_broad": -1, "nominal_2y": -1, "breakeven_10y": +1, "vix": +1}
# variazione assoluta (punti) per i tassi, percentuale per i livelli
USE_PCT = {"dollar_broad": True, "vix": True}

LABELS_IT = {
    "real_yield_10y": "rendimenti reali 10y",
    "dollar_broad": "dollaro",
    "nominal_2y": "tasso 2y (Fed)",
    "breakeven_10y": "inflazione attesa",
    "cot": "posizionamento COT",
    "vix": "VIX",
}


@dataclass
class MacroResult:
    bias: float
    confidence: float
    components: dict = field(default_factory=dict)
    regime: str = ""
    summary: str = ""
    risk_multiplier: float = 1.0
    data_age_days: float = 0.0


def zscore_change(s: pd.Series, window: int = 20, lookback: int = 252, pct: bool = False) -> pd.Series:
    s = s.dropna()
    chg = s.pct_change(window) if pct else s.diff(window)
    mu = chg.rolling(lookback, min_periods=60).mean()
    sd = chg.rolling(lookback, min_periods=60).std()
    return (chg - mu) / sd.replace(0, np.nan)


def component_series(series: dict[str, pd.Series]) -> pd.DataFrame:
    """Serie storica giornaliera di tutti i componenti (valori -1..+1)."""
    cols = {}
    for name, sgn in SIGN.items():
        if name not in series or series[name].empty:
            continue
        z = zscore_change(series[name], pct=USE_PCT.get(name, False))
        cols[name] = np.tanh(sgn * z / 1.5)
    df = pd.DataFrame(cols)
    if df.empty:
        return df
    df = df.sort_index().ffill(limit=5)
    return df


def cot_score_series(cot: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.Series:
    """Contrarian agli estremi: percentile del net long Managed Money (% OI) sugli ultimi 3 anni."""
    if cot is None or cot.empty:
        return pd.Series(np.nan, index=index)
    s = cot.set_index("available_from")["mm_net_pct_oi"].sort_index()
    pct = s.rolling(156, min_periods=52).apply(lambda w: (w <= w[-1]).mean(), raw=True)

    def score(p):
        if np.isnan(p):
            return np.nan
        if p > 0.85:
            return -min(1.0, (p - 0.85) / 0.15)
        if p < 0.15:
            return min(1.0, (0.15 - p) / 0.15)
        return 0.0

    sc = pct.apply(score)
    return sc.reindex(index.union(sc.index)).sort_index().ffill(limit=10).reindex(index)


def combine(row: pd.Series) -> tuple[float, float]:
    """Combina i componenti di un giorno in (bias, confidenza)."""
    vals = {k: v for k, v in row.items() if k in WEIGHTS and not pd.isna(v)}
    if not vals:
        return 0.0, 0.0
    wsum = sum(WEIGHTS[k] for k in vals)
    bias = sum(WEIGHTS[k] * v for k, v in vals.items()) / wsum
    # concordanza: quanto i driver puntano nella stessa direzione
    active = {k: v for k, v in vals.items() if abs(v) > 0.1}
    if active:
        aw = sum(WEIGHTS[k] for k in active)
        agreement = abs(sum(WEIGHTS[k] * math.copysign(1, v) for k, v in active.items())) / aw
    else:
        agreement = 0.0
    coverage = wsum / sum(WEIGHTS.values())
    confidence = agreement * min(1.0, abs(bias) * 2.0) * coverage
    return float(np.clip(bias, -1, 1)), float(np.clip(confidence, 0, 1))


def build_history(series: dict[str, pd.Series], cot: pd.DataFrame | None = None) -> pd.DataFrame:
    """Storico giornaliero bias/confidenza per il backtest del cBot (macro_history.csv)."""
    comp = component_series(series)
    if comp.empty:
        return pd.DataFrame(columns=["bias", "confidence"])
    bidx = pd.bdate_range(comp.index.min(), comp.index.max())
    comp = comp.reindex(bidx).ffill(limit=5)
    comp["cot"] = cot_score_series(cot, comp.index)
    out = comp.apply(lambda r: pd.Series(combine(r), index=["bias", "confidence"]), axis=1)
    # il dato del giorno D è noto solo a fine giornata/il giorno dopo: il cBot usa date < oggi
    return out.dropna()


def latest(series: dict[str, pd.Series], cot: pd.DataFrame | None = None, today: pd.Timestamp | None = None) -> MacroResult:
    comp = component_series(series)
    if comp.empty:
        return MacroResult(0.0, 0.0, summary="dati macro non disponibili")
    today = today or pd.Timestamp.now("UTC").tz_localize(None).normalize()
    last = comp.iloc[-1].copy()
    cot_s = cot_score_series(cot, pd.DatetimeIndex([today]))
    last["cot"] = cot_s.iloc[-1] if len(cot_s) else np.nan
    bias, conf = combine(last)

    age = (today - comp.index[-1]).days
    if age > 4:  # dati vecchi (es. FRED in ritardo / festività)
        conf *= max(0.3, 1 - (age - 4) * 0.1)

    comps = {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in last.items()}
    res = MacroResult(bias=round(bias, 3), confidence=round(conf, 3), components=comps, data_age_days=age)
    res.regime = classify_regime(comps)
    res.summary = summarize(res, series)
    res.risk_multiplier = risk_multiplier(series)
    return res


def classify_regime(c: dict) -> str:
    ry, usd = c.get("real_yield_10y") or 0, c.get("dollar_broad") or 0
    if ry > 0.3 and usd > 0.3:
        return "favorevole: reali e dollaro in calo"
    if ry < -0.3 and usd < -0.3:
        return "sfavorevole: reali e dollaro in salita"
    if ry > 0.3 or usd > 0.3:
        return "moderatamente favorevole"
    if ry < -0.3 or usd < -0.3:
        return "moderatamente sfavorevole"
    return "neutro / misto"


def risk_multiplier(series: dict[str, pd.Series]) -> float:
    vix = series.get("vix")
    if vix is None or vix.empty:
        return 1.0
    v = float(vix.iloc[-1])
    if v >= 40:
        return 0.5
    if v >= 30:
        return 0.75
    return 1.0


def summarize(res: MacroResult, series: dict[str, pd.Series]) -> str:
    parts = []
    for k, v in sorted(res.components.items(), key=lambda kv: -abs(kv[1] or 0)):
        if v is None or abs(v) < 0.15:
            continue
        parts.append(f"{LABELS_IT.get(k, k)} {'pro' if v > 0 else 'contro'} oro ({v:+.2f})")
    direction = "rialzista" if res.bias > 0.15 else "ribassista" if res.bias < -0.15 else "neutrale"
    ry = series.get("real_yield_10y")
    extra = f" | reale 10y {ry.iloc[-1]:.2f}%" if ry is not None and not ry.empty else ""
    return f"Macro {direction} (bias {res.bias:+.2f}, conf {res.confidence:.2f}): " + ("; ".join(parts[:4]) or "driver deboli") + extra
