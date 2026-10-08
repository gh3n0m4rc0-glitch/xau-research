"""Servizio macro / news / pre-news / order flow / comitato AI per XAU Committee Bot.

Uso:
    python -m xau_macro.service --once          # un ciclo e termina (per cron / launchd)
    python -m xau_macro.service                 # ciclo continuo (default ogni 30 min)
    python -m xau_macro.service --history       # crea macro_history.csv per il backtest
    python -m xau_macro.service --committee     # forza subito una riunione del comitato AI

Scrive nella cartella dati del cBot:
    macro_bias.json       bias macro+news, confidenza, HALT, viste pre-news (pre_news)
    news_calendar.json    eventi USD rilevanti in UTC (tier 1/2/3)
    volume_levels.json    order flow reale futures CME (se DATABENTO_API_KEY)
    ai_committee.json     vista sintetica del comitato AI (se ANTHROPIC_API_KEY)
    committee_minutes.md  verbale leggibile dell'ultima riunione
Legge dal cBot: market_snapshot.json (cosa vede il bot sul grafico).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import committee, engine, knowledge, llm, news, orderflow, prenews, sources

log = logging.getLogger("xau_macro")
HERE = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG = {
    "output_dir": "./output",
    "interval_minutes": 30,
    "calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "news_feeds": [
        "https://news.google.com/rss/search?q=gold+price+OR+%22Federal+Reserve%22+OR+inflation+OR+treasury+yields&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=geopolitical+risk+OR+central+bank+gold+buying&hl=en-US&gl=US&ceid=US:en",
    ],
    "news_weight": 0.25,
    "history_start": "2015-01-01",
    "anthropic_model": "claude-sonnet-4-6",
    "knowledge_dir": str(HERE / "knowledge"),
    "prenews_hours": 10,
    "prenews_refresh_hours": 2,
    "committee_enabled": True,
    "committee_hours": 4,
    "committee_rounds": 2,
    "orderflow_minutes": 30,
    "telegram_token": "",
    "telegram_chat_id": "",
    "morning_brief_hour_utc": 6,
}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    p = Path(path) if path else HERE / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    cfg["telegram_token"] = os.environ.get("TELEGRAM_TOKEN", cfg["telegram_token"])
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram_chat_id"])
    return cfg


def write_atomic(path: Path, text: str) -> None:
    """Scrittura atomica: il cBot non legge mai un file a metà."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, max_age_hours: float | None = None) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if max_age_hours is not None:
        try:
            ts = datetime.strptime(d.get("timestamp_utc", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours):
                return None
        except ValueError:
            return None
    return d


def blend(macro: engine.MacroResult, news_view: dict | None, news_weight: float) -> dict:
    bias, conf = macro.bias, macro.confidence
    halt, halt_reason, flags, summary = False, "", [], macro.summary
    risk_mult = macro.risk_multiplier
    if news_view:
        nb, nc = news_view["news_bias"], news_view["confidence"]
        w = news_weight * nc
        bias = (1 - w) * bias + w * nb
        agree = 1 if nb * macro.bias > 0 else -1 if nb * macro.bias < 0 else 0
        conf = max(0.0, min(1.0, conf + 0.15 * agree * nc))
        halt = news_view["halt_trading"]
        halt_reason = news_view.get("halt_reason", "")
        flags = news_view.get("risk_flags", [])
        if flags:
            risk_mult = min(risk_mult, 0.8)
        if news_view.get("summary_it"):
            summary = summary + " | News: " + news_view["summary_it"]
    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bias": round(max(-1.0, min(1.0, bias)), 3),
        "confidence": round(conf, 3),
        "regime": macro.regime,
        "summary": summary,
        "components": macro.components,
        "news": news_view or {},
        "risk_flags": flags,
        "risk_multiplier": round(risk_mult, 2),
        "halt_trading": halt,
        "halt_reason": halt_reason,
        "data_age_days": macro.data_age_days,
        "pre_news": [],
    }


