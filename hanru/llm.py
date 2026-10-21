"""Клиент OpenAI-совместимых чат-API (Ollama local/cloud, OpenRouter, LM Studio, vLLM, DeepSeek…).

Один глобальный пул слотов на все запросы (агент + движок), ретраи на 429/5xx/таймауты,
учёт токенов по ролям.
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field

import httpx

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
            parts = [f"{k}: {v['calls']} выз., {v['prompt'] + v['completion']:,} ток."
                     for k, v in self.by_role.items()]
        return "; ".join(parts) or "нет вызовов"


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


class LLMClient:
    def __init__(self, parallel: int = 8):
        self.http = httpx.Client(timeout=httpx.Timeout(600, connect=30),
                                 limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
        self.pool = AdaptivePool(start=max(2, parallel // 2), maximum=parallel)
        self.usage = Usage()
        self.max_retries = 6

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
        for attempt in range(1, self.max_retries + 1):
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
            finally:
                if use_pool:
                    self.pool.release(ok, rate_limited)
            delay = min(60.0, (2 ** attempt) + random.random() * 2)
            time.sleep(delay)
        raise LLMError(f"{ref.spec}: не удалось после {self.max_retries} попыток: {last_err}")

    @staticmethod
    def _parse(data: dict, ref: ModelRef) -> ChatResult:
        if "choices" not in data or not data["choices"]:
            raise LLMError(f"пустой ответ: {str(data)[:300]}")
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
        headers = {"Authorization": f"Bearer {ref.api_key}"} if ref.api_key else {}
        r = self.http.get(f"{ref.base_url}/models", headers=headers, timeout=30)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))
