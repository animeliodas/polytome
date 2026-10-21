"""Выравнивание абзацев оригинала и готового перевода (Gale–Church по длинам).

Нужно, чтобы импортировать чужой перевод (Hermes, человек), где абзацы разбиты/склеены иначе.
Переходы: 1–1, 1–2, 1–3 (переводчик разбил абзац), 2–1 (склеил), 1–0 (пропуск), 0–1 (лишний абзац).
"""
from __future__ import annotations

import math
import re

_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _slen(s: str) -> float:
    h = len(_HAN.findall(s))
    return float(h if h else max(1, len(s) // 2))


def _tlen(s: str) -> float:
    return float(max(1, len(re.sub(r"\s", "", s))))


def align_beads(src: list[str], tgt: list[str]) -> list[tuple[int, int, int, int]]:
    """Выравнивание группами: [(i0, i1, j0, j1)] — абзацы оригинала src[i0:i1] переведены абзацами tgt[j0:j1].
    Группы идут подряд и покрывают оба списка целиком; j0 == j1 — у абзаца оригинала нет перевода."""
    n, m = len(src), len(tgt)
    if n == m:
        return [(i, i + 1, i, i + 1) for i in range(n)]
    sl = [_slen(s) for s in src]
    tl = [_tlen(t) for t in tgt]
    ratio = sum(tl) / max(1.0, sum(sl))
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    moves = [(1, 1, 0.0), (1, 2, 1.2), (1, 3, 2.5), (2, 1, 1.5), (1, 0, 6.0), (0, 1, 6.0)]

    def mcost(a: float, b: float) -> float:
        if a == 0 or b == 0:
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
    beads = []
    i, j = n, m
    while i > 0 or j > 0:
        di, dj = back[i][j]
        beads.append((i - di, i, j - dj, j))
        i, j = i - di, j - dj
    return beads[::-1]


def align(src: list[str], tgt: list[str]) -> list[str]:
    """→ список той же длины, что src: перевод для каждого абзаца оригинала ('' — склеен с предыдущим или нет
    перевода). Для чтения и оценки; для правки готового перевода — align_beads (абзацы не склеиваются)."""
    out = [""] * len(src)
    for i0, i1, j0, j1 in align_beads(src, tgt):
        text = " ".join(tgt[j0:j1])
        if i1 > i0:
            out[i0] = text
        elif out and text:  # 0–1: лишний абзац перевода — к предыдущему абзацу оригинала
            k = max(0, i0 - 1)
            out[k] = (out[k] + " " + text).strip()
    return out
