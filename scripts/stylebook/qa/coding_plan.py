"""Coding Plan 固定套餐请求；失败不转其它后端。"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

MODEL = "deepseek-v4.1-flash"
BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"


def request(content: list[dict], *, model: str | None, timeout: int,
            budget: int = 8000) -> tuple[str, dict]:
    from .reviewer import QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable
    base = os.environ.get("ARK_CODING_PLAN_BASE_URL", "").rstrip("/")
    key = os.environ.get("ARK_CODING_PLAN_API_KEY", "")
    if base != BASE_URL or not key:
        raise ReviewerAuthUnavailable("Coding Plan 套餐地址或 Key 不可用；停止，不切其它通道")
    if model not in (None, MODEL):
        raise ValueError(f"Coding Plan 本入口只允许 {MODEL}")
    payload = {"model": MODEL, "thinking": {"type": "disabled"},
               "max_tokens": budget, "messages": [{"role": "user", "content": content}]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", body,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as handle:
            response = json.loads(handle.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise QuotaExhausted("Coding Plan 额度用尽或限流；停止，不切其它通道") from exc
        if exc.code in (401, 403):
            raise ReviewerAuthUnavailable("Coding Plan 鉴权被拒；停止，不切其它通道") from exc
        raise RuntimeError(f"Coding Plan HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ReviewerNetworkUnavailable("Coding Plan 网络不可用；停止，不切其它通道") from exc
    normalize = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    returned = str(response.get("model") or "")
    if not normalize(returned).startswith(normalize(MODEL)):
        raise RuntimeError(f"Coding Plan 返回型号不符：{returned}")
    choices = response.get("choices") or []
    choice = choices[0] if choices else {}
    answer = (choice.get("message") or {}).get("content")
    if choice.get("finish_reason") != "stop" or not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("Coding Plan 响应为空或未完整结束")
    return answer, response