def committee_due(state: dict, now: datetime, cfg: dict, calendar: list[dict]) -> bool:
    if not cfg.get("committee_enabled", True):
        return False
    last = state.get("last_committee")
    if not last:
        return True
    elapsed = now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if elapsed >= timedelta(hours=float(cfg["committee_hours"])):
        return True
    # con una news tier1 nelle prossime ore il comitato si riunisce ogni ora
    soon = prenews.events_in_window(calendar, now, float(cfg["prenews_hours"]), max_tier=1)
    return bool(soon) and elapsed >= timedelta(hours=1)


def run_once(cfg: dict, force_committee: bool = False) -> dict:
    out_dir = Path(cfg["output_dir"]).expanduser()
    now = datetime.now(timezone.utc)
    state = read_json(out_dir / "service_state.json") or {}
    kb = knowledge.KnowledgeBase(cfg["knowledge_dir"])

    # 1) calendario
    calendar: list[dict] = []
    try:
        calendar = news.normalize_calendar(sources.fetch_calendar(cfg["calendar_url"]))
        write_atomic(out_dir / "news_calendar.json", json.dumps(calendar, ensure_ascii=False, indent=1))
        log.info("Calendario: %d eventi rilevanti", len(calendar))
    except Exception as e:
        log.warning("Calendario non aggiornato (il cBot userà l'ultimo file valido + regole FOMC/NFP): %s", e)
        old = out_dir / "news_calendar.json"
        if old.exists():
            try:
                calendar = json.loads(old.read_text(encoding="utf-8"))
            except Exception:
                pass

    # 2) macro quantitativa
    start = (pd.Timestamp.now("UTC") - pd.Timedelta(days=900)).strftime("%Y-%m-%d")
    series = sources.fetch_all_fred(start)
    cot = None
    try:
        cot = sources.fetch_cot_gold(start)
    except Exception as e:
        log.warning("COT non disponibile: %s", e)
    macro = engine.latest(series, cot)
    log.info(macro.summary)

    # 3) order flow CME (opzionale)
    of = read_json(out_dir / "volume_levels.json")
    last_of = state.get("last_orderflow")
    if os.environ.get("DATABENTO_API_KEY") and (not last_of or now - datetime.strptime(last_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= timedelta(minutes=int(cfg["orderflow_minutes"]))):
        new_of = orderflow.fetch_levels()
        if new_of:
            of = new_of
            write_atomic(out_dir / "volume_levels.json", json.dumps(of, ensure_ascii=False, indent=1))
            state["last_orderflow"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            log.info("Order flow CME: POC %s, delta trend %s, grandi ordini %s", of.get("composite", {}).get("poc"), of.get("delta_trend"), of.get("big_trades_bias"))
    volume_note = json.dumps({k: of.get(k) for k in ("composite", "delta_trend", "big_trades_bias", "cvd_divergence")}, ensure_ascii=False) if of else "order flow CME non disponibile"

    # 4) news generali + Claude
    headlines = sources.fetch_headlines(cfg.get("news_feeds", []))
    view = llm.analyze(headlines, macro.summary, news.upcoming(calendar, now, 72, 3), cfg.get("anthropic_model"),
                       kb.context("oro news reazione Fed inflazione dollaro geopolitica", 4))
    result = blend(macro, view, float(cfg.get("news_weight", 0.25)))

    # 5) ricerca pre-news (10 ore prima degli eventi pesanti)
    result["pre_news"] = prenews.run(calendar, now, out_dir / "prenews_cache.json", result["summary"], volume_note,
                                     kb.context("reazione oro NFP CPI FOMC sorpresa consenso continuation", 5),
                                     float(cfg["prenews_hours"]), float(cfg["prenews_refresh_hours"]), cfg.get("anthropic_model"))
    result["upcoming_events"] = news.upcoming(calendar, now, 48, 2)[:10]
    write_atomic(out_dir / "macro_bias.json", json.dumps(result, ensure_ascii=False, indent=1))
    log.info("macro_bias.json: bias %+.2f conf %.2f halt=%s, viste pre-news %d", result["bias"], result["confidence"], result["halt_trading"], len(result["pre_news"]))

    # 6) comitato AI
    if (force_committee or committee_due(state, now, cfg, calendar)) and llm.available():
        snap = read_json(out_dir / "market_snapshot.json", max_age_hours=3)
        briefing = committee.build_briefing(snap, result, headlines, news.upcoming(calendar, now, 48, 2), result["pre_news"], of)
        res = committee.run(briefing, lambda q: kb.context(q, 5), int(cfg["committee_rounds"]), cfg.get("anthropic_model"))
        if res:
            slim = {k: v for k, v in res.items() if k != "views"}
            write_atomic(out_dir / "ai_committee.json", json.dumps(slim, ensure_ascii=False, indent=1))
            write_atomic(out_dir / "committee_minutes.md", committee.minutes_markdown(res))
            state["last_committee"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            log.info("Comitato AI: %s conv %.2f (accordo %.2f)", res["stance"], res["conviction"], res["agreement"])
            telegram(cfg, f"🏛 Comitato AI: {res['stance']} (conv {res['conviction']}, accordo {res['agreement']})\n{res['summary_it']}\nDissenso: {res['dissent']}")
    write_atomic(out_dir / "service_state.json", json.dumps(state))
    return result


def build_history_file(cfg: dict) -> Path:
    series = sources.fetch_all_fred(cfg["history_start"])
    cot = None
    try:
        cot = sources.fetch_cot_gold(cfg["history_start"])
    except Exception as e:
        log.warning("COT storico non disponibile: %s", e)
    hist = engine.build_history(series, cot)
    out = Path(cfg["output_dir"]).expanduser() / "macro_history.csv"
    lines = ["date,bias,confidence"] + [f"{d:%Y-%m-%d},{r.bias:.4f},{r.confidence:.4f}" for d, r in hist.iterrows()]
    write_atomic(out, "\n".join(lines) + "\n")
    log.info("Storico macro: %d giorni -> %s", len(hist), out)
    return out


def telegram(cfg: dict, text: str) -> None:
    if not cfg.get("telegram_token") or not cfg.get("telegram_chat_id"):
        return
    import requests
    try:
        requests.get(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage",
                     params={"chat_id": cfg["telegram_chat_id"], "text": text[:4000]}, timeout=20)
    except Exception as e:
        log.warning("Telegram: %s", e)


def morning_brief(result: dict) -> str:
    ev = "\n".join(f"• {e['time_utc'][5:16].replace('T', ' ')} UTC {e['title']} (T{e['tier']})" for e in result.get("upcoming_events", [])[:6]) or "• nessuno"
    pre = "\n".join(f"• {p['event']}: oro {'su' if p['gold_direction'] > 0 else 'giù' if p['gold_direction'] < 0 else 'neutro'} (conf {p['confidence']})" for p in result.get("pre_news", [])) or "• nessuna"
    flags = ", ".join(result.get("risk_flags", [])) or "nessuno"
    return (f"☀️ Briefing oro\n{result['summary']}\n\nBias {result['bias']:+.2f} | conf {result['confidence']:.2f} | "
            f"rischio x{result['risk_multiplier']}\nFlag: {flags}\nHALT: {'SÌ - ' + result['halt_reason'] if result['halt_trading'] else 'no'}"
            f"\n\nEventi 48h:\n{ev}\n\nViste pre-news:\n{pre}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Servizio macro/news/AI per XAU Committee Bot")
    ap.add_argument("--config")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--committee", action="store_true", help="forza una riunione del comitato AI")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.history:
        build_history_file(cfg)
        return 0
    if args.once or args.committee:
        res = run_once(cfg, force_committee=args.committee)
        if datetime.now(timezone.utc).hour == int(cfg.get("morning_brief_hour_utc", 6)):
            telegram(cfg, morning_brief(res))
        return 0

    last_brief_day = None
    while True:
        try:
            res = run_once(cfg)
            now = datetime.now(timezone.utc)
            if now.hour >= int(cfg.get("morning_brief_hour_utc", 6)) and last_brief_day != now.date():
                telegram(cfg, morning_brief(res))
                last_brief_day = now.date()
        except Exception as e:
            log.exception("Errore nel ciclo: %s", e)
        time.sleep(int(cfg.get("interval_minutes", 30)) * 60)


if __name__ == "__main__":
    sys.exit(main())
