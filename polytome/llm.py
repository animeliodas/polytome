"""Клиент OpenAI-совместимых чат-API (Ollama local/cloud, OpenRouter, LM Studio, vLLM, DeepSeek…).

Один глобальный пул слотов на все запросы (агент + движок), ретраи на 429/5xx/таймауты,
учёт токенов по ролям.
"""
from __future__ import annotations

import json
import random
import socket
import threading
import time
from dataclasses import dataclass, field

import httpx

from .i18n import _
from .config import ModelRef


class LLMError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning: str = ""
    model: str = ""
    seconds: float = 0.0
    raw_message: dict = field(default_factory=dict)


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_role: dict[str, dict] = {}

    def add(self, role: str, res: ChatResult):
        with self.lock:
            r = self.by_role.setdefault(role, {"calls": 0, "prompt": 0, "completion": 0, "seconds": 0.0})
            r["calls"] += 1
            r["prompt"] += res.prompt_tokens
            r["completion"] += res.completion_tokens
            r["seconds"] += res.seconds

    def total(self) -> int:
        with self.lock:
            return sum(r["prompt"] + r["completion"] for r in self.by_role.values())

    def summary(self) -> str:
        with self.lock:
            parts = [_("{0}: {1} выз., {2:,} ток.").format(k, v['calls'], v['prompt'] + v['completion'])
                     for k, v in self.by_role.items()]
        return "; ".join(parts) or _("нет вызовов")


class AdaptivePool:
    """Глобальный лимит параллельности с AIMD: +1 после серии успехов, ÷2 при 429."""

    def __init__(self, start: int, maximum: int):
        self.max = max(1, maximum)
        self.limit = max(1, min(start, self.max))
        self.active = 0
        self.streak = 0
        self.cv = threading.Condition()

    def acquire(self):
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait()
            self.active += 1

    def release(self, ok: bool, rate_limited: bool = False):
        with self.cv:
            self.active -= 1
            if rate_limited:
                self.limit = max(1, self.limit // 2)
                self.streak = 0
            elif ok:
                self.streak += 1
                if self.streak >= 6 and self.limit < self.max:
                    self.limit += 1
                    self.streak = 0
            self.cv.notify_all()


def keepalive_options() -> list[tuple[int, int, int]]:
    """TCP keepalive для соединений с провайдером. Ответ без стриминга приходит целиком, когда модель допишет главу,
    а до тех пор соединение молчит минутами. Мобильные операторы такие соединения тихо обрывают (NAT забывает
    их через несколько минут), и запрос висел до таймаута в 10 минут, потом снова — так одна глава могла стоять
    часами. Пробы каждые 10 с после 20 с тишины держат соединение живым, а мёртвое замечается примерно за минуту."""
    opts = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)  # macOS: TCP_KEEPALIVE
    for opt, value in ((idle, 20), (getattr(socket, "TCP_KEEPINTVL", None), 10), (getattr(socket, "TCP_KEEPCNT", None), 4)):
        if opt is not None:
            opts.append((socket.IPPROTO_TCP, opt, value))
    return opts


