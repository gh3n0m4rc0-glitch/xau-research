"""Comitato AI multi-ruolo: il "consiglio d'investimento" del desk oro.

Ogni specialista (Claude con un ruolo diverso) riceve lo stesso briefing (snapshot del cBot, macro, news,
pre-news, order flow, playbook) e dà la sua vista. Poi c'è un secondo giro in cui ognuno legge gli altri e
deve rispondere all'obiezione più forte (può cambiare idea). Infine il CIO sintetizza.
Una guardia numerica impedisce al CIO di essere più convinto di quanto il comitato sia d'accordo.

Output: ai_committee.json (letto dal cBot come capo "AI") + committee_minutes.md (verbale leggibile).
"""
from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import llm

log = logging.getLogger(__name__)

COMMON = """Fai parte del comitato d'investimento di un desk che opera SOLO sull'oro (XAUUSD), orizzonte da poche ore a qualche giorno.
Il desk esegue con un cBot che ha già capi per strategia, conferma, liquidità, volumi, rischio: la tua vista è un input, non un ordine.
Ragiona sui dati del briefing, cita numeri e livelli concreti, non inventare dati che non vedi. Se i dati sono insufficienti dillo.
Rispondi SOLO con JSON valido:
{"stance": "long|short|flat", "conviction": 0..1, "playbook": "trend|mean_reversion|breakout|wait",
 "key_points": [max 4 stringhe], "levels": {"support": [numeri], "resistance": [numeri], "invalidation": numero o null},
 "risks": [max 3 stringhe]}"""

ROLES = {
    "macro": ("Macroeconomista", "Sei il macroeconomista: Fed e percorso dei tassi, rendimenti reali, dollaro, inflazione, banche centrali (acquisti di oro), geopolitica, flussi ETF. Ricorda che dal 2022 la correlazione oro/rendimenti reali si è indebolita per gli acquisti delle banche centrali: pesa i driver in base al regime attuale.",
              "macro Fed rendimenti reali dollaro banche centrali geopolitica"),
    "volume": ("Stratega volumetrico", "Sei lo stratega volumetrico: volume profile (POC, VAH, VAL, HVN, LVN), VWAP e bande, RVOL per ora del giorno, delta e CVD, divergenze, order flow dei futures CME (grandi ordini), letture Wyckoff/VSA (climax, assorbimento, no demand/no supply). Dove sono accettazione e rifiuto del prezzo?",
               "volume profile POC value area VWAP delta CVD assorbimento Wyckoff VSA"),
    "tecnico": ("Stratega tecnico e liquidità", "Sei lo stratega di struttura e liquidità: trend multi-timeframe, swing, pool di liquidità (massimi/minimi di giorno e settimana, equal highs/lows, range asiatico, numeri tondi), stop run, sessioni Londra/New York, estensione dalla media.",
                "liquidità stop run sessione Londra New York struttura trend range asiatico"),
    "news": ("Analista news e sentiment", "Sei l'analista news e sentiment: titoli recenti, viste pre-news sugli eventi in arrivo, forum, posizionamento della folla, cosa è già prezzato e dove può esserci sorpresa.",
             "news NFP CPI FOMC reazione oro sorpresa consenso sentiment"),
    "risk": ("Risk manager", "Sei il risk manager: posizioni aperte, drawdown, perdite consecutive, volatilità, rischio evento (news entro 24h), weekend, liquidità. Il tuo compito è dire quando NON è il momento (stance flat, playbook wait) e perché.",
             "rischio drawdown volatilità news weekend gestione"),
    "devil": ("Avvocato del diavolo", "Sei l'avvocato del diavolo: individua la direzione che il resto del comitato probabilmente sosterrà e costruisci il caso più forte possibile per l'opposto, con dati del briefing. Se il caso opposto è debole, ammettilo con conviction bassa.",
              "errori trappole falsi breakout inversione"),
}

