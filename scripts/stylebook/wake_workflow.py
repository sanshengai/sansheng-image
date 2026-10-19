"""Image-to-motion orchestration for the current Agent, using an external wake runtime."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import html
import io
import json
import math
from pathlib import Path
import re
import shutil
import sys

from . import wake_runtime as R

SCENES = {"article": "article", "wxillus": "article", "infographic": "infographic", "info": "infographic",
          "xiaohongshu": "xiaohongshu", "xhs": "xiaohongshu", "brand": "brand"}
CHECKS = ("motion_value", "integrity", "causality", "composition", "legibility", "loop")
GENERATED = {"frames", "frames_loop", "stills", "render.log", "qa_sheet.jpg", "__pycache__", ".DS_Store"}
PREVIEW_FILES = {"timeline.jpg", "materials.jpg", "inspect.json"}
FONT_MATERIALS = {".otf", ".ttf", ".woff", ".woff2"}
RENDER_FILES = {"motion.mp4", "motion.gif", "motion.webp", "qa.json", "replay.json", "timeline.jpg", "materials.jpg",
                "UPSTREAM.md", "director.json"}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R.WakeError(f"缺少具体说明：{name}")
    return value.strip()


def local(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise R.WakeError("工程文件须使用工程内相对路径")
    p = (root / relative).resolve()
    if root not in p.parents or not p.is_file() or (root / relative).is_symlink():
        raise R.WakeError(f"工程文件缺失或不在工程内：{relative}")
    return p


def source_hashes(src: Path) -> dict:
    result = {}
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in GENERATED for part in rel.parts):
            continue
        if p.is_symlink():
            raise R.WakeError(f"工程含符号链接，请使用自包含素材：{rel}")
        if p.is_file():
            result[rel.as_posix()] = R.sha(p)
    if "index.html" not in result:
        raise R.WakeError("工程缺少 src/index.html")
    return result


def project(path: Path) -> tuple[Path, dict]:
    root = path.expanduser().resolve()
    data = R.read_json(root / "motion.json")
    if data.get("schema_version") != 1 or data.get("upstream_revision") != R.LOCK["revision"]:
        raise R.WakeError("不支持的动态工程或上游版本")
    if not isinstance(data.get("inputs"), dict) or not data["inputs"]:
        raise R.WakeError("动态工程缺少原始输入")
    for rel, digest in data["inputs"].items():
        if R.sha(local(root, rel)) != digest:
            raise R.WakeError(f"原始输入已变化：{rel}；不能沿用旧审片，请另建候选")
    return root, data


def snapshot(root: Path, data: dict) -> dict:
    return {"inputs": data["inputs"], "manifest": R.sha(root / "motion.json"),
            "director": R.sha(root / "director.json"), "src": source_hashes(root / "src")}


def next_dir(root: Path, kind: str) -> Path:
    parent = root / kind
    for i in range(1, 10000):
        candidate = parent / f"{i:03d}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise R.WakeError("候选过多，请检查工程目录")


def init(a) -> dict:
    config = R.settings()
    image = a.image.expanduser().resolve()
    if not image.is_file() or image.stat().st_size == 0:
        raise R.WakeError("原图不存在或为空")
    out = a.out.expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise R.WakeError("工程目录已存在，不覆盖；请使用新目录")
    out = out.resolve()
    if out in image.parents:
        raise R.WakeError("不能将新工程建在原图的父目录")
    duration = a.duration
    if not math.isfinite(duration) or duration <= 0 or not 1 <= a.fps <= 60:
        raise R.WakeError("duration 须为正有限秒数，fps 须为 1..60")
    purpose = text(a.purpose, "purpose")
    brief_path = a.brief.expanduser().resolve() if a.brief else None
    brief = R.read_json(brief_path) if brief_path else {}
    if brief_path and not brief:
        raise R.WakeError("静态 brief 不能为空")
    prepared = {}
    if brief:
        if "tasks" in brief:
            if brief.get("version") != 1 or not isinstance(brief.get("selection"), dict):
                raise R.WakeError("不支持的静态 prepared.json")
            prepared = brief
        else:
            # Reuse the static compiler's real contract: raw source is a path,
            # sb2 code resolves style/palette, prepared.json binds source hashes.
            from .make import prepare
            prepared = prepare(brief, base_path=brief_path.parent)
    inherited = {}
    article = a.article.expanduser().resolve() if a.article else None
    if brief:
        items = prepared.get("tasks")
        if not isinstance(items, list) or not items:
            raise R.WakeError("静态需求缺少图片 tasks")
        chosen = [v for v in items if isinstance(v, dict) and (str(v.get("id")) == a.item if a.item else len(items) == 1)]
        if len(chosen) != 1:
            raise R.WakeError("请由 Agent 用 --item 指定本次复用的静态图 id")
        manifest = chosen[0].get("manifest")
        if not isinstance(manifest, dict) or not all(manifest.get(k) for k in ("style", "palette", "format")):
            raise R.WakeError("静态任务缺少已解析的 style/palette/format")
        inherited = {"style": manifest["style"], "palette": manifest["palette"], "format": manifest["format"],
                     "selection": prepared["selection"], "item": chosen[0]}
        declared = prepared.get("source", {})
        if declared:
            if not isinstance(declared, dict) or not declared.get("path") or not declared.get("sha256"):
                raise R.WakeError("brief.source 须包含 path 与 sha256")
            declared_path = Path(declared["path"]).expanduser()
            if not declared_path.is_absolute():
                declared_path = brief_path.parent / declared_path
            if article and article != declared_path.resolve():
                raise R.WakeError("--article 与静态 brief 的原文不是同一文件")
            article = declared_path.resolve()
            if not article.is_file() or R.sha(article) != declared["sha256"]:
                raise R.WakeError("原文与静态 brief 的 sha256 不符")
    raw_scene = a.scene or prepared.get("scene") or "article"
    if raw_scene not in SCENES:
        raise R.WakeError(f"动态场景不支持 {raw_scene}；PPT、音乐封面继续走静态流程")
    scene = SCENES[raw_scene]
    if article:
        article.read_text(encoding="utf-8")  # Validate before creating a workspace.
    size = a.size or ("1280x720" if scene != "xiaohongshu" else None)
    out.mkdir(parents=True)
    (out / "inputs").mkdir()
    original = out / "inputs" / ("original" + image.suffix.lower())
    shutil.copyfile(image, original)
    inputs = {original.relative_to(out).as_posix(): R.sha(original)}
    if brief_path:
        target = out / "inputs/brief.json"
        shutil.copyfile(brief_path, target)
        inputs["inputs/brief.json"] = R.sha(target)
        R.write_json(out / "inputs/prepared.json", prepared)
        inputs["inputs/prepared.json"] = R.sha(out / "inputs/prepared.json")
    if article:
        target = out / "inputs/article.md"
        shutil.copyfile(article, target)
        inputs["inputs/article.md"] = R.sha(target)
    command = R.python_command(config, "new_project.py", original, "working", "--parent", out,
                               "--dur", duration, "--fps", a.fps)
    if size:
        command += ["--out", size]
    R.run(command, cwd=out, log=out / "logs/init.log", config=config)
    (out / "working-alive/src").rename(out / "src")
    (out / "working-alive").rmdir()
    data = {"schema_version": 1, "created_at": stamp(), "scene": scene, "purpose": purpose,
            "original": original.relative_to(out).as_posix(), "inputs": inputs, "inherit": inherited,
            "output": {"size": size or "source", "duration": duration, "fps": a.fps},
            "upstream_revision": R.LOCK["revision"], "upstream_license": R.LOCK["license"],
            "template_index_sha256": R.sha(out / "src/index.html")}
    R.write_json(out / "motion.json", data)
    R.write_json(out / "director.json", {
        "decision": "pending", "mode": "", "why_motion": "", "preserve": [],
        "beats": [], "materials": [], "review_times": [], "loop": "", "limitations": []})
    (out / "UPSTREAM.md").write_text(
        f"# 外部制作依赖\n\nLemomo / lemo-wake\n\n{R.LOCK['repository']}\n\n"
        f"版本：{R.LOCK['version']}，commit `{R.LOCK['revision']}`\n\n"
        f"上游代码、模板与文档许可：{R.LOCK['license']}；{R.LOCK['license_url']}\n\n"
        "src/ 初始模板来自上述上游；后续素材与编排修改由当前制作过程记录。"
        "本工程不因调用叁笙生图而将上游内容改为 MIT。请保留来源、修改说明与实际素材授权。\n",
        encoding="utf-8")
    (out / "DIRECTOR.md").write_text(
        f"# 动态制作说明\n\n用途：{purpose}\n\n"
        "由当前 Agent 看原图与原文后填写：为什么值得动；保留什么；素材实测能承担哪些动作；"
        "动作的触发、响应和落位；画幅改编；审片发现的问题与返修。\n\n"
        "先检查材料，再定完整动作。不要把本文件、计划字段或渲染成功当作已经审美通过。\n", encoding="utf-8")
    return {"state": "needs-materials-and-direction", "project": str(out),
            "read": [str(original), *([str(out / "inputs/article.md")] if article else []),
                     str(R.ROOT / "references/motion-wake.md"), str(Path(config["wake_skill"]) / "SKILL.md"),
                     str(Path(config["wake_skill"]) / "references/taste.md")],
            "type_guides": str(Path(config["wake_skill"]) / "types"),
            "next": "看原图，按类型取用上游工具并检查分件；填写 director.json，再执行 animate wake preview <工程>。"}


def validate_director(root: Path, data: dict) -> dict:
    plan = R.read_json(root / "director.json")
    if plan.get("decision") == "static":
        text(plan.get("why_motion"), "保留静态的理由")
        raise R.WakeError("本图已判断保留静态，不执行动态渲染")
    if plan.get("decision") != "animate":
        raise R.WakeError("先由 Agent 检查图片与材料，完成 director.json 的 animate/static 判断")
    text(plan.get("why_motion"), "why_motion")
    text(plan.get("loop"), "loop")
    reset = plan.get("reset")
    if reset is not None:
        if not isinstance(reset, dict) or not re.fullmatch(r"[a-z][a-z0-9_-]*", str(reset.get("kind", ""))):
            raise R.WakeError("reset 须含上游 reset.py --list 中的 kind")
        for key, default, low, high in (("hold", 0.7, 0, 5), ("duration", 0.6, 0.001, 3)):
            value = reset.get(key, default)
            if type(value) not in (int, float) or not low <= value <= high:
                raise R.WakeError(f"reset.{key} 范围须为 {low}..{high}")
    if plan.get("mode") not in {"atmosphere", "narrative", "explanation", "material"}:
        raise R.WakeError("mode 使用 atmosphere/narrative/explanation/material")
    if not isinstance(plan.get("preserve"), list) or not plan["preserve"] or any(not isinstance(v, str) or not v.strip() for v in plan["preserve"]):
        raise R.WakeError("preserve 须说明本图需要保护的内容、构图或颜色")
    duration = data["output"]["duration"]
    beats = plan.get("beats")
    if not isinstance(beats, list) or len(beats) < 2:
        raise R.WakeError("beats 须记录变化和落定，不能只有一个静态状态")
    for beat in beats:
        if not isinstance(beat, dict) or type(beat.get("at")) not in (int, float) or not 0 <= beat["at"] <= duration:
            raise R.WakeError("beats.at 须在工程时长内")
        text(beat.get("event"), "beats.event")
    times = plan.get("review_times")
    if (not isinstance(times, list) or len(set(map(str, times))) < 3 or
            any(type(t) not in (int, float) or not 0 <= t < duration for t in times)):
        raise R.WakeError("review_times 至少三个不同有效时刻，覆盖主动作极值、遮挡和收束；不要求固定九帧")
    materials = plan.get("materials")
    if not isinstance(materials, list) or not materials:
        raise R.WakeError("materials 须列出实际使用并查看过的素材")
    for material in materials:
        if not isinstance(material, dict):
            raise R.WakeError("materials 每项须为对象")
        local(root, material.get("path"))
        text(material.get("observation"), "materials.observation：实际边缘、遮挡或隐藏面的观察")
        if material.get("motion") not in {"fixed", "move", "reveal"}:
            raise R.WakeError("materials.motion 使用 fixed/move/reveal")
        if material["motion"] != "fixed" and material.get("complete") is not True:
            raise R.WakeError(f"运动素材尚不完整：{material['path']}；先补素材或改动作")
    if R.sha(root / "src/index.html") == data["template_index_sha256"]:
        raise R.WakeError("仍是上游初始模板；请由 Agent 实现本图的动作后再预览")
    return plan


def visual_materials(root: Path, plan: dict) -> list[Path]:
    """Keep fonts in the bound inventory, but do not pass them to Pillow's image sheet."""
    files = [local(root, m["path"]) for m in plan["materials"]]
    pictures = [p for p in files if p.suffix.lower() not in FONT_MATERIALS]
    if not pictures:
        raise R.WakeError("素材联系表至少需要原图或一张图片；字体不能代替视觉素材")
    return pictures


