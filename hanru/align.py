"""Выравнивание абзацев оригинала и готового перевода (Gale–Church по длинам).

Нужно, чтобы импортировать чужой перевод (Hermes, человек), где абзацы разбиты/склеены иначе.
Переходы: 1–1, 1–2, 1–3 (переводчик разбил абзац), 2–1 (склеил), 1–0 (пропуск).
"""
from __future__ import annotations

import math
import re

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _slen(s: str) -> float:
    h = len(_HAN.findall(s))
    return float(h if h else max(1, len(s) // 2))


def _tlen(s: str) -> float:
    return float(max(1, len(re.sub(r"\s", "", s))))


def align(src: list[str], tgt: list[str]) -> list[str]:
    """→ список той же длины, что src: перевод для каждого абзаца оригинала ('' если склеен с предыдущим)."""
    if len(src) == len(tgt):
        return list(tgt)
    n, m = len(src), len(tgt)
    sl = [_slen(s) for s in src]
    tl = [_tlen(t) for t in tgt]
    ratio = sum(tl) / max(1.0, sum(sl))
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    moves = [(1, 1, 0.0), (1, 2, 1.2), (1, 3, 2.5), (2, 1, 1.5), (1, 0, 6.0)]

    def mcost(a: float, b: float) -> float:
        if b == 0:
            return 0.0
        return abs(math.log((b + 5) / (a * ratio + 5))) * 3

    for i in range(n + 1):
        for j in range(m + 1):
            c0 = cost[i][j]
            if c0 == INF:
                continue
            for di, dj, pen in moves:
                ni, nj = i + di, j + dj
                if ni > n or nj > m:
                    continue
                c = c0 + pen + mcost(sum(sl[i:ni]), sum(tl[j:nj]))
                if c < cost[ni][nj]:
                    cost[ni][nj] = c
                    back[ni][nj] = (di, dj)
    out = [""] * n
    i, j = n, m
    while i > 0 or j > 0:
        mv = back[i][j]
        if mv is None:
            break
        di, dj = mv
        text = " ".join(tgt[j - dj:j])
        if di == 1:
            out[i - 1] = text
        else:  # 2–1: склеенный перевод относим к первому абзацу
            out[i - 2], out[i - 1] = text, ""
        i, j = i - di, j - dj
    return out
