import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from xau_macro import committee, forums, knowledge, llm, orderflow, prenews, service


def test_volume_profile_poc_and_value_area():
    rng = np.random.default_rng(0)
    n = 2000
    px = 2000 + rng.normal(0, 3, n)          # volume concentrato attorno a 2000
    bars = pd.DataFrame({"high": px + 0.5, "low": px - 0.5, "volume": rng.integers(50, 150, n)})
    vp = orderflow.volume_profile(bars, bin_size=0.5)
    assert abs(vp["poc"] - 2000) < 2
    assert vp["val"] < vp["poc"] < vp["vah"]
    assert 2 < vp["vah"] - vp["val"] < 15


def test_delta_stats_buyers_dominate():
    ts = pd.date_range("2026-10-01 08:00", periods=600, freq="1min", tz="UTC")
    side = np.where(np.arange(600) % 4 == 0, "A", "B")    # 75% acquisti aggressivi
    size = np.where(np.arange(600) % 50 == 0, 40, 2)
    tr = pd.DataFrame({"ts": ts, "price": np.linspace(2000, 2010, 600), "size": size, "side": side})
    d = orderflow.delta_stats(tr)
    assert d["session_delta"] > 0 and d["delta_trend"] > 0.3
    assert d["big_trades_count"] == 12


def test_build_levels_json_serializable():
    bars = pd.DataFrame({"high": [2001, 2002, 2003] * 10, "low": [1999, 2000, 2001] * 10, "volume": [100] * 30})
    tr = pd.DataFrame({"ts": pd.date_range("2026-10-01", periods=5, freq="1h", tz="UTC"), "price": [2000] * 5, "size": [1] * 5, "side": ["B"] * 5})
    out = orderflow.build_levels(bars, bars, tr, 2010.5, datetime(2026, 10, 1, tzinfo=timezone.utc))
    json.dumps(out)
    assert out["gc_last"] == 2010.5 and "poc" in out["composite"]


def test_knowledge_search(tmp_path):
    (tmp_path / "a.md").write_text("La regola dell'80% del value area: rientro nella value area e attraversamento verso il POC.", encoding="utf-8")
    (tmp_path / "b.md").write_text("Le banche centrali comprano oro e i rendimenti reali contano meno.", encoding="utf-8")
    kb = knowledge.KnowledgeBase(tmp_path)
    res = kb.search("value area POC rientro", 2)
    assert res and res[0][1] == "a.md"
    assert "banche centrali" in kb.context("banche centrali oro", 1)


def test_real_playbook_indexed():
    kb = knowledge.KnowledgeBase(Path(__file__).resolve().parent.parent / "knowledge")
    assert len(kb.chunks) > 5
    assert "assorbimento" in kb.context("assorbimento delta CVD", 3).lower()


def fake_transport(responses):
    def t(system, user, model, max_tokens):
        for key, val in responses.items():
            if key in system:
                return val if isinstance(val, str) else json.dumps(val)
        return "{}"
    return t


def test_prenews_window_and_research(tmp_path, monkeypatch):
    now = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)
    cal = [{"time_utc": "2026-10-02T12:30:00Z", "currency": "USD", "impact": "High", "title": "Non-Farm Employment Change", "tier": 1, "forecast": "120K", "previous": "90K"},
           {"time_utc": "2026-10-03T12:30:00Z", "currency": "USD", "impact": "High", "title": "CPI m/m", "tier": 1}]
    assert len(prenews.events_in_window(cal, now, 10)) == 1
    monkeypatch.setattr(forums, "news_previews", lambda t, extra="gold": [{"title": "NFP preview: economists see 120K"}])
    monkeypatch.setattr(forums, "reddit_search", lambda q, **k: [])
    llm.TRANSPORT = fake_transport({"pre-news": {"gold_direction": 1, "confidence": 0.65, "expected_surprise": "below", "continuation_plan": "rottura sopra PDH con volume"}})
    try:
        views = prenews.run(cal, now, tmp_path / "c.json", "macro neutra")
        assert len(views) == 1 and views[0]["gold_direction"] == 1 and views[0]["confidence"] == 0.65
        # seconda chiamata entro 2 ore: usa la cache (nessuna nuova ricerca)
        llm.TRANSPORT = fake_transport({"pre-news": {"gold_direction": -1, "confidence": 0.9}})
        views2 = prenews.run(cal, now + timedelta(minutes=30), tmp_path / "c.json", "macro neutra")
        assert views2[0]["gold_direction"] == 1
        # dopo l'evento la vista resta disponibile per la continuation (fino a 6 ore)
        views3 = prenews.run(cal, datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc), tmp_path / "c.json", "x")
        assert views3 and views3[0]["event"] == "Non-Farm Employment Change"
    finally:
        llm.TRANSPORT = None


def test_committee_debate_and_cio_guard():
    role_view = {"stance": "long", "conviction": 0.8, "playbook": "trend", "key_points": ["reali in calo"]}
    devil = {"stance": "short", "conviction": 0.4, "playbook": "mean_reversion", "key_points": ["esteso"]}
    llm.TRANSPORT = fake_transport({"Avvocato del diavolo": devil, "RUOLO": role_view,
                                    "CIO": {"stance": "long", "conviction": 0.95, "preferred_playbook": "trend", "summary_it": "ok", "dissent": "estensione"}})
    try:
        res = committee.run("briefing di prova", lambda q: "", rounds=2)
        assert res["stance"] == "long"
        assert res["conviction"] <= res["agreement"] + 1e-9      # guardia: niente sovra-convinzione
        assert set(res["votes"]) == set(committee.ROLES)
        md = committee.minutes_markdown(res)
        assert "Verbale comitato AI" in md
    finally:
        llm.TRANSPORT = None


def test_committee_cio_cannot_contradict_consensus():
    llm.TRANSPORT = fake_transport({"RUOLO": {"stance": "short", "conviction": 0.7, "playbook": "trend"},
                                    "CIO": {"stance": "long", "conviction": 0.9}})
    try:
        res = committee.run("b", None, rounds=1)
        assert res["stance"] == "flat"
    finally:
        llm.TRANSPORT = None


def test_committee_due_logic():
    now = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
    cfg = dict(service.DEFAULT_CONFIG)
    assert service.committee_due({}, now, cfg, [])
    st = {"last_committee": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert not service.committee_due(st, now, cfg, [])
    cal = [{"time_utc": "2026-10-02T12:30:00Z", "tier": 1, "title": "NFP"}]
    assert service.committee_due(st, now, cfg, cal)       # news tier1 in arrivo: riunione ogni ora


def test_book_imbalance():
    book = pd.DataFrame({**{f"bid_sz_{i:02d}": [30, 30] for i in range(10)}, **{f"ask_sz_{i:02d}": [10, 10] for i in range(10)}})
    assert orderflow.book_imbalance(book) == 0.5
