"""Sentiment da forum/community per la ricerca pre-news.

- Reddit: dal 2026 gli endpoint .json anonimi rispondono 403. Serve un'app Reddit gratuita (tipo "script")
  per uso non commerciale: REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (OAuth "client credentials").
- Google News RSS: anteprime/preview degli analisti sull'evento (nessuna chiave).
Tutto è facoltativo e "fail-safe": se una fonte non risponde, la ricerca prosegue con le altre.
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote_plus

import requests

from . import sources

log = logging.getLogger(__name__)
UA = "xau-committee-bot/2.0 (by u/your_username)"
SUBREDDITS = ["Gold", "Forex", "wallstreetbets", "economy", "investing", "Daytrading"]
_token_cache: dict = {}


def _reddit_token() -> str | None:
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    if _token_cache.get("exp", 0) > time.time() + 60:
        return _token_cache["tok"]
    try:
        r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                          data={"grant_type": "client_credentials"}, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        j = r.json()
        _token_cache.update(tok=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
        return _token_cache["tok"]
    except Exception as e:
        log.warning("Reddit OAuth fallito: %s", e)
        return None


def reddit_search(query: str, limit: int = 12, hours: int = 48) -> list[dict]:
    tok = _reddit_token()
    if not tok:
        return []
    out = []
    t = "day" if hours <= 24 else "week"
    for sub in SUBREDDITS:
        try:
            r = requests.get(f"https://oauth.reddit.com/r/{sub}/search", headers={"Authorization": f"bearer {tok}", "User-Agent": UA},
                             params={"q": query, "restrict_sr": 1, "sort": "new", "t": t, "limit": limit}, timeout=20)
            r.raise_for_status()
            for ch in r.json().get("data", {}).get("children", []):
                d = ch.get("data", {})
                out.append({"source": f"r/{sub}", "title": d.get("title", ""), "text": (d.get("selftext") or "")[:400],
                            "score": d.get("score", 0), "comments": d.get("num_comments", 0), "created_utc": d.get("created_utc", 0)})
        except Exception as e:
            log.info("Reddit r/%s: %s", sub, e)
    return sorted(out, key=lambda x: -(x["score"] + 2 * x["comments"]))[:40]


def news_previews(event_title: str, extra: str = "gold") -> list[dict]:
    q = quote_plus(f'"{event_title}" (preview OR forecast OR expectations OR consensus) {extra}')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return sources.fetch_headlines([url], max_per_feed=30)
