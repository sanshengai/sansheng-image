"""Coding Plan 固定套餐请求；失败不转其它后端。

本模块还放三个复核后端（Coding Plan / Agent Plan / claude_cli）共用的错误分类函数，
让同一类故障在三条路径上得到同一类异常、同一种指引。
"""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.request

MODEL = "deepseek-v4.1-flash"
BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
SETUP_HINT = "配置方式见 references/backends.md「独立复核后端」；密钥写进 ~/.config/sansheng-image/.env 或环境变量，不要发到对话里"


def _exc_types():
    from .reviewer import QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable
    return QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable


SECRET_ENV = ("ARK_CODING_PLAN_API_KEY", "ARK_AGENT_PLAN_API_KEY")


def snippet(text, limit: int = 200) -> str:
    """响应/输出片段：压成一行、遮掉密钥并截断，只用于诊断信息（服务端偶尔会回显请求头）。"""
    text = str(text or "")
    for name in SECRET_ENV:
        secret = os.environ.get(name, "")
        if len(secret) >= 8:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}", r"\1***", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def classify_http_error(exc: urllib.error.HTTPError, label: str, *, stop_note: str = ""):
    """HTTPError -> 统一异常（返回而不是抛，调用处 `raise ... from exc`）。"""
    quota, auth, _network = _exc_types()
    try:
        body = snippet(exc.read().decode("utf-8", errors="replace")) if hasattr(exc, "read") and exc.fp else ""
    except Exception:  # noqa: BLE001 - 读响应体失败不能掩盖原始 HTTP 错误
        body = ""
    tail = f"{stop_note}" if stop_note else ""
    if exc.code == 429:
        return quota(f"{label} 额度用尽或限流（HTTP 429）{tail}" + (f"：{body}" if body else ""))
    if exc.code in (401, 403):
        return auth(f"{label} 鉴权或套餐访问被拒（HTTP {exc.code}）：检查 Key 是否有效、是否开通了对应套餐{tail}。{SETUP_HINT}"
                    + (f"。服务返回：{body}" if body else ""))
    return RuntimeError(f"{label} HTTP {exc.code}" + (f"：{body}" if body else "（无响应内容）"))


def classify_transport_error(exc: BaseException, label: str, timeout: float | int | None, *, stop_note: str = ""):
    """超时/连接类故障 -> ReviewerNetworkUnavailable（超时单独成句，便于用户调大 timeout）。"""
    _quota, _auth, network = _exc_types()
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return network(f"{label} 请求超时（{timeout}s 内没有响应）{stop_note}；稍后重试，或换时延更低的网络")
    return network(f"{label} 网络不可用：{snippet(reason)}{stop_note}")


def request(content: list[dict], *, model: str | None, timeout: int,
            budget: int = 8000, temperature: float = 0,
            length_hint: str = "") -> tuple[str, dict]:
    quota, auth, network = _exc_types()
    base = os.environ.get("ARK_CODING_PLAN_BASE_URL", "").rstrip("/")
    key = os.environ.get("ARK_CODING_PLAN_API_KEY", "")
    if not key or base != BASE_URL:
        missing = []
        if not key:
            missing.append("ARK_CODING_PLAN_API_KEY")
        if base != BASE_URL:
            missing.append(f"ARK_CODING_PLAN_BASE_URL（必须是 {BASE_URL}）")
        raise auth("Coding Plan 复核缺少或配错：" + "、".join(missing) + "。" + SETUP_HINT
                   + "；没有火山方舟 Coding Plan 时，可改用 ark_agent_plan 或 claude_cli 后端（见同一份文档）。停止，不切其它通道")
    if model not in (None, MODEL):
        raise ValueError(f"Coding Plan 本入口只允许 {MODEL}")
    payload = {"model": MODEL, "thinking": {"type": "disabled"}, "temperature": temperature,
               "max_tokens": budget, "messages": [{"role": "user", "content": content}]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", body,
                                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as handle:
            response = json.loads(handle.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise classify_http_error(exc, "Coding Plan", stop_note="；停止，不切其它通道") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        raise classify_transport_error(exc, "Coding Plan", timeout, stop_note="；停止，不切其它通道") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Coding Plan 返回的不是 JSON：{exc}") from exc
    normalize = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    returned = str(response.get("model") or "")
    if not normalize(returned).startswith(normalize(MODEL)):
        raise RuntimeError(f"Coding Plan 返回型号不符：{returned}")
    choices = response.get("choices") or []
    choice = choices[0] if choices else {}
    answer = (choice.get("message") or {}).get("content")
    finish = choice.get("finish_reason")
    if finish == "length":
        raise RuntimeError(f"Coding Plan 输出在 max_tokens={budget} 处被截断，没有说完"
                           + (f"；{length_hint}" if length_hint else "；调大输出预算后重试"))
    if finish != "stop" or not isinstance(answer, str) or not answer.strip():
        raise RuntimeError(f"Coding Plan 响应为空或未完整结束（finish_reason={finish!r}）")
    return answer, response
