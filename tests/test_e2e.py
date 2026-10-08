"""Ciclo completo del servizio senza rete: fonti e Claude simulati. Verifica i file letti dal cBot."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from xau_macro import forums, llm, service, sources
from tests.test_engine import series_gold_bullish

# campi che il cBot legge (vedi RefreshMacro / RefreshAi / RefreshExternalVolume in XauCommitteeBot.cs)
CBOT_MACRO = {"timestamp_utc", "bias", "confidence", "regime", "summary", "risk_multiplier", "halt_trading", "halt_reason", "pre_news"}
CBOT_PRENEWS = {"event", "time_utc", "tier", "gold_direction", "confidence", "continuation_plan"}
CBOT_AI = {"timestamp_utc", "stance", "conviction", "preferred_playbook", "summary_it"}


def test_full_cycle(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    ev_time = (now + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    monkeypatch.setattr(sources, "fetch_calendar", lambda url: [{"title": "CPI m/m", "country": "USD", "date": ev_time, "impact": "High", "forecast": "0.3%", "previous": "0.4%"}])
    monkeypatch.setattr(sources, "fetch_all_fred", lambda start: series_gold_bullish())
    monkeypatch.setattr(sources, "fetch_cot_gold", lambda start: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(sources, "fetch_headlines", lambda feeds, max_per_feed=25: [{"title": "Gold climbs as yields slip", "published": "", "source": "x"}])
    monkeypatch.setattr(forums, "news_previews", lambda t, extra="gold": [{"title": "CPI preview: core seen cooling"}])
    monkeypatch.setattr(forums, "reddit_search", lambda q, **k: [])
    (tmp_path / "market_snapshot.json").write_text(json.dumps({"timestamp_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "price": 4000}))

    def transport(system, user, model, max_tokens):
        if "pre-news" in system:
            return json.dumps({"gold_direction": 1, "confidence": 0.6, "continuation_plan": "sopra VAH con volume"})
        if "CIO" in system:
            return json.dumps({"stance": "long", "conviction": 0.7, "preferred_playbook": "trend", "summary_it": "rialzo", "dissent": "-"})
        if "RUOLO" in system:
            return json.dumps({"stance": "long", "conviction": 0.6, "playbook": "trend", "key_points": ["x"]})
        return json.dumps({"news_bias": 0.4, "confidence": 0.5, "halt_trading": False, "summary_it": "positivo"})
    llm.TRANSPORT = transport
    try:
        cfg = dict(service.DEFAULT_CONFIG, output_dir=str(tmp_path))
        res = service.run_once(cfg)
    finally:
        llm.TRANSPORT = None

    macro = json.loads((tmp_path / "macro_bias.json").read_text())
    assert CBOT_MACRO <= set(macro) and macro["bias"] > 0
    assert len(macro["pre_news"]) == 1 and CBOT_PRENEWS <= set(macro["pre_news"][0])
    ai = json.loads((tmp_path / "ai_committee.json").read_text())
    assert CBOT_AI <= set(ai) and ai["stance"] == "long"
    cal = json.loads((tmp_path / "news_calendar.json").read_text())
    assert cal[0]["tier"] == 1 and cal[0]["time_utc"].endswith("Z")
    assert (tmp_path / "committee_minutes.md").exists()
    # secondo ciclo subito dopo: il comitato NON si riunisce di nuovo... tranne che con tier1 entro 10h e >1h trascorsa
    st = json.loads((tmp_path / "service_state.json").read_text())
    assert "last_committee" in st
