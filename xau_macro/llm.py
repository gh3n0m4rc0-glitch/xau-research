"""Accesso a Claude (Anthropic API) condiviso da tutti gli analisti AI del servizio.

È OPZIONALE: senza ANTHROPIC_API_KEY il servizio funziona solo con i motori quantitativi.
Gli LLM NON piazzano ordini: producono viste strutturate (JSON) che il cBot pesa insieme agli altri capi.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable

log = logging.getLogger(__name__)

# Per i test si può sostituire con una funzione fake(system, user, model, max_tokens) -> str
TRANSPORT: Callable[[str, str, str, int], str] | None = None


def available() -> bool:
    return TRANSPORT is not None or bool(os.environ.get("ANTHROPIC_API_KEY"))


def default_model(model: str | None = None) -> str:
    return model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def ask_text(system: str, user: str, model: str | None = None, max_tokens: int = 900) -> str | None:
    model = default_model(model)
    if TRANSPORT is not None:
        return TRANSPORT(system, user, model, max_tokens)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        log.warning("pacchetto 'anthropic' non installato: pip install anthropic")
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        log.warning("Chiamata Claude fallita: %s", e)
        return None


def extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def ask_json(system: str, user: str, model: str | None = None, max_tokens: int = 900) -> dict | None:
    return extract_json(ask_text(system, user, model, max_tokens))


def clamp(x, lo, hi, default=0.0) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------------------------
#  Analista news generale (titoli recenti -> bias news + flag di rischio)
# ---------------------------------------------------------------------------------------------
SYSTEM_NEWS = """Sei il capo analista news di un desk che opera SOLO sull'oro spot (XAUUSD) in ottica swing/intraday.
Ricevi: titoli recenti, quadro macro quantitativo, calendario economico ed estratti del playbook del desk.
Valuta l'impatto atteso sull'oro nei prossimi 1-5 giorni: politica Fed e tassi reali, dollaro, inflazione, rischio
geopolitico (bene rifugio), acquisti delle banche centrali, flussi ETF, domanda Cina/India, stress finanziario.
Distingui notizie già prezzate da sorprese. Se le notizie sono miste o vecchie, confidenza bassa.
halt_trading=true SOLO per eventi estremi e in corso (crisi sistemica, mercati in caos, sospensioni, flash crash).
Rispondi SOLO con JSON valido:
{"news_bias": -1..1, "confidence": 0..1, "key_drivers": [max 4 stringhe], "risk_flags": [stringhe],
 "halt_trading": true/false, "halt_reason": "stringa", "summary_it": "max 2 frasi in italiano"}"""


def analyze(headlines: list[dict], macro_summary: str, calendar: list[dict], model: str | None = None, knowledge: str = "") -> dict | None:
    if not available():
        log.info("ANTHROPIC_API_KEY assente: analisi news con Claude disattivata")
        return None
    titles = "\n".join(f"- [{h.get('published', '')}] {h['title']}" for h in headlines[:60]) or "(nessun titolo)"
    cal = "\n".join(f"- {e['time_utc']} {e['currency']} T{e['tier']} {e['title']} (prev {e.get('previous', '')}, cons {e.get('forecast', '')})"
                    for e in calendar[:25]) or "(nessun evento)"
    user = f"QUADRO MACRO:\n{macro_summary}\n\nCALENDARIO:\n{cal}\n\nTITOLI RECENTI:\n{titles}\n\nPLAYBOOK (estratti):\n{knowledge[:3000]}"
    return parse_llm_json(ask_text(SYSTEM_NEWS, user, model, 800))


def parse_llm_json(text: str | None) -> dict | None:
    d = extract_json(text)
    if d is None:
        return None
    d["news_bias"] = clamp(d.get("news_bias", 0), -1, 1)
    d["confidence"] = clamp(d.get("confidence", 0), 0, 1)
    d["halt_trading"] = bool(d.get("halt_trading", False))
    d.setdefault("risk_flags", [])
    d.setdefault("key_drivers", [])
    d.setdefault("summary_it", "")
    d.setdefault("halt_reason", "")
    return d
