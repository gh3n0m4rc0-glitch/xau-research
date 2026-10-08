"""Memoria di ricerca del desk: indice locale (BM25) dei documenti di analisi.

Metti nella cartella knowledge/ tutto ciò che vuoi che gli analisti AI "studino": il playbook del desk,
report di banche, libri/estratti su Wyckoff, volume profile, order flow, note tue, PDF (con pypdf installato).
Ad ogni analisi vengono recuperati i passaggi più pertinenti alla situazione corrente e passati a Claude.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)
TOKEN = re.compile(r"[a-zàèéìòù0-9]+", re.I)
STOP = set("il lo la i gli le un una di a da in con su per tra fra e o che non è the a an of to in on for and or is are be with as at by".split())


def _tok(text: str) -> list[str]:
    return [t for t in (w.lower() for w in TOKEN.findall(text)) if t not in STOP and len(t) > 1]


def _read(path: Path) -> str:
    if path.suffix.lower() in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception as e:
            log.warning("PDF %s non letto (installa pypdf): %s", path.name, e)
    return ""


class KnowledgeBase:
    def __init__(self, folder: str | Path, chunk_words: int = 180):
        self.chunks: list[tuple[str, str]] = []   # (fonte, testo)
        folder = Path(folder)
        if folder.exists():
            for p in sorted(folder.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".md", ".txt", ".pdf"):
                    words = _read(p).split()
                    for i in range(0, len(words), chunk_words):
                        self.chunks.append((p.name, " ".join(words[i:i + chunk_words + 30])))
        self.docs = [_tok(t) for _, t in self.chunks]
        self.df = Counter(w for d in self.docs for w in set(d))
        self.avgdl = sum(len(d) for d in self.docs) / max(1, len(self.docs))
        log.info("Knowledge base: %d passaggi da %s", len(self.chunks), folder)

    def search(self, query: str, k: int = 6) -> list[tuple[float, str, str]]:
        q = _tok(query)
        n = len(self.docs)
        if not q or not n:
            return []
        scores = []
        for i, d in enumerate(self.docs):
            tf = Counter(d)
            s = 0.0
            for w in q:
                if w not in tf:
                    continue
                idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                s += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(d) / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [(round(s, 2), self.chunks[i][0], self.chunks[i][1]) for s, i in scores[:k]]

    def context(self, query: str, k: int = 6, max_chars: int = 4000) -> str:
        out, size = [], 0
        for s, src, txt in self.search(query, k):
            piece = f"[{src}] {txt}"
            if size + len(piece) > max_chars:
                break
            out.append(piece)
            size += len(piece)
        return "\n---\n".join(out)
