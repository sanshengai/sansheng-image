#!/usr/bin/env python3
"""叁笙生图「动态版」本机渲染封装（原创实现）。

子命令：doctor（环境自检）、plan（只读预演）、render（check → 渲染母版 → 导出）、qa（真实文件复验 + 首末帧接缝 + 联系表）。

边界：
- HyperFrames 只通过 --hyperframes-cli 指定的绝对路径调用，用解析到的 node 绝对路径，argv 列表、不经 shell；
  不用 npx/npm，不联网、不上云、不下载、不发布、不初始化账号、不装 Skill、不改任何 settings。
- 子进程环境变量只放行白名单（无任何 API Key），并强制关闭遥测。
- 不改、不复制用户工程；输出目录必须尚不存在，成品绝不覆盖已有文件。
- preview-webp 选路：ffmpeg 有 libwebp_anim 就用它；否则 ffmpeg 抽帧（只写本次新输出内的专属临时目录，结束即删）+ Pillow 动画 WebP；
  两者都没有就在做任何事之前拒绝。动画 WebP 用 Pillow 读回帧数/尺寸/时长/loop 做行为校验，不声称 ffprobe 验过。
- 只做候选：不声称可直接上传任何平台，不声称透明底，iPhone 实况照片未实现。
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html.parser
import importlib.util
import io
import json
import math
import os
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

HYPERFRAMES_VERSION = "0.8.140"
MIN_NODE_MAJOR = 22          # 0.8.140 的 bin/hyperframes.mjs 自检要求 Node >= 22（dist/runtimeVersion.js），比文档常说的 18 更高
MAX_TIMEOUT = 300
GIF_MAX_BYTES = 10 * 1024 * 1024
WEBP_DEFAULT_MAX_BYTES = 8 * 1024 * 1024     # 本地候选参数，不是任何平台限额
WEBP_MAX_FRAMES = 450                        # Pillow 路径一次最多编码的帧数
WEBP_MAX_RAW_BYTES = 512 * 1024 * 1024       # Pillow 路径内存预算：帧数 × 宽 × 高 × 4（按 RGBA 保守估算），超出直接拒绝
WEBP_READ_MAX_FRAMES = 1000                  # 行为校验最多逐帧读取的帧数
WEBP_FRAME_DIR = ".webp-frames"              # Pillow 路径的临时抽帧目录名，只建在本次新输出目录内，结束即删
ROUTE_FFMPEG = "ffmpeg-libwebp_anim"
ROUTE_PILLOW = "ffmpeg-frames+pillow"
ENV_ALLOW = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "HYPERFRAMES_BROWSER_PATH", "PRODUCER_HEADLESS_SHELL_PATH")
ENV_FORCE = {"DO_NOT_TRACK": "1", "HYPERFRAMES_NO_TELEMETRY": "1", "NO_COLOR": "1"}
SYSTEM_CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium")

SCENES = ("brand", "article", "infographic", "xiaohongshu", "ppt", "music")
FPS_CHOICES = (15, 24, 25, 30, 50, 60)
MANIFEST_KEYS = ("version", "project", "scene", "style_code", "width", "height", "duration", "fps", "loop", "presets")
STYLE_RE = re.compile(r"^C\d{1,3}$")
STATUS_LOCAL = "本机导出；需人工审片"
STATUS_DEVICE = "平台接收待真机验证"

PRESETS: dict[str, dict[str, Any]] = {
    "preview-gif": {"kind": "gif", "file": "preview.gif", "desc": "GIF 预览，体积自检 <= 10 MiB，超限只拒绝并给参数建议"},
    "preview-webp": {"kind": "webp", "file": "preview.webp", "desc": "动画 WebP 预览（不透明底；透明底另行后续，本版不声称透明）"},
    "share-mp4": {"kind": "mp4", "file": "share.mp4", "desc": "H.264 yuv420p faststart 转码，尺寸不变"},
    "xhs-3x4-mp4": {"kind": "mp4", "file": "xhs-3x4.mp4", "desc": "小红书 3:4 候选；要求源本身就是 3:4，不裁横图", "gate": "ratio_3x4"},
    "ppt-mp4-candidate": {"kind": "mp4", "file": "ppt-candidate.mp4", "desc": "PPT 内嵌用 MP4 候选格式；未确定平台/软件限额"},
    "music-loop-mp4-candidate": {"kind": "mp4", "file": "music-loop-candidate.mp4", "desc": "音乐封面循环 MP4 候选格式；要求 loop=true；未确定平台限额", "gate": "loop_true"},
    "iphone-live-photo": {"kind": None, "file": None, "desc": "iPhone 实况照片：未实现（pending）", "pending": True},
}


class PipelineError(Exception):
    exit_code = 1


class ManifestError(PipelineError):
    exit_code = 2


class PathError(PipelineError):
    exit_code = 2


class ToolError(PipelineError):
    exit_code = 1


class StageFailed(PipelineError):
    exit_code = 1


# ───────────────────────── 子进程 ─────────────────────────

@dataclasses.dataclass
class CmdResult:
    argv: list
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None


def _dec(b: Any) -> str:
    if b is None:
        return ""
    return b if isinstance(b, str) else b.decode("utf-8", "replace")


def run_cmd(argv: list, *, timeout: int, cwd: str, env: dict) -> CmdResult:
    """唯一的子进程入口：argv 列表、shell=False、stdin 关闭、带超时。"""
    try:
        p = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired as e:
        return CmdResult(list(argv), None, _dec(e.stdout), _dec(e.stderr), True, f"超时 {timeout}s")
    except OSError as e:
        return CmdResult(list(argv), None, "", "", False, str(e))
    return CmdResult(list(argv), p.returncode, _dec(p.stdout), _dec(p.stderr))


def safe_env(environ: dict, browser: str | None = None) -> dict:
    env = {k: environ[k] for k in ENV_ALLOW if k in environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    env.update(ENV_FORCE)
    if browser:
        env["HYPERFRAMES_BROWSER_PATH"] = browser     # 显式指定，避免 render 在找不到浏览器时自动下载
    return env


def _tail(s: str, n: int = 800) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else "…" + s[-n:]


def _fail_text(r: CmdResult) -> str:
    if r.timed_out or r.error:
        return r.error or "超时"
    return f"退出码 {r.returncode}：{_tail(r.stderr or r.stdout, 500)}"


@dataclasses.dataclass
class Tools:
    node: str
    cli: str | None
    ffmpeg: str
    ffprobe: str
    environ: dict
    cwd: str
    timeout: int = MAX_TIMEOUT
    runner: Callable[..., CmdResult] = run_cmd
    browser: str | None = None
    encoders: set = dataclasses.field(default_factory=set)
    versions: dict = dataclasses.field(default_factory=dict)
    webp_route: str | None = None
    webp_route_detail: str = ""

    def run(self, argv: list) -> CmdResult:
        return self.runner(argv, timeout=self.timeout, cwd=self.cwd, env=safe_env(self.environ))

    def run_hf(self, argv: list) -> CmdResult:
        return self.runner(argv, timeout=self.timeout, cwd=self.cwd, env=safe_env(self.environ, self.browser))

    def cleanup(self) -> None:
        shutil.rmtree(self.cwd, ignore_errors=True)


def _resolve_exe(name: str, given: str | None, environ: dict) -> str:
    found = given or shutil.which(name, path=environ.get("PATH", "/usr/bin:/bin"))
    if not found:
        raise ToolError(f"找不到 {name}（可用 --{name} 指定绝对路径）")
    p = os.path.abspath(found)
    if not (os.path.isfile(p) and os.access(p, os.X_OK)):
        raise ToolError(f"{name} 不是可执行文件：{p}")
    return p


def resolve_tools(cli: str | None, *, node: str | None = None, ffmpeg: str | None = None, ffprobe: str | None = None,
                  timeout: int = MAX_TIMEOUT, runner: Callable[..., CmdResult] = run_cmd, environ: dict | None = None) -> Tools:
    environ = dict(os.environ if environ is None else environ)
    if cli is not None:
        if not os.path.isabs(cli):
            raise ToolError("--hyperframes-cli 必须是绝对路径")
        if not os.path.isfile(cli):
            raise ToolError(f"HyperFrames CLI 不存在：{cli}")
    return Tools(node=_resolve_exe("node", node, environ), cli=cli, ffmpeg=_resolve_exe("ffmpeg", ffmpeg, environ),
                 ffprobe=_resolve_exe("ffprobe", ffprobe, environ), environ=environ,
                 cwd=tempfile.mkdtemp(prefix="motion-pipeline-cwd-"), timeout=timeout, runner=runner)


# ───────────────────────── doctor ─────────────────────────

def find_render_browser(environ: dict) -> tuple[str | None, str | None]:
    """按 HyperFrames 的查找顺序做只读探测（env → puppeteer 缓存 → hyperframes 缓存 → 系统 Chrome）；绝不下载。
    探测是启发式的：渲染实际用哪个由 root 实测确认。"""
    cands: list[tuple[str, Path]] = []
    for k in ("HYPERFRAMES_BROWSER_PATH", "PRODUCER_HEADLESS_SHELL_PATH"):
        if environ.get(k):
            cands.append(("env", Path(environ[k])))
    home = environ.get("HOME")
    if home:
        cache = Path(home) / ".cache"
        for root, pat in ((cache / "puppeteer" / "chrome-headless-shell", "*/chrome-headless-shell-*/chrome-headless-shell"),
                          (cache / "hyperframes" / "chrome", "**/chrome-headless-shell")):
            if root.is_dir():
                cands += [("cache", g) for g in sorted(root.glob(pat), reverse=True)]
    cands += [("system", Path(p)) for p in SYSTEM_CHROME]
    for src, p in cands:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p), src
    return None, None


def _parse_encoders(text: str) -> set:
    names, started = set(), False
    for line in text.splitlines():
        if line.strip().startswith("------"):
            started = True
            continue
        parts = line.split()
        if started and len(parts) >= 2:
            names.add(parts[1])
    return names


def run_doctor(tools: Tools, *, with_hf: bool = True) -> list[dict]:
    """只检查渲染必需项；Docker / 字幕 / TTS 等可选项缺失不算失败，也不检查。Pillow 仅 qa 需要，列为可选。"""
    checks: list[dict] = []

    def add(name: str, required: bool, ok: bool, detail: str) -> None:
        checks.append({"name": name, "required": required, "ok": bool(ok), "detail": detail})

    if with_hf:
        r = tools.run([tools.node, "--version"])
        mt = re.match(r"^v(\d+)\.\d+\.\d+", r.stdout.strip()) if r.returncode == 0 else None
        tools.versions["node"] = r.stdout.strip()
        add("node", True, bool(mt) and int(mt.group(1)) >= MIN_NODE_MAJOR,
            f"{r.stdout.strip()}（要求 >= {MIN_NODE_MAJOR}）" if mt else _fail_text(r))
        pkg_ok, pkg_detail = False, "未读到 package.json"
        try:
            pkg = json.loads((Path(os.path.realpath(tools.cli)).parent.parent / "package.json").read_text(encoding="utf-8"))
            pkg_ok = pkg.get("name") == "hyperframes" and pkg.get("version") == HYPERFRAMES_VERSION
            pkg_detail = f"{pkg.get('name')}@{pkg.get('version')}（要求 hyperframes@{HYPERFRAMES_VERSION}）"
        except (OSError, ValueError):
            pass
        add("hyperframes-package", True, pkg_ok, pkg_detail)
        r = tools.run([tools.node, tools.cli, "--version"])
        add("hyperframes-cli", True, r.returncode == 0 and r.stdout.strip() == HYPERFRAMES_VERSION,
            f"--version → {r.stdout.strip()!r}" if r.returncode == 0 else _fail_text(r))
        browser, source = find_render_browser(tools.environ)
        tools.browser = browser
        add("render-browser", True, browser is not None, f"{source}: {browser}" if browser else "未找到渲染浏览器；本封装不会触发自动下载，请先自行准备")
    for exe in ("ffmpeg", "ffprobe"):
        r = tools.run([getattr(tools, exe), "-version"])
        first = r.stdout.splitlines()[0] if r.stdout.strip() else ""
        tools.versions[exe] = first
        add(exe, True, r.returncode == 0 and first.startswith(exe), first or _fail_text(r))
    r = tools.run([tools.ffmpeg, "-hide_banner", "-encoders"])
    tools.encoders = _parse_encoders(r.stdout) if r.returncode == 0 else set()
    add("ffmpeg-encoders", False, bool(tools.encoders), "已读到 %d 个编码器（libx264 / gif 仅在对应 preset 需要时才要求；WebP 选路见 webp-route）" % len(tools.encoders))
    add("pillow", False, importlib.util.find_spec("PIL") is not None, "qa 的像素检查/联系表需要；无 libwebp_anim 时 preview-webp 的编码与行为校验也需要")
    tools.webp_route, tools.webp_route_detail = select_webp_route(tools)
    add("webp-route", False, tools.webp_route is not None,
        f"preview-webp 选路 {tools.webp_route}：{tools.webp_route_detail}" if tools.webp_route else f"preview-webp 无可用编码路：{tools.webp_route_detail}")
    return checks


def pillow_webp_anim_probe() -> tuple[bool, str]:
    """实际检测 Pillow 的动画 WebP 编码能力：内存里编码两帧并读回，核对帧数、逐帧时长、loop=0/1；不看版本号、不看 features 标志位。"""
    try:
        from PIL import Image, features
    except ImportError:
        return False, "未安装 Pillow"
    try:
        ver = f"Pillow {Image.__version__}, libwebp {features.version('webp')}"
    except Exception:  # noqa: BLE001 - 版本信息只用于展示
        ver = "Pillow"
    try:
        frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
        for loop in (0, 1):
            buf = io.BytesIO()
            frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:], duration=[40, 60], loop=loop, quality=70, lossless=False)
            buf.seek(0)
            with Image.open(buf) as im:
                durs = []
                for i in range(getattr(im, "n_frames", 1)):
                    im.seek(i)
                    im.load()  # WebP duration 在实际解码该帧后才更新
                    durs.append(im.info.get("duration"))
                if im.format != "WEBP" or getattr(im, "n_frames", 1) != 2 or not getattr(im, "is_animated", False):
                    return False, f"{ver}：写出的不是 2 帧动画 WebP"
                if im.info.get("loop") != loop or durs != [40, 60]:
                    return False, f"{ver}：loop/帧时长读回不一致（loop={im.info.get('loop')} 期望 {loop}，时长 {durs}）"
    except Exception as e:  # noqa: BLE001 - 任何编码/解码异常都意味着此路不可用
        return False, f"{ver}：动画 WebP 编码自检失败：{type(e).__name__}: {e}"
    return True, f"{ver}，动画 WebP 编码/读回/loop/帧时长自检通过"


def select_webp_route(tools: Tools) -> tuple[str | None, str]:
    """ffmpeg 有 libwebp_anim 走 ffmpeg；否则 ffmpeg 抽帧 + Pillow 动画 WebP；都不行返回 None。必须先读过 ffmpeg 编码器列表。"""
    if "libwebp_anim" in tools.encoders:
        return ROUTE_FFMPEG, "ffmpeg 含 libwebp_anim 编码器"
    ok, detail = pillow_webp_anim_probe()
    if ok:
        return ROUTE_PILLOW, f"ffmpeg 无 libwebp_anim，改用 ffmpeg 抽帧 + Pillow 动画 WebP（{detail}）"
    return None, f"ffmpeg 无 libwebp_anim，且 Pillow 动画 WebP 不可用（{detail}）"


def _needed_encoders(presets: tuple) -> dict[str, str]:
    """只列 ffmpeg 必须具备的编码器；WebP 不在此列，它的编码路由 select_webp_route 决定。"""
    need = {}
    for p in presets:
        kind = PRESETS[p]["kind"]
        need.update({"gif": {"gif": p}, "webp": {}, "mp4": {"libx264": p}}[kind])
    return need


# ───────────────────────── manifest ─────────────────────────

@dataclasses.dataclass(frozen=True)
class Manifest:
    path: Path
    base_dir: Path
    sha256: str
    project_rel: str
    project_dir: Path
    scene: str
    style_code: str | None
    width: int
    height: int
    duration: float
    fps: int
    loop: bool
    presets: tuple

    def to_dict(self) -> dict:
        return {"version": 1, "project": self.project_rel, "scene": self.scene, "style_code": self.style_code, "width": self.width,
                "height": self.height, "duration": self.duration, "fps": self.fps, "loop": self.loop, "presets": list(self.presets)}


def _no_dup_pairs(pairs: list) -> dict:
    d: dict = {}
    for k, v in pairs:
        if k in d:
            raise ManifestError(f"manifest 出现重复字段：{k}")
        d[k] = v
    return d


def _is_int(v: Any) -> bool:
    return type(v) is int


def load_manifest(path: str | os.PathLike) -> Manifest:
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ManifestError(f"manifest 不是普通文件（符号链接也拒绝）：{p}")
    real = Path(os.path.realpath(p))
    raw = real.read_bytes()
    if len(raw) > 256 * 1024:
        raise ManifestError("manifest 超过 256 KiB")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dup_pairs)
    except (UnicodeDecodeError, ValueError) as e:
        raise ManifestError(f"manifest 不是合法 JSON：{e}") from e
    if not isinstance(data, dict):
        raise ManifestError("manifest 顶层必须是对象")
    unknown = sorted(set(data) - set(MANIFEST_KEYS))
    if unknown:
        raise ManifestError(f"manifest 含未知字段：{unknown}")
    missing = [k for k in MANIFEST_KEYS if k not in data]
    if missing:
        raise ManifestError(f"manifest 缺少字段：{missing}（style_code 品牌可写 null，但必须出现）")
    if not (_is_int(data["version"]) and data["version"] == 1):
        raise ManifestError("version 必须是整数 1")
    scene = data["scene"]
    if scene not in SCENES:
        raise ManifestError(f"scene 必须是 {list(SCENES)} 之一")
    style = data["style_code"]
    if style is None:
        if scene != "brand":
            raise ManifestError("只有 brand 场景的 style_code 可以是 null")
    elif not (isinstance(style, str) and STYLE_RE.match(style)):
        raise ManifestError("style_code 必须形如 C24（品牌场景可为 null）")
    w, h = data["width"], data["height"]
    if not (_is_int(w) and _is_int(h) and 16 <= w <= 3840 and 16 <= h <= 3840 and w % 2 == 0 and h % 2 == 0):
        raise ManifestError("width/height 必须是 16–3840 的偶数整数（H.264 yuv420p 要求偶数）")
    d = data["duration"]
    if not (type(d) in (int, float) and math.isfinite(d) and 1 <= d <= 15):
        raise ManifestError("duration 必须是 1–15 秒的数字")
    if not (_is_int(data["fps"]) and data["fps"] in FPS_CHOICES):
        raise ManifestError(f"fps 必须是整数 {list(FPS_CHOICES)} 之一")
    if type(data["loop"]) is not bool:
        raise ManifestError("loop 必须是 true/false")
    presets = data["presets"]
    if not (isinstance(presets, list) and all(isinstance(x, str) for x in presets)) or len(set(presets)) != len(presets):
        raise ManifestError("presets 必须是不重复的字符串列表（可为空）")
    for name in presets:
        spec = PRESETS.get(name)
        if spec is None:
            raise ManifestError(f"未知 preset：{name}（可选 {[k for k, v in PRESETS.items() if not v.get('pending')]}）")
        if spec.get("pending"):
            raise ManifestError(f"preset {name} 未实现（pending），不会假装生成")
        if spec.get("gate") == "ratio_3x4" and w * 4 != h * 3:
            raise ManifestError(f"preset {name} 需要源画面本身就是 3:4（当前 {w}x{h}）；不会裁剪横图，请改做 3:4 源")
        if spec.get("gate") == "loop_true" and not data["loop"]:
            raise ManifestError(f"preset {name} 要求 loop=true")
    rel = data["project"]
    if not isinstance(rel, str) or not rel or "\x00" in rel or os.path.isabs(rel) or rel.startswith("~"):
        raise ManifestError("project 必须是相对 manifest 所在目录的路径")
    base = Path(os.path.realpath(real.parent))
    proj = Path(os.path.normpath(base / rel))
    if Path(os.path.realpath(proj)) != proj:
        raise PathError(f"project 路径含符号链接或不存在：{proj}")
    if not proj.is_dir():
        raise PathError(f"project 不是目录：{proj}")
    return Manifest(real, base, hashlib.sha256(raw).hexdigest(), rel, proj, scene, style, w, h, float(d), data["fps"],
                    data["loop"], tuple(presets))


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def resolve_out(out_arg: str, m: Manifest) -> Path:
    if not out_arg:
        raise PathError("必须用 --out 指定输出目录")
    o = Path(os.path.abspath(out_arg))
    if os.path.lexists(o):
        raise PathError(f"输出目录已存在（空目录与符号链接也拒绝，不覆盖）：{o}")
    parent = Path(os.path.realpath(o.parent))
    if not parent.is_dir():
        raise PathError(f"输出目录的上级不存在：{parent}")
    out = parent / o.name
    if _within(out, m.project_dir):
        raise PathError("输出不能放在输入工程目录内（不对用户工程写入）")
    if _within(m.project_dir, out) or _within(m.path, out):
        raise PathError("输出不能是输入工程或 manifest 的祖先目录")
    return out


# ───────────────────────── 工程检查与清单 ─────────────────────────

_REF_TAGS = {"script", "img", "link", "source", "video", "audio", "iframe", "image", "use", "object", "embed", "track"}
_REF_ATTRS = ("src", "href", "xlink:href", "poster", "data", "srcset")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?['\"]?([^'\")\s;]+)", re.I)


def _ref_problem(ref: str, depth: int) -> str | None:
    r = ref.strip()
    low = r.lower()
    if not r or low.startswith(("data:", "#")):
        return None
    if low.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*:", low):
        return "远程或非本地引用"
    if r.startswith(("/", "\\")):
        return "绝对路径引用"
    norm = posixpath.normpath(re.split(r"[?#]", r)[0].replace("\\", "/"))
    ups = 0
    while norm.startswith("../"):
        ups += 1
        norm = norm[3:]
    if norm == ".." or ups > depth:
        return "引用越出工程目录"
    return None


class _Scan(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[dict] = []
        self.refs: list[str] = []
        self.css: list[str] = []
        self._style = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: (v or "") for k, v in attrs}
        if "data-composition-id" in a:
            self.roots.append(a)
        if tag in _REF_TAGS:
            for k in _REF_ATTRS:
                if a.get(k):
                    if k == "srcset":
                        self.refs += [part.strip().split()[0] for part in a[k].split(",") if part.strip()]
                    else:
                        self.refs.append(a[k])
        if a.get("style"):
            self.css.append(a["style"])
        self._style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._style = False

    def handle_data(self, data: str) -> None:
        if self._style:
            self.css.append(data)


def _css_problems(text: str, depth: int) -> list[str]:
    refs = [m.group(2) for m in _CSS_URL.finditer(text)] + [m.group(1) for m in _CSS_IMPORT.finditer(text)]
    return [f"CSS 引用 {r[:80]!r}：{p}" for r in refs if (p := _ref_problem(r, depth))]


def inventory_project(project: Path) -> dict:
    """所有文件的 sha256 清单；拒绝符号链接与非普通文件；忽略 .DS_Store。"""
    files: dict[str, dict] = {}
    total = 0
    for root, dirs, names in os.walk(project, followlinks=False):
        dirs.sort()
        for d in dirs:
            if os.path.islink(os.path.join(root, d)):
                raise ManifestError(f"工程内含符号链接目录：{os.path.relpath(os.path.join(root, d), project)}")
        for n in sorted(names):
            if n == ".DS_Store":
                continue
            fp = os.path.join(root, n)
            rel = os.path.relpath(fp, project).replace(os.sep, "/")
            st = os.lstat(fp)
            if not stat.S_ISREG(st.st_mode):
                raise ManifestError(f"工程内含符号链接或非普通文件：{rel}")
            h = hashlib.sha256()
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            total += st.st_size
            files[rel] = {"sha256": h.hexdigest(), "bytes": st.st_size}
            if len(files) > 5000 or total > (1 << 30):
                raise ManifestError("工程文件超过 5000 个或 1 GiB，拒绝")
    tree = hashlib.sha256("".join(f"{k}\0{v['sha256']}\n" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"files": files, "file_count": len(files), "total_bytes": total, "tree_sha256": tree}


def inspect_project(m: Manifest) -> dict:
    """清单 + 静态规则：index.html 仅一个根 composition 且 data-width/height/duration 与 manifest 完全一致；无远程资源。
    脚本里动态 fetch 的远程地址无法静态判定，列入 README 已知边界。"""
    inv = inventory_project(m.project_dir)
    problems: list[str] = []
    if "index.html" not in inv["files"]:
        raise ManifestError("工程根目录缺少 index.html")
    root_attrs = None
    for rel in sorted(inv["files"]):
        low = rel.lower()
        if not low.endswith((".html", ".htm", ".css", ".svg")):
            continue
        if inv["files"][rel]["bytes"] > 5 * 1024 * 1024:
            problems.append(f"{rel}：文本文件超过 5 MiB，不扫描")
            continue
        text = (m.project_dir / rel).read_text(encoding="utf-8", errors="replace")
        depth = rel.count("/")
        if low.endswith(".css"):
            problems += [f"{rel}：{p}" for p in _css_problems(text, depth)]
            continue
        sc = _Scan()
        sc.feed(text)
        sc.close()
        problems += [f"{rel}：引用 {r[:80]!r}：{p}" for r in sc.refs if (p := _ref_problem(r, depth))]
        problems += [f"{rel}：{p}" for css in sc.css for p in _css_problems(css, depth)]
        if rel == "index.html":
            if len(sc.roots) != 1:
                problems.append(f"index.html 必须恰好一个 data-composition-id 根元素（实际 {len(sc.roots)}）")
            else:
                root_attrs = sc.roots[0]
        elif sc.roots and low.endswith((".html", ".htm")):
            problems.append(f"{rel}：含 data-composition-id，工程只允许 index.html 一个根 composition")
    if root_attrs is not None:
        for key, want in (("data-width", m.width), ("data-height", m.height), ("data-duration", m.duration)):
            raw = root_attrs.get(key)
            try:
                val = float(raw) if raw is not None and re.fullmatch(r"\s*\d+(\.\d+)?\s*", raw) else None
            except ValueError:
                val = None
            if val is None:
                problems.append(f"根 composition 缺少或写错 {key}（须为纯数字）：{raw!r}")
            elif abs(val - want) > 1e-6:
                problems.append(f"根 composition 的 {key}={val:g} 与 manifest 的 {want:g} 不一致")
    if problems:
        raise ManifestError("工程不合格：\n  - " + "\n  - ".join(problems[:20]))
    return inv


def assert_project_unchanged(m: Manifest, before: dict, stage: str) -> None:
    if inventory_project(m.project_dir)["tree_sha256"] != before["tree_sha256"]:
        raise StageFailed(f"{stage} 之后输入工程内容发生了变化（本封装不允许改用户工程），已中止")


# ───────────────────────── 命令构造与结果判定 ─────────────────────────

def hf_check_argv(t: Tools, project: Path) -> list:
    return [t.node, t.cli, "check", "--json", str(project)]


def hf_render_argv(t: Tools, project: Path, dest: Path, fps: int, quality: str) -> list:
    return [t.node, t.cli, "render", f"--fps={fps}", "--format=mp4", f"--quality={quality}", "--workers=1",
            "--experimental-fast-capture=false", "-o", str(dest), str(project)]


def scaled_size(m: Manifest, width: int) -> tuple[int, int]:
    w = min(width, m.width)
    w -= w % 2
    return w, round(w * m.height / m.width)


def poster_argv(t: Tools, master: Path, dest: Path) -> list:
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-frames:v", "1", str(dest)]


def gif_argv(t: Tools, m: Manifest, master: Path, dest: Path, width: int, fps: int, colors: int) -> list:
    w, h = scaled_size(m, width)
    vf = (f"fps={min(fps, m.fps)},scale={w}:{h}:flags=lanczos,split[a][b];"
          f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5")
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-vf", vf, "-loop", "0" if m.loop else "-1", str(dest)]


def webp_argv(t: Tools, m: Manifest, master: Path, dest: Path, width: int, fps: int, quality: int) -> list:
    w, h = scaled_size(m, width)
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-vf", f"fps={min(fps, m.fps)},scale={w}:{h}:flags=lanczos",
            "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(quality), "-compression_level", "6",
            "-loop", "0" if m.loop else "1", "-an", str(dest)]


def mp4_argv(t: Tools, master: Path, dest: Path, crf: int) -> list:
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dest)]


def frame_argv(t: Tools, master: Path, idx: int, dest: Path) -> list:
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-vf", f"select=eq(n\\,{idx})", "-frames:v", "1", str(dest)]


_CHECK_SECTIONS = ("lint", "runtime", "layout", "motion", "contrast")


def evaluate_check(res: CmdResult) -> dict:
    """check 必须同时满足：退出码 0、stdout 整体是一个 JSON 对象、ok=true、浏览器未跳过、各分区 errorCount 均为 0。"""
    if res.timed_out or res.returncode is None:
        raise StageFailed(f"hyperframes check 未完成：{_fail_text(res)}")
    data = None
    try:
        data = json.loads(res.stdout.strip()) if res.stdout.strip() else None
    except ValueError:
        pass
    if res.returncode != 0:
        extra = ""
        if isinstance(data, dict):
            errs = [f"{f.get('code')}: {str(f.get('message'))[:120]}" for s in _CHECK_SECTIONS
                    for f in (data.get(s) or {}).get("findings", []) if isinstance(f, dict) and f.get("severity") == "error"]
            extra = "；错误：" + " | ".join(errs[:5]) if errs else ""
        raise StageFailed(f"hyperframes check 退出码 {res.returncode}{extra}")
    if not res.stdout.strip():
        raise StageFailed("hyperframes check 的 stdout 为空，不能当作通过")
    if not isinstance(data, dict):
        raise StageFailed("hyperframes check 的 stdout 不是单个 JSON 对象（可能夹带噪声），不能当作通过")
    if data.get("ok") is not True:
        raise StageFailed("hyperframes check 报告 ok != true")
    if data.get("browserSkipped") is not False:
        raise StageFailed("hyperframes check 的浏览器环节被跳过，布局/动效/对比度结果是空占位，不能当作通过")
    warnings: dict[str, int] = {}
    for s in _CHECK_SECTIONS:
        sec = data.get(s)
        if not isinstance(sec, dict) or not _is_int(sec.get("errorCount")) or not _is_int(sec.get("warningCount")):
            raise StageFailed(f"check JSON 缺少分区 {s} 的 errorCount/warningCount，不能当作通过")
        if sec["errorCount"] != 0:
            raise StageFailed(f"check 分区 {s} 有 {sec['errorCount']} 个 error（即使退出码为 0 也拒绝）")
        warnings[s] = sec["warningCount"]
    lint = data["lint"]
    if not (_is_int(lint.get("filesScanned")) and lint["filesScanned"] >= 1):
        raise StageFailed("check 的 lint 没有扫描任何文件，不能当作通过")
    kept = [{"section": s, "code": f.get("code"), "message": str(f.get("message"))[:200]}
            for s in _CHECK_SECTIONS for f in data[s].get("findings", []) if isinstance(f, dict) and f.get("severity") == "warning"]
    return {"ok": True, "warnings": warnings, "warning_findings": kept[:20]}


def _frac(s: Any) -> Fraction | None:
    try:
        return Fraction(str(s))
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(t: Tools, path: Path) -> dict:
    """对真实文件跑 ffprobe（含逐帧计数）。"""
    r = t.run([t.ffprobe, "-v", "error", "-count_frames", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    if r.returncode != 0:
        raise StageFailed(f"ffprobe 失败：{_fail_text(r)}")
    try:
        data = json.loads(r.stdout)
        vs = [s for s in data["streams"] if s.get("codec_type") == "video"]
        v = vs[0]
        frames = v.get("nb_read_frames")
        return {"video_streams": len(vs), "audio_streams": sum(1 for s in data["streams"] if s.get("codec_type") == "audio"),
                "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"), "pix_fmt": v.get("pix_fmt"),
                "r_fps": _frac(v.get("r_frame_rate")), "frames": int(frames) if frames not in (None, "N/A") else None,
                "duration": float(data["format"]["duration"]), "bytes": os.path.getsize(path)}
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise StageFailed(f"ffprobe 输出无法解析：{e}") from e


def verify_video(p: dict, m: Manifest, *, codec: str = "h264", pix_fmt: str | None = None) -> list[str]:
    bad = []
    if p["video_streams"] != 1:
        bad.append(f"视频流数量 {p['video_streams']} != 1")
    if p["codec"] != codec:
        bad.append(f"编码 {p['codec']} != {codec}")
    if (p["width"], p["height"]) != (m.width, m.height):
        bad.append(f"尺寸 {p['width']}x{p['height']} != {m.width}x{m.height}")
    if p["r_fps"] != m.fps:
        bad.append(f"帧率 {p['r_fps']} != {m.fps}")
    if abs(p["duration"] - m.duration) > 1.5 / m.fps + 0.02:
        bad.append(f"时长 {p['duration']:.3f}s 与 manifest 的 {m.duration:g}s 不符")
    want = round(m.duration * m.fps)
    if p["frames"] is None or abs(p["frames"] - want) > 1:
        bad.append(f"帧数 {p['frames']} 与期望 {want} 不符")
    if pix_fmt and p["pix_fmt"] != pix_fmt:
        bad.append(f"像素格式 {p['pix_fmt']} != {pix_fmt}")
    return bad


def _aspect_ok(w: int, h: int, m: Manifest) -> bool:
    return h > 0 and abs(w * m.height - h * m.width) <= max(m.width, m.height)


def verify_poster(path: Path, m: Manifest) -> list[str]:
    head = path.read_bytes()[:24]
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return ["poster 不是 PNG"]
    w, h = struct.unpack(">II", head[16:24])
    return [] if (w, h) == (m.width, m.height) else [f"poster 尺寸 {w}x{h} != {m.width}x{m.height}"]


def verify_gif(t: Tools, path: Path, m: Manifest) -> list[str]:
    head = path.read_bytes()[:4096]
    if head[:4] != b"GIF8":
        return ["不是 GIF"]
    bad = []
    w, h = struct.unpack("<HH", head[6:10])
    if not _aspect_ok(w, h, m):
        bad.append(f"GIF 尺寸 {w}x{h} 与源 {m.width}x{m.height} 比例不同")
    if (b"NETSCAPE2.0" in head) != m.loop:
        bad.append(f"GIF 循环标记与 loop={m.loop} 不一致")
    p = probe_media(t, path)
    if not p["frames"] or p["frames"] < 2:
        bad.append("GIF 不是多帧动图")
    return bad


def webp_info(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("不是 WebP")
    info: dict[str, Any] = {"anim": False, "frames": 0, "loop": None, "canvas": None}
    pos = 12
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"VP8X" and len(body) >= 10:
            info["anim"] = bool(body[0] & 0x02)
            info["canvas"] = (int.from_bytes(body[4:7], "little") + 1, int.from_bytes(body[7:10], "little") + 1)
        elif tag == b"ANIM" and len(body) >= 6:
            info["loop"] = struct.unpack("<H", body[4:6])[0]
        elif tag == b"ANMF":
            info["frames"] += 1
        pos += 8 + size + (size & 1)
    return info


def _opaque(im: Any) -> bool:
    return im.convert("RGBA").getchannel("A").getextrema()[0] == 255


def webp_behavior(path: Path, m: Manifest, *, expect_size: tuple | None = None, expect_frames: int | None = None,
                  fps: int | None = None) -> tuple[list[str], dict]:
    """用 Pillow 实际读回动画 WebP 做行为校验（帧数、尺寸、逐帧时长、loop、不透明）。ffprobe 不一定支持动画 WebP，所以这里不借 ffprobe。
    不足 2 帧一律拒绝：单帧 WebP 没有可验证的时长与 loop，不管源是否静态都不能冒充动画。"""
    try:
        from PIL import Image
    except ImportError:
        return ["WebP 行为校验需要 Pillow，但未安装"], {}
    bad: list[str] = []
    info: dict[str, Any] = {"checked_by": "pillow-behavior"}
    want_loop = 0 if m.loop else 1
    try:
        with Image.open(path) as im:
            if im.format != "WEBP":
                return [f"Pillow 读到的格式是 {im.format}，不是 WEBP"], info
            n = getattr(im, "n_frames", 1)
            info.update(frames=n, size=list(im.size), loop=im.info.get("loop"))
            if max(im.size) > 3840 or n > WEBP_READ_MAX_FRAMES:
                return [f"WebP 尺寸 {im.size} 或帧数 {n} 超出读取上限，不逐帧读取"], info
            if expect_size is not None and tuple(im.size) != tuple(expect_size):
                bad.append(f"WebP 尺寸 {tuple(im.size)} != 期望 {tuple(expect_size)}")
            elif expect_size is None and not _aspect_ok(*im.size, m):
                bad.append(f"WebP 尺寸 {tuple(im.size)} 与源 {m.width}x{m.height} 比例不同")
            if expect_frames is not None and n > expect_frames:
                bad.append(f"WebP 帧数 {n} 多于输入帧数 {expect_frames}")
            if n < 2:
                bad.append(f"不是多帧动画 WebP（Pillow 读到 {n} 帧）")
            else:
                if not getattr(im, "is_animated", False):
                    bad.append("Pillow 不认为这是动画")
                if im.info.get("loop") != want_loop:
                    bad.append(f"loop={m.loop} 应读回 loop={want_loop}，实际 {im.info.get('loop')}")
                durs: list = []
                for i in range(n):
                    im.seek(i)
                    im.load()  # WebP duration 在实际解码该帧后才更新
                    durs.append(im.info.get("duration"))
                    if i in (0, n - 1) and not _opaque(im):
                        bad.append(f"第 {i} 帧含透明像素（本版只做不透明底）")
                if any(not (type(d) is int and d > 0) for d in durs):
                    bad.append(f"存在无效帧时长：{[d for d in durs if not (type(d) is int and d > 0)][:3]}")
                else:
                    total = sum(durs)
                    info.update(duration_ms_total=total, timing_verifiable=True)
                    if fps is not None and expect_frames is not None and abs(total - round(expect_frames * 1000 / fps)) > 2:
                        bad.append(f"总时长 {total}ms 与 {expect_frames} 帧 @ {fps}fps 的 {round(expect_frames * 1000 / fps)}ms 不符")
                    if abs(total / 1000 - m.duration) > 1.5 * max(durs) / 1000 + 0.02:
                        bad.append(f"总时长 {total / 1000:.3f}s 与 manifest 的 {m.duration:g}s 不符")
    except Exception as e:  # noqa: BLE001 - 坏文件/坏帧一律算校验失败
        bad.append(f"Pillow 无法完整读取该 WebP：{type(e).__name__}: {e}")
    return bad, info


def verify_webp(path: Path, m: Manifest, *, behavior: bool = False, expect_size: tuple | None = None, expect_frames: int | None = None,
                fps: int | None = None, info_out: dict | None = None) -> list[str]:
    """RIFF 结构解析必做；behavior=True 再叠加 Pillow 读回的行为校验。无任何单帧例外。"""
    try:
        i = webp_info(path)
    except ValueError as e:
        return [str(e)]
    bad: list[str] = []
    info: dict = {}
    if behavior:
        b2, info = webp_behavior(path, m, expect_size=expect_size, expect_frames=expect_frames, fps=fps)
        bad += b2
        if info_out is not None:
            info_out.update(info)
    if not i["anim"] or i["frames"] < 2:
        bad.append(f"不是多帧动画 WebP（anim={i['anim']} frames={i['frames']}）")
    if i["canvas"] is None or not _aspect_ok(*i["canvas"], m):
        bad.append(f"WebP 画布 {i['canvas']} 与源比例不同")
    want = 0 if m.loop else 1
    if i["loop"] != want:
        bad.append(f"loop={m.loop} 时 WebP 的 ANIM 循环计数应为 {want}，实际 {i['loop']}")
    if behavior and i["anim"] and info.get("frames") not in (None, i["frames"]):
        bad.append(f"RIFF 的 ANMF 帧数 {i['frames']} 与 Pillow 读到的 {info.get('frames')} 不一致")
    return bad


# ───────────────────────── render ─────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def suggest_oversize(kind: str, size: int, limit: int, opts: dict) -> str:
    k = math.sqrt(limit / size) * 0.9
    if kind == "gif":
        w = max(160, int(opts["gif_width"] * k) // 2 * 2)
        return f"{size / 1048576:.1f} MiB > {limit / 1048576:.0f} MiB。试：--gif-width {w}；或 --gif-fps {max(5, opts['gif_fps'] // 2)}；或 --gif-colors 128"
    w = max(160, int(opts["webp_width"] * k) // 2 * 2)
    return f"{size / 1048576:.1f} MiB > {limit / 1048576:.1f} MiB。试：--webp-width {w}；或 --webp-quality {max(30, opts['webp_quality'] - 15)}；或 --webp-fps 15"


def _artifact(name: str, path: Path, status: str, **extra: Any) -> dict:
    d = {"name": name, "file": path.name, "status": status}
    if status == "ok" and path.is_file():
        d.update(bytes=path.stat().st_size, sha256=_file_sha(path))
    d.update(extra)
    return d


def webp_plan(m: Manifest, opts: dict) -> dict:
    w, h = scaled_size(m, opts["webp_width"])
    fps = min(opts["webp_fps"] or m.fps, m.fps)
    return {"width": w, "height": h, "fps": fps, "frames": round(m.duration * fps)}


def webp_frames_argv(t: Tools, master: Path, frame_dir: Path, plan: dict) -> list:
    return [t.ffmpeg, "-v", "error", "-n", "-i", str(master), "-vf", f"fps={plan['fps']},scale={plan['width']}:{plan['height']}:flags=lanczos",
            "-frames:v", str(plan["frames"]), str(frame_dir / "f_%04d.png")]


def _encode_webp_pillow(t: Tools, m: Manifest, master: Path, out: Path, dest: Path, opts: dict) -> tuple[str | None, str, dict]:
    """ffmpeg 抽帧到本次输出目录内的专属临时目录 → Pillow 编码动画 WebP（独占创建 dest，绝不覆盖）→ 无论成败都清理自己的临时帧。
    返回 (失败状态或 None, 原因, 额外报告字段)。"""
    pl = webp_plan(m, opts)
    w, h, fps, n = pl["width"], pl["height"], pl["fps"], pl["frames"]
    frame_dir = out / WEBP_FRAME_DIR
    extra: dict[str, Any] = {"argv": webp_frames_argv(t, master, frame_dir, pl), "webp_route": ROUTE_PILLOW, "webp_plan": pl}
    if n < 2:
        return "failed", f"按 {fps}fps 只有 {n} 帧，做不成动画", extra
    if n > WEBP_MAX_FRAMES or n * w * h * 4 > WEBP_MAX_RAW_BYTES:
        return "rejected_limits", (f"{n} 帧 × {w}x{h} 超出 Pillow 路径上限（最多 {WEBP_MAX_FRAMES} 帧、{WEBP_MAX_RAW_BYTES // 1048576} MiB 内存预算）。"
                                   f"试：--webp-width 更小；或 --webp-fps 15"), extra
    try:
        os.mkdir(frame_dir)
    except OSError as e:
        return "failed", f"无法创建临时抽帧目录 {frame_dir}：{e}", extra
    created = False
    try:
        r = t.run(extra["argv"])
        if r.returncode != 0:
            return "failed", "ffmpeg 抽帧失败：" + _fail_text(r), extra
        files = sorted(p for p in frame_dir.iterdir() if re.fullmatch(r"f_\d{4}\.png", p.name))
        got = len(files)
        extra["frames_extracted"] = got
        if not (n - 1 <= got <= n):
            return "failed", f"抽帧数 {got} 与期望 {n} 不符（允许 {n - 1}–{n}）", extra
        from PIL import Image
        frames, seen = [], set()
        for p in files:
            try:
                with Image.open(p) as im:
                    if im.size != (w, h):
                        return "failed", f"抽帧 {p.name} 尺寸 {im.size} != {(w, h)}", extra
                    rgb = im.convert("RGB")          # 不透明底：丢弃一切 alpha
            except Exception as e:  # noqa: BLE001 - 坏帧
                return "failed", f"抽帧 {p.name} 无法解码：{type(e).__name__}: {e}", extra
            seen.add(hashlib.sha256(rgb.tobytes()).digest())
            frames.append(rgb)
        edges = [round(i * 1000 / fps) for i in range(got + 1)]      # 累计取整，逐帧时长之和不漂移
        durs = [edges[i + 1] - edges[i] for i in range(got)]
        extra.update(frames_in=got, distinct_source_frames=len(seen), duration_ms_planned=sum(durs))
        if len(seen) == 1:      # 全部输入帧逐字节相同：libwebp 会合并成单帧，不是动画；明确 not-animated，不算 preset 成功
            return "rejected_static", "not-animated：母版全部输出帧像素完全相同（静态画面），不会生成声称动画的 WebP", extra
        with open(dest, "xb") as f:
            created = True
            frames[0].save(f, format="WEBP", save_all=True, append_images=frames[1:], duration=durs, loop=0 if m.loop else 1,
                           quality=opts["webp_quality"], lossless=False, method=6)
        return None, "", extra
    except Exception as e:  # noqa: BLE001 - 编码失败：只清理自己创建的文件
        if created:
            dest.unlink(missing_ok=True)
        return "failed", f"Pillow 编码失败：{type(e).__name__}: {e}", extra
    finally:
        if frame_dir.parent == out and frame_dir.is_dir() and not frame_dir.is_symlink():
            shutil.rmtree(frame_dir, ignore_errors=True)
        extra["frame_dir_cleaned"] = not os.path.lexists(frame_dir)


def _make_derived(t: Tools, m: Manifest, master: Path, out: Path, name: str, opts: dict) -> dict:
    spec = PRESETS[name]
    dest = out / spec["file"]
    kind = spec["kind"]
    base = {"preset": name, "kind": kind, "local_status": STATUS_LOCAL, "device_status": STATUS_DEVICE, "note": spec["desc"]}
    if os.path.lexists(dest):
        return _artifact(name, dest, "failed", reason="目标文件已存在，拒绝覆盖", argv=[], **base)
    route = None
    if kind == "webp":
        route = t.webp_route
        if route is None:
            return _artifact(name, dest, "failed", reason=f"没有可用的 WebP 编码路：{t.webp_route_detail}", argv=[], **base)
    plan = None
    if route == ROUTE_PILLOW:
        status, reason, extra = _encode_webp_pillow(t, m, master, out, dest, opts)
        base.update(extra)
        plan = extra["webp_plan"]
        if status is None and not extra.get("frame_dir_cleaned"):
            status, reason = "failed", "临时抽帧目录未能清理干净"
        if status is not None:
            dest.unlink(missing_ok=True)       # dest 在入口已确认不存在，这里只可能删掉本次自己写的残片
            return _artifact(name, dest, status, reason=reason, **base)
        limit = opts["webp_max_bytes"]
    else:
        if kind == "gif":
            argv, limit = gif_argv(t, m, master, dest, opts["gif_width"], opts["gif_fps"], opts["gif_colors"]), GIF_MAX_BYTES
        elif kind == "webp":
            argv, limit = webp_argv(t, m, master, dest, opts["webp_width"], opts["webp_fps"] or m.fps, opts["webp_quality"]), opts["webp_max_bytes"]
            base["webp_route"] = ROUTE_FFMPEG
        else:
            argv, limit = mp4_argv(t, master, dest, opts["mp4_crf"]), None
        base["argv"] = argv
        r = t.run(argv)
        if r.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return _artifact(name, dest, "failed", reason=_fail_text(r), **base)
    if not dest.is_file() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return _artifact(name, dest, "failed", reason="输出文件为空或缺失", **base)
    size = dest.stat().st_size
    if limit is not None and size > limit:
        dest.unlink(missing_ok=True)
        return _artifact(name, dest, "rejected_oversize", reason=suggest_oversize(kind, size, limit, opts), **base)
    try:
        if kind == "gif":
            bad = verify_gif(t, dest, m)
        elif kind == "webp":
            vinfo: dict = {}
            if route == ROUTE_PILLOW:
                bad = verify_webp(dest, m, behavior=True, expect_size=(plan["width"], plan["height"]), expect_frames=base["frames_in"],
                                  fps=plan["fps"], info_out=vinfo)
                base["verified_by"] = "RIFF 解析 + Pillow 读回行为校验（帧数/尺寸/逐帧时长/loop/不透明）；未使用 ffprobe"
            else:
                has_pillow = importlib.util.find_spec("PIL") is not None
                bad = verify_webp(dest, m, behavior=has_pillow, info_out=vinfo)
                base["verified_by"] = ("RIFF 解析 + Pillow 读回行为校验；未使用 ffprobe" if has_pillow else "仅 RIFF 解析（Pillow 不可用，行为未校验）；未使用 ffprobe")
            base["webp_verify"] = vinfo
        else:
            bad = verify_video(probe_media(t, dest), m, pix_fmt="yuv420p")
    except PipelineError as e:
        bad = [str(e)]
    if bad:
        os.replace(dest, dest.with_name(dest.name + ".rejected"))
        return _artifact(name, dest, "failed_verification", reason="; ".join(bad), **base)
    return _artifact(name, dest, "ok", **base)


def default_opts() -> dict:
    return {"quality": "draft", "gif_width": 800, "gif_fps": 15, "gif_colors": 192, "webp_width": 960, "webp_fps": None,
            "webp_quality": 70, "webp_max_bytes": WEBP_DEFAULT_MAX_BYTES, "mp4_crf": 20}


def preflight(t: Tools, m: Manifest, out_arg: str) -> tuple[Path, dict]:
    out = resolve_out(out_arg, m)
    inv = inspect_project(m)
    checks = run_doctor(t)
    bad = [c for c in checks if c["required"] and not c["ok"]]
    if bad:
        raise ToolError("环境不满足：" + "；".join(f"{c['name']}: {c['detail']}" for c in bad))
    miss = {enc: p for enc, p in _needed_encoders(m.presets).items() if enc not in t.encoders}
    if miss:
        raise ToolError(f"ffmpeg 缺少所需编码器：{miss}（preset 对应关系：编码器→preset）")
    if any(PRESETS[p]["kind"] == "webp" for p in m.presets) and t.webp_route is None:
        raise ToolError(f"preview-webp 没有可用的编码路：{t.webp_route_detail}")
    return out, inv


def run_render(t: Tools, m: Manifest, out_arg: str, opts: dict) -> tuple[int, dict]:
    out, inv = preflight(t, m, out_arg)
    try:
        os.mkdir(out)
    except FileExistsError as e:
        raise PathError(f"输出目录已存在：{out}") from e
    master = out / "master.mp4"
    report: dict[str, Any] = {
        "schema": "motion-pipeline/render-report/1", "status": "running", "started_utc": _now(),
        "manifest": m.to_dict(), "manifest_sha256": m.sha256, "project": {"dir": str(m.project_dir), **{k: inv[k] for k in ("file_count", "total_bytes", "tree_sha256")}, "files": inv["files"]},
        "tools": {"node": t.node, "hyperframes_cli": t.cli, "hyperframes_version": HYPERFRAMES_VERSION, "ffmpeg": t.ffmpeg, "ffprobe": t.ffprobe,
                  "versions": t.versions, "render_browser": t.browser, "webp_route": t.webp_route, "webp_route_detail": t.webp_route_detail},
        "options": opts, "artifacts": [], "manual_review_required": True,
        "note": "render 只完成渲染与格式校验；首末帧接缝、联系表请再跑 qa；文字清晰度与 logo/label 文字保护需人工审片，不做 OCR。",
    }
    try:
        argv = hf_check_argv(t, m.project_dir)
        report["check"] = evaluate_check(t.run_hf(argv)) | {"argv": argv}
        assert_project_unchanged(m, inv, "check")
        argv = hf_render_argv(t, m.project_dir, master, m.fps, opts["quality"])
        report["render_argv"] = argv
        r = t.run_hf(argv)
        if r.returncode != 0 or not master.is_file() or master.stat().st_size == 0:
            master.unlink(missing_ok=True)
            raise StageFailed(f"hyperframes render 失败：{_fail_text(r)}")
        assert_project_unchanged(m, inv, "render")
        probe = probe_media(t, master)
        report["master_probe"] = {k: (str(v) if isinstance(v, Fraction) else v) for k, v in probe.items()}
        bad = verify_video(probe, m)
        if bad:
            os.replace(master, out / "master.rejected.mp4")
            raise StageFailed("母版 ffprobe 校验未通过（已改名为 master.rejected.mp4，不是成品）：" + "；".join(bad))
        arts = [_artifact("master", master, "ok", note="HyperFrames 渲染母版，尺寸/时长/帧率/编码已由 ffprobe 校验")]
        poster = out / "poster.png"
        r = t.run(poster_argv(t, master, poster))
        bad = verify_poster(poster, m) if r.returncode == 0 and poster.is_file() else [_fail_text(r)]
        if bad:
            poster.unlink(missing_ok=True)
            arts.append(_artifact("poster", poster, "failed", reason="; ".join(bad)))
        else:
            arts.append(_artifact("poster", poster, "ok", note="母版首帧"))
        arts += [_make_derived(t, m, master, out, name, opts) for name in m.presets]
        report["artifacts"] = arts
        report["status"] = "ok" if all(a["status"] == "ok" for a in arts) else "partial"
    except PipelineError as e:
        report.update(status="failed", error=str(e))
    report["finished_utc"] = _now()
    report["output_dir"] = str(out)
    _write_json(out / "render_report.json", report)
    return {"ok": 0, "partial": 3}.get(report["status"], 1), report


# ───────────────────────── qa ─────────────────────────

def sample_indices(n: int, k: int = 8) -> list[int]:
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _require_pillow() -> Any:
    try:
        import PIL.Image as image  # noqa: F401
    except ImportError as e:
        raise ToolError("像素 QA 需要 Pillow（pip install pillow）；只想做 ffprobe 复验请显式加 --probe-only，不会静默跳过") from e
    return image


def build_contact(frames: list[tuple[int, Path]], fps: int, dest: Path) -> None:
    from PIL import Image, ImageDraw
    thumbs = []
    for idx, p in frames:
        im = Image.open(p).convert("RGB")
        tw = min(480, im.width)
        thumbs.append((idx, im.resize((tw, max(1, round(im.height * tw / im.width))), Image.LANCZOS)))
    cols = min(4, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    cw, ch = max(t.width for _, t in thumbs), max(t.height for _, t in thumbs) + 16
    sheet = Image.new("RGB", (cols * (cw + 8) + 8, rows * (ch + 8) + 8), (32, 32, 32))
    d = ImageDraw.Draw(sheet)
    for i, (idx, th) in enumerate(thumbs):
        x, y = 8 + (i % cols) * (cw + 8), 8 + (i // cols) * (ch + 8)
        d.text((x + 2, y + 2), f"#{idx}  t={idx / fps:.2f}s", fill=(230, 230, 230))
        sheet.paste(th, (x, y + 16))
    sheet.save(dest)


def run_qa(t: Tools, m: Manifest, out: Path, *, probe_only: bool, max_seam: float, min_std: float) -> tuple[int, dict]:
    out = Path(os.path.realpath(out))
    master = out / "master.mp4"
    if not out.is_dir() or master.is_symlink() or not master.is_file():
        raise PathError(f"输出目录里没有 master.mp4：{out}")
    qa_dir = out / "qa"
    if os.path.lexists(qa_dir):
        raise PathError(f"{qa_dir} 已存在，不覆盖；请先改名或删除")
    if not probe_only:
        _require_pillow()
    run_doctor(t, with_hf=False)
    probs: list[str] = []
    probe = probe_media(t, master)       # 对真实文件重新探测，不读 render_report.json
    probs += [f"master：{b}" for b in verify_video(probe, m)]
    checked = [{"name": "master", "probe": {k: (str(v) if isinstance(v, Fraction) else v) for k, v in probe.items()}}]
    poster = out / "poster.png"
    if not poster.is_file():
        probs.append("poster.png 缺失")
    else:
        probs += [f"poster：{b}" for b in verify_poster(poster, m)]
    for name in m.presets:
        f = out / PRESETS[name]["file"]
        if not f.is_file():
            probs.append(f"{name}：文件缺失（render 可能已拒绝它，见 render_report.json）")
            continue
        try:
            kind = PRESETS[name]["kind"]
            if kind == "webp":       # 非 probe-only 时叠加 Pillow 行为校验；单帧输出一律拒绝，不看母版是否静态
                bad = verify_webp(f, m, behavior=not probe_only)
            else:
                bad = verify_gif(t, f, m) if kind == "gif" else verify_video(probe_media(t, f), m, pix_fmt="yuv420p")
            if kind == "gif" and f.stat().st_size > GIF_MAX_BYTES:
                bad.append("GIF 超过 10 MiB")
        except PipelineError as e:
            bad = [str(e)]
        probs += [f"{name}：{b}" for b in bad]
    pixel: dict[str, Any] = {"executed": False}
    os.mkdir(qa_dir)
    n = probe["frames"] or 0
    if not probe_only and n >= 2:
        frame_dir = qa_dir / "frames"
        os.mkdir(frame_dir)
        got: list[tuple[int, Path]] = []
        for idx in sample_indices(n):
            dest = frame_dir / f"f_{idx:04d}.png"
            r = t.run(frame_argv(t, master, idx, dest))
            if r.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
                probs.append(f"抽帧 #{idx} 失败：{_fail_text(r)}")
            else:
                got.append((idx, dest))
        if got and got[0][0] == 0 and got[-1][0] == n - 1:
            from PIL import Image, ImageChops, ImageStat
            first, last = Image.open(got[0][1]).convert("RGB"), Image.open(got[-1][1]).convert("RGB")
            std = ImageStat.Stat(first.convert("L")).stddev[0]
            seam = sum(ImageStat.Stat(ImageChops.difference(first, last)).mean) / 3
            build_contact(got, m.fps, qa_dir / "contact.png")
            pixel = {"executed": True, "first_frame_luma_stddev": round(std, 3), "min_stddev": min_std, "first_frame_nonblank": std >= min_std,
                     "seam_mean_abs_diff": round(seam, 3), "max_seam_diff": max_seam, "loop_checked": m.loop,
                     "seam_ok": (seam <= max_seam) if m.loop else None, "contact_sheet": "qa/contact.png", "contact_frames": [i for i, _ in got]}
            if std < min_std:
                probs.append(f"首帧疑似空白/纯色（亮度标准差 {std:.2f} < {min_std}）")
            if m.loop and seam > max_seam:
                probs.append(f"首末帧平均像素差 {seam:.2f} > 阈值 {max_seam}（接缝可能跳变）")
        else:
            probs.append("未能取到首帧与末帧，像素 QA 无结果")
    status = "fail" if probs else ("probe-only-pixel-qa-NOT-run" if probe_only else "auto-pass-needs-manual-review")
    report = {"schema": "motion-pipeline/qa-report/1", "status": status, "problems": probs, "checked": checked, "pixel": pixel,
              "manual_review_required": True, "finished_utc": _now(),
              "note": "量化指标（固定阈值、非自适应）只验证非空与首末接缝，不保证任意时刻文字清晰；文字清晰度、label/logo 文字保护必须人工审片，不做 OCR。"}
    _write_json(qa_dir / "qa_report.json", report)
    return (1 if probs else 0), report


# ───────────────────────── CLI ─────────────────────────

def _timeout(s: str) -> int:
    v = int(s)
    if not 1 <= v <= MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"--timeout 必须在 1–{MAX_TIMEOUT} 秒")
    return v


def _quality(s: str) -> str:
    if not re.fullmatch(r"[a-z]+", s):
        raise argparse.ArgumentTypeError("quality 只能是小写字母")
    return s


def _bounded(lo: int, hi: int) -> Callable[[str], int]:
    def conv(s: str) -> int:
        v = int(s)
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(f"取值必须在 {lo}–{hi}")
        return v
    return conv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="motion_pipeline.py", description="叁笙生图动态版本机渲染封装")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, cli: bool) -> None:
        if cli:
            sp.add_argument("--hyperframes-cli", required=True, help="hyperframes.mjs 的绝对路径（必须 0.8.140）")
        sp.add_argument("--node"), sp.add_argument("--ffmpeg"), sp.add_argument("--ffprobe")
        sp.add_argument("--timeout", type=_timeout, default=MAX_TIMEOUT, help="每条外部命令的超时秒数，上限 300")
        sp.add_argument("--json", action="store_true", help="完整 JSON 输出")

    d = sub.add_parser("doctor", help="检查必要环境")
    common(d, True)
    for name, h in (("plan", "只读预演：校验 manifest/工程/输出路径并列出将执行的命令"), ("render", "check → 渲染母版 → 导出 preset")):
        sp = sub.add_parser(name, help=h)
        sp.add_argument("manifest")
        sp.add_argument("--out", required=True, help="输出目录，必须尚不存在")
        common(sp, True)
        if name == "render":
            sp.add_argument("--quality", default="draft", type=_quality, help="原样传给 hyperframes render --quality=；合法取值由 root 对照 0.8.140 帮助确认")
            sp.add_argument("--gif-width", type=_bounded(160, 3840), default=800), sp.add_argument("--gif-fps", type=_bounded(1, 50), default=15)
            sp.add_argument("--gif-colors", type=_bounded(2, 256), default=192)
            sp.add_argument("--webp-width", type=_bounded(160, 3840), default=960), sp.add_argument("--webp-fps", type=_bounded(1, 60), default=None)
            sp.add_argument("--webp-quality", type=_bounded(0, 100), default=70)
            sp.add_argument("--webp-max-bytes", type=_bounded(1, 1 << 30), default=WEBP_DEFAULT_MAX_BYTES)
            sp.add_argument("--mp4-crf", type=_bounded(0, 51), default=20)
    q = sub.add_parser("qa", help="对输出目录里的真实文件复验：ffprobe + 首末帧接缝 + 联系表")
    q.add_argument("manifest")
    q.add_argument("--out", required=True, help="render 产出的目录")
    q.add_argument("--probe-only", action="store_true", help="显式放弃像素 QA（不装 Pillow 时用）；结果不会标记为通过")
    q.add_argument("--max-seam-diff", type=float, default=4.0, help="首末帧 RGB 平均绝对差上限（0–255，固定阈值）")
    q.add_argument("--min-stddev", type=float, default=2.0, help="首帧亮度标准差下限（低于视为空白）")
    common(q, False)
    return p


def _print(args: argparse.Namespace, payload: dict, lines: list[str]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str) if args.json else "\n".join(lines))


def main(argv: list[str] | None = None, *, runner: Callable[..., CmdResult] = run_cmd, environ: dict | None = None,
         manifest_validator: Callable[[Manifest], None] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tools = None
    try:
        tools = resolve_tools(getattr(args, "hyperframes_cli", None), node=args.node, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                              timeout=args.timeout, runner=runner, environ=environ)
        if args.cmd == "doctor":
            checks = run_doctor(tools)
            ok = all(c["ok"] for c in checks if c["required"])
            _print(args, {"ok": ok, "checks": checks}, [f"[{'OK ' if c['ok'] else ('FAIL' if c['required'] else 'opt ')}] {c['name']}: {c['detail']}" for c in checks]
                   + ["环境满足渲染必需项。" if ok else "环境不满足，见 FAIL 项。"])
            return 0 if ok else 1
        m = load_manifest(args.manifest)
        if manifest_validator is not None:
            manifest_validator(m)
        if args.cmd == "plan":
            out = resolve_out(args.out, m)
            inv = inspect_project(m)
            opts = default_opts()
            plan = {"manifest": m.to_dict(), "manifest_sha256": m.sha256, "project": str(m.project_dir), "tree_sha256": inv["tree_sha256"],
                    "file_count": inv["file_count"], "out": str(out), "will_run": [hf_check_argv(tools, m.project_dir),
                    hf_render_argv(tools, m.project_dir, out / "master.mp4", m.fps, opts["quality"])],
                    "artifacts": ["master.mp4", "poster.png"] + [PRESETS[p]["file"] for p in m.presets],
                    "presets": {p: {"local_status": STATUS_LOCAL, "device_status": STATUS_DEVICE, "desc": PRESETS[p]["desc"]} for p in m.presets},
                    "note": "plan 不执行任何外部命令，不创建任何文件；浏览器/CLI 是否可用请先跑 doctor。"}
            _print(args, plan, [f"工程：{m.project_dir}（{inv['file_count']} 个文件，tree {inv['tree_sha256'][:12]}…）", f"输出：{out}（尚不存在，OK）",
                                "将执行：", *("  " + " ".join(a) for a in plan["will_run"]), "产物：" + ", ".join(plan["artifacts"]), plan["note"]])
            return 0
        if args.cmd == "render":
            opts = default_opts() | {"quality": args.quality, "gif_width": args.gif_width, "gif_fps": args.gif_fps, "gif_colors": args.gif_colors,
                                      "webp_width": args.webp_width, "webp_fps": args.webp_fps, "webp_quality": args.webp_quality,
                                      "webp_max_bytes": args.webp_max_bytes, "mp4_crf": args.mp4_crf}
            code, rep = run_render(tools, m, args.out, opts)
            lines = [f"状态：{rep['status']}  报告：{rep.get('output_dir')}/render_report.json"]
            if rep.get("error"):
                lines.append("失败原因：" + rep["error"])
            lines += [f"  [{a['status']}] {a['name']}" + (f" — {a['reason']}" if a.get("reason") else "") for a in rep["artifacts"]]
            if rep["status"] != "failed":
                lines.append("下一步：python3 motion_pipeline.py qa（接缝/联系表）；文字清晰度需人工审片。")
            _print(args, rep, lines)
            return code
        code, rep = run_qa(tools, m, Path(args.out), probe_only=args.probe_only, max_seam=args.max_seam_diff, min_std=args.min_stddev)
        _print(args, rep, [f"QA 状态：{rep['status']}", *("  问题：" + x for x in rep["problems"]), rep["note"]])
        return code
    except PipelineError as e:
        print(f"错误：{e}", file=sys.stderr)
        return e.exit_code
    finally:
        if tools is not None:
            tools.cleanup()


if __name__ == "__main__":
    sys.exit(main())
