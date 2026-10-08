"""Normalizzazione del calendario economico per il cBot (news_calendar.json)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TIER1_KEYWORDS = (
    "non-farm", "nonfarm", "fomc", "federal funds rate", "fed chair", "cpi", "pce",
    "powell", "interest rate decision", "jackson hole",
)
RELEVANT_CCY = {"USD", "ALL", "CNY", "XAU"}


def tier_of(title: str, impact: str) -> int:
    t = (title or "").lower()
    if any(k in t for k in TIER1_KEYWORDS):
        return 1
    if (impact or "").lower() == "high":
        return 2
    if (impact or "").lower() == "medium":
        return 3
    return 4


def normalize_calendar(raw: list[dict]) -> list[dict]:
    out = []
    for e in raw:
        ccy = (e.get("country") or e.get("currency") or "").upper()
        if ccy not in RELEVANT_CCY:
            continue
        try:
            dt = datetime.fromisoformat(str(e.get("date") or e.get("time_utc")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        tier = tier_of(e.get("title", ""), e.get("impact", ""))
        if ccy == "CNY" and tier > 2:
            continue
        if tier > 3:
            continue
        out.append({
            "time_utc": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "currency": "USD" if ccy == "USD" else ccy,
            "impact": e.get("impact", ""),
            "title": e.get("title", ""),
            "tier": tier,
            "forecast": e.get("forecast", ""),
            "previous": e.get("previous", ""),
        })
    return sorted(out, key=lambda x: x["time_utc"])


def upcoming(events: list[dict], now: datetime, hours: int = 48, max_tier: int = 2) -> list[dict]:
    res = []
    for e in events:
        t = datetime.strptime(e["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now <= t <= now + timedelta(hours=hours) and e["tier"] <= max_tier:
            res.append(e)
    return res