def preview(path: Path) -> dict:
    config = R.settings()
    root, data = project(path)
    plan = validate_director(root, data)
    pictures = visual_materials(root, plan)
    before = snapshot(root, data)
    out = next_dir(root, "previews")
    out.mkdir(parents=True)
    shutil.copytree(root / "src", out / "src", ignore=shutil.ignore_patterns(*GENERATED))
    if source_hashes(out / "src") != before["src"]:
        raise R.WakeError("拷贝期间源码变化，请重新预览")
    times = list(dict.fromkeys(plan["review_times"]))
    inspect = ["node", str(R.ROOT / "scripts/stylebook/assets/inspect-wake.mjs"),
               config["wake_skill"], str(out / "src"), json.dumps(times)]
    R.run(inspect, cwd=root, log=out / "inspect.json", config=config)
    inspection = R.read_json(out / "inspect.json")
    cfg = inspection["cfg"]
    if cfg.get("fps") != data["output"]["fps"] or cfg.get("dur") != data["output"]["duration"]:
        raise R.WakeError("window.CFG 的 fps/dur 与 motion.json 不符；同时更新工程规格再预览")
    if data["output"]["size"] != "source":
        # Compare exact dimensions only; --size may also be a ratio understood by upstream.
        size = data["output"]["size"].lower().split("x")
        if len(size) == 2 and [int(v) for v in size] != [cfg["outW"], cfg["outH"]]:
            raise R.WakeError("window.CFG 画幅与 motion.json 不符")
    R.run(["node", str(Path(config["wake_skill"]) / "scripts/render.mjs"), str(out / "src"), "stills", *map(str, times)],
          cwd=root, log=out / "render-stills.log", config=config)
    R.run(R.python_command(config, "sheet.py", out / "timeline.jpg", "--stills", out / "src", *times, "--cols", "3"),
          cwd=root, log=out / "timeline.log", config=config)
    R.run(R.python_command(config, "sheet.py", out / "materials.jpg", *pictures, "--cols", "3"),
          cwd=root, log=out / "materials.log", config=config)
    if snapshot(root, data) != before:
        raise R.WakeError("预览期间输入或工程变化，请重新预览")
    record = {"state": "preview-needs-visual-inspection", "created_at": stamp(), "snapshot": before,
              "times": times, "cfg": cfg, "evidence": {p.name: R.sha(p) for p in (out / "timeline.jpg", out / "materials.jpg", out / "inspect.json")}}
    R.write_json(out / "preview.json", record)
    return {"state": record["state"], "preview": str(out), "view": [str(out / "materials.jpg"), str(out / "timeline.jpg")],
            "next": "由当前 Agent 查看原图、分件和极值帧；有问题先修，合适再执行 animate wake render <工程>。"}