class LLMClient:
    READ_TIMEOUT_ATTEMPTS = 2  # модель молчала все 10 минут — ещё одна попытка, не шесть (не держать задачу час)
    # Провайдер недоступен (нет сети, сервер лежит): один запрос исчерпал все попытки на ошибках соединения — дальше
    # каждый запрос к нему пробует один раз, пока какой-нибудь не пройдёт. Иначе без сети каждый абзац сжигал бы
    # полный цикл повторов, и глава «падала» бы десятки минут вместо пары.
    DOWN_FOR_S = 60

    def __init__(self, parallel: int = 8):
        limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
        self.http = httpx.Client(timeout=httpx.Timeout(600, connect=30),
                                 transport=httpx.HTTPTransport(limits=limits, socket_options=keepalive_options()))
        self.pool = AdaptivePool(start=max(2, parallel // 2), maximum=parallel)
        self.usage = Usage()
        self.max_retries = 6
        self.down_until: dict[str, float] = {}  # base_url → до какого времени провайдер считается недоступным

    def chat(self, ref: ModelRef, messages: list[dict], *, tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False, role: str = "misc", use_pool: bool = True) -> ChatResult:
        body: dict = {"model": ref.model, "messages": messages, "stream": False}
        t = ref.temperature if ref.temperature is not None else temperature
        if t is not None:
            body["temperature"] = t
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(ref.extra or {})
        headers = {"Content-Type": "application/json"}
        if ref.api_key:
            headers["Authorization"] = f"Bearer {ref.api_key}"
        url = f"{ref.base_url}/chat/completions"

        last_err: Exception | None = None
        read_timeouts = 0
        tries = 1 if time.time() < self.down_until.get(ref.base_url, 0) else self.max_retries
        for attempt in range(1, tries + 1):
            if use_pool:
                self.pool.acquire()
            ok = rate_limited = False
            t0 = time.time()
            try:
                r = self.http.post(url, headers=headers, json=body)
                if r.status_code in (429, 500, 502, 503, 504, 529):
                    rate_limited = r.status_code in (429, 503, 529)
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}", r.status_code)
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}", r.status_code)
                data = r.json()
                res = self._parse(data, ref)
                res.seconds = time.time() - t0
                ok = True
                self.down_until.pop(ref.base_url, None)  # провайдер снова отвечает
                tries = self.max_retries
                self.usage.add(role, res)
                # Рассуждающая модель съела весь лимит на «мысли» и не ответила — повтор с low и бо́льшим лимитом.
                if (not res.content.strip() and not res.tool_calls and res.finish_reason == "length"
                        and body.get("reasoning_effort") not in ("low", "none") and attempt < self.max_retries):
                    body["reasoning_effort"] = "low"
                    if body.get("max_tokens"):
                        body["max_tokens"] *= 2
                    continue
                return res
            except LLMError as e:
                last_err = e
                if e.status and 400 <= e.status < 500 and e.status not in (408, 429):
                    raise  # ошибка запроса — повтор не поможет
            except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError) as e:
                last_err = e
                read_timeouts += isinstance(e, httpx.ReadTimeout)
            finally:
                if use_pool:
                    self.pool.release(ok, rate_limited)
            if read_timeouts >= self.READ_TIMEOUT_ATTEMPTS or attempt == tries:
                break  # после последней попытки не ждать минуту впустую
            delay = min(60.0, (2 ** attempt) + random.random() * 2)
            time.sleep(delay)
        if isinstance(last_err, httpx.TransportError):  # соединение, а не ответ сервера — провайдер недоступен
            self.down_until[ref.base_url] = time.time() + self.DOWN_FOR_S
        raise LLMError(_("{0}: не удалось после {1} попыток: {2}").format(ref.spec, attempt, last_err))

    @staticmethod
    def _parse(data: dict, ref: ModelRef) -> ChatResult:
        if "choices" not in data or not data["choices"]:
            raise LLMError(_("пустой ответ: {0}").format(str(data)[:300]))
        ch = data["choices"][0]
        msg = ch.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            if isinstance(raw, dict):
                args, raw_s = raw, json.dumps(raw, ensure_ascii=False)
            else:
                raw_s = raw
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {"__invalid_json__": raw}
            calls.append(ToolCall(tc.get("id") or f"call_{i}", fn.get("name", ""), args, raw_s))
        usage = data.get("usage") or {}
        return ChatResult(
            content=msg.get("content") or "",
            tool_calls=calls,
            finish_reason=ch.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning=msg.get("reasoning") or msg.get("reasoning_content") or "",
            model=ref.spec,
            raw_message=msg,
        )

    def list_models(self, ref: ModelRef) -> list[str]:
        return [m["id"] for m in self.list_models_info(ref)]

    def list_models_info(self, ref: ModelRef) -> list[dict]:
        """Модели провайдера из его /models: id, название, контекст и цена за 1 млн токенов, если провайдер их
        сообщает (OpenRouter — да, большинство — только id). Провайдеры меняют список — он всегда свежий."""
        headers = {"Authorization": f"Bearer {ref.api_key}"} if ref.api_key else {}
        r = self.http.get(f"{ref.base_url}/models", headers=headers, timeout=30)
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            item = {"id": m["id"], "name": m.get("name") or m["id"]}
            ctx = m.get("context_length") or (m.get("top_provider") or {}).get("context_length")
            if ctx:
                item["context"] = int(ctx)
            pricing = m.get("pricing") or {}
            try:
                if "prompt" in pricing:
                    item["price_in"] = round(float(pricing["prompt"]) * 1_000_000, 4)
                if "completion" in pricing:
                    item["price_out"] = round(float(pricing["completion"]) * 1_000_000, 4)
            except (TypeError, ValueError):
                pass
            if item.get("price_in") == 0 and item.get("price_out") == 0:
                item["free"] = True
            out.append(item)
        return sorted(out, key=lambda x: x["id"])
