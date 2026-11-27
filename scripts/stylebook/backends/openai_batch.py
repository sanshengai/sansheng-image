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
MAX_BATCH_FILE_BYTES = 160 * 1024 * 1024  # 预留余量，避开 OpenAI Batch 文件 200 MB 上限
POLL_RETRIES = 5
RETRYABLE_KINDS = {"busy", "network", "unknown"}


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


def _call_with_retry(
    fn: Callable[[], object],
    *,
    retries: int = POLL_RETRIES,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
    label: str = "OpenAI Batch 请求",
):
    """对可重试的瞬时网络/5xx/限流错误做指数退避；鉴权、参数、安全策略错误立即抛出。"""
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except BackendError as exc:
            if not (exc.retryable and exc.kind in RETRYABLE_KINDS) or attempt >= attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
            if log:
                log(f"{label}遇到临时错误（{exc.kind}），{delay:.0f}s 后重试（{attempt}/{attempts - 1}）")
            sleep(delay)


def _split_shards(pairs: list[tuple[dict, dict]], max_bytes: int) -> list[list[tuple[dict, dict, str]]]:
    """按 JSONL UTF-8 字节上限切分批次，避免多参考图 base64 撑爆单文件 200 MB 限制。"""
    limit = max(1, int(max_bytes))
    shards: list[list[tuple[dict, dict, str]]] = []
    cur: list[tuple[dict, dict, str]] = []
    cur_bytes = 0
    for job, line in pairs:
        raw = json.dumps(line, ensure_ascii=False)
        size = len(raw.encode("utf-8")) + 1
        if cur and cur_bytes + size > limit:
            shards.append(cur)
            cur = []
            cur_bytes = 0
        cur.append((job, line, raw))
        cur_bytes += size
    if cur:
        shards.append(cur)
    return shards


def run_openai_batch(
    jobs: list[dict],
    *,
    model: str,
    quality: str | None = None,
    state_file: Path | None = None,
    poll_interval: float = 10.0,
    timeout: float = 7200.0,
    max_file_bytes: int | None = None,
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

    shard_limit = MAX_BATCH_FILE_BYTES if max_file_bytes is None else max_file_bytes
    state = _load_state(state_file)
    batches_state = state.setdefault("batches", {})
    active: dict[str, dict] = {}

    for endpoint, pairs in buckets.items():
        shards = _split_shards(pairs, shard_limit)
        multi = len(shards) > 1
        for shard_idx, shard in enumerate(shards, 1):
            bucket_key = f"{endpoint}#shard{shard_idx}" if multi else endpoint
            ids = [str(job["id"]) for job, _, _ in shard]
            shard_tag = f"{endpoint}，分片 {shard_idx}/{len(shards)}" if multi else endpoint
            existing = batches_state.get(bucket_key)
            if (isinstance(existing, dict) and existing.get("batch_id")
                    and existing.get("ids") == ids and existing.get("model") == model
                    and existing.get("status") not in {"failed", "expired", "cancelled"}):
                batch_id = existing["batch_id"]
                if log:
                    log(f"继续等待已有 OpenAI Batch：{batch_id}（{shard_tag}，{len(ids)} 张）")
                active[bucket_key] = {"batch_id": batch_id, "ids": ids, "endpoint": endpoint}
                continue

            jsonl = "\n".join(raw for _, _, raw in shard) + "\n"
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
            batches_state[bucket_key] = {
                "batch_id": batch_id,
                "input_file_id": file_id,
                "endpoint": endpoint,
                "model": model,
                "ids": ids,
                "status": created.get("status", "validating"),
            }
            _save_state(state_file, state)
            if log:
                log(f"已提交 OpenAI Batch（5 折）：{batch_id}（{shard_tag}，{len(ids)} 张）")
            active[bucket_key] = {"batch_id": batch_id, "ids": ids, "endpoint": endpoint}

    final_objs: dict[str, dict] = {}
    deadline = t0 + timeout
    while len(final_objs) < len(active):
        for bucket_key, meta in active.items():
            if bucket_key in final_objs:
                continue
            info = _call_with_retry(
                lambda bid=meta["batch_id"]: _get_json(f"{base}/batches/{bid}", headers),
                sleep=sleep,
                log=log,
                label=f"轮询 OpenAI Batch {meta['batch_id']}",
            )
            st = info.get("status", "unknown")
            batches_state[bucket_key]["status"] = st
            batches_state[bucket_key]["request_counts"] = info.get("request_counts")
            batches_state[bucket_key]["output_file_id"] = info.get("output_file_id")
            batches_state[bucket_key]["error_file_id"] = info.get("error_file_id")
            _save_state(state_file, state)
            if st in TERMINAL_STATES:
                final_objs[bucket_key] = info
                if log:
                    log(f"OpenAI Batch 结束：{meta['batch_id']} -> {st}（{info.get('request_counts')}）")
        if len(final_objs) < len(active):
            if now() >= deadline:
                ids_str = ", ".join(m["batch_id"] for bk, m in active.items() if bk not in final_objs)
                raise BackendError("busy", f"等待 OpenAI Batch 超时（已保留进度，可稍后重跑续接）：{ids_str}", retryable=True)
            sleep(poll_interval)

    results: dict[str, Result | BackendError] = {}
    elapsed = round(now() - t0, 1)

    for bucket_key, info in final_objs.items():
        ids = active[bucket_key]["ids"]
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
            out_fid = info["output_file_id"]
            raw_out = _call_with_retry(
                lambda fid=out_fid: _get_text(f"{base}/files/{fid}/content", headers),
                sleep=sleep,
                log=log,
                label=f"下载 OpenAI Batch 输出 {out_fid}",
            )
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
                        img = _call_with_retry(
                            lambda item=body["data"][0]: _decode_item(item),
                            sleep=sleep,
                            log=log,
                            label=f"读取 OpenAI Batch 图片 {cid}",
                        )
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
            err_fid = info["error_file_id"]
            raw_err = _call_with_retry(
                lambda fid=err_fid: _get_text(f"{base}/files/{fid}/content", headers),
                sleep=sleep,
                log=log,
                label=f"下载 OpenAI Batch 错误日志 {err_fid}",
            )
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