def check_files(root: Path, files, required: set[str], label: str) -> None:
    if not isinstance(files, dict) or set(files) != required:
        raise R.WakeError(f"{label}缺少完整文件清单")
    for rel, digest in files.items():
        p = local(root, rel)
        if p.stat().st_size == 0 or R.sha(p) != digest:
            raise R.WakeError(f"{label}已被修改或为空：{rel}")


def latest_preview(root: Path, current: dict) -> tuple[Path, dict]:
    for p in sorted((root / "previews").glob("*/preview.json"), reverse=True):
        record = R.read_json(p)
        if record.get("snapshot") == current:
            check_files(p.parent, record.get("evidence"), PREVIEW_FILES, "预览证据")
            return p.parent, record
    raise R.WakeError("缺少与当前输入、导演稿及源码一致的预览；先执行 animate wake preview")


def review_page(root: Path, data: dict, out: Path) -> None:
    image = out / ("original" + Path(data["original"]).suffix)
    shutil.copyfile(local(root, data["original"]), image)
    title = html.escape(data["purpose"])
    image_name = html.escape(image.name, quote=True)
    (out / "review.html").write_text(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#101914;color:#eee9dd;font:16px/1.7 system-ui}}main{{max-width:1320px;margin:auto;padding:32px 20px}}h1{{font-size:25px}}.pair{{display:flex;align-items:stretch;gap:6px;position:relative}}.pair img{{width:24%;object-fit:contain;background:#18251e;border-radius:16px}}video{{width:calc(76% - 6px);background:#18251e;border-radius:16px}}a{{color:#81d5b5}}.sheet{{max-width:100%;margin-top:24px}}small{{color:#b7c5bc}}@media(max-width:650px){{.pair{{flex-direction:column}}.pair img{{width:40%;max-height:35vh;align-self:center}}video{{width:100%}}}}</style>
<main><h1>{title}</h1><small>原图 → 动态候选。制作审片与用户采用分别记录。</small>
<div class="pair"><img src="{image_name}" alt="完整原图"><video src="motion.mp4" muted autoplay loop playsinline controls></video></div>
<p><a href="motion.gif" download>GIF</a> · <a href="motion.webp" download>WebP</a> · <a href="motion.mp4" download>MP4</a></p>
<img class="sheet" src="timeline.jpg" alt="关键状态联系表">
<script>const v=document.querySelector('video');const r=matchMedia('(prefers-reduced-motion: reduce)');if(r.matches)v.pause();new IntersectionObserver(e=>{{if(e[0].isIntersecting&&!r.matches)v.play().catch(()=>{{}});else v.pause()}}).observe(v);document.addEventListener('visibilitychange',()=>{{if(document.hidden)v.pause()}});</script></main></html>''', encoding="utf-8")


def render(path: Path) -> dict:
    config = R.settings()
    root, data = project(path)
    plan = validate_director(root, data)
    current = snapshot(root, data)
    preview_dir, record = latest_preview(root, current)
    out = next_dir(root, "renders")
    from replay_wake import main as replay
    captured = io.StringIO()
    args = [str(root / "src"), "--wake-skill", config["wake_skill"], "--out", str(out),
            "--fps", str(data["output"]["fps"]), "--stills", *map(str, record["times"])]
    if plan.get("reset"):
        reset = plan["reset"]
        args += ["--reset", reset["kind"], "--hold", str(reset.get("hold", 0.7)),
                 "--transition", str(reset.get("duration", 0.6))]
    # The replay CLI remains available independently for existing prepared projects.
    with contextlib.redirect_stdout(captured):
        with _runtime_env(config):
            code = replay(args)
    if out.exists():
        (out / "workflow-render.log").write_text(captured.getvalue(), encoding="utf-8")
    if code:
        raise R.WakeError(f"渲染失败，保留已有结果与日志：{out}")
    if snapshot(root, data) != current:
        raise R.WakeError("渲染期间输入或源码变化，本次不签发有效候选")
    receipt = R.read_json(out / "replay.json")
    if receipt["input_sha256"] != current["src"]:
        raise R.WakeError("渲染实际使用的源码与预览不一致")
    for name in ("timeline.jpg", "materials.jpg"):
        shutil.copyfile(preview_dir / name, out / name)
    for name in ("UPSTREAM.md", "director.json"):
        shutil.copyfile(root / name, out / name)
    R.write_json(out / "review-template.json", {"verdict": "pending", "reviewer": "", "playback": {"cycles": 0, "method": ""},
        "checks": {k: {"result": "pending", "note": ""} for k in CHECKS},
        "evidence": ["timeline.jpg", "materials.jpg"], "issues": [], "platform": {"state": "untested", "note": "本文件尚未实际上传测试"}})
    review_page(root, data, out)
    candidate = {"state": "rendered-needs-visual-review", "created_at": stamp(), "snapshot": current,
                 "preview": str(preview_dir.relative_to(root)), "preview_sha256": R.sha(preview_dir / "preview.json"),
                 "files": {name: R.sha(out / name) for name in sorted(RENDER_FILES)}}
    R.write_json(out / "candidate.json", candidate)
    return {"state": candidate["state"], "render": str(out), "review_page": str(out / "review.html"),
            "next": "完整播放并查看原尺寸关键帧，填写审片 JSON，再执行 animate wake review <工程> --render <目录> --assessment <JSON>。"}


@contextlib.contextmanager
def _runtime_env(config):
    import os
    keys = ("LEMO_WAKE_MODELS", "CHROME")
    old = {k: os.environ.get(k) for k in keys}
    env = R.environment(config)
    try:
        for k in keys:
            if k in env:
                os.environ[k] = env[k]
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def check_candidate(root: Path, out: Path, current: dict) -> dict:
    if out.parent != root / "renders":
        raise R.WakeError("须指定本工程 renders/ 下的一次候选目录")
    candidate = R.read_json(out / "candidate.json")
    if candidate.get("snapshot") != current:
        raise R.WakeError("输入、导演稿或工程已变化；旧渲染不能继续采用")
    check_files(out, candidate.get("files"), RENDER_FILES, "成片或审片材料")
    proof = local(root, candidate.get("preview", "") + "/preview.json")
    if R.sha(proof) != candidate.get("preview_sha256"):
        raise R.WakeError("候选对应的预览记录已变化，请重新渲染")
    return candidate


def review(path: Path, render_dir: Path, assessment: Path) -> dict:
    root, data = project(path)
    out = render_dir.expanduser().resolve()
    candidate = check_candidate(root, out, snapshot(root, data))
    report = R.read_json(assessment.expanduser().resolve())
    if report.get("verdict") not in {"accept", "revise"}:
        raise R.WakeError("审片结论使用 accept/revise；空白或 pending 不算通过")
    if report["verdict"] == "accept":
        qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
        if (not isinstance(qa, list) or not qa or
                any(not isinstance(item, dict) or item.get("level") not in {"PASS", "WARN", "FAIL"} for item in qa)):
            raise R.WakeError("机器 QA 为空或无效，不能采用")
        if any(item["level"] == "FAIL" for item in qa):
            raise R.WakeError("机器 QA 仍有 FAIL，先修复后重新渲染；可以如实记录返修")
    text(report.get("reviewer"), "reviewer")
    playback = report.get("playback", {})
    if not isinstance(playback, dict) or type(playback.get("cycles")) is not int or playback["cycles"] < 0:
        raise R.WakeError("须记录实际播放轮数，未播放时为 0")
    if report["verdict"] == "accept" and playback["cycles"] < 2:
        raise R.WakeError("采用须记录至少两轮实际连续播放；联系表不替代播放")
    text(playback.get("method"), "playback.method")
    checks = report.get("checks", {})
    if not isinstance(checks, dict):
        raise R.WakeError("checks 须为逐项审片对象")
    for key in CHECKS:
        item = checks.get(key, {})
        allowed = {"pass", "fail", "na"} if report["verdict"] == "accept" else {"pass", "fail", "na", "pending"}
        if not isinstance(item, dict) or item.get("result") not in allowed:
            raise R.WakeError(f"审片缺少有效检查：{key}")
        text(item.get("note"), key)
        if report["verdict"] == "accept" and (item["result"] == "fail" or (key != "legibility" and item["result"] == "na")):
            raise R.WakeError("存在未通过的必要视觉项，不能采用；先返修")
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise R.WakeError("须保留实际查看的审片材料")
    bound = {name: R.sha(local(out, name)) for name in evidence}
    if report["verdict"] == "revise":
        issues = report.get("issues")
        if not isinstance(issues, list) or not issues or any(not isinstance(i, str) or not i.strip() for i in issues):
            raise R.WakeError("返修须记录问题与下一步修改位置")
        if not any(item["result"] == "fail" for item in checks.values() if isinstance(item, dict)):
            raise R.WakeError("返修须指出至少一项实际未通过的检查；尚未观看不是已审完")
    platform = report.get("platform", {})
    if not isinstance(platform, dict) or platform.get("state") not in {"untested", "tested"}:
        raise R.WakeError("platform.state 须明确 tested/untested")
    text(platform.get("note"), "platform.note")
    if platform["state"] == "tested":
        proof = platform.get("evidence")
        if not isinstance(proof, list) or not proof:
            raise R.WakeError("平台实测须有本文件对应的证据")
        bound.update({p: R.sha(local(out, p)) for p in proof})
    state = "agent-reviewed-candidate" if report["verdict"] == "accept" else "needs-revision"
    receipt = {"state": state, "reviewed_at": stamp(), "candidate_sha256": R.sha(out / "candidate.json"),
               "assessment": report, "evidence_sha256": bound, "user_adoption": "not-recorded"}
    target = next_dir(out, "reviews")
    target.mkdir(parents=True)
    R.write_json(target / "review.json", receipt)
    return {"state": state, "review": str(target / "review.json"), "files": candidate["files"],
            "platform": platform, "user_adoption": "not-recorded"}


def status(path: Path) -> dict:
    root, data = project(path)
    plan = R.read_json(root / "director.json")
    if plan.get("decision") == "static":
        return {"state": "keep-static", "reason": text(plan.get("why_motion"), "保留静态的理由"),
                "original": str(local(root, data["original"]))}
    try:
        validate_director(root, data)
        current = snapshot(root, data)
        preview_dir, _ = latest_preview(root, current)
        for p in sorted((root / "renders").glob("*/candidate.json"), reverse=True):
            candidate = R.read_json(p)
            if candidate.get("snapshot") != current:
                continue
            try:
                check_candidate(root, p.parent, current)
            except R.WakeError as exc:
                return {"state": "needs-render", "render": str(p.parent), "reason": str(exc)}
            reviews = sorted((p.parent / "reviews").glob("*/review.json"), reverse=True)
            if reviews:
                latest = R.read_json(reviews[0])
                evidence = latest.get("evidence_sha256")
                if latest.get("candidate_sha256") == R.sha(p) and isinstance(evidence, dict) and evidence:
                    check_files(p.parent, evidence, set(evidence), "审片证据")
                    return {"state": latest["state"], "render": str(p.parent), "review": str(reviews[0]),
                            "user_adoption": latest.get("user_adoption", "not-recorded")}
            return {"state": "rendered-needs-visual-review", "render": str(p.parent),
                    "review_page": str(p.parent / "review.html"), "next": "完整播放两轮，再填写并提交 review"}
        return {"state": "preview-current", "preview": str(preview_dir), "next": "查看预览，再 render；成片需单独 review"}
    except R.WakeError as exc:
        return {"state": "needs-preparation", "next": str(exc)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="叁笙生图：原图到动态的统一工作流，由当前 Agent 导演与审片")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--wake-skill", type=Path); p.add_argument("--install", action="store_true")
    p.add_argument("--models-dir", type=Path); p.add_argument("--chrome", type=Path)
    p.add_argument("--node-deps", action="store_true"); p.add_argument("--disable", action="store_true")
    sub.add_parser("doctor")
    p = sub.add_parser("init")
    p.add_argument("image", type=Path); p.add_argument("--out", "-o", type=Path, required=True)
    p.add_argument("--purpose", required=True); p.add_argument("--scene", choices=tuple(SCENES))
    p.add_argument("--article", type=Path); p.add_argument("--brief", type=Path); p.add_argument("--item")
    p.add_argument("--size"); p.add_argument("--duration", type=float, default=6); p.add_argument("--fps", type=int, default=30)
    p = sub.add_parser("tool")
    p.add_argument("project", type=Path)
    p.add_argument("tool", choices=("depth", "segment", "inpaint", "sheet", "measure", "models"))
    p.add_argument("args", nargs=argparse.REMAINDER)
    for command in ("preview", "render", "status", "review"):
        p = sub.add_parser(command); p.add_argument("project", type=Path)
        if command == "review":
            p.add_argument("--render", type=Path, required=True); p.add_argument("--assessment", type=Path, required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == "setup":
            result = R.setup(skill=a.wake_skill, install=a.install, models=a.models_dir, chrome=a.chrome,
                             node_deps=a.node_deps, disable=a.disable)
        elif a.command == "doctor":
            result = R.doctor()
        elif a.command == "init":
            result = init(a)
        elif a.command == "tool":
            config = R.settings(); root, _ = project(a.project)
            log_dir = next_dir(root, "tool-runs"); log_dir.mkdir(parents=True)
            command = R.python_command(config, a.tool + ".py", *a.args)
            R.write_json(log_dir / "command.json", {"at": stamp(), "command": command, "revision": R.LOCK["revision"]})
            R.run(command, cwd=root, log=log_dir / "output.log", config=config)
            result = {"state": "tool-completed-needs-inspection", "log": str(log_dir / "output.log"),
                      "output": (log_dir / "output.log").read_text(encoding="utf-8")[-2000:]}
        elif a.command == "preview":
            result = preview(a.project)
        elif a.command == "render":
            result = render(a.project)
        elif a.command == "review":
            result = review(a.project, a.render, a.assessment)
        else:
            result = status(a.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("state") == "missing-dependencies" else 0
    except (R.WakeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