SYSTEM_CIO = """Sei il CIO del desk oro. Hai le viste finali degli specialisti (dopo il confronto) e il briefing.
Decidi la vista del desk pesando la QUALITÀ delle argomentazioni (dati concreti > opinioni), non solo il numero di voti.
Regole: se gli specialisti sono molto divisi o c'è un evento ad alto impatto imminente, preferisci "stand_aside" o "flat".
La conviction non deve superare il grado di accordo reale. Spiega il dissenso principale.
Rispondi SOLO con JSON valido:
{"stance": "long|short|flat|stand_aside", "conviction": 0..1, "preferred_playbook": "trend|mean_reversion|breakout|wait",
 "key_levels": [numeri], "invalidation": "stringa", "summary_it": "max 3 frasi", "dissent": "stringa"}"""


def build_briefing(snapshot: dict | None, macro: dict, headlines: list[dict], calendar: list[dict], prenews: list[dict], orderflow: dict | None) -> str:
    parts = []
    if snapshot:
        parts.append("SNAPSHOT DEL cBot (prezzi spot XAUUSD):\n" + json.dumps(snapshot, ensure_ascii=False)[:6000])
    else:
        parts.append("SNAPSHOT DEL cBot: non disponibile (bot fermo o file vecchio).")
    m = {k: macro.get(k) for k in ("bias", "confidence", "regime", "summary", "components", "risk_flags", "halt_trading")}
    parts.append("MACRO:\n" + json.dumps(m, ensure_ascii=False))
    if orderflow:
        parts.append("ORDER FLOW FUTURES CME (prezzi futures, base rispetto allo spot indicata dal cBot):\n" + json.dumps(orderflow, ensure_ascii=False)[:2500])
    if prenews:
        parts.append("VISTE PRE-NEWS:\n" + json.dumps(prenews, ensure_ascii=False)[:2500])
    if calendar:
        parts.append("CALENDARIO 48h:\n" + "\n".join(f"- {e['time_utc']} T{e['tier']} {e['title']} (prev {e.get('previous', '')}, cons {e.get('forecast', '')})" for e in calendar[:15]))
    if headlines:
        parts.append("TITOLI RECENTI:\n" + "\n".join(f"- {h['title']}" for h in headlines[:30]))
    return "\n\n".join(parts)


def _norm_view(d: dict | None) -> dict | None:
    if not d:
        return None
    st = str(d.get("stance", "flat")).lower()
    d["stance"] = st if st in ("long", "short", "flat") else "flat"
    d["conviction"] = round(llm.clamp(d.get("conviction", 0), 0, 1), 2)
    d.setdefault("playbook", "wait")
    d.setdefault("key_points", [])
    d.setdefault("risks", [])
    return d


def numeric_consensus(views: dict[str, dict]) -> tuple[float, float]:
    """(direzione media pesata -1..1, accordo 0..1). L'avvocato del diavolo pesa la metà."""
    tot = num = 0.0
    signs = []
    for role, v in views.items():
        w = 0.5 if role == "devil" else 1.0
        s = {"long": 1, "short": -1}.get(v["stance"], 0)
        num += w * s * v["conviction"]
        tot += w
        signs.append(s)
    direction = num / tot if tot else 0.0
    nz = [s for s in signs if s != 0]
    agreement = abs(sum(nz)) / len(signs) if signs else 0.0
    return round(direction, 3), round(agreement, 3)


