"""Export a reviewed, self-contained article-media candidate; adoption remains separate."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile

from . import wake_runtime as R

CONTRACT = "sansheng.article-motion/1"


def export(project: Path, render_dir: Path, destination: Path, anchor: str) -> dict:
    from PIL import Image
    from . import wake_workflow as W

    root, data = W.project(project)
    out = render_dir.expanduser().resolve()
    W.check_candidate(root, out, W.snapshot(root, data))
    current = W.status(root)
    if current.get("state") != "agent-reviewed-candidate" or Path(current["render"]) != out:
        raise R.WakeError("只有当前已完成技术与实际审片的候选才能交回文章；用户采用仍须单独记录")
    article = W.local(root, "inputs/article.md")
    if not anchor.strip() or article.read_text(encoding="utf-8").count(anchor) != 1:
        raise R.WakeError("动态插图锚点须在冻结文章中唯一命中")
    review = Path(current["review"])
    report = R.read_json(review)
    if report["assessment"].get("verdict") != "accept" or report["assessment"].get("playback", {}).get("cycles", 0) < 2:
        raise R.WakeError("缺少实际完整播放两轮的通过记录")
    target = destination.expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise R.WakeError("动态交接目录已存在，不覆盖旧候选")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="article-motion-", dir=target.parent) as temporary:
        stage = Path(temporary) / "handoff"
        stage.mkdir()
        copied = {"gif": out / "motion.gif", "preview": out / "motion.mp4", "review": review,
                  "candidate": out / "candidate.json",
                  "timeline": out / "timeline.jpg", "upstream": out / "UPSTREAM.md"}
        names = {"gif": "motion.gif", "preview": "motion.mp4", "review": "review.json",
                 "timeline": "timeline.jpg", "upstream": "UPSTREAM.md", "poster": "poster.png",
                 "candidate": "candidate.json"}
        for key, source in copied.items():
            shutil.copy2(source, stage / names[key])
        with Image.open(stage / "motion.gif") as gif:
            if gif.format != "GIF" or gif.n_frames < 2:
                raise R.WakeError("动态交接需要真实的多帧 GIF")
            metadata = {"width": gif.width, "height": gif.height, "frames": gif.n_frames,
                        "loop": gif.info.get("loop"), "durations_ms": []}
            gif.seek(0)
            gif.convert("RGBA").save(stage / "poster.png")
            for i in range(gif.n_frames):
                gif.seek(i)
                metadata["durations_ms"].append(gif.info.get("duration", 0))
        files = {key: {"path": name, "sha256": R.sha(stage / name), "bytes": (stage / name).stat().st_size}
                 for key, name in names.items()}
        inherited = data.get('inherit', {})
        style = inherited.get('item', {}).get('compiled', {}).get('style') or inherited.get('style')
        manifest = {"schema_version": 1, "contract": CONTRACT, "state": "reviewed_pending_user_adoption",
                    "source": {"article_sha256": R.sha(article), "static_sha256": data["inputs"][data["original"]],
                               "anchor": anchor, "style": style, "palette": inherited.get('palette')},
                    "purpose": data["purpose"], "files": files, "animation": metadata,
                    "candidate_sha256": R.sha(out / "candidate.json"), "platform": report["assessment"]["platform"],
                    "upstream_revision": data["upstream_revision"], "upstream_license": data["upstream_license"],
                    "exporter_sha256": R.sha(Path(__file__))}
        R.write_json(stage / "article-media.json", manifest)
        # Recheck after reading/copying; a changed project cannot produce a seemingly current handoff.
        W.check_candidate(root, out, W.snapshot(root, data))
        if R.sha(review) != files["review"]["sha256"]:
            raise R.WakeError("交接期间审片记录变化")
        stage.rename(target)
    return {"state": manifest["state"], "manifest": str(target / "article-media.json"),
            "contract": CONTRACT, "user_adoption": "not-recorded"}
