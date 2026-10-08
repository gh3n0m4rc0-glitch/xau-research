"""Ricerca PRE-NEWS: nelle 10 ore prima di un dato pesante il desk si informa (anteprime degli analisti,
consenso vs "whisper", forum, posizionamento, quadro macro e volumetrico) e forma una VISTA:
in che direzione dovrebbe muoversi l'oro se il dato esce come il desk si aspetta.

Il cBot usa questa vista in due modi:
  1) prima della news: obiezione/veto ai trade contrari alla vista (capo News nel confronto);
  2) dopo la news (strategia E): continuation SOLO se il movimento conferma la vista già formata.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import forums, llm

log = logging.getLogger(__name__)

SYSTEM = """Sei lo specialista "pre-news" di un desk che opera solo sull'oro (XAUUSD).
Tra poche ore esce un dato macro USA importante. Hai: dati del calendario (precedente, consenso), anteprime degli
analisti, discussioni dai forum, quadro macro quantitativo, livelli volumetrici e il playbook del desk.
Compito:
1. Stima se il dato tende a sorprendere sopra/sotto il consenso (whisper, indicatori anticipatori citati, tendenza recente).
2. Traduci in impatto sull'oro: dato USA forte/inflazione alta/Fed più restrittiva => oro giù; debole/dovish => oro su.
   Considera cosa è già prezzato e il posizionamento (se tutti attendono la stessa cosa, la reazione può essere opposta).
3. Indica la direzione attesa per l'oro DOPO il rilascio (-1, 0, +1) e la confidenza (0..1). Sii prudente: con
   informazioni contraddittorie usa 0 o confidenza bassa. Il desk farà continuation SOLO se il prezzo conferma.
Rispondi SOLO con JSON valido:
{"gold_direction": -1|0|1, "confidence": 0..1, "expected_surprise": "above|below|inline|unclear",
 "scenarios": {"hot": "stringa", "cool": "stringa", "inline": "stringa"},
 "continuation_plan": "condizioni di prezzo/volume che confermano la vista (max 2 frasi)",
 "crowd_positioning": "stringa breve", "summary_it": "max 2 frasi"}"""


def events_in_window(calendar: list[dict], now: datetime, hours: float = 10.0, max_tier: int = 2) -> list[dict]:
    out = []
    for e in calendar:
        t = datetime.strptime(e["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now <= t <= now + timedelta(hours=hours) and e.get("tier", 9) <= max_tier:
            out.append(e)
    return out


def _key(e: dict) -> str:
    return f"{e['time_utc']}|{e['title']}"


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def research_event(e: dict, macro_summary: str, volume_note: str, knowledge: str, model: str | None) -> dict | None:
    previews = forums.news_previews(e["title"])
    forum = forums.reddit_search(_forum_query(e["title"]))
    prev_txt = "\n".join(f"- {p['title']} ({p.get('published', '')})" for p in previews[:25]) or "(nessuna anteprima trovata)"
    forum_txt = "\n".join(f"- [{p['source']}, score {p['score']}] {p['title']} :: {p['text'][:200]}" for p in forum[:20]) or "(forum non disponibili)"
    user = (f"EVENTO: {e['title']} alle {e['time_utc']} (tier {e.get('tier')}), precedente {e.get('previous', '?')}, consenso {e.get('forecast', '?')}\n\n"
            f"ANTEPRIME ANALISTI:\n{prev_txt}\n\nFORUM / COMMUNITY:\n{forum_txt}\n\nQUADRO MACRO:\n{macro_summary}\n\n"
            f"VOLUMI / POSIZIONAMENTO:\n{volume_note}\n\nPLAYBOOK (estratti):\n{knowledge[:2500]}")
    d = llm.ask_json(SYSTEM, user, model, 900)
    if not d:
        return None
    return {
        "event": e["title"], "time_utc": e["time_utc"], "tier": e.get("tier", 1),
        "gold_direction": int(max(-1, min(1, round(llm.clamp(d.get("gold_direction", 0), -1, 1))))),
        "confidence": round(llm.clamp(d.get("confidence", 0), 0, 1), 2),
        "expected_surprise": d.get("expected_surprise", "unclear"),
        "scenarios": d.get("scenarios", {}),
        "continuation_plan": d.get("continuation_plan", ""),
        "crowd_positioning": d.get("crowd_positioning", ""),
        "summary_it": d.get("summary_it", ""),
        "sources": {"previews": len(previews), "forum_posts": len(forum)},
        "researched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _forum_query(title: str) -> str:
    t = title.lower()
    if "non-farm" in t or "nonfarm" in t:
        return "NFP OR payrolls"
    if "cpi" in t:
        return "CPI inflation"
    if "fomc" in t or "federal funds" in t or "rate" in t:
        return "FOMC OR Fed decision"
    if "pce" in t:
        return "PCE inflation"
    return title


def run(calendar: list[dict], now: datetime, cache_path: Path, macro_summary: str, volume_note: str = "",
        knowledge: str = "", hours: float = 10.0, refresh_hours: float = 2.0, model: str | None = None) -> list[dict]:
    """Restituisce le viste pre-news valide (ricercando gli eventi nuovi o con vista vecchia di oltre refresh_hours)."""
    cache = load_cache(cache_path)
    views = []
    for e in events_in_window(calendar, now, hours):
        k = _key(e)
        v = cache.get(k)
        stale = v is None or (now - datetime.strptime(v["researched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)) > timedelta(hours=refresh_hours)
        if stale and llm.available():
            log.info("Ricerca pre-news: %s (%s)", e["title"], e["time_utc"])
            nv = research_event(e, macro_summary, volume_note, knowledge, model)
            if nv:
                cache[k] = v = nv
        if v:
            views.append(v)
    # le viste restano utili per la continuation fino a 6 ore dopo l'evento
    for k, v in cache.items():
        t = datetime.strptime(v["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if now - timedelta(hours=6) <= t < now and v not in views:
            views.append(v)
    cache = {k: v for k, v in cache.items()
             if datetime.strptime(v["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) > now - timedelta(days=2)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return sorted(views, key=lambda x: x["time_utc"])