def run(briefing: str, knowledge_fn=None, rounds: int = 2, model: str | None = None) -> dict | None:
    if not llm.available():
        log.info("Comitato AI disattivato (nessuna ANTHROPIC_API_KEY)")
        return None

    def ask_role(role: str, extra: str = "") -> tuple[str, dict | None]:
        name, brief, kq = ROLES[role]
        kb = knowledge_fn(kq) if knowledge_fn else ""
        system = f"{COMMON}\n\nRUOLO: {name}. {brief}"
        user = f"{briefing}\n\nPLAYBOOK DEL DESK (estratti pertinenti):\n{kb[:3000]}{extra}"
        return role, _norm_view(llm.ask_json(system, user, model, 900))

    with ThreadPoolExecutor(max_workers=len(ROLES)) as ex:
        r1 = dict(ex.map(lambda r: ask_role(r), ROLES))
    views = {k: v for k, v in r1.items() if v}
    if len(views) < 3:
        log.warning("Comitato AI: troppe risposte mancanti (%d)", len(views))
        return None

    if rounds >= 2:
        def table(vs):
            return "\n".join(f"- {ROLES[k][0]}: {v['stance']} (conv {v['conviction']}), playbook {v['playbook']}: " + "; ".join(v["key_points"][:3]) for k, v in vs.items())
        extra = ("\n\nCONFRONTO - viste degli altri specialisti al primo giro:\n" + table(views) +
                 "\n\nRispondi all'obiezione più forte contro la tua vista. Puoi cambiare idea se i dati degli altri sono migliori. "
                 "Aggiungi al JSON i campi \"changed_mind\": true/false e \"rebuttal\": \"stringa\".")
        with ThreadPoolExecutor(max_workers=len(views)) as ex:
            r2 = dict(ex.map(lambda r: ask_role(r, extra), list(views)))
        for k, v in r2.items():
            if v:
                views[k] = v

    direction, agreement = numeric_consensus(views)
    cio_user = (briefing[:5000] + "\n\nVISTE FINALI DEGLI SPECIALISTI:\n" + json.dumps(views, ensure_ascii=False)[:7000] +
                f"\n\nCONSENSO NUMERICO: direzione {direction:+.2f}, accordo {agreement:.2f}")
    cio = llm.ask_json(SYSTEM_CIO, cio_user, model, 900) or {}
    stance = str(cio.get("stance", "flat")).lower()
    if stance not in ("long", "short", "flat", "stand_aside"):
        stance = "flat"
    conviction = llm.clamp(cio.get("conviction", 0), 0, 1)
    # guardia: il CIO non può essere più convinto dell'accordo reale, né andare contro un consenso netto
    conviction = min(conviction, max(agreement, 0.2))
    if stance in ("long", "short") and direction * (1 if stance == "long" else -1) < -0.15:
        stance, conviction = "flat", min(conviction, 0.3)

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stance": stance,
        "conviction": math.floor(conviction * 100) / 100,
        "preferred_playbook": cio.get("preferred_playbook", "wait"),
        "key_levels": cio.get("key_levels", []),
        "invalidation": cio.get("invalidation", ""),
        "summary_it": cio.get("summary_it", ""),
        "dissent": cio.get("dissent", ""),
        "consensus_direction": direction,
        "agreement": agreement,
        "votes": {k: {"stance": v["stance"], "conviction": v["conviction"], "playbook": v["playbook"]} for k, v in views.items()},
        "views": views,
    }


def minutes_markdown(res: dict) -> str:
    lines = [f"# Verbale comitato AI — {res['timestamp_utc']}", "",
             f"**Decisione CIO:** {res['stance']} (convinzione {res['conviction']}) · playbook {res['preferred_playbook']}",
             f"**Consenso numerico:** direzione {res['consensus_direction']:+.2f}, accordo {res['agreement']:.2f}", "",
             res.get("summary_it", ""), "", f"**Dissenso:** {res.get('dissent', '')}", f"**Invalidazione:** {res.get('invalidation', '')}", ""]
    for k, v in res.get("views", {}).items():
        lines.append(f"## {ROLES[k][0]} — {v['stance']} ({v['conviction']})")
        lines += [f"- {p}" for p in v.get("key_points", [])]
        if v.get("rebuttal"):
            lines.append(f"- *Replica:* {v['rebuttal']}" + (" (ha cambiato idea)" if v.get("changed_mind") else ""))
        if v.get("risks"):
            lines.append("- Rischi: " + "; ".join(v["risks"]))
        lines.append("")
    return "\n".join(lines)
