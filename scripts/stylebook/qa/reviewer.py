"""独立看图默认 Coding Plan deepseek-v4.1-flash；发送实际图片，失败停止。

SANSHENG_IMAGE_QA_BACKEND 可显式选择既有适配器；不会自动回退。
本项目执行仅用 ark_coding_plan，旧 Claude/Agent Plan 保留历史兼容。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import active_items, missing_items, review_prompt, review_schema

DEFAULT_MODEL = os.environ.get("SANSHENG_IMAGE_QA_MODEL", "claude-sonnet-5")
ARK_MODEL = "doubao-seed-2.1-turbo"
ARK_PLAN_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
INDEPENDENT_SOURCES = frozenset({"claude_cli", "ark_agent_plan", "ark_coding_plan"})


class QuotaExhausted(RuntimeError):
    """看图用的 Claude 额度用尽或限流：整轮停下，不逐张空转。"""


class ReviewerAuthUnavailable(RuntimeError):
    """独立看图进程没有可用登录态或订阅访问权：整轮停下，保留已有结果。"""


class ReviewerNetworkUnavailable(RuntimeError):
    """独立看图请求无法连接：整轮停下，保留已有结果。"""


class ReviewerIncompleteResult(RuntimeError):
    """保留漏答的原始回答，交消费者记录失败，不自动再请求。"""

    def __init__(self, data: dict | None, response: dict, gaps: list[str]):
        super().__init__("Coding Plan 看图缺少完整逐项结论；保留图片，不自动重试")
        self.review = dict(data or {})
        self.review["_reviewer"] = f"ark_coding_plan:{response['model']}"
        self.review["_response_metadata"] = response
        self.review["_incomplete_review"] = gaps


def _bind_clause_ids(data: dict | None, contract: dict, text_mode: str) -> tuple[dict | None, list[str]]:
    """仅按本次完整合同的明确标识绑定；不模糊匹配、不猜漏项。"""
    if data is None:
        return None, []
    bound, errors = dict(data), []
    mapping = {}
    for key, prefix, clauses in zip(("must_see", "must_not_see"), ("M", "B"),
                                    active_items(contract, text_mode)):
        lookup = {f"{prefix}{i}": label for i, label in enumerate(clauses, 1)}
        mapping.update(lookup)
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        seen, normalized = set(), []
        for row in rows:
            entry = dict(row) if isinstance(row, dict) else row
            if isinstance(entry, dict) and "id" in entry:
                ident = entry["id"]
                label = lookup.get(ident) if isinstance(ident, str) else None
                if label is None or ident in seen:
                    errors.append(f"{key}：未知或重复条款标识 {ident!r}")
                elif "item" in entry and entry["item"] != label:
                    errors.append(f"{key}：条款标识与原文冲突 {ident}")
                else:
                    entry["item"] = label
                if isinstance(ident, str):
                    seen.add(ident)
            normalized.append(entry)
        bound[key] = normalized
    bound["_criteria_id_map"] = mapping
    return bound, errors


def _quota_hit(text: str) -> bool:
    return bool(re.search(r'"api_error_status"\s*:\s*429|usage limit|rate[_ ]limit', text or "", re.I))


def _auth_hit(text: str) -> bool:
    return bool(re.search(r"not logged in|please run /login|organization has disabled Claude subscription access for Claude Code",
                          text or "", re.I))


def _claude_bin() -> str | None:
    env = os.environ.get("SANSHENG_IMAGE_QA_CLAUDE")
    if env:
        return env
    home = Path.home() / ".local" / "bin" / "claude"
    return str(home) if home.exists() else shutil.which("claude")


def _extract(text: str) -> dict | None:
    text = (text or "").strip()
    try:
        env = json.loads(text)
        if isinstance(env, dict):
            if isinstance(env.get("structured_output"), dict):
                return env["structured_output"]
            inner = env.get("result")
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, str):
                text = inner
            elif "must_see" in env:
                return env
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def source() -> str:
    backend = os.environ.get("SANSHENG_IMAGE_QA_BACKEND", "ark_coding_plan")
    if backend not in INDEPENDENT_SOURCES:
        raise ValueError(f"未知看图后端：{backend}")
    return backend


def _ark_text(response: dict) -> str:
    return "".join(
        content["text"] for item in response.get("output", []) if isinstance(item, dict)
        for content in item.get("content", []) if isinstance(content, dict)
        and content.get("type") in ("output_text", "text") and isinstance(content.get("text"), str)
    ).strip()


def _image_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"Ark Agent Plan 看图不支持图片格式：{path}")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _review_ark(image: Path, contract: dict, expected_text: list[str] | None, text_mode: str,
                model: str | None, timeout: int, tries: int) -> dict:
    base = os.environ.get("ARK_AGENT_PLAN_BASE_URL", "").rstrip("/")
    key = os.environ.get("ARK_AGENT_PLAN_API_KEY", "")
    if base != ARK_PLAN_URL or not key:
        raise ReviewerAuthUnavailable("独立看图只使用已配置的 Ark Agent Plan 套餐地址与 Key，不切到按量平台")
    name = model or ARK_MODEL
    content = [{"type": "input_text", "text": review_prompt(contract, expected_text, text_mode)},
               {"type": "input_text", "text": "待验收的大图："},
               {"type": "input_image", "image_url": _image_uri(image)}]
    thumb = contract.get("_thumbnail_expectation")
    if thumb:
        content.extend([{"type": "input_text", "text": "真实展示尺寸预览：只依据下一张小图判断 thumbnail_readable。"},
                        {"type": "input_image", "image_url": _image_uri(Path(thumb))}])
    square = contract.get("_square_crop_expectation")
    if square:
        content.extend([{"type": "input_text", "text": "最终导出图的实际居中方形裁切：只依据下一张方形图判断 square_crop。"},
                        {"type": "input_image", "image_url": _image_uri(Path(square))}])
    prompt = review_prompt(contract, expected_text, text_mode)
    ask, best, last = prompt, None, ""
    for _ in range(tries):
        content[0]["text"] = ask
        body = json.dumps({
            "model": name,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": 8000,
            **({"reasoning": {"effort": "low"}} if name.startswith("doubao-seed-") else {}),
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(base + "/responses", body,
                                     {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as handle:
                response = json.loads(handle.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300].replace(key, "[REDACTED]")
            if exc.code == 429:
                raise QuotaExhausted("Ark Agent Plan 看图额度用尽或限流，本轮停止复核") from exc
            if exc.code in (401, 403):
                raise ReviewerAuthUnavailable("Ark Agent Plan 看图鉴权或套餐访问被拒，本轮停止复核") from exc
            raise RuntimeError(f"Ark Agent Plan 看图 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ReviewerNetworkUnavailable(f"Ark Agent Plan 看图网络不可用，本轮停止复核：{exc.reason}") from exc
        if response.get("status") != "completed":
            raise RuntimeError(f"Ark Agent Plan 看图未完成：{response.get('status')}")
        returned = str(response.get("model") or "")
        normalize = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
        if not normalize(returned).startswith(normalize(name)):
            raise RuntimeError(f"Ark Agent Plan 看图模型漂移：{returned}")
        answer = _ark_text(response)
        data = _extract(answer)
        if data and isinstance(data.get("must_see"), list):
            data["_reviewer"] = returned + " via Ark Agent Plan"
            miss = missing_items(data, contract, text_mode)
            if not miss:
                return data
            best, last = data, f"漏答 {len(miss)} 条"
            ask = prompt + "\n\n上一次漏答了以下条目。逐条照抄 item 原文并重新判断：\n" + "\n".join(f"- {x}" for x in miss)
        else:
            last = answer[:300] or "空响应"
    if best is not None:
        return best
    raise RuntimeError(f"Ark Agent Plan 看图没有给出可用结论：{last}")


def review(image: Path, contract: dict, expected_text: list[str] | None = None, text_mode: str = "none",
           model: str | None = None, timeout: int = 420, tries: int = 2) -> dict:
    if source() == "ark_coding_plan":
        from .coding_plan import request
        content = [{"type": "text", "text": review_prompt(contract, expected_text, text_mode, clause_ids=True)},
                   {"type": "text", "text": "待验收的大图："},
                   {"type": "image_url", "image_url": {"url": _image_uri(image.resolve())}}]
        for key, label in [("_thumbnail_expectation", "真实展示尺寸预览：只据此判断 thumbnail_readable。"),
                           ("_square_crop_expectation", "实际居中方形裁切：只据此判断 square_crop。")]:
            if contract.get(key):
                content.extend([{"type": "text", "text": label},
                                {"type": "image_url", "image_url": {"url": _image_uri(Path(contract[key]))}}])
        answer, response = request(content, model=model, timeout=timeout)
        data, id_errors = _bind_clause_ids(_extract(answer), contract, text_mode)
        gaps = id_errors + missing_items(data, contract, text_mode)
        if not data or gaps:
            raise ReviewerIncompleteResult(data, response, gaps)
        data["_reviewer"] = f"ark_coding_plan:{response['model']}"
        data["_response_metadata"] = response
        return data
    if source() == "ark_agent_plan":
        return _review_ark(image.resolve(), contract, expected_text, text_mode, model, timeout, tries)
    claude = _claude_bin()
    if not claude:
        raise RuntimeError("找不到 claude 命令行；在 Codex 等宿主里请用 manual 模式由宿主模型看图")
    image = image.resolve()
    thumb = contract.get("_thumbnail_expectation")
    square = contract.get("_square_crop_expectation")
    prompt = (review_prompt(contract, expected_text, text_mode)
              + f"\n\n## 图片位置\n待验收的图片：{image}\n"
              + (f"真实展示尺寸预览：{thumb}\n" if thumb else "")
              + (f"最终导出图的居中方形裁切：{square}\n" if square else "")
              + "先用 Read 工具看待验收图；有预览或方形裁切时也分别读取对应图片（不要读别的文件、不要运行命令），再逐条判断。")
    work = Path(tempfile.mkdtemp(prefix="stylebook-qa-"))
    cmd = [claude, "-p", "--model", model or DEFAULT_MODEL, "--output-format", "json",
           "--json-schema", json.dumps(review_schema(contract), ensure_ascii=False), "--tools", "Read",
           "--add-dir", str(image.parent), "--permission-mode", "dontAsk", "--no-session-persistence", "--strict-mcp-config"]
    last, best = "", None
    ask = prompt
    for _ in range(tries):
        cp = subprocess.run(cmd, cwd=str(work), input=ask, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, check=False)
        if _quota_hit(cp.stdout) or _quota_hit(cp.stderr):
            raise QuotaExhausted("看图用的 Claude 额度用尽或被限流，稍后续跑即可（已出的图与已有结论都保留）")
        if _auth_hit(cp.stdout) or _auth_hit(cp.stderr):
            raise ReviewerAuthUnavailable("独立看图用的 Claude CLI 未登录或订阅访问被禁用；本轮停止复核，已有图片与验收记录保留")
        if cp.returncode == 0:
            data = _extract(cp.stdout)
            if data and isinstance(data.get("must_see"), list):
                data["_reviewer"] = model or DEFAULT_MODEL
                miss = missing_items(data, contract, text_mode)
                if not miss:
                    return data
                best, last = data, f"漏答 {len(miss)} 条"
                # 漏答就整份重问一次，并点名要求逐条原文作答
                ask = prompt + "\n\n注意：上一次漏答了下面这些条目。每一条都必须原样照抄条目文字作为 item 并给出判断：\n" + "\n".join(f"- {x}" for x in miss)
                continue
            last = cp.stdout[:300]
        else:
            last = (cp.stderr or cp.stdout)[-300:]
    if best is not None:
        return best  # 仍有漏答：交给判定环节按「漏答」判不合格，不静默放过
    raise RuntimeError(f"看图进程没有给出合格结论：{last}")
