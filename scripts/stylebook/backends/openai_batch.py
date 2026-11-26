"""OpenAI 官方 Batch API 批量出图（5 折）：支持 /v1/images/generations 与 /v1/images/edits。

只用标准库。仅在 OpenAI 官方端点（api.openai.com）启用；第三方中转默认不自动切入 Batch，避免不兼容接口报错。
提交后会把批次 ID 写进可选的 state_file，中断后重跑可继续轮询已有批次而不重复计费。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .base import BackendError, Result, classify, fetch_bytes, get, now, post_json, post_multipart

TERMINAL_STATES = {"completed", "failed", "expired", "cancelled"}


def official_openai_base(base_url: str | None = None) -> str:
    return (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")


def is_official_openai_base(base_url: str | None = None) -> bool:
    host = (urlsplit(official_openai_base(base_url)).hostname or "").lower()
    return host == "api.openai.com"


def batch_threshold(cfg: dict | None = None) -> int:
    raw = os.environ.get("SANSHENG_IMAGE_OPENAI_BATCH_THRESHOLD")
    if raw is None and cfg is not None:
        raw = cfg.get("batch_threshold")
    if raw in (None, "", False):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BackendError("bad_request", f"batch_threshold 必须是非负整数，实际：{raw!r}") from exc
    if value < 0:
        raise BackendError("bad_request", f"batch_threshold 不能小于 0，实际：{value}")
    return value


def should_use_batch(provider: str, count: int, *, explicit: bool | None = None,
                     base_url: str | None = None, cfg: dict | None = None) -> bool:
    if explicit is False or count <= 0:
        return False
    if provider != "openai":
        if explicit is True:
            raise BackendError("bad_request", f"OpenAI Batch 模式仅支持 openai 服务，当前是 {provider}")
        return False
    if not is_official_openai_base(base_url):
        if explicit is True:
            raise BackendError("bad_request", "OpenAI Batch 模式仅支持官方接口 https://api.openai.com/v1，当前是自定义中转地址")
        return False
    if explicit is True:
        return True
    threshold = batch_threshold(cfg)
    return threshold > 0 and count >= threshold


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _normalize_quality(quality: str | None) -> str:
    q = quality or "normal"
    return {"normal": "medium", "high": "high", "low": "low"}.get(q, q)


def _request_line(job: dict, *, model: str, default_quality: str | None) -> tuple[str, dict]:
    q = _normalize_quality(job.get("quality") or default_quality)
    w, h = job["size"]
    refs = [Path(p) for p in (job.get("refs") or [])]
    body: dict = {
        "model": model,
        "prompt": job["prompt"],
        "size": f"{int(w)}x{int(h)}",
        "quality": q,
        "n": 1,
    }
    if job.get("transparent"):
        body["background"] = "transparent"
    if refs:
        endpoint = "/v1/images/edits"
        body["images"] = [{"image_url": _data_uri(p)} for p in refs]
    else:
        endpoint = "/v1/images/generations"
    line = {"custom_id": str(job["id"]), "method": "POST", "url": endpoint, "body": body}
    return endpoint, line


def _decode_item(item: dict) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return fetch_bytes(item["url"])
    raise BackendError("unknown", "Batch 响应里没有图片数据")


def _load_state(state_file: Path | None) -> dict:
    if state_file and state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {"version": 1, "batches": {}}


def _save_state(state_file: Path | None, state: dict) -> None:
    if not state_file:
        return
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_json(url: str, headers: dict, timeout: float = 60.0) -> dict:
    status, raw = get(url, headers, timeout=timeout)
    text = raw.decode("utf-8", "replace")
    if status >= 400:
        raise classify(status, text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise classify(status, text) from None


def _get_text(url: str, headers: dict, timeout: float = 180.0) -> str:
    status, raw = get(url, headers, timeout=timeout)
    text = raw.decode("utf-8", "replace")
    if status >= 400:
        raise classify(status, text)
    return text


def run_openai_batch(
    jobs: list[dict],
    *,
    model: str,
    quality: str | None = None,
    state_file: Path | None = None,
    poll_interval: float = 10.0,
    timeout: float = 7200.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
    estimate_fn: Callable[[str, str, tuple[int, int], bool], float | None] | None = None,
    record_log: Callable[[dict], None] | None = None,
) -> dict[str, Result | BackendError]:
    """把多张出图请求按端点打包提交到 OpenAI Batch API（5 折），轮询完成后落盘原图。"""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise BackendError("unconfigured", "没有配置 OpenAI 的密钥（环境变量 OPENAI_API_KEY）。")
    base = official_openai_base()
    if not is_official_openai_base(base):
        raise BackendError("bad_request", "OpenAI Batch 模式仅支持官方接口 https://api.openai.com/v1")
    if not jobs:
        return {}

    headers = {"Authorization": f"Bearer {key}"}
    t0 = now()
    buckets: dict[str, list[tuple[dict, dict]]] = {}
    job_by_id: dict[str, dict] = {}
    for job in jobs:
        cid = str(job["id"])
        if cid in job_by_id:
            raise BackendError("bad_request", f"Batch 任务 id 重复：{cid}")
        job_by_id[cid] = job
        endpoint, line = _request_line(job, model=model, default_quality=quality)
        buckets.setdefault(endpoint, []).append((job, line))

    state = _load_state(state_file)
    batches_state = state.setdefault("batches", {})
    active: dict[str, dict] = {}

    for endpoint, pairs in buckets.items():
        ids = [str(job["id"]) for job, _ in pairs]
        existing = batches_state.get(endpoint)
        if (isinstance(existing, dict) and existing.get("batch_id")
                and existing.get("ids") == ids and existing.get("model") == model
                and existing.get("status") not in {"failed", "expired", "cancelled"}):
            batch_id = existing["batch_id"]
            if log:
                log(f"继续等待已有 OpenAI Batch：{batch_id}（{endpoint}，{len(ids)} 张）")
            active[endpoint] = {"batch_id": batch_id, "ids": ids}
            continue

        jsonl = "\n".join(json.dumps(line, ensure_ascii=False) for _, line in pairs) + "\n"
        with tempfile.TemporaryDirectory(prefix="stylebook-oai-batch-") as tmp:
            p = Path(tmp) / "batch.jsonl"
            p.write_text(jsonl, encoding="utf-8")
            uploaded = post_multipart(f"{base}/files", {"purpose": "batch"}, [("file", p)], headers, timeout=300)
        file_id = uploaded.get("id")
        if not file_id:
            raise BackendError("unknown", f"上传 Batch 文件未返回 file_id：{str(uploaded)[:200]}")
        created = post_json(
            f"{base}/batches",
            {"input_file_id": file_id, "endpoint": endpoint, "completion_window": "24h"},
            headers,
            timeout=120,
        )
        batch_id = created.get("id")
        if not batch_id:
            raise BackendError("unknown", f"创建 Batch 未返回 batch_id：{str(created)[:200]}")
        batches_state[endpoint] = {
            "batch_id": batch_id,
            "input_file_id": file_id,
            "endpoint": endpoint,
            "model": model,
            "ids": ids,
            "status": created.get("status", "validating"),
        }
        _save_state(state_file, state)
        if log:
            log(f"已提交 OpenAI Batch（5 折）：{batch_id}（{endpoint}，{len(ids)} 张）")
        active[endpoint] = {"batch_id": batch_id, "ids": ids}

    final_objs: dict[str, dict] = {}
    deadline = t0 + timeout
    while len(final_objs) < len(active):
        for endpoint, meta in active.items():
            if endpoint in final_objs:
                continue
            info = _get_json(f"{base}/batches/{meta['batch_id']}", headers)
            st = info.get("status", "unknown")
            batches_state[endpoint]["status"] = st
            batches_state[endpoint]["request_counts"] = info.get("request_counts")
            batches_state[endpoint]["output_file_id"] = info.get("output_file_id")
            batches_state[endpoint]["error_file_id"] = info.get("error_file_id")
            _save_state(state_file, state)
            if st in TERMINAL_STATES:
                final_objs[endpoint] = info
                if log:
                    log(f"OpenAI Batch 结束：{meta['batch_id']} -> {st}（{info.get('request_counts')}）")
        if len(final_objs) < len(active):
            if now() >= deadline:
                ids_str = ", ".join(m["batch_id"] for ep, m in active.items() if ep not in final_objs)
                raise BackendError("busy", f"等待 OpenAI Batch 超时（已保留进度，可稍后重跑续接）：{ids_str}", retryable=True)
            sleep(poll_interval)

    results: dict[str, Result | BackendError] = {}
    elapsed = round(now() - t0, 1)

    for endpoint, info in final_objs.items():
        ids = active[endpoint]["ids"]
        st = info.get("status")
        if st != "completed" and not info.get("output_file_id"):
            err_msg = f"OpenAI Batch 未完成（status={st}）：{str(info.get('errors') or '')[:200]}"
            for cid in ids:
                err = BackendError("busy" if st == "expired" else "unknown", err_msg, retryable=(st == "expired"))
                results[cid] = err
                if record_log:
                    job = job_by_id[cid]
                    record_log({"tag": job.get("tag", cid), "provider": "openai", "model": model, "batch": True,
                                "batch_id": info.get("id"), "ok": False, "attempts": 1, "kind": err.kind,
                                "msg": str(err)[:160], "seconds": elapsed, "attempt_seconds": elapsed})
            continue

        if info.get("output_file_id"):
            raw_out = _get_text(f"{base}/files/{info['output_file_id']}/content", headers)
            for raw_line in raw_out.splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                cid = str(row.get("custom_id", ""))
                if cid not in job_by_id:
                    continue
                job = job_by_id[cid]
                resp = row.get("response") or {}
                code = resp.get("status_code")
                body = resp.get("body") or {}
                if code == 200 and body.get("data"):
                    try:
                        img = _decode_item(body["data"][0])
                    except BackendError as exc:
                        results[cid] = exc
                        continue
                    out_path = Path(job["out"])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(img)
                    q_used = job.get("quality") or quality or "normal"
                    est = estimate_fn(model, q_used, tuple(job["size"]), True) if estimate_fn else None
                    results[cid] = Result(img, "openai", model, 1, elapsed, est)
                    if record_log:
                        usage = body.get("usage") or {}
                        record_log({
                            "tag": job.get("tag", cid), "provider": "openai", "model": model, "batch": True,
                            "batch_id": info.get("id"), "size": list(job["size"]), "quality": q_used, "ok": True,
                            "attempts": 1, "est_usd": est, "refs": len(job.get("refs") or []),
                            "seconds": elapsed, "attempt_seconds": elapsed,
                            **({"tokens": usage.get("total_tokens")} if usage.get("total_tokens") else {}),
                        })
                else:
                    err_detail = json.dumps(row.get("error") or body, ensure_ascii=False)
                    err = classify(code, err_detail)
                    results[cid] = err
                    if record_log:
                        record_log({"tag": job.get("tag", cid), "provider": "openai", "model": model, "batch": True,
                                    "batch_id": info.get("id"), "ok": False, "attempts": 1, "kind": err.kind,
                                    "msg": str(err)[:160], "seconds": elapsed, "attempt_seconds": elapsed})

        if info.get("error_file_id"):
            raw_err = _get_text(f"{base}/files/{info['error_file_id']}/content", headers)
            for raw_line in raw_err.splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                cid = str(row.get("custom_id", ""))
                if cid not in job_by_id or cid in results:
                    continue
                resp = row.get("response") or {}
                code = resp.get("status_code")
                detail = json.dumps(row.get("error") or resp.get("body") or row, ensure_ascii=False)
                err = classify(code, detail)
                results[cid] = err
                if record_log:
                    job = job_by_id[cid]
                    record_log({"tag": job.get("tag", cid), "provider": "openai", "model": model, "batch": True,
                                "batch_id": info.get("id"), "ok": False, "attempts": 1, "kind": err.kind,
                                "msg": str(err)[:160], "seconds": elapsed, "attempt_seconds": elapsed})

        for cid in ids:
            if cid not in results:
                err = BackendError("unknown", f"OpenAI Batch 结果里缺少任务 {cid}")
                results[cid] = err
                if record_log:
                    job = job_by_id[cid]
                    record_log({"tag": job.get("tag", cid), "provider": "openai", "model": model, "batch": True,
                                "batch_id": info.get("id"), "ok": False, "attempts": 1, "kind": err.kind,
                                "msg": str(err)[:160], "seconds": elapsed, "attempt_seconds": elapsed})

    return results
