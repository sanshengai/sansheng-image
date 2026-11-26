"""OpenAI 官方 Batch API 5 折批量出图：阈值判据、端点分流、断点续接、部分失败与 CLI 集成。"""
from __future__ import annotations

import base64
import copy
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _png_bytes(color="ivory", size=(1024, 1024)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def B(tmp_path, monkeypatch):
    for k in ("OPENAI_BASE_URL", "SANSHENG_IMAGE_OPENAI_BATCH_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SANSHENG_IMAGE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("SANSHENG_IMAGE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-official")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.chdir(tmp_path)
    import stylebook.backends.base as base
    importlib.reload(base)
    import stylebook.backends.openai_batch as ob
    importlib.reload(ob)
    import stylebook.backends.providers as prov
    importlib.reload(prov)
    import stylebook.backends as b
    importlib.reload(b)
    return b


def test_should_use_batch_gates_and_counterexamples(B, monkeypatch):
    # 默认未配置阈值 -> 不自动启用
    assert not B.should_use_openai_batch("openai", 10)
    B.save_config({"provider": "openai", "model": "gpt-image-2.6-sunburst", "batch_threshold": 4})
    assert not B.should_use_openai_batch("openai", 3)
    assert B.should_use_openai_batch("openai", 4)
    assert not B.should_use_openai_batch("openai", 10, explicit=False)
    assert B.should_use_openai_batch("openai", 2, explicit=True)

    # 反例 1：非 openai 服务拒绝强制 Batch，自动模式返回 False
    assert not B.should_use_openai_batch("codex", 10)
    with pytest.raises(B.BackendError) as exc:
        B.should_use_openai_batch("codex", 10, explicit=True)
    assert exc.value.kind == "bad_request"

    # 反例 2：第三方中转端点自动模式返回 False，强制模式直接拦截
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
    assert not B.should_use_openai_batch("openai", 10)
    with pytest.raises(B.BackendError) as exc2:
        B.should_use_openai_batch("openai", 10, explicit=True)
    assert exc2.value.kind == "bad_request"


def test_run_openai_batch_splits_generations_and_edits_with_half_price_and_resume(B, tmp_path, monkeypatch):
    import stylebook.backends.openai_batch as ob

    ref_img = tmp_path / "ref.png"
    ref_img.write_bytes(_png_bytes("red", (64, 64)))
    out_png_b64 = base64.b64encode(_png_bytes("blue", (1024, 1024))).decode("ascii")

    uploaded_payloads: list[tuple[str, list[dict]]] = []
    created_batches: list[dict] = []
    poll_count = {"n": 0}

    def fake_post_multipart(url, fields, files, headers, timeout=300):
        assert url == "https://api.openai.com/v1/files"
        assert fields["purpose"] == "batch"
        content = files[0][1].read_text(encoding="utf-8")
        lines = [json.loads(x) for x in content.splitlines() if x.strip()]
        fid = f"file-{len(uploaded_payloads) + 1}"
        uploaded_payloads.append((fid, lines))
        return {"id": fid, "status": "processed"}

    def fake_post_json(url, payload, headers, timeout=120):
        assert url == "https://api.openai.com/v1/batches"
        bid = f"batch-{len(created_batches) + 1}"
        created_batches.append({"id": bid, **payload})
        return {"id": bid, "status": "validating"}

    def fake_get(url, headers, timeout=60):
        if "/batches/" in url:
            bid = url.rsplit("/", 1)[-1]
            poll_count["n"] += 1
            if poll_count["n"] <= 2:
                return 200, json.dumps({"id": bid, "status": "in_progress"}).encode()
            return 200, json.dumps({
                "id": bid, "status": "completed",
                "output_file_id": f"out-{bid}",
                "error_file_id": f"err-{bid}" if bid == "batch-1" else None,
                "request_counts": {"total": 2, "completed": 1, "failed": 1},
            }).encode()
        if url.endswith("/files/out-batch-1/content"):
            row = {"custom_id": "g1", "response": {"status_code": 200, "body": {
                "data": [{"b64_json": out_png_b64}], "usage": {"total_tokens": 900}}}}
            return 200, (json.dumps(row) + "\n").encode()
        if url.endswith("/files/err-batch-1/content"):
            row = {"custom_id": "g2", "response": {"status_code": 400, "body": {
                "error": {"code": "content_policy_violation", "message": "blocked by safety"}}}}
            return 200, (json.dumps(row) + "\n").encode()
        if url.endswith("/files/out-batch-2/content"):
            row = {"custom_id": "e1", "response": {"status_code": 200, "body": {
                "data": [{"b64_json": out_png_b64}], "usage": {"total_tokens": 1100}}}}
            return 200, (json.dumps(row) + "\n").encode()
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(ob, "post_multipart", fake_post_multipart)
    monkeypatch.setattr(ob, "post_json", fake_post_json)
    monkeypatch.setattr(ob, "get", fake_get)

    state_file = tmp_path / "batch-state.json"
    jobs = [
        {"id": "g1", "prompt": "p1", "out": tmp_path / "g1.png", "size": (1024, 1024), "refs": [], "tag": "t:g1"},
        {"id": "g2", "prompt": "p2", "out": tmp_path / "g2.png", "size": (1024, 1024), "refs": [], "tag": "t:g2"},
        {"id": "e1", "prompt": "p3", "out": tmp_path / "e1.png", "size": (1536, 864), "refs": [ref_img],
         "transparent": True, "tag": "t:e1"},
    ]
    res = B.generate_openai_batch(jobs, model="gpt-image-2.6-sunburst", quality="normal",
                                  state_file=state_file, poll_interval=0, sleep=lambda _: None)

    # 正确拆分为 generations 与 edits 两个批次
    assert [b["endpoint"] for b in created_batches] == ["/v1/images/generations", "/v1/images/edits"]
    edit_line = uploaded_payloads[1][1][0]
    assert edit_line["url"] == "/v1/images/edits"
    assert edit_line["body"]["background"] == "transparent"
    assert edit_line["body"]["images"][0]["image_url"].startswith("data:image/png;base64,")

    # 成功项落盘且估算费用为实时价的 5 折
    assert isinstance(res["g1"], B.Result) and (tmp_path / "g1.png").is_file()
    assert res["g1"].est_usd == round(0.053 * 0.5, 4)
    assert isinstance(res["e1"], B.Result) and (tmp_path / "e1.png").is_file()
    assert res["e1"].est_usd == round(0.041 * 0.5, 4)
    # 失败项分类准确，不拖垮同批成功项
    assert isinstance(res["g2"], B.BackendError) and res["g2"].kind == "policy"

    # 成本日志记录 batch=True 与 tokens
    logs = [json.loads(line) for line in (tmp_path / "logs" / "cost.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(r.get("tag") == "t:g1" and r.get("batch") is True and r.get("tokens") == 900 for r in logs)
    assert any(r.get("tag") == "t:g2" and r.get("ok") is False and r.get("kind") == "policy" for r in logs)

    # 断点续接：再次以同一 state_file 调用不再重复上传或创建新批次
    assert state_file.is_file()
    prev_uploads, prev_creates = len(uploaded_payloads), len(created_batches)
    res2 = B.generate_openai_batch(jobs, model="gpt-image-2.6-sunburst", quality="normal",
                                   state_file=state_file, poll_interval=0, sleep=lambda _: None)
    assert len(uploaded_payloads) == prev_uploads and len(created_batches) == prev_creates
    assert isinstance(res2["g1"], B.Result) and isinstance(res2["e1"], B.Result)


def test_make_uses_openai_batch_at_threshold_and_sync_below(B, tmp_path, monkeypatch):
    from stylebook import make as MK, profile as P

    monkeypatch.setenv("SANSHENG_IMAGE_PREFERENCES", str(tmp_path / "prefs.json"))
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path / "absent-profile"))
    monkeypatch.setattr(P, "lookup", lambda *a, **k: None)
    B.save_config({"provider": "openai", "model": "gpt-image-2.6-sunburst", "batch_threshold": 4})

    batch_calls = []
    sync_calls = []

    def fake_batch(jobs, *, model=None, quality=None, **kw):
        batch_calls.append(len(jobs))
        out_map = {}
        for j in jobs:
            data = _png_bytes("ivory", j["size"])
            Path(j["out"]).write_bytes(data)
            out_map[j["id"]] = B.Result(data, "openai", model or "gpt-image-2.6-sunburst", 1, 2.0, 0.0205)
        return out_map

    def fake_sync(prompt, out, *, size, **kw):
        sync_calls.append(out.name)
        data = _png_bytes("ivory", size)
        Path(out).write_bytes(data)
        return B.Result(data, "openai", "gpt-image-2.6-sunburst", 1, 1.0, 0.041)

    monkeypatch.setattr(B, "generate_openai_batch", fake_batch)
    monkeypatch.setattr(B, "generate", fake_sync)

    def make_brief(n: int) -> dict:
        return {
            "version": 1, "scene": "wxillus", "style": "C42", "reason": "batch test",
            "items": [{"id": f"p{i:02d}", "position": f"位置 {i}", "message": f"观点 {i}",
                       "visual": f"Simple flat card {i}", "text": []} for i in range(1, n + 1)],
        }

    # 3 张 < 阈值 4 -> 走实时同步
    r3 = MK.run(make_brief(3), tmp_path / "out3", base_path=tmp_path)
    assert r3["status"] == "pending_visual_review" and not r3.get("openai_batch")
    assert len(sync_calls) == 3 and not batch_calls

    # 4 张 >= 阈值 4 -> 一次走 OpenAI Batch（5 折）
    r4 = MK.run(make_brief(4), tmp_path / "out4", base_path=tmp_path)
    assert r4["status"] == "pending_visual_review" and r4.get("openai_batch") is True
    assert batch_calls == [4] and len(sync_calls) == 3


def test_raw_batch_cli_sync_and_batch_modes(B, tmp_path, monkeypatch):
    jobs_file = tmp_path / "jobs.json"
    jobs = [{"id": f"j{i}", "prompt": f"card {i}", "out": str(tmp_path / f"j{i}.png"), "size": "1024x1024"}
            for i in range(1, 3)]
    jobs_file.write_text(json.dumps(jobs), encoding="utf-8")

    # 直接测 cmd_raw_batch（同进程便于 mock 后端）
    import sb as SB

    seen = {"batch": 0, "sync": 0}

    def fake_batch(items, **kw):
        seen["batch"] += len(items)
        out = {}
        for j in items:
            Path(j["out"]).write_bytes(_png_bytes())
            out[j["id"]] = B.Result(b"PNG", "openai", "gpt-image-2.6-sunburst", 1, 1.5, 0.0265)
        return out

    def fake_sync(prompt, out, **kw):
        seen["sync"] += 1
        Path(out).write_bytes(_png_bytes())
        return B.Result(b"PNG", "openai", "gpt-image-2.6-sunburst", 1, 1.0, 0.053)

    monkeypatch.setattr(B, "generate_openai_batch", fake_batch)
    monkeypatch.setattr(B, "generate", fake_sync)
    B.save_config({"provider": "openai", "model": "gpt-image-2.6-sunburst", "batch_threshold": 4})

    rc1 = SB.main(["raw-batch", "--jobs-file", str(jobs_file)])
    assert rc1 == 0 and seen == {"batch": 0, "sync": 2}

    rc2 = SB.main(["raw-batch", "--jobs-file", str(jobs_file), "--openai-batch"])
    assert rc2 == 0 and seen == {"batch": 2, "sync": 2}


def test_batch_run_uses_openai_batch_and_skips_regen(B, tmp_path, monkeypatch):
    from stylebook import batch as BT

    B.save_config({"provider": "openai", "model": "gpt-image-2.6-sunburst", "batch_threshold": 4})
    batch_calls = []
    sync_calls = []

    def fake_batch(jobs, *, model=None, quality=None, **kw):
        batch_calls.append(len(jobs))
        out_map = {}
        for j in jobs:
            data = _png_bytes("ivory", j["size"])
            Path(j["out"]).write_bytes(data)
            out_map[j["id"]] = B.Result(data, "openai", model or "gpt-image-2.6-sunburst", 1, 2.0, 0.0205)
        return out_map

    def fake_sync(prompt, out, *, size, **kw):
        sync_calls.append(out.name)
        data = _png_bytes("ivory", size)
        Path(out).write_bytes(data)
        return B.Result(data, "openai", "gpt-image-2.6-sunburst", 1, 1.0, 0.041)

    monkeypatch.setattr(B, "generate_openai_batch", fake_batch)
    monkeypatch.setattr(B, "generate", fake_sync)

    plan = {
        "scene": "wxillus",
        "series": False,
        "style": {"code": "C42", "source": "explicit", "why": "Flat clean visual suited for article cards"},
        "items": [{"id": f"p{i:02d}", "position": f"「这是第 {i} 段原文」之后", "what": f"核心观点 {i}",
                   "why": f"用对比卡片说明第 {i} 点差异", "shape": "story", "form": "scene",
                   "subject": f"Minimal flat visual diagram {i}", "text": []} for i in range(1, 5)],
    }
    st = BT.run(plan, tmp_path / "batch_out", do_review=False, log=lambda _: None)
    assert batch_calls == [4] and sync_calls == []
    assert all(st["items"][f"p{i:02d}"]["status"] == "generated" for i in range(1, 5))
    assert all(st["items"][f"p{i:02d}"].get("batch") is True for i in range(1, 5))

