"""官网首页「让图动起来」：动态示范清单的严格校验、媒体来源核对与构建输出。

源码三件（gallery/motion.js、motion.css、motion-data.json）进 Git 和公开包；媒体（每个示范一张 poster.png 和一支 share.mp4）
不进 Git、不进下载包，由外置目录 <motion_dir>/<id>/{poster.png,share.mp4} 提供，构建时逐个核对 SHA-256 后复制到 <out>/motion/。
没给外置目录（公开无媒体包）时只登记同样的期望清单，不造媒体也不报错，因此公开包能重建出与完整仓库相同的页面与清单。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from . import contract as CT

GALLERY_DIR = CT.ROOT / "gallery"
SOURCE_FILES = ("motion.js", "motion.css", "motion-data.json")
DATA_NAME = "motion-data.json"
MANIFEST_NAME = "motion-manifest.json"
MEDIA_DIR = "motion"
MOTION_DIR_ENV = "SANSHENG_IMAGE_MOTION_DIR"
MEDIA_URL_PREFIX = "/tools/image/motion/"
MEDIA_FILES = (("poster", "poster.png", "poster_sha256"), ("video", "share.mp4", "video_sha256"))
OPTIONAL_MEDIA_FILES = (("source", "source.png", "source_sha256"), ("gif", "share.gif", "gif_sha256"))
MAX_ITEMS = 12
SCHEMA_VERSION = 1
MIN_DIM, MAX_DIM = 16, 8000          # 与 motion.js 的 dim() 同口径
MAX_DURATION = 60

REQUIRED_KEYS = frozenset({"id", "title", "scene", "scene_label", "width", "height", "duration",
                           "poster", "video", "poster_sha256", "video_sha256"})
OPTIONAL_KEYS = frozenset({"style_code", "homepage_scene", "note", "placement",
                           "source", "source_sha256", "gif", "gif_sha256",
                           "category", "tags", "context", "source_label"})
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
STYLE_RE = re.compile(r"C\d{1,3}")
HOME_SCENE_RE = re.compile(r"[a-z0-9_-]{1,32}")
SHA_RE = re.compile(r"[0-9a-f]{64}")


# ---------------- 解析与校验 ----------------

def _no_dup(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"JSON 对象里有重复键：{key}")
        out[key] = value
    return out


def _no_constant(name):
    raise ValueError(f"JSON 不允许 {name}")


def parse_text(text) -> dict:
    """文本 → 已通过严格校验的清单。重复键、NaN/Infinity、非 UTF-8、结构不符一律抛 ValueError。"""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("动态清单不是 UTF-8 文本") from exc
    if not isinstance(text, str):
        raise ValueError("动态清单必须是文本")
    try:
        doc = json.loads(text, object_pairs_hook=_no_dup, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"动态清单不是有效 JSON：{exc}") from exc
    problems = validate_document(doc)
    if problems:
        raise ValueError("动态清单校验不通过：" + "；".join(problems[:20]))
    return doc


def _is_int(v) -> bool:
    return type(v) is int          # 排除 bool 与 float


def _text(v, hi: int) -> bool:
    return isinstance(v, str) and 1 <= len(v) <= hi and v.strip() != ""


def _item_problems(it, where: str) -> list[str]:
    if not isinstance(it, dict):
        return [f"{where} 必须是对象"]
    p: list[str] = []
    unknown = sorted(str(k) for k in set(it) - ALLOWED_KEYS)
    missing = sorted(REQUIRED_KEYS - set(it))
    if unknown:
        p.append(f"{where} 有不允许的字段 {unknown}")
    if missing:
        p.append(f"{where} 缺少字段 {missing}")
    iid = it.get("id")
    id_ok = isinstance(iid, str) and ID_RE.fullmatch(iid) is not None
    if "id" in it and not id_ok:
        p.append(f"{where}.id 不合法")
    for key, hi in (("title", 60), ("scene", 32), ("scene_label", 20)):
        if key in it and not _text(it[key], hi):
            p.append(f"{where}.{key} 必须是 1..{hi} 字的非空白文本")
    if "note" in it and not _text(it["note"], 240):
        p.append(f"{where}.note 必须是 1..240 字的非空白文本")
    if "source_label" in it and not _text(it["source_label"], 24):
        p.append(f"{where}.source_label 必须是 1..24 字的非空白文本")
    if "category" in it and not (isinstance(it["category"], str) and ID_RE.fullmatch(it["category"])):
        p.append(f"{where}.category 不合法")
    if "tags" in it:
        tags = it["tags"]
        if (not isinstance(tags, list) or not 1 <= len(tags) <= 4 or
                any(not _text(t, 16) for t in tags) or len(set(t for t in tags if isinstance(t, str))) != len(tags)):
            p.append(f"{where}.tags 必须是 1..4 个不重复的短标签")
    if "context" in it:
        context = it["context"]
        limits = {"title": 100, "excerpt": 1200, "purpose": 300}
        if (not isinstance(context, dict) or set(context) != set(limits) or
                any(not _text(context.get(k), hi) for k, hi in limits.items())):
            p.append(f"{where}.context 须提供 title、excerpt、purpose 三段有效文本")
    if "placement" in it and it["placement"] not in ("featured", "brand", "background"):
        p.append(f"{where}.placement 必须是 featured、brand 或 background")
    sc = it.get("style_code")
    if sc is not None and not (isinstance(sc, str) and STYLE_RE.fullmatch(sc)):
        p.append(f"{where}.style_code 不合法")
    hs = it.get("homepage_scene")
    if hs is not None and not (isinstance(hs, str) and HOME_SCENE_RE.fullmatch(hs)):
        p.append(f"{where}.homepage_scene 不合法")
    for key in ("width", "height"):
        if key in it and not (_is_int(it[key]) and MIN_DIM <= it[key] <= MAX_DIM):
            p.append(f"{where}.{key} 必须是 {MIN_DIM}..{MAX_DIM} 的整数")
    if "duration" in it:
        d = it["duration"]
        if not (type(d) in (int, float) and math.isfinite(d) and 0 < d <= MAX_DURATION):
            p.append(f"{where}.duration 必须是大于 0 且不超过 {MAX_DURATION} 的数")
    for field, name, sha_key in MEDIA_FILES:
        if field in it:
            expected = f"{MEDIA_URL_PREFIX}{iid}/{name}" if id_ok else None
            if expected is None or it[field] != expected:
                p.append(f"{where}.{field} 必须恰好是 {MEDIA_URL_PREFIX}<id>/{name}")
        if sha_key in it and not (isinstance(it[sha_key], str) and SHA_RE.fullmatch(it[sha_key])):
            p.append(f"{where}.{sha_key} 必须是小写 64 位十六进制")
    for field, name, sha_key in OPTIONAL_MEDIA_FILES:
        if (field in it) != (sha_key in it):
            p.append(f"{where}.{field} 与 {sha_key} 必须同时提供")
        if field in it:
            if not id_ok or it[field] != f"{MEDIA_URL_PREFIX}{iid}/{name}":
                p.append(f"{where}.{field} 必须恰好是 {MEDIA_URL_PREFIX}<id>/{name}")
            if not (isinstance(it.get(sha_key), str) and SHA_RE.fullmatch(it[sha_key])):
                p.append(f"{where}.{sha_key} 必须是小写 64 位十六进制")
    return p


def item_media(it):
    return MEDIA_FILES + tuple(row for row in OPTIONAL_MEDIA_FILES if row[0] in it)


def validate_document(doc) -> list[str]:
    """返回问题列表（空表示通过）。任何类型的输入都不抛异常。"""
    if not isinstance(doc, dict):
        return ["顶层必须是对象"]
    problems: list[str] = []
    unknown = sorted(str(k) for k in set(doc) - {"schema_version", "items", "categories", "default_category", "showcase"})
    if unknown:
        problems.append(f"顶层有不允许的字段 {unknown}")
    if not (_is_int(doc.get("schema_version")) and doc["schema_version"] == SCHEMA_VERSION):
        problems.append(f"schema_version 必须是 {SCHEMA_VERSION}")
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items 必须是非空数组")
        return problems
    if len(items) > MAX_ITEMS:
        problems.append(f"items 最多 {MAX_ITEMS} 项（现在 {len(items)}）")
    portrait_showcase = doc.get("showcase") == "portrait-to-landscape"
    if "showcase" in doc and not portrait_showcase:
        problems.append("showcase 只支持 portrait-to-landscape")
    categories: set[str] = set()
    if "categories" in doc or "default_category" in doc:
        rows = doc.get("categories")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
            problems.append("categories 必须是 1..8 项的非空数组")
        else:
            for i, row in enumerate(rows):
                if (not isinstance(row, dict) or set(row) != {"id", "label", "description"} or
                        not isinstance(row.get("id"), str) or not ID_RE.fullmatch(row["id"]) or row["id"] == "all" or
                        not _text(row.get("label"), 20) or not _text(row.get("description"), 240)):
                    problems.append(f"categories[{i}] 须有有效 id、label、description，id 不能为 all")
                    continue
                if row["id"] in categories:
                    problems.append(f"categories[{i}].id 重复")
                categories.add(row["id"])
        if not isinstance(doc.get("default_category"), str) or doc["default_category"] not in categories:
            problems.append("default_category 须指向已定义的分类")
    seen: set[str] = set()
    for i, it in enumerate(items):
        problems += _item_problems(it, f"items[{i}]")
        iid = it.get("id") if isinstance(it, dict) else None
        if isinstance(iid, str):
            if iid in seen:
                problems.append(f"items[{i}].id 重复：{iid}")
            seen.add(iid)
        if isinstance(it, dict):
            category = it.get("category")
            if category is not None and (not isinstance(category, str) or category not in categories):
                problems.append(f"items[{i}].category 未在 categories 中定义")
            if categories and it.get("placement", "featured") == "featured" and category is None:
                problems.append(f"items[{i}] 精选作品缺少 category")
    if categories:
        used = {it.get("category") for it in items if isinstance(it, dict) and
                it.get("placement", "featured") == "featured" and isinstance(it.get("category"), str)}
        if doc.get("default_category") not in used:
            problems.append("默认分类须至少有一件精选作品")
    if portrait_showcase:
        if not categories:
            problems.append("分类卡片作品墙须定义 categories")
        for category in categories:
            count = sum(isinstance(it, dict) and it.get("placement", "featured") == "featured"
                        and it.get("category") == category for it in items)
            if not 1 <= count <= 2:
                problems.append(f"分类 {category} 须有 1..2 件精选作品")
        for i, it in enumerate(items):
            if not isinstance(it, dict) or it.get("placement", "featured") != "featured":
                continue
            if "source" not in it:
                problems.append(f"items[{i}] 分类卡片作品须提供完整 9:16 原图")
            w, h = it.get("width"), it.get("height")
            if not (_is_int(w) and _is_int(h) and h > 0 and abs(w / h / (16 / 9) - 1) <= .01):
                problems.append(f"items[{i}] 分类卡片动态须为 16:9")
    return problems


def check_scenes(doc: dict, scenes) -> None:
    """homepage_scene 非空的示范，必须落在页面真实场景里，且画风属于该场景的静态画风池（即有静态资格）；
    homepage_scene 为空的保持动态示范，不会因此给场景多加静态筛选。"""
    by = {s.get("id"): s for s in scenes if isinstance(s, dict)}
    problems = []
    for it in doc["items"]:
        hs = it.get("homepage_scene")
        if hs is None:
            continue
        sc = by.get(hs)
        if sc is None:
            problems.append(f"{it['id']} 的 homepage_scene「{hs}」不是页面场景")
        elif it.get("style_code") not in (sc.get("pool") or []):
            problems.append(f"{it['id']} 的画风 {it.get('style_code')} 不在场景「{hs}」的画风池里")
    if problems:
        raise ValueError("动态示范与页面场景对不上：" + "；".join(problems))


# ---------------- 媒体与清单 ----------------

def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def expected_media(doc: dict) -> list[tuple[str, str]]:
    """(相对输出目录的路径, 期望 SHA-256)，按路径排序。"""
    out = [(f"{MEDIA_DIR}/{it['id']}/{name}", it[sha_key])
           for it in doc["items"] for _field, name, sha_key in item_media(it)]
    return sorted(out)


def read_media(doc: dict, motion_dir) -> dict[str, bytes]:
    """读出并核对全部媒体：必须齐全、是普通文件（非符号链接）、SHA 与清单一致。问题一次列全，任何一个不对都不返回。"""
    root = Path(motion_dir).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"动态媒体目录不存在或不是目录：{root}")
    problems: list[str] = []
    out: dict[str, bytes] = {}
    for it in doc["items"]:
        idir = root / it["id"]
        if idir.is_symlink():
            problems.append(f"{it['id']}/ 是符号链接")
            continue
        for _field, name, sha_key in item_media(it):
            rel = f"{MEDIA_DIR}/{it['id']}/{name}"
            path = idir / name
            try:
                st = os.lstat(path)
            except OSError:
                problems.append(f"缺少媒体 {rel}")
                continue
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"{rel} 是符号链接")
            elif not stat.S_ISREG(st.st_mode):
                problems.append(f"{rel} 不是普通文件")
            else:
                try:
                    raw = path.read_bytes()
                except OSError:
                    problems.append(f"{rel} 读不出来")
                    continue
                if _sha(raw) != it[sha_key]:
                    problems.append(f"{rel} 的 SHA-256 与清单不符")
                else:
                    if (doc.get("showcase") == "portrait-to-landscape"
                            and it.get("placement", "featured") == "featured"
                            and _field in ("source", "poster")):
                        from PIL import Image
                        try:
                            with Image.open(io.BytesIO(raw)) as image:
                                width, height = image.size
                                image.verify()
                            ratio = 9 / 16 if _field == "source" else 16 / 9
                            if abs(width / height / ratio - 1) > .01:
                                problems.append(f"{rel} 实际比例须为 {'9:16' if _field == 'source' else '16:9'}")
                        except (OSError, ValueError, ZeroDivisionError):
                            problems.append(f"{rel} 不是可读取的图片")
                    out[rel] = raw
    if problems:
        raise ValueError("动态媒体校验不通过：" + "；".join(problems))
    return out


def build_manifest(doc: dict, sources: dict[str, bytes]) -> dict:
    return {"schema_version": 1,
            "sources": [{"path": n, "sha256": _sha(sources[n])} for n in SOURCE_FILES],
            "media": [{"path": rel, "sha256": sha} for rel, sha in expected_media(doc)]}


def manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"


@dataclass(frozen=True)
class Plan:
    doc: dict
    sources: dict          # 源码文件名 -> 字节
    manifest: dict
    media: dict | None     # 相对路径 -> 字节；None 表示没给外置目录，只登记清单


def _check_out(out_dir: Path) -> None:
    """输出位置不能让我们写穿符号链接或覆盖目录。"""
    target = out_dir / MEDIA_DIR
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError(f"输出位置 {target} 不是普通目录，不写入")
    for name in (*SOURCE_FILES, MANIFEST_NAME):
        p = out_dir / name
        if p.is_symlink() or p.is_dir():
            raise ValueError(f"输出位置 {p} 不是普通文件，不写入")


def prepare(motion_dir=None, out_dir=None, gallery_dir=None) -> Plan:
    """写出任何东西之前完成所有能做的校验：源码文件、清单结构、媒体来源与 SHA、输出位置。"""
    gdir = Path(gallery_dir) if gallery_dir is not None else GALLERY_DIR
    sources: dict[str, bytes] = {}
    for name in SOURCE_FILES:
        p = gdir / name
        if p.is_symlink() or not p.is_file():
            raise ValueError(f"缺少动态展示源码 {name}：{gdir}")
        sources[name] = p.read_bytes()
    try:
        doc = parse_text(sources[DATA_NAME])
    except ValueError as exc:
        raise ValueError(f"{DATA_NAME}：{exc}") from exc
    if motion_dir is None:
        motion_dir = os.environ.get(MOTION_DIR_ENV) or None
    media = read_media(doc, motion_dir) if motion_dir is not None else None
    if out_dir is not None:
        _check_out(Path(out_dir))
    return Plan(doc=doc, sources=sources, manifest=build_manifest(doc, sources), media=media)


def write(plan: Plan, out_dir) -> None:
    """只写自己的资源：三份源码、清单，以及（给了外置目录时）motion/ 下的媒体；不碰输出目录里的其他文件。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _check_out(out_dir)
    for name, raw in plan.sources.items():
        (out_dir / name).write_bytes(raw)
    (out_dir / MANIFEST_NAME).write_text(manifest_text(plan.manifest), encoding="utf-8")
    if plan.media is not None:
        target = out_dir / MEDIA_DIR
        if target.exists():
            shutil.rmtree(target)
        for rel, raw in sorted(plan.media.items()):
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
