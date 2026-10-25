"""网站选择器（首页）行为测试：页面体积与样图齐全、带/不带结尾斜杠打开时图片都能显示、
复制的 sb2 码能被 CLI 解析、「设为默认」与「只用一次」两条路径、搜索与「帮我挑」三问、品牌图输入边界、
数据转义、缺少动画 API 时的降级、系统「减少动态效果」下的静止。

- 所有输出写进 pytest 临时目录，不写 Skill 源目录；缩略图只在本组测试里缓存（按路径、大小、mtime、尺寸、质量），
  源样图只读，其余注册表、尺寸、清单和输出流程照常执行；
- 私有层显式指向一个不存在的目录，测试不读取本机真实 profile；
- 浏览器测试需要 playwright + chromium，缺失时这些项会被跳过，跳过原因会写明「未验证页面行为」。
运行：python3 -m pytest -p no:cacheprovider tests/test_picker.py -rs
"""
import contextlib
import functools
import http.server
import io
import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from PIL import Image

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
from stylebook import stylecode as SC  # noqa: E402

TEMPLATE = Path(BD.PICKER)

BROWSER_BOUNDARY = ("需要 playwright + Chromium：浏览器行为（渲染、复制、搜索、三问、手机排版、动态降级、减少动态效果）"
                    "在这里没有被验证，只验证了构建产物；请在装有 Chromium 的环境补跑")

# 首页精选 12 与首屏轮播的固定名单（只剔除缺 s2 同题图的，不自动替补）
PINNED_CAROUSEL = [(c, "cv") for c in ["C01", "C17", "C74", "C70", "C20", "C64", "C27", "C46", "C63", "C18", "C30", "C22",
                                        "C96", "C19", "C24", "C49", "C08", "C69", "C76", "C66", "C77", "C92"]]
PINNED_HERO_MOTION = ["moving", "travel", "beach"]

_webp_buffer = io.BytesIO()
Image.new("RGB", (2, 2), "white").save(_webp_buffer, "WEBP", lossless=True)
FAKE_WEBP = _webp_buffer.getvalue()
DATA_RE = re.compile(r'<script id="page-data" type="application/json">(.*?)</script>', re.S)

SITE_URL = "https://github.com/sanshengai/sansheng-image"
GRID_TEXTURE = 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'56\' height=\'56\' viewBox=\'0 0 56 56\'%3E%3Cpath d=\'M.6 0V56M0 .6H56\' fill=\'none\' stroke=\'%23EEE8DC\' stroke-opacity=\'.065\' stroke-width=\'1.2\' stroke-linecap=\'butt\' stroke-dasharray=\'2 5\'/%3E%3C/svg%3E")'


@pytest.fixture(scope="module", autouse=True)
def _thumb_cache():
    """源样图在本组测试里只读：同一文件/大小/mtime/尺寸/质量只编码一次，避免十几次重复编码整套图库。"""
    original = getattr(BD, "_thumb_bytes", None)
    if original is None:
        yield
        return

    @functools.lru_cache(maxsize=4096)
    def cached(path, size, modified_ns, m, q):
        return original(path, m, q)

    def once(path, m=BD.THUMB, q=60):
        info = path.stat()
        return cached(path, info.st_size, info.st_mtime_ns, m, q)

    BD._thumb_bytes = once
    try:
        yield
    finally:
        BD._thumb_bytes = original
        cached.cache_clear()


def _no_profile(tmp_path_factory) -> Path:
    p = tmp_path_factory.getbasetemp() / "no-profile"
    assert not p.exists()
    return p


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(_no_profile(tmp_path_factory)))  # 不存在的目录＝禁用私有层
    monkeypatch.delenv(BD.BRAND_DIR_ENV, raising=False)
    monkeypatch.delenv(BD.MG.MOTION_DIR_ENV, raising=False)  # 普通静态出口测试不受本机外置动态媒体目录影响


def _data(html: Path) -> dict:
    return json.loads(DATA_RE.search(html.read_text(encoding="utf-8")).group(1))


def _manifest(out: Path) -> list[str]:
    return json.loads((out / "img-manifest.json").read_text(encoding="utf-8"))["images"]


REAL_MOTION_GALLERY = Path(BD.MG.GALLERY_DIR)   # 导入时记下真实源目录；built 夹具会临时把 MG.GALLERY_DIR 指向合成副本
_SYNTHETIC_VIDEO = b"SYNTHETIC-TEST-PLACEHOLDER-NOT-A-VIDEO:"


def _synthetic_motion(base: Path) -> dict:
    """生成便携的合成动态媒体，仅用来测浏览器的图片加载路径与构建完整性；不是真实批准影片。

    - 临时复制真实 motion.js / motion.css，解析真实 motion-data.json，保留每个示范的全部字段，
      只把 poster_sha256 / video_sha256 换成临时合成文件的 SHA；真实数据与真实 SHA 一概不动；
    - poster.png 是真实有效的小 PNG（浏览器能解码），share.mp4 是明确的测试占位字节（不能播放、不能解码）；
    - 真实批准影片的存在与 SHA 由独立验收覆盖，这里不声称播放或解码验收，测试也没有点击播放。"""
    gallery, media = base / "motion-gallery-src", base / "motion-media"
    gallery.mkdir(parents=True)
    media.mkdir()
    for name in ("motion.js", "motion.css"):
        (gallery / name).write_bytes((REAL_MOTION_GALLERY / name).read_bytes())
    real = BD.MG.parse_text((REAL_MOTION_GALLERY / BD.MG.DATA_NAME).read_bytes())
    items = []
    for n, item in enumerate(real["items"]):
        buf = io.BytesIO()
        Image.new("RGB", (16, 9), (40 + n * 17 % 200, 120, 200)).save(buf, "PNG")
        poster, video = buf.getvalue(), _SYNTHETIC_VIDEO + item["id"].encode()
        idir = media / item["id"]
        idir.mkdir()
        (idir / "poster.png").write_bytes(poster)
        (idir / "share.mp4").write_bytes(video)
        row = {**item, "poster_sha256": BD.MG._sha(poster), "video_sha256": BD.MG._sha(video)}
        for field, filename, sha_key in BD.MG.OPTIONAL_MEDIA_FILES:
            if field not in item:
                continue
            extra = io.BytesIO()
            size = (16, 9) if field == "gif" else (9, 16)
            Image.new("RGB", size, (60, 130, 170)).save(extra, "GIF" if field == "gif" else "PNG")
            raw = extra.getvalue()
            (idir / filename).write_bytes(raw)
            row[sha_key] = BD.MG._sha(raw)
        items.append(row)
    doc = {**real, "items": items}
    (gallery / BD.MG.DATA_NAME).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    assert BD.MG.parse_text((gallery / BD.MG.DATA_NAME).read_bytes()) == doc   # 合成清单同样通过严格校验
    return {"gallery": gallery, "media": media, "doc": doc, "real": real}


@pytest.fixture(scope="module")
def synthetic_motion(tmp_path_factory):
    return _synthetic_motion(tmp_path_factory.mktemp("synthetic-motion"))   # 在扫描树之外，不写 Skill 内任何文件


@pytest.fixture(scope="module")
def built(tmp_path_factory, synthetic_motion):
    mp = pytest.MonkeyPatch()
    mp.setenv("SANSHENG_IMAGE_PROFILE", str(_no_profile(tmp_path_factory)))
    mp.delenv(BD.BRAND_DIR_ENV, raising=False)
    mp.delenv(BD.MG.MOTION_DIR_ENV, raising=False)
    mp.setattr(BD.MG, "GALLERY_DIR", synthetic_motion["gallery"])   # 仅此夹具：用合成源，真实 SHA 对应的媒体本机不一定有
    try:
        d = tmp_path_factory.mktemp("picker")
        yield d, BD.picker(d, motion_dir=synthetic_motion["media"])   # 显式传临时媒体目录，不依赖本机 assets 路径
    finally:
        mp.undo()


# ---------------- 模板与出口契约 ----------------

from html.parser import HTMLParser  # noqa: E402

MOTION_CSS_HREF = "/tools/image/motion.css"
MOTION_JS_SRC = "/tools/image/motion.js"
_RESOURCE_ATTRS = {"src", "srcset", "poster", "data", "background", "ping", "manifest", "action", "formaction"}
_FORBIDDEN_TAGS = {"iframe", "frame", "object", "embed", "base", "applet"}


class _AssetScan(HTMLParser):
    """只看真正的起始标签：注释、<script>/<style> 里的文字不会被当成元素。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.links: list[dict] = []
        self.scripts: list[dict] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag, attrs):
        names = [k for k, _ in attrs]
        if len(names) != len(set(names)):
            self.problems.append(f"<{tag}> 属性重复：{names}")
        for k in names:
            if re.fullmatch(r"on[a-z]+", k):
                self.problems.append(f"<{tag}> 带事件 handler：{k}")
        if tag in _FORBIDDEN_TAGS:
            self.problems.append(f"不允许的嵌入标签 <{tag}>")
        if tag == "link":
            self.links.append(dict(attrs))
        elif tag == "script":
            self.scripts.append(dict(attrs))
        else:
            for k in names:
                if k in _RESOURCE_ATTRS:
                    self.problems.append(f"<{tag}> 带外部资源属性 {k}")


def _template_static_asset_violations(html: str) -> list[str]:
    """纯模板校验：除正式首页同站的 motion.css / motion.js 两项外，不允许任何外链资源。
    精确比较路径与属性集合（不用前缀匹配）；公网、协议相对、data:、多余或重复的 link / script 都会被拒绝。"""
    scan = _AssetScan()
    scan.feed(html)
    scan.close()
    problems = list(scan.problems)
    if len(scan.links) != 1:
        problems.append(f"<link> 必须恰好一个（现在 {len(scan.links)} 个）")
    for link in scan.links:
        if link != {"rel": "stylesheet", "href": MOTION_CSS_HREF}:
            problems.append(f"<link> 必须恰为 rel=stylesheet href={MOTION_CSS_HREF}：{link}")
    external = [s for s in scan.scripts if "src" in s]
    inline = [s for s in scan.scripts if "src" not in s]
    if len(external) != 1:
        problems.append(f"带 src 的 <script> 必须恰好一个（现在 {len(external)} 个）")
    for s in external:
        if s != {"src": MOTION_JS_SRC, "defer": None}:
            problems.append(f"外部 <script> 必须恰为 src={MOTION_JS_SRC} defer：{s}")
    page_data = {"id": "page-data", "type": "application/json"}
    if sorted(inline, key=lambda s: len(s)) != [{}, page_data]:
        problems.append(f"内联 <script> 必须恰为一个无属性脚本加一个 page-data JSON：{inline}")
    return problems


def test_template_has_one_placeholder_and_no_external_or_preview_refs():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("__META__") == 1
    # One fixed, inline SVG texture is local; every other URL/data image remains forbidden.
    assert html.count(GRID_TEXTURE) == 1
    asset_checked_html = html.replace(GRID_TEXTURE, "")
    for bad in ("homepage-", "data-url", "fetch(", "../", "url(", "@import", "data:image", "<img src="):
        assert bad not in asset_checked_html, bad
    assert _template_static_asset_violations(html) == []
    assert 'data-asset-root="img/"' in html
    # 首页从作品开始；品牌只使用独立的静态 Logo。
    assert set(re.findall(r"[A-Za-z0-9_-]+\.webp", html)) == {"brand-logo.webp"}


_MOTION_LINK = f'<link rel="stylesheet" href="{MOTION_CSS_HREF}">'
_MOTION_SCRIPT = f'<script src="{MOTION_JS_SRC}" defer></script>'


def _swap(old: str, new: str):
    def apply(html: str) -> str:
        assert html.count(old) == 1, old   # 变异必须真的改到模板，不能空转
        return html.replace(old, new)
    return apply


_ASSET_COUNTEREXAMPLES = {
    "extra-link": _swap("</head>", '<link rel="stylesheet" href="/tools/image/extra.css"></head>'),
    "extra-src-script": _swap("</body>", '<script src="/tools/image/extra.js"></script></body>'),
    "duplicate-link": _swap(_MOTION_LINK, _MOTION_LINK + _MOTION_LINK),
    "duplicate-script": _swap(_MOTION_SCRIPT, _MOTION_SCRIPT + _MOTION_SCRIPT),
    "missing-link": _swap(_MOTION_LINK, ""),
    "missing-script": _swap(_MOTION_SCRIPT, ""),
    "replaced-css-url": _swap(MOTION_CSS_HREF, "/tools/image/other.css"),
    "replaced-js-url": _swap(MOTION_JS_SRC, "/tools/image/other.js"),
    "prefix-only-css-url": _swap(MOTION_CSS_HREF, MOTION_CSS_HREF + ".evil"),
    "public-js-url": _swap(MOTION_JS_SRC, "https://cdn.example.com/motion.js"),
    "protocol-relative-js-url": _swap(MOTION_JS_SRC, "//cdn.example.com/motion.js"),
    "data-js-url": _swap(MOTION_JS_SRC, "data:text/javascript,alert(1)"),
    "public-css-url": _swap(MOTION_CSS_HREF, "https://cdn.example.com/motion.css"),
    "extra-link-attribute": _swap(_MOTION_LINK, f'<link rel="stylesheet" href="{MOTION_CSS_HREF}" media="print">'),
    "script-loses-defer": _swap(" defer></script>", "></script>"),
    "module-inline-script": _swap("</body>", '<script type="module">1</script></body>'),
    "event-handler-on-body": _swap('<body data-asset-root="img/">', '<body data-asset-root="img/" onload="x()">'),
    "img-with-src": _swap("</body>", '<img src="https://evil.example/x.png"></body>'),
    "iframe": _swap("</body>", "<iframe></iframe></body>"),
    "preload-link": _swap("</head>", '<link rel="preload" href="/tools/image/motion.css"></head>'),
}


def test_template_static_asset_validator_accepts_current_template():
    assert _template_static_asset_violations(TEMPLATE.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(_ASSET_COUNTEREXAMPLES))
def test_template_static_asset_validator_rejects_counterexamples(name):
    html = TEMPLATE.read_text(encoding="utf-8")
    assert _template_static_asset_violations(_ASSET_COUNTEREXAMPLES[name](html)), name


def test_template_static_asset_validator_ignores_comments_and_strings_and_rejects_empty():
    html = TEMPLATE.read_text(encoding="utf-8")
    decoy = '<!-- <link href="//evil.example/x.css"><script src="//evil.example/x.js"></script> -->'
    assert _template_static_asset_violations(_swap("</head>", decoy + "</head>")(html)) == []
    assert _template_static_asset_violations("") != []


def test_template_never_writes_data_through_innerhtml():
    script = TEMPLATE.read_text(encoding="utf-8").split('<script id="page-data"')[1]
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "document.write" not in script


def test_single_placeholder_is_enforced(tmp_path, monkeypatch):
    for n, body in ((0, "<html></html>"), (2, "__META__ __META__")):
        bad = tmp_path / f"t{n}.html"
        bad.write_text(body, encoding="utf-8")
        monkeypatch.setattr(BD, "PICKER", bad)
        with pytest.raises(ValueError, match="__META__"):
            BD.picker(tmp_path / f"o{n}")


# ---------------- 页面体积、公开数据、用途与样图 ----------------

def test_page_is_small_and_images_are_external(built):
    d, html = built
    # 包内完整版本记录随页面分发，保留 300 KB 文本预算；图片仍必须外链。
    assert 0 < html.stat().st_size < 300_000, "页面本体应只含文字与数据，且不超过 300 KB"
    assert len(list((d / "img").glob("*.webp"))) >= 57 * 3
    assert "data:image" not in html.read_text(encoding="utf-8").replace(GRID_TEXTURE, "")


@pytest.mark.parametrize("content", ["", "x" * 300_000], ids=["empty", "oversized"])
def test_page_size_guard_rejects_empty_and_oversized_output(tmp_path, content):
    html = tmp_path / "index.html"
    html.write_text(content, encoding="utf-8")
    # 直接调用同一被测路径，确认空输出和超预算输出不能因后续图片断言而假绿。
    with pytest.raises(AssertionError, match="300 KB"):
        test_page_is_small_and_images_are_external((tmp_path, html))


def test_public_page_has_no_private_styles(built):
    _, html = built
    text = html.read_text(encoding="utf-8")
    assert not re.search(r'"id":\s*"S\d', text)
    assert all(re.fullmatch(r"C\d{2,3}", c["id"]) for c in _data(html)["cands"])


def test_every_use_has_its_own_sample_kind_and_palettes_have_stories(built):
    _, html = built
    meta = _data(html)
    ids = {s["id"] for s in meta["scenes"]}
    assert ids >= {"wxcover", "wxillus", "xhs", "ppt", "info", "comic4"} and "motion" not in ids
    assert len(ids) == 9, ids
    assert not any(k.startswith("mo") for c in meta["cands"] for k in c["imgs"]) and "motion" not in meta
    assert not any(k.startswith("mo") for k in meta["kinds"])
    assert "githubStars" in meta
    named = [p for p in meta["pals"] if p["colors"]]
    assert len(named) >= 16 and all(p["story"] and p["en"] and p["group"] for p in named)


def test_every_style_in_a_pool_has_that_uses_own_sample(built):
    _, html = built
    meta = _data(html)
    imgs = {c["id"]: set(c["imgs"]) for c in meta["cands"]}
    # 音乐/播客封面必须有自己的 1:1 方形样图（不能拿 2.35:1 公众号封面充数）
    siblings = {"cm": {"cm", "cx", "ce"}, "wxi": {"wxi", "wxt", "wxx"}}  # 同组的兄弟种类也算本用途样图（页面同样按组回退）
    missing = [(s["id"], c) for s in meta["scenes"] for c in s["pool"]
               if not (siblings.get(s["sample"], {s["sample"]}) & imgs[c])]
    assert missing == [], f"这些画风缺它所属用途的样图：{missing[:8]}"
    square = [s for s in meta["scenes"] if s["sample"] == "au"]
    assert square, "应有一个用方形音乐封面做样图的用途"
    assert all("au" in imgs[c] for s in square for c in s["pool"]), "方形用途不能用别的比例的样图充数"


def test_set_labels_cover_every_page(built):
    """详情里每张图标页型名：PPT 12 页、信息图 11 种都要有名字，且与入库的张数对得上。"""
    _, html = built
    meta = _data(html)
    assert len(meta["labels"]["ppt"]) == 12 and len(meta["labels"]["inf"]) == 11
    for c in meta["cands"]:
        for kind, cap in (("ppt", 12), ("inf", 11), ("xhs", 6)):
            n = sum(1 for k in c["imgs"] if k == kind or k.startswith(kind + "-"))
            assert n <= cap, (c["id"], kind, n)
    assert all(c.get("tone") and c.get("craft") for c in meta["cands"]), "三问推荐需要每个画风的气质与画法"


# ---------------- 嵌入数据与注册表、manifest 一致 ----------------

def test_embedded_data_matches_registry_and_manifest(built):
    d, html = built
    meta = _data(html)
    names = set(_manifest(d))
    reg = BD.registry(False)

    by_code: dict[str, list[str]] = {}
    shots: dict[str, set[str]] = {}
    compare: dict[str, set[str]] = {}
    for n in names:
        m = re.fullmatch(r"(C\d{2,3})-(.+)\.webp", n)
        if not m:
            continue
        code, kind = m.groups()
        if kind.startswith("pl-"):
            shots.setdefault(kind[3:], set()).add(code)
        elif kind.startswith("pc-"):
            compare.setdefault(code, set()).add(kind[3:])
        else:
            by_code.setdefault(code, []).append(kind)

    cands = {c["id"]: c for c in meta["cands"]}
    assert set(cands) == set(by_code)
    for code, c in cands.items():
        assert c["imgs"] == sorted(by_code[code]), code
        assert c["uses"] == [s["id"] for s in meta["scenes"] if code in s["pool"]], code
        assert c["name"] and c["essence"] and c["tone"] and c["craft"], code
    assert not re.search(r'"id":\s*"S\d', html.read_text(encoding="utf-8"))

    selection = BD.palette_showcase()
    reviewed_shots = {pal: {row["code"] for row in rows} for pal, rows in selection["cards"].items()}
    assert {k: {x["code"] for x in v} for k, v in meta["palShots"].items()} == reviewed_shots
    assert all(reviewed_shots[pal] <= shots[pal] for pal in reviewed_shots)
    for pid, lst in meta["palShots"].items():
        assert all(x["kind"] == f"pl-{pid}" and x["k"] in ("o", "i") and x["ratio"] > 0 for x in lst)
    assert {c: set(v["pals"]) for c, v in meta["palCompare"].items()} == {c: v - {"orig"} for c, v in compare.items() if c in selection["comparisons"] and "orig" in v and v - {"orig"}}
    assert all(v["ratio"] > 0 for v in meta["palCompare"].values())

    want_scenes = [s["id"] for s in reg["scenes"] if not s.get("hidden") and not s.get("site_hide") and s.get("use")]
    assert [s["id"] for s in meta["scenes"]] == want_scenes
    for s in meta["scenes"]:
        assert set(s["pool"]) <= set(cands) and set(s["alternates"]) <= set(s["pool"]) | set(cands)
        if s["image"]:
            assert s["image"] in names and s["image"].startswith(s["representative"] + "-")
            assert s["representative"] in cands
    assert [p["id"] for p in meta["pals"]] == [p["id"] for p in reg["palettes"]["palettes"]]
    named = [p for p in meta["pals"] if p["colors"]]
    assert len(named) >= 16 and all(p["story"] and p["en"] and p["group"] for p in named)

    assert meta["first"] == "wxcover" and meta["kinds"] == BD.SITE_KINDS and meta["labels"] == BD.KIND_LABELS
    stars = json.loads((BD.CT.ROOT / "gallery" / "github-meta.json").read_text(encoding="utf-8")).get("stars")
    assert meta["githubStars"] == (stars if isinstance(stars, int) and not isinstance(stars, bool) and stars > 0 else "")  # 0 星不展示
    assert meta["metrics"] == {"styles": len(cands), "scenes": len(want_scenes), "palettes": len(named), "samples": sum(n not in (*BD.BRAND_IMAGES, BD.BRAND_LOGO_NAME) and not n.endswith("-anchor.webp") and not n.startswith("hero-") for n in names)}

    assert "featured" not in meta  # 精选 12 区已删，首屏下面直接是画风库
    # 首屏展台：人工挑的「每种画风一张代表作」，静态图有单独的清晰图并登记进 manifest；一种画风只出现一次
    assert BD.HOME_CAROUSEL == PINNED_CAROUSEL
    motion = {m["id"]: m for m in json.loads((BD.CT.ROOT / "gallery" / "motion-data.json").read_text(encoding="utf-8"))["items"]}
    # 展台只放静态宽屏封面，不放动态版
    want = [(c, k) for c, k in PINNED_CAROUSEL if k in cands.get(c, {}).get("imgs", [])]
    assert [(c["id"], c["kind"]) for c in meta["carousel"]] == want
    ids = [c["id"] for c in meta["carousel"]]
    assert len(ids) == len(set(ids)), "展台里同一种画风只放一张"
    for c in meta["carousel"]:
        assert "motion" not in c
        assert c["name"] == cands[c["id"]]["name"] and c["scene"] and c["ratio"] > 0
        assert c["image"] == f"hero-{c['id']}-{c['kind']}.webp" and c["image"] in names
    # 首屏背景近景浮图只播这三支动态版
    assert BD.HERO_MOTION == PINNED_HERO_MOTION
    assert {m["id"] for m in meta["heroMotion"]} == set(PINNED_HERO_MOTION) & set(motion)

    plugin = json.loads((BD.CT.ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert meta["site"] == {"pkgVersion": plugin["version"], "releases": SITE_URL + "/releases",
                            "changelog": BD._release_history(plugin["version"])}
    assert meta["site"]["changelog"][0]["version"] == plugin["version"]


def test_manifest_lists_every_image_the_page_can_request(built):
    d, _ = built
    names = _manifest(d)
    assert names == sorted(set(names)) and {"logo.webp", "logo-poster.webp"} <= set(names)
    on_disk = {p.name for p in (d / "img").glob("*.webp")}
    assert on_disk <= set(names), "img/ 里不能有 manifest 没登记的多余图"
    assert all((d / "img" / n).stat().st_size > 0 for n in on_disk)


def test_public_package_without_brand_images_keeps_manifest_and_does_not_fake(built):
    d, _ = built
    assert not (d / "img" / "logo.webp").exists() and not (d / "img" / "logo-poster.webp").exists()
    assert {"logo.webp", "logo-poster.webp"} <= set(_manifest(d))
    # 产品字标随包提供；缺少外置动态演示不影响真正的静态品牌入口。
    assert (d / "img" / BD.BRAND_LOGO_NAME).read_bytes() == BD.BRAND_LOGO_PATH.read_bytes()
    with Image.open(d / "img" / BD.BRAND_LOGO_NAME) as logo:
        assert logo.n_frames == 1


@pytest.mark.parametrize("invalid", ["animated", "empty"])
def test_picker_rejects_invalid_static_brand_before_touching_output(tmp_path, monkeypatch, invalid):
    source = tmp_path / "bad-logo.webp"
    if invalid == "animated":
        Image.new("RGB", (16, 8), "red").save(
            source, format="WEBP", save_all=True,
            append_images=[Image.new("RGB", (16, 8), "blue")], duration=100, loop=0)
        with Image.open(source) as logo:
            assert logo.n_frames == 2
    else:
        source.write_bytes(b"")
    monkeypatch.setattr(BD, "BRAND_LOGO_PATH", source)
    out = tmp_path / "existing"
    out.mkdir()
    (out / "index.html").write_text("KEEP EXISTING PAGE", encoding="utf-8")
    with pytest.raises(ValueError, match="品牌 Logo"):
        BD.picker(out)
    assert (out / "index.html").read_text(encoding="utf-8") == "KEEP EXISTING PAGE"
    assert not (out / "img").exists()


def test_build_is_deterministic_and_does_not_touch_source(built, tmp_path):
    d, html = built
    before = {p: p.read_bytes() for p in (Path(BD.__file__), TEMPLATE)}
    again = BD.picker(tmp_path)
    assert again.read_bytes() == html.read_bytes()
    assert (tmp_path / "img-manifest.json").read_bytes() == (d / "img-manifest.json").read_bytes()
    assert sorted(p.name for p in (tmp_path / "img").iterdir()) == sorted(p.name for p in (d / "img").iterdir())
    assert before == {p: p.read_bytes() for p in before}
    assert not re.search(rb"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", html.read_bytes())  # 页面里没有生成时间戳


# ---------------- 合成动态媒体（仅测图片路径与构建完整性） ----------------

def test_synthetic_motion_media_and_sha_are_checked_by_real_build(built, synthetic_motion):
    d, html = built
    doc, real = synthetic_motion["doc"], synthetic_motion["real"]
    assert _data(html)["motionPreview"] == doc
    expected = BD.MG.expected_media(doc)
    assert expected and len(expected) == sum(len(BD.MG.item_media(i)) for i in doc["items"])
    on_disk = sorted(p.relative_to(d).as_posix() for p in (d / BD.MG.MEDIA_DIR).rglob("*") if p.is_file())
    assert on_disk == [rel for rel, _ in expected], "motion/ 下的媒体集合必须恰为清单登记的集合"
    assert all(BD.MG._sha((d / rel).read_bytes()) == sha for rel, sha in expected)
    manifest = json.loads((d / BD.MG.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["media"] == [{"path": rel, "sha256": sha} for rel, sha in expected]
    # 保留真实清单的全部项目字段，只换合成媒体的 SHA（没有碰真实数据）。
    strip = lambda it: {k: v for k, v in it.items() if not k.endswith("_sha256")}  # noqa: E731
    assert [strip(i) for i in doc["items"]] == [strip(i) for i in real["items"]]
    assert all(a["poster_sha256"] != b["poster_sha256"] and a["video_sha256"] != b["video_sha256"]
               for a, b in zip(doc["items"], real["items"]))
    assert (d / BD.MG.DATA_NAME).read_bytes() == (synthetic_motion["gallery"] / BD.MG.DATA_NAME).read_bytes()
    assert all((d / rel).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for rel, _ in expected if rel.endswith("poster.png"))


@pytest.mark.parametrize("damage", ["poster-bytes", "video-bytes", "missing-video"])
def test_synthetic_motion_wrong_sha_or_missing_media_is_rejected_by_build(tmp_path, monkeypatch, synthetic_motion, damage):
    media = tmp_path / "media"
    shutil.copytree(synthetic_motion["media"], media)
    first = synthetic_motion["doc"]["items"][0]["id"]
    if damage == "poster-bytes":
        with (media / first / "poster.png").open("ab") as f:
            f.write(b"x")
        match = "poster.png.*SHA-256 与清单不符"
    elif damage == "video-bytes":
        with (media / first / "share.mp4").open("ab") as f:
            f.write(b"x")
        match = "share.mp4.*SHA-256 与清单不符"
    else:
        (media / first / "share.mp4").unlink()
        match = "缺少媒体.*share.mp4"
    monkeypatch.setattr(BD.MG, "GALLERY_DIR", synthetic_motion["gallery"])
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=match):
        BD.picker(out, motion_dir=media)
    assert not (out / "index.html").exists() and not (out / BD.MG.MEDIA_DIR).exists()   # 校验在写任何文件之前完成
    BD.picker(tmp_path / "ok", motion_dir=synthetic_motion["media"])   # 对照：未损坏的合成媒体同一路径能通过


def test_static_build_without_motion_dir_ignores_environment_and_writes_no_media(tmp_path):
    assert BD.MG.MOTION_DIR_ENV not in os.environ   # 隔离夹具已清除
    BD.picker(tmp_path)
    assert not (tmp_path / BD.MG.MEDIA_DIR).exists() and (tmp_path / BD.MG.MANIFEST_NAME).is_file()


# ---------------- 品牌图输入边界 ----------------

def _brand(tmp_path: Path) -> Path:
    b = tmp_path / "brand-src"
    b.mkdir()
    (b / "logo.webp").write_bytes(FAKE_WEBP + b"a")
    (b / "logo-poster.webp").write_bytes(FAKE_WEBP + b"b")
    return b


def test_brand_dir_copies_exact_bytes_and_keeps_source(tmp_path):
    b = _brand(tmp_path)
    src = {p.name: p.read_bytes() for p in b.iterdir()}
    out = tmp_path / "o"
    BD.picker(out, brand_dir=b)
    assert {n: (out / "img" / n).read_bytes() for n in src} == src
    assert {p.name: p.read_bytes() for p in b.iterdir()} == src
    assert {"logo.webp", "logo-poster.webp"} <= set(_manifest(out))


def test_brand_dir_from_environment_equals_parameter(tmp_path, monkeypatch):
    b = _brand(tmp_path)
    BD.picker(tmp_path / "a", brand_dir=b)
    monkeypatch.setenv(BD.BRAND_DIR_ENV, str(b))
    BD.picker(tmp_path / "e")
    for n in ("index.html", "img-manifest.json", "img/logo.webp", "img/logo-poster.webp"):
        assert (tmp_path / "a" / n).read_bytes() == (tmp_path / "e" / n).read_bytes()


def test_brand_dir_pointing_at_output_img_survives_cleanup(tmp_path):
    b = _brand(tmp_path)
    out = tmp_path / "o"
    BD.picker(out, brand_dir=b)
    BD.picker(out, brand_dir=out / "img")  # 清理 img/ 之前必须先把品牌图读出来
    assert (out / "img" / "logo.webp").read_bytes() == FAKE_WEBP + b"a"


@pytest.mark.parametrize("damage", ["missing", "not_webp"])
def test_bad_brand_dir_fails_before_touching_output(tmp_path, damage):
    b = _brand(tmp_path)
    out = tmp_path / "o"
    BD.picker(out, brand_dir=b)
    snapshot = {p.name: p.read_bytes() for p in (out / "img").iterdir()}
    if damage == "missing":
        (b / "logo-poster.webp").unlink()
        match = "logo-poster.webp"
    else:
        (b / "logo.webp").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        match = "logo.webp"
    with pytest.raises(ValueError, match=match):
        BD.picker(out, brand_dir=b)
    assert {p.name: p.read_bytes() for p in (out / "img").iterdir()} == snapshot


def test_truncated_webp_header_is_rejected_before_output_cleanup(tmp_path):
    brand = tmp_path / "brand-invalid"
    brand.mkdir()
    for name in BD.BRAND_IMAGES:
        (brand / name).write_bytes(b"RIFF\x14\x00\x00\x00WEBPVP8 " + b"\x00" * 8)
    output = tmp_path / "output-invalid"
    (output / "img").mkdir(parents=True)
    marker = output / "img" / "existing.webp"
    marker.write_bytes(FAKE_WEBP)
    with pytest.raises(ValueError, match="无法解码"):
        BD.picker(output, brand_dir=brand)
    assert marker.read_bytes() == FAKE_WEBP


def test_private_build_writes_no_manifest(tmp_path):
    html = BD.picker(tmp_path, private=True)
    assert html.name == "index.private.html" and not (tmp_path / "img-manifest.json").exists()


# ---------------- 可序列化数据的转义 ----------------

def test_embedded_json_cannot_break_out_of_script(tmp_path, monkeypatch):
    payload = "</script><!--<img src=x onerror=window.__xss=1>&amp;"
    orig = BD.registry

    def evil(private=False):
        r = orig(private)
        for s in r["styles"]:
            s["zh"] = payload + s["code"]
        return r

    monkeypatch.setattr(BD, "registry", evil)
    monkeypatch.setattr(BD, "_github_stars", lambda: payload)
    html = BD.picker(tmp_path).read_text(encoding="utf-8")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("</script") == tpl.count("</script") and html.count("<!--") == tpl.count("<!--")
    meta = json.loads(DATA_RE.search(html).group(1))
    assert meta["githubStars"] == payload and all(c["name"] == payload + c["id"] for c in meta["cands"])


# ---------------- 浏览器行为（需要 playwright + chromium） ----------------

@contextlib.contextmanager
def serve(directory: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(directory), **k)

        def translate_path(self, path):
            if path.split("?")[0].rstrip("/") == "/tools/image":  # 线上就是这样：不带斜杠也直接给页面
                path = "/index.html"
            elif path.startswith("/tools/img/"):
                return str(directory / "__missing__")            # 资产根写成相对路径时会落到这里
            elif path.startswith("/tools/image/"):
                path = path[len("/tools/image"):]
            return super().translate_path(path)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api", reason=BROWSER_BOUNDARY)
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium 不可用（{exc}）。{BROWSER_BOUNDARY}")
        yield b
        b.close()


@contextlib.contextmanager
def open_page(browser, directory: Path, width=1440, height=900, init_script=None, **context_options):
    with serve(directory) as base:
        ctx = browser.new_context(viewport={"width": width, "height": height},
                                  permissions=["clipboard-read", "clipboard-write"], **context_options)
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: requests.append(r.url))
        page.base, page.requests = base, requests
        try:
            yield page
        finally:
            ctx.close()
        assert not errors, errors


def _open(page, frag="", slash=""):
    page.goto(f"{page.base}/tools/image{slash}{frag}")   # 注意：默认没有结尾斜杠
    page.wait_for_selector("#lib-grid > li", state="attached")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize('width', [1440, 1989, 390])
def test_hero_aligns_with_nav_and_benefit_rows_remain_readable(browser, built, width):
    d, _ = built
    with open_page(browser, d, width=width) as page:
        _open(page)
        nav = page.locator('.topnav-in').bounding_box()
        hero = page.locator('.hero-grid').bounding_box()
        assert abs(hero['x'] - nav['x']) < 1
        assert abs(hero['width'] - nav['width']) < 1
        if width > 900:
            stage = page.locator('#stage').bounding_box()
            assert abs(stage['width'] - (hero['width'] - 48) * .56) < 1
            assert abs(stage['x'] + stage['width'] - nav['x'] - nav['width']) < 1
            heading = page.locator('.hero-title').bounding_box()
            left = page.locator('#hero-left').bounding_box()
            assert abs(heading['width'] - left['width']) < 1
            assert abs(heading['x'] - left['x']) < 1
            assert heading['x'] + heading['width'] < stage['x']
            lead = page.locator('.hero-title-lead').bounding_box()
            accent = page.locator('.hero-title-accent').bounding_box()
            assert lead['y'] + lead['height'] < accent['y']
            assert accent['height'] > 2 * lead['height']
        assert page.locator('#top h1').count() == 1
        assert page.locator('.hero-scenes, .hero-reasons').count() == 0
        labels = page.locator('.hero-benefit-label')
        assert len(labels.all_text_contents()) == 4
        assert all(len(text) == 3 for text in labels.all_text_contents())
        assert page.locator('.hero-benefits li').evaluate_all('''rows => rows.every(row => {
            const label = row.querySelector('.hero-benefit-label');
            const copy = row.querySelector('.hero-benefit-copy');
            const a = label.getBoundingClientRect(), b = copy.getBoundingClientRect();
            const labelStyle = getComputedStyle(label), copyStyle = getComputedStyle(copy);
            const diff = parseFloat(labelStyle.fontSize) - parseFloat(copyStyle.fontSize);
            return a.right < b.left && diff >= 2 && diff <= 4
                && labelStyle.color === 'rgb(52, 199, 149)'
                && copyStyle.color !== labelStyle.color
                && copy.scrollWidth <= copy.clientWidth + 1;
        })''')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('slash', ['', '/'])
@pytest.mark.parametrize('has_brand', [True, False], ids=['brand-image', 'text-fallback'])
def test_brand_uses_packaged_asset_path_and_missing_images_keep_text(browser, built, tmp_path, slash, has_brand):
    d, _ = built
    out = tmp_path / 'site'
    shutil.copytree(d, out)
    if has_brand:
        (out / 'img' / 'brand-logo.webp').write_bytes(FAKE_WEBP)
    else:
        (out / 'img' / 'brand-logo.webp').unlink()
    with open_page(browser, out) as page:
        _open(page, slash=slash)
        images = page.locator('.brand .brand-logo')
        assert images.count() == 2, '页眉、页脚都应使用同一品牌图'
        assert images.evaluate_all('xs => xs.every(i => i.complete)')
        assert images.evaluate_all('xs => xs.every(i => new URL(i.src).pathname === "/tools/image/img/brand-logo.webp")')
        if has_brand:
            assert images.evaluate_all('xs => xs.every(i => i.naturalWidth > 0 && !i.hidden)')
            assert page.locator('.brand .brand-text').evaluate_all('xs => xs.every(x => x.hidden)')
        else:
            assert images.evaluate_all('xs => xs.every(i => i.hidden && i.naturalWidth === 0)')
            assert page.locator('.brand .brand-text').evaluate_all('xs => xs.every(x => !x.hidden)')
        assert not any(url.split('?')[0] == page.base + '/logo.webp' for url in page.requests)


STUB_CLIPBOARD = "window.__copied=null;navigator.clipboard.writeText=t=>{window.__copied=t;return Promise.resolve()}"


def _choose_detail_palette(page, palette):
    common = page.locator(f'#dlg-pal-common [data-detail-pal="{palette}"]')
    if common.is_visible():
        common.click()
    else:
        page.click('#dlg-pal-more')
        page.locator(f'#dlg-pal-all [data-detail-pal="{palette}"]').click()


def test_install_copy_has_a_manual_fallback_when_clipboard_is_blocked(browser, built):
    d, _ = built
    with open_page(browser, d) as page:
        _open(page)
        page.evaluate(STUB_CLIPBOARD)
        page.click('#install-copy')
        assert 'npx skills add sanshengai/sansheng-image -g' in page.evaluate('window.__copied')
        assert not page.locator('#install-panel').is_visible()
        page.evaluate("navigator.clipboard.writeText=()=>Promise.reject(new Error('denied'));document.execCommand=()=>false")
        page.click('#install-copy')
        page.wait_for_selector('#install-panel', state='visible')
        assert page.locator('#install-ask').evaluate('e=>e===document.activeElement')
        assert 'npx skills add sanshengai/sansheng-image -g' in page.inner_text('#install-ask')


@pytest.mark.parametrize('width', [1440, 390])
def test_sticky_tools_and_detail_palette_keyboard_remain_reachable(browser, built, width):
    d, html = built
    free = next(c['id'] for c in _data(html)['cands'] if c['recolor'] == 'free')
    with open_page(browser, d, width=width, height=844) as page:
        _open(page)
        page.locator('#library').scroll_into_view_if_needed()
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(150)
        for selector in ('#quiz-btn', '#tray-btn'):
            box = page.locator(selector).bounding_box()
            assert box and 0 <= box['x'] and box['x'] + box['width'] <= width
            assert 0 <= box['y'] < 400, '向下浏览后常用操作仍在吸顶栏'
        page.click('#tray-btn')
        page.wait_for_selector('#tray[open]')
        page.keyboard.press('Escape')
        _open(page, f'#s={free}')
        page.wait_for_selector('#dlg[open]')
        page.click('#dlg-pal-more')
        page.keyboard.press('Escape')
        assert page.locator('#dlg[open]').is_visible()
        assert not page.locator('#dlg-pal-panel').is_visible()
        assert page.locator('#dlg-pal-more').evaluate('e=>e===document.activeElement')
        _choose_detail_palette(page, 'zhusha')
        assert page.input_value('#dlg-palette') == 'zhusha'
        assert page.locator('#dlg-pal-common [data-detail-pal="zhusha"]').is_visible()
        assert '-zhusha' in page.inner_text('#dlg-copyline')


def _visible(page) -> set[str]:
    return set(page.eval_on_selector_all("#lib-grid > li:not([hidden])", "e=>e.map(x=>x.dataset.style)"))


def _first_card(page):
    return page.locator("#lib-grid > li:not([hidden]) .style-card").first


def _forbidden_picker_data_requests(requests, base):
    """动态清单仅允许当前测试站的精确地址；旧数据端点与预览资源仍拒绝。"""
    from urllib.parse import urlsplit
    allowed = base + "/tools/image/motion-data.json"
    return [u for u in requests
            if "homepage-" in u or (urlsplit(u).path.endswith("data.json") and u != allowed)]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9999/tools/image/data.json",
    "http://127.0.0.1:9999/tools/image/old-data.json",
    "http://127.0.0.1:9999/tools/image/motion-data.json?extra=1",
    "http://127.0.0.1:9999/tools/image/motion-data.json#extra",
    "http://127.0.0.1:9999/elsewhere/motion-data.json",
    "https://evil.example/tools/image/motion-data.json",
    "//evil.example/tools/image/motion-data.json",
    "http://127.0.0.1:9999/tools/image/homepage-preview.webp",
])
def test_picker_data_requests_allow_only_exact_same_site_motion_manifest(url):
    base = "http://127.0.0.1:9999"
    assert _forbidden_picker_data_requests([base + "/tools/image/motion-data.json"], base) == []
    assert _forbidden_picker_data_requests([url], base) == [url]


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_page_renders_all_blocks_without_overflow_and_only_requests_manifest_images(browser, built, size):
    d, html = built
    meta = _data(html)
    names = set(_manifest(d))
    on_disk = {p.name for p in (d / "img").glob("*.webp")}
    with open_page(browser, d, *size) as page:
        _open(page)
        for y in range(0, 12000, 1500):
            page.evaluate(f"window.scrollTo(0,{y})")
            page.wait_for_timeout(60)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert page.locator("#lib-grid > li").count() == len(meta["cands"])
        assert page.locator("#scene-grid, #feat-grid, .scene-card").count() == 0  # 场景卡区、精选区已删
        assert page.eval_on_selector_all("main > section", "e=>e.map(s=>s.id)") == ["library", "motion-showcase", "palettes"]
        assert page.inner_text("[data-metric=styles]").replace(",", "") == str(meta["metrics"]["styles"])
        assert page.locator("[data-metric=perStyle]").count() == 0
        assert page.inner_text("[data-metric=palettes]") == str(meta["metrics"]["palettes"])
        imgs = [u.split("?")[0] for u in page.requests if u.endswith(".webp")]
        assert all("/tools/image/img/" in u for u in imgs), [u for u in imgs if "/tools/image/img/" not in u][:3]
        assert {u.rsplit("/", 1)[1] for u in imgs} <= names
        assert _forbidden_picker_data_requests(page.requests, page.base) == []
        if on_disk:
            bad = page.evaluate("[...document.querySelectorAll('img')].filter(i=>i.src&&i.complete&&!(i.naturalWidth>0)).map(i=>i.src)")
            assert [b for b in bad if b.rsplit('/', 1)[1] in on_disk] == []


CARD_IMGS = "[...document.querySelectorAll('#lib-grid > li:not([hidden]) img')]"


@pytest.mark.parametrize("slash", ["", "/"], ids=["no-trailing-slash", "trailing-slash"])
def test_images_load_for_every_card_with_and_without_trailing_slash(browser, built, slash):
    """每个用途下画风库全部卡片的图都要能加载，不能有裂图；两种网址写法结果一致。"""
    d, html = built
    meta = _data(html)
    scene_ids = {s["id"] for s in meta["scenes"]}
    assert {p.name for p in (d / "img").glob("*.webp")}, "构建产物里没有任何样图，无法验证图片加载"
    with open_page(browser, d) as page:
        for scene in ("wxcover", "xhs", "ppt", "comic4", "audio"):
            assert scene in scene_ids, scene
            _open(page, f"#u={scene}", slash)
            page.reload()
            page.wait_for_selector("#lib-grid > li", state="attached")
            # 图片按需加载：测试里把当前可见的卡片全部强制加载
            page.evaluate(CARD_IMGS + ".forEach(i=>{if(!i.getAttribute('src')&&i.dataset.src)i.src=i.dataset.src})")
            page.wait_for_function(f"{CARD_IMGS}.filter(i=>i.getAttribute('src')).every(i=>i.complete)", timeout=60000)
            # 本地测试服务器在几十张图同时请求时偶有连接失败：没加载出来的重新请求一次；路径写错的图重试也照样裂
            page.evaluate(f"{CARD_IMGS}.filter(i=>i.getAttribute('src')&&!(i.naturalWidth>0)).forEach(i=>{{const s=i.src;i.src='';i.src=s;}})")
            page.wait_for_function(f"{CARD_IMGS}.filter(i=>i.getAttribute('src')).every(i=>i.complete)", timeout=60000)
            pool = next(s["pool"] for s in meta["scenes"] if s["id"] == scene)
            assert page.locator("#lib-grid > li:not([hidden]) img").count() == len(pool), scene
            checked = page.evaluate(f"{CARD_IMGS}.filter(i=>i.getAttribute('src')).length")
            assert checked >= len(pool), (scene, checked)
            bad = page.evaluate(f"{CARD_IMGS}.filter(i=>i.getAttribute('src')&&!(i.naturalWidth>0)).map(i=>i.src)")
            assert bad == [], (scene, bad[:5])


def _assert_no_retired_library_labels(page):
    # 历史更新日志应保留原文；这里只检查用户当前操作的画风库。
    live_text = page.locator('#library').inner_text()
    for label in ('其余画风', '试用中'):
        assert label not in live_text, label


@pytest.mark.parametrize('label', ['其余画风', '试用中'])
def test_retired_label_check_rejects_visible_library_controls(browser, built, label):
    d, _ = built
    with open_page(browser, d) as page:
        _open(page)
        _assert_no_retired_library_labels(page)
        page.eval_on_selector('#library', '(el, text) => { const b = document.createElement("button"); b.textContent = text; el.append(b); }', label)
        with pytest.raises(AssertionError, match=label):
            _assert_no_retired_library_labels(page)


def test_each_use_shows_its_pool_and_default_is_cover(browser, built):
    """用途页只放适合的画风；默认/旧全部链接回到封面；不再提供全部入口。"""
    d, html = built
    meta = _data(html)
    assert {s["id"] for s in meta["scenes"]}.isdisjoint({"tb-vocab", "tb-grammar"})
    board = next(s for s in meta["scenes"] if s["id"] == "board")
    assert board["name"] == "AI 短剧分镜" and sorted(board["pool"]) == sorted(
        "C01 C22 C24 C49 C56 C57 C58 C63 C69 C70".split())
    with open_page(browser, d) as page:
        for scene in meta["scenes"]:
            _open(page, f"#u={scene['id']}")
            assert _visible(page) == set(scene["pool"]), scene["id"]
            assert page.locator("#lib-grid > li:not([hidden])").count() == len(scene["pool"]), scene["id"]
            _assert_no_retired_library_labels(page)
        cover = next(sc for sc in meta["scenes"] if sc["id"] == "wxcover")
        for frag in ("", "#u=all", "#u=unknown"):
            _open(page, frag)
            assert _visible(page) == set(cover["pool"])
            assert page.locator('#lib-scenes .chip[data-scene="all"]').count() == 0
            assert page.get_attribute('#lib-scenes .chip[data-scene="wxcover"]', "aria-pressed") == "true"
        _assert_no_retired_library_labels(page)


def test_copied_code_roundtrips_defaults_and_modes(browser, built):
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    pals = {p["id"]: p for p in meta["pals"]}
    xhs = next(s for s in meta["scenes"] if s["id"] == "xhs")
    free = next(c for c in xhs["pool"] if cands[c]["recolor"] == "free")
    pal = next(p["id"] for p in meta["pals"] if p["colors"] and p["id"] not in ("orig", "brand"))
    with open_page(browser, d) as page:
        _open(page, "#u=xhs")
        page.click(f"[data-style={free}] .style-card")
        page.wait_for_selector("#dlg[open]")
        _choose_detail_palette(page, pal)
        page.click('#dlg-light button[data-v="-1"]')
        page.click('#dlg-sat button[data-v="1"]')
        line = page.inner_text("#dlg-copyline")
        code = SC.find(line)[0]
        sc = SC.parse(code)
        assert (sc.scene, sc.style, sc.palette, sc.light, sc.sat) == ("xhs", free, pal, -1, 1) and SC.validate(sc) == []
        assert SC.find(line) == [code]  # 整句里只有一个完整有效码
        expected = f"用叁笙生图帮我出一组{xhs['name']}：画风 {free} {cands[free]['name']}，色系「{pals[pal]['name']}」（{code}）。内容是："
        assert line == expected

        # 只用一次：复制出去的就是页面上显示的整句
        page.evaluate(STUB_CLIPBOARD)
        page.click("#dlg-copy")
        once = page.evaluate("window.__copied")
        assert once == line and once.startswith("用叁笙生图帮我出一组") and once.endswith(f"（{code}）。内容是：")
        assert SC.parse(SC.find(once)[0]).scene == "xhs"
        assert page.inner_text("#dlg-copy") == "已复制"
        page.wait_for_function("document.getElementById('dlg-copy').textContent === '复制给 AI'", timeout=5000)

        # 网址里带着同一个选择
        frag = page.evaluate("location.hash")
        q = parse_qs(frag.lstrip("#"))
        assert (q["u"], q["s"], q["p"], q["l"], q["t"]) == (["xhs"], [free], [pal], ["-1"], ["1"]), frag

        # 保存默认
        page.click("#dlg-default")
        assert page.inner_text("#tray-n") == "1"
        assert "已设为" in page.inner_text("#dlg-saved") and xhs["name"] in page.inner_text("#dlg-saved")
        saved = json.loads(page.evaluate("localStorage.getItem('sb-mine')"))
        assert saved == {"xhs": {"style": free, "code": code, "name": cands[free]["name"]}}
        page.click("#dlg-close")

        # 默认复制：把已保存的各用途汇总成一段话
        page.click("#tray-btn")
        page.click("#tray-copy")
        text = page.evaluate("window.__copied")
        assert text.startswith("请 AI 助手记住") and "没特别说明" in text
        assert f"- {xhs['name']}：画风 {free} {cands[free]['name']}（{code}）" in text
        page.click("#tray-close")

        # 刷新后「我的默认」仍在
        page.reload()
        page.wait_for_selector("#lib-grid > li", state="attached")
        assert page.inner_text("#tray-n") == "1"
        page.click("#tray-btn")
        page.wait_for_selector("#tray[open]")
        assert page.inner_text("#tray-list .tray-code") == code

        # 网址还原同一选择：全新打开带这段网址的页面，复制句不变
        page.goto("about:blank")
        _open(page, frag)
        page.wait_for_selector("#dlg[open]")
        assert page.inner_text("#dlg-copyline") == line


def test_locked_style_cannot_recolor_and_brand_needs_hex(browser, built):
    d, html = built
    meta = _data(html)
    locked = next((c["id"] for c in meta["cands"] if c["recolor"] == "locked"), None)
    assert locked, "数据里应有配色固定的画风"
    free = next(c["id"] for c in meta["cands"] if c["recolor"] == "free")
    with open_page(browser, d) as page:
        _open(page, f"#s={locked}")
        page.wait_for_selector("#dlg[open]")
        enabled = page.eval_on_selector_all("#dlg-palette option", "o=>o.filter(x=>!x.disabled).map(x=>x.value)")
        assert enabled == ["orig"]
        assert re.fullmatch(rf"sb2:[\w-]+/{locked}", SC.find(page.inner_text("#dlg-copyline"))[0])  # 配色固定：码里没有色系后缀
        _open(page, f"#s={free}")
        page.wait_for_selector("#dlg[open]")
        _choose_detail_palette(page, "brand")
        assert page.locator("#dlg-copy").is_disabled() and page.locator("#dlg-default").is_disabled()
        page.fill("#dlg-hex", "1F6F8B F4F1E8")
        code = SC.find(page.inner_text("#dlg-copyline"))[0]
        assert code.endswith("-hex.1F6F8B.F4F1E8") and SC.parse(code).custom == ("#1F6F8B", "#F4F1E8")
        assert page.locator("#dlg-copy").is_enabled()


@pytest.mark.parametrize('width', [1440, 390])
def test_palette_categories_stick_and_switch_without_losing_cards(browser, built, width):
    d, _ = built
    with open_page(browser, d, width=width, height=844) as page:
        _open(page, '#tab=palettes')
        total = page.locator('.pal-card').count()
        groups = page.locator('#pal-categories [data-group]').evaluate_all('xs => xs.map(x => x.dataset.group)')
        assert {'', '甜美', '自然', '东方'} <= set(groups) and len(groups) == len(set(groups))
        for group in ['东方', '自然', '甜美']:
            button = page.locator(f'#pal-categories [data-group="{group}"]')
            button.focus()
            page.keyboard.press('Enter')
            assert button.get_attribute('aria-pressed') == 'true'
            assert page.locator('.pal-group:visible').count() == 1
            assert page.locator('.pal-group:visible').get_attribute('data-group') == group
            page.evaluate('window.scrollBy({top:240,behavior:"instant"})')
            assert page.locator('#pal-nav').bounding_box()['y'] >= page.locator('#topnav').bounding_box()['height'] - 1
            assert page.locator('#pal-nav').bounding_box()['y'] < 150
        page.locator('#pal-categories [data-group=""]').click()
        assert page.locator('.pal-card:visible').count() == total
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_palette_real_shots_page_is_consumed(browser, built):
    d, html = built
    meta = _data(html)
    cand_ids = {c["id"] for c in meta["cands"]}
    with_shots = {pid: [x for x in v if x["code"] in cand_ids] for pid, v in meta["palShots"].items()}
    with_shots = {k: v for k, v in with_shots.items() if v}
    if not with_shots:
        pytest.skip("当前数据没有色系真图，色系真图页无法验证")
    named = [p for p in meta["pals"] if p["colors"] and p["id"] not in ("orig", "brand")]
    with open_page(browser, d) as page:
        _open(page, "#tab=palettes")
        assert page.locator(".pal-card").count() == len(named)
        for pid, v in with_shots.items():
            assert page.locator(f'.pal-card[data-p="{pid}"] .pal-shot').count() == len(v), pid
        pid, v = next(iter(with_shots.items()))
        page.click(f'.pal-card[data-p="{pid}"] .pal-shot >> nth=0')
        page.wait_for_selector("#dlg[open]")
        style = next(c for c in meta["cands"] if c["id"] == v[0]["code"])
        assert page.inner_text("#dlg-title") == style["name"]
        assert page.input_value("#dlg-palette") == ("orig" if style["recolor"] == "locked" else pid)
        page.click("#dlg-close")
        page.click(f'[data-usepal="{pid}"]')
        assert page.get_attribute(f'[data-usepal="{pid}"]', "aria-pressed") == "true"
        assert page.get_attribute(f'#lib-palettes .chip[data-pal="{pid}"]', "aria-pressed") == "true"
        assert page.locator('#pal-jump, #pal-entry').count() == 0
        assert page.inner_text("#palettes-title") == "色系图鉴"
        page.evaluate("window.scrollTo(0, document.getElementById('library').offsetTop)")
        page.click("#nav-pal")  # 唯一入口保留在全局导航
        page.wait_for_function("Math.abs(document.getElementById('palettes').getBoundingClientRect().top) < innerHeight / 2", timeout=5000)



_HIDDEN_PALS = """(() => { const box = document.getElementById('lib-palettes'), bb = box.getBoundingClientRect();
  return [...box.querySelectorAll('.chip[data-pal]')].filter(c => { const r = c.getBoundingClientRect();
    return c.hidden || r.bottom > bb.bottom + 1 || r.right > bb.right + 1 || r.left < bb.left - 1; }).map(c => c.dataset.pal); })()"""


@pytest.mark.parametrize("width", [1920, 1440, 1000, 390, 360, 320])
def test_every_palette_chip_is_reachable_when_folded(browser, built, width):
    """默认单排，展开全部；非推荐项收起、刷新和改变宽度后仍可见，隐藏项不接受键盘焦点。"""
    d, _ = built
    with open_page(browser, d, width=width) as page:
        _open(page, "")
        hidden = page.evaluate(_HIDDEN_PALS)
        more = page.locator("#pal-more")
        assert hidden and more.is_visible() and more.get_attribute("aria-expanded") == "false"
        tops = page.eval_on_selector_all("#lib-palettes .chip:not([hidden])", "cs => cs.map(c => Math.round(c.getBoundingClientRect().top))")
        assert len(set(tops)) == 1, tops
        assert page.eval_on_selector_all("#lib-palettes .chip[hidden]", "cs => cs.every(c => c.getClientRects().length === 0)")
        more.click()
        assert more.get_attribute("aria-expanded") == "true"
        assert page.evaluate(_HIDDEN_PALS) == []
        pals = page.eval_on_selector_all("#lib-palettes .chip[data-pal]", "cs => cs.map(c => c.dataset.pal)")
        last = pals[-1]
        page.click(f'#lib-palettes .chip[data-pal="{last}"]')
        assert page.get_attribute(f'#lib-palettes .chip[data-pal="{last}"]', "aria-pressed") == "true"
        more.click()
        assert more.get_attribute("aria-expanded") == "false"
        assert last not in page.evaluate(_HIDDEN_PALS)
        page.set_viewport_size({"width": 360, "height": 900})
        assert page.locator(f'#lib-palettes .chip[data-pal="{last}"]').is_visible()
        assert last not in page.evaluate(_HIDDEN_PALS)
        page.reload()
        page.wait_for_selector('#lib-palettes .chip[aria-pressed="true"]')
        assert page.get_attribute(f'#lib-palettes .chip[data-pal="{last}"]', "aria-pressed") == "true"
        assert last not in page.evaluate(_HIDDEN_PALS)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "手机页面横向溢出"


_MOTION_CARDS = "[...document.querySelectorAll('#lib-grid > li.motion-lib-item')].map(l => l.dataset.style).sort()"


def test_library_motion_badges_follow_scene_and_respect_reduced_motion(browser, built, synthetic_motion):
    """只给精选中已登记场景的画风加角标；背景退出作品墙；减少动效时不自动加载视频。"""
    d, _ = built
    doc = json.loads(json.dumps(synthetic_motion["doc"]))
    # 本例覆盖旧的场景角标／逐件标签兼容入口；正式分类作品墙由 motion_gallery 专项测试覆盖。
    for key in ('showcase', 'categories', 'default_category'):
        doc.pop(key, None)
    for m in doc['items']:
        m.pop('category', None)
    # 保留一条明确关联场景的合成示范，避免未准入的新作品被测试夹具错误地接进场景池。
    next(m for m in doc["items"] if m["id"] == "moving")["placement"] = "featured"
    for m in doc["items"]:
        if m["id"] in {"paper-city", "travel-journal"}:
            m["placement"] = "featured"  # 只在合成输入验证作品切换，不改变正式采用状态。
    items = [m for m in doc["items"] if m.get("placement", "featured") == "featured" and m["style_code"] and m["homepage_scene"]]
    scenes = {m["homepage_scene"] for m in items}
    assert scenes, "合成数据里没有挂到场景的动态示例，无法验证角标"
    with open_page(browser, d) as page:
        page.route("**/motion-data.json", lambda route: route.fulfill(json=doc))
        _open(page, "#u=wxillus")
        page.wait_for_selector("#lib-grid > li.motion-lib-item", state="attached")
        assert page.evaluate(_MOTION_CARDS) == sorted({m["style_code"] for m in items})
        for sid in sorted(scenes):
            _open(page, f"#u={sid}")
            page.wait_for_function(f"document.getElementById('lib-grid').dataset.scene === '{sid}'")
            want = sorted({m["style_code"] for m in items if m["homepage_scene"] == sid})
            page.wait_for_function(f"JSON.stringify({_MOTION_CARDS}) === JSON.stringify({want!r})".replace("'", '"'))
            assert page.locator("#lib-grid > li.motion-lib-item .motion-badge").first.inner_text() == "会动"
        for sid in ("wxcover", "board", "ppt"):
            _open(page, f"#u={sid}")
            page.wait_for_function(f"document.getElementById('lib-grid').dataset.scene === '{sid}'")
            assert page.evaluate(_MOTION_CARDS) == [], sid
        featured = [m for m in doc["items"] if m.get("placement", "featured") == "featured"]
        assert page.locator("#motion-showcase [role='tab']").count() == 1
        assert page.locator("#motion-selected .motion-card").count() == len(featured)
        assert not page.locator("#motion-showcase .motion-brand").evaluate("e => e.open")
        assert page.locator("#motion-selected a[download$='.gif']").count() == sum("gif" in m for m in featured)
        assert page.locator("#motion-selected .motion-source img[alt^='原图：']").count() == sum("source" in m for m in featured)
        old_video = page.locator("#motion-selected video").first.element_handle()
        page.locator("#motion-showcase [role='tab']").first.press("Home")
        assert page.locator("#motion-selected .motion-caption h3").first.inner_text() == featured[0]["title"]
        assert page.locator("#motion-selected video").count() == len(featured)
        assert old_video.evaluate("v => [v.isConnected, v.getAttribute('src')]") == [False, None]
        assert page.locator("#motion-showcase [role='tab'][aria-selected='true']").inner_text() == f"全部 {len(featured)}"
    with open_page(browser, d, reduced_motion="reduce") as page:
        page.route("**/motion-data.json", lambda route: route.fulfill(json=doc))
        _open(page, f"#u={items[0]['homepage_scene']}")
        li = page.locator("#lib-grid > li.motion-lib-item:not([hidden])").first
        li.wait_for(state="attached")
        li.scroll_into_view_if_needed()
        li.locator(".style-card").hover()
        page.wait_for_timeout(600)
        assert li.evaluate("l => [l.querySelector('video').getAttribute('src'), getComputedStyle(l.querySelector('.motion-badge')).display]") == [None, "none"]
        page.locator("#motion-showcase").scroll_into_view_if_needed()
        assert page.eval_on_selector_all("#motion-showcase video", "v => v.every(e => !e.getAttribute('src'))")
        assert page.locator("#motion-showcase .motion-toggle").first.inner_text() == "播放动态"

# ---------------- GitHub 入口、安装命令与来源措辞 ----------------

def _star_text(v) -> str:
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)) and v >= 0:
        return f"{v:,}"
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ""


def _norm(text: str) -> str:
    return " ".join(text.split())


def test_hero_github_button_install_commands_and_origin_wording(browser, built):
    d, html = built
    meta = _data(html)
    stars = _star_text(meta["githubStars"])
    with open_page(browser, d) as page:
        _open(page)
        btn = page.locator("#github-btn")
        assert _norm(btn.inner_text()) == (f"GitHub ★ {stars}" if stars else "GitHub")
        assert btn.get_attribute("href") == SITE_URL
        page.click("#install-btn")
        assert page.is_visible("#install-panel") and page.get_attribute("#install-btn", "aria-expanded") == "true"
        assert "npx skills add sanshengai/sansheng-image -g" in page.inner_text("#install-cmd")
        assert "npx skills add sanshengai/sansheng-image -g" in page.inner_text("#install-ask")
        assert f"git clone {SITE_URL}.git" in page.text_content("#install-git")  # 折叠在「其他装法」里，读文本不要求可见
        assert "与相关创作者无关联" in page.inner_text(".foot-note")
        assert page.locator(".foot-links a").count() == 3
        assert page.locator(".foot-links button").count() == 1
        assert page.locator("#product-version").inner_text() == "v" + meta["site"]["pkgVersion"]
        for trigger in ("#product-version", "#releases-link"):
            before = page.url
            assert page.get_attribute(trigger, "href") is None
            page.click(trigger)
            page.wait_for_selector("#release-dialog[open]")
            assert page.url == before
            versions = meta["site"]["changelog"]
            assert page.locator("#release-nav [role=tab]").count() == len(versions)
            page.locator("#release-tab-0").press("ArrowDown")
            assert page.locator("#release-panel h3").inner_text() == "v" + versions[1]["version"]
            page.keyboard.press("Escape")
            assert not page.locator("#release-dialog").is_visible()
            assert page.locator(trigger).evaluate("e => e === document.activeElement")

        with_origin = next(c for c in meta["cands"] if c.get("origin"))
        _open(page, f"#s={with_origin['id']}")
        page.wait_for_selector("#dlg[open]")
        origin = page.inner_text("#dlg-origin")
        assert origin.startswith("类似 ") and with_origin["origin"] in origin and "的风格" in origin
        assert "不代表与创作者有关" in origin and "原作灵感" not in origin

        without = next((c for c in meta["cands"] if not c.get("origin")), None)
        if without:
            _open(page, f"#s={without['id']}")
            page.wait_for_selector("#dlg[open]")
            assert page.is_hidden("#dlg-origin")


@pytest.mark.parametrize("stars,want", [("1.2k", "GitHub ★ 1.2k"), (1234, "GitHub ★ 1,234")])
def test_github_star_snapshot_is_shown_as_given(browser, tmp_path, monkeypatch, stars, want):
    monkeypatch.setattr(BD, "_github_stars", lambda: stars)
    BD.picker(tmp_path)
    with open_page(browser, tmp_path) as page:
        _open(page)
        assert _norm(page.inner_text("#github-btn")) == want


@pytest.mark.parametrize("width", [360, 390])
def test_phone_copy_actions_fit_viewport(browser, built, width):
    d, _ = built
    with open_page(browser, d, width, 844) as page:
        _open(page, "#u=xhs")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        _first_card(page).click()
        page.wait_for_selector("#dlg[open]")
        dlg = page.eval_on_selector("#dlg", "e=>{const r=e.getBoundingClientRect();return [r.left,r.right,e.scrollWidth-e.clientWidth]}")
        assert dlg[0] >= 0 and dlg[1] <= width + 1 and dlg[2] <= 1, dlg
        for sel in ("#dlg-copy", "#dlg-default", "#dlg-copyline"):
            page.locator(sel).scroll_into_view_if_needed()
            assert page.is_visible(sel), sel
            box = page.eval_on_selector(sel, "e=>{const r=e.getBoundingClientRect();return [r.left,r.right,e.scrollWidth-e.clientWidth]}")
            assert box[0] >= dlg[0] - 1 and box[1] <= dlg[1] + 1 and box[1] <= width + 1, (sel, box)
            assert box[2] <= 1, (sel, "文字被截断", box)
        assert page.inner_text("#dlg-default").startswith("设为")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


# ---------------- 搜索与「帮我挑」三问 ----------------

OLD_TONES = ["温暖治愈", "清新明亮", "专业克制", "东方雅致", "个性潮流", "可爱童趣"]   # 选项顺序
OLD_CRAFTS = ["手绘插画", "扁平图形", "立体质感", "漫画动画", "海报版式"]


def _hay(c: dict, scenes=()) -> str:
    by_id = {x['id']: x for x in scenes}
    uses = ' '.join(by_id[u]['name'] + ' ' + by_id[u].get('short', '') for u in c['uses'] if u in by_id)
    return " ".join([c["id"], c["name"], c.get("origin") or "", c.get('family') or '', c.get('legacyFamily') or '', c.get('essence') or '', " ".join(c["tone"]), c["craft"], uses]).lower()


def _match(c: dict, q: str, scenes=()) -> bool:
    return all(w in _hay(c, scenes) for w in q.strip().lower().split())


def _ref_picks(meta: dict, scene_id: str, tone, craft) -> list[str]:
    """独立再算一遍推荐公式：气质 +2、画法 +1.5、场景默认/备选 +0.5，同分按编号，命中分>=1 的不足 3 个就用全表，取前 5。"""
    sc = next(s for s in meta["scenes"] if s["id"] == scene_id)
    pref = [sc["default"], *sc["alternates"]]
    rows = []
    for c in meta["cands"]:
        if scene_id not in c["uses"]:
            continue
        s = (2 if tone and tone in c["tone"] else 0) + (1.5 if craft and c["craft"] == craft else 0) + (0.5 if c["id"] in pref else 0)
        rows.append((s, c["id"]))
    rows.sort(key=lambda r: (-r[0], r[1]))
    hit = [r for r in rows if r[0] >= 1]
    return [r[1] for r in (hit if len(hit) >= 3 else rows)[:5]]


def _picks(page) -> list[str]:
    return page.eval_on_selector_all("#quiz-res .quiz-pick", "e=>e.map(x=>x.dataset.qpick)")


def test_search_matches_id_name_origin_family_essence_tone_craft_and_use_names(browser, built):
    d, html = built
    meta = _data(html)
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    c_id = cands[0]
    c_name = next(c for c in cands if len(c["name"]) >= 2)
    c_origin = next((c for c in cands if re.search(r"[A-Za-z]{3,}", c.get("origin") or "")), None)
    cases = [("id 小写", c_id["id"].lower()), ("中文名", c_name["name"][:2]),
             ("id+气质 两词都要命中", f'{c_id["id"].lower()} {c_id["tone"][0]}'),
             ("气质", c_id["tone"][0]), ("画法", c_id["craft"])]
    cases += [('家族', c_id['family']), ('特点', c_id['essence'].split('；')[0]), ('用途名', meta['scenes'][0]['name'])]
    if c_origin:
        cases.append(("原作 大写", re.search(r"[A-Za-z]{3,}", c_origin["origin"]).group(0).upper()))
    else:
        o = next(c for c in cands if c.get("origin"))
        cases.append(("原作", o["origin"][:3]))
    with open_page(browser, d) as page:
        _open(page)
        for label, q in cases:
            page.fill("#lib-q", q)
            want = {c["id"] for c in cands if _match(c, q, meta["scenes"])}
            assert want, label
            assert _visible(page) == want, (label, q)
            if label not in ("气质", "画法", "家族", "特点", "用途名"):
                assert len(want) < len(cands), (label, "搜索词没有收窄结果")
            assert page.is_hidden("#lib-empty") and page.is_visible("#lib-q-clear"), label
            assert str(len(want)) in page.inner_text("#lib-status"), label
        page.fill("#lib-q", "")
        assert len(_visible(page)) == len(cands) and page.is_hidden("#lib-q-clear")


def test_search_intersects_scene_shows_empty_message_and_clears(browser, built):
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    found = None
    for sc in meta["scenes"]:
        pool = set(sc["pool"])
        for tok in dict.fromkeys(t for c in cands.values() for t in [*c["tone"], c["craft"]]):
            allm = {i for i, c in cands.items() if _match(c, tok, meta["scenes"])}
            inter = allm & pool
            if inter and inter != allm and inter != pool:    # 场景和搜索各自都会排除一部分，交集才有区别
                found = (sc, tok, pool, allm, inter)
                break
        if found:
            break
    assert found, "数据里找不到能区分「场景∩搜索」的场景与搜索词：交集行为无法验证，请检查数据而不是跳过"
    sc, tok, pool, allm, inter = found
    xss = '<img src=x onerror="window.__xss=1">'
    with open_page(browser, d) as page:
        _open(page, f"#u={sc['id']}")
        assert _visible(page) == pool
        page.fill("#lib-q", tok)
        assert _visible(page) == inter
        assert tok in page.inner_text("#lib-status") and sc["name"] in page.inner_text("#lib-status")
        # 在场景内搜不到：明确提示，且恶意字符只当文字
        page.fill("#lib-q", xss)
        assert _visible(page) == set()
        msg = page.inner_text("#lib-empty")
        assert page.is_visible("#lib-empty") and xss in msg and "没有匹配" in msg and sc["name"] in msg
        assert page.locator("#lib-empty img").count() == 0 and page.evaluate("window.__xss") is None
        # 清除按钮：输入框清空、回到场景全部画风、焦点回到输入框
        page.click("#lib-q-clear")
        assert page.input_value("#lib-q") == "" and _visible(page) == pool and page.is_hidden("#lib-empty")
        assert page.evaluate("document.activeElement.id") == "lib-q"
        # 先搜后换场景：切到封面仍与搜索取交集
        page.fill("#lib-q", tok)
        page.click('#lib-scenes .chip[data-scene="wxcover"]')
        cover = next(sc for sc in meta["scenes"] if sc["id"] == "wxcover")
        assert _visible(page) == allm & set(cover["pool"])


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_three_questions_use_scene_pool_and_real_tone_craft_fields(browser, built, size):
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    all_tones = {t for c in cands.values() for t in c["tone"]}
    all_crafts = {c["craft"] for c in cands.values()}
    sc = max(meta["scenes"], key=lambda s: len(s["pool"]))
    pool_ids = set(sc["pool"])
    tone = next(t for t in OLD_TONES if any(t in cands[i]["tone"] for i in pool_ids))
    craft = next(k for k in OLD_CRAFTS if any(cands[i]["craft"] == k for i in pool_ids))
    with open_page(browser, d, *size) as page:
        _open(page)
        page.click("#quiz-btn")
        page.wait_for_selector("#quiz[open]")
        labels = lambda sel: page.eval_on_selector_all(sel + " .chip", "e=>e.map(x=>x.textContent)")  # noqa: E731
        # 选项只来自页面数据里真实存在的气质、画法，不凭空多出来
        assert labels("#quiz-u") == [s["name"] for s in meta["scenes"]]
        assert labels("#quiz-t") == [t for t in OLD_TONES if t in all_tones] and labels("#quiz-t")
        assert labels("#quiz-k") == [k for k in OLD_CRAFTS if k in all_crafts] and labels("#quiz-k")
        page.click(f'#quiz-u .chip[data-v="{sc["id"]}"]')
        base = _picks(page)
        assert base == _ref_picks(meta, sc["id"], None, None) and 0 < len(base) <= 5 and set(base) <= pool_ids
        page.click(f'#quiz-t .chip[data-v="{tone}"]')
        page.click(f'#quiz-k .chip[data-v="{craft}"]')
        picks = _picks(page)
        assert picks == _ref_picks(meta, sc["id"], tone, craft) and set(picks) <= pool_ids and len(picks) <= 5
        top = cands[picks[0]]
        assert tone in top["tone"] or top["craft"] == craft   # 气质或画法真的参与了排序
        # 再点一次同一个选项＝取消，结果回到只选了用途的状态
        page.click(f'#quiz-t .chip[data-v="{tone}"]')
        page.click(f'#quiz-k .chip[data-v="{craft}"]')
        assert _picks(page) == base
        assert page.get_attribute(f'#quiz-t .chip[data-v="{tone}"]', "aria-pressed") == "false"
        # 换一个用途，结果只来自那个用途的画风池
        other = next(s for s in meta["scenes"] if s["id"] != sc["id"] and s["pool"])
        page.click(f'#quiz-u .chip[data-v="{other["id"]}"]')
        assert set(_picks(page)) <= set(other["pool"]) and _picks(page) == _ref_picks(meta, other["id"], None, None)
        # 手机和桌面都不横向溢出，对话框在视口内
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        box = page.eval_on_selector("#quiz", "e=>{const r=e.getBoundingClientRect();return [r.left,r.right,e.scrollWidth-e.clientWidth,innerWidth]}")
        assert box[0] >= 0 and box[1] <= box[3] + 1 and box[2] <= 1, box


def test_quiz_recommends_only_from_the_chosen_use(browser, built):
    """黏土、毛绒、积木等立体可爱画风大多不做 PPT：过滤失效时它们会排进推荐。"""
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    pool = set(next(s["pool"] for s in meta["scenes"] if s["id"] == "ppt"))
    tone, craft = "可爱童趣", "立体质感"
    outsiders = {i for i, c in cands.items() if i not in pool and tone in c["tone"] and c["craft"] == craft}
    assert outsiders, "数据里应有「可爱童趣 + 立体质感」却不做 PPT 的画风，否则这条过滤检查是空转"
    with open_page(browser, d) as page:
        _open(page, "#u=xhs")
        page.click("#quiz-btn")
        page.wait_for_selector("#quiz[open]")
        page.click('#quiz-u .chip[data-v="ppt"]')
        page.click(f'#quiz-t .chip[data-v="{tone}"]')
        page.click(f'#quiz-k .chip[data-v="{craft}"]')
        picks = _picks(page)
        assert 1 <= len(picks) <= 5 and all(p in pool for p in picks), picks
        assert not set(picks) & outsiders and picks == _ref_picks(meta, "ppt", tone, craft)
        first = cands[picks[0]]
        assert tone in first["tone"] or first["craft"] == craft
        page.click(f'.quiz-pick[data-qpick="{picks[0]}"]')
        page.wait_for_selector("#dlg[open]")
        assert SC.find(page.inner_text("#dlg-copyline"))[0].startswith(f"sb2:ppt/{picks[0]}")


def test_recommendation_click_opens_real_detail_with_selected_scene_and_code(browser, built):
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    sc = next(s for s in meta["scenes"] if s["id"] != meta["first"] and len(s["pool"]) >= 3)
    with open_page(browser, d) as page:
        _open(page)                                   # 画风库默认封面，三问里另选场景
        assert page.get_attribute('#lib-scenes .chip[data-scene="wxcover"]', "aria-pressed") == "true"
        page.click("#quiz-btn")
        page.wait_for_selector("#quiz[open]")
        page.click(f'#quiz-u .chip[data-v="{sc["id"]}"]')
        want = _picks(page)[1]                        # 点第二个，防止只是碰巧点了默认项
        page.click(f'.quiz-pick[data-qpick="{want}"]')
        page.wait_for_selector("#dlg[open]")
        # 等 quiz 的异步 close 事件收尾，再看锁滚动和焦点没被它抢走
        page.wait_for_function("document.documentElement.classList.contains('lock') && document.getElementById('quiz').open === false", timeout=2000)
        assert page.is_hidden("#quiz") and page.evaluate("document.documentElement.classList.contains('lock')")
        assert page.inner_text("#dlg-title") == cands[want]["name"] and page.inner_text("#dlg-id").startswith(want)
        assert page.get_attribute('#lib-scenes .chip[data-scene="wxcover"]', "aria-pressed") == "false"
        assert page.get_attribute(f'#lib-scenes .chip[data-scene="{sc["id"]}"]', "aria-pressed") == "true"
        assert page.eval_on_selector_all('#dlg-uses button[aria-pressed="true"]', "e=>e.map(x=>x.dataset.for)") == [sc["id"]]
        parsed = SC.parse(SC.find(page.inner_text("#dlg-copyline"))[0])
        assert (parsed.scene, parsed.style) == (sc["id"], want) and SC.validate(parsed) == []
        frag = page.evaluate("location.hash")
        assert f"u={sc['id']}" in frag and f"s={want}" in frag
        assert _visible(page) == set(sc["pool"])      # 底下的画风库也已切到这个场景
        page.keyboard.press("Escape")
        page.wait_for_selector("#dlg:not([open])", state="attached")
        page.wait_for_function("document.activeElement.id === 'quiz-btn' && !document.documentElement.classList.contains('lock')", timeout=2000)
        assert page.evaluate("document.activeElement.id") == "quiz-btn"
        assert not page.evaluate("document.documentElement.classList.contains('lock')")


def test_three_questions_can_be_dismissed_with_escape_and_close_with_focus_restored(browser, built):
    d, _ = built
    with open_page(browser, d) as page:
        _open(page)
        for how in ("escape", "button", "backdrop"):
            page.click("#quiz-btn")
            page.wait_for_selector("#quiz[open]")
            assert page.evaluate("document.documentElement.classList.contains('lock')")
            if how == "escape":
                page.keyboard.press("Escape")
            elif how == "button":
                page.click("#quiz-close")
            else:
                page.mouse.click(3, 3)                # 对话框外的遮罩
            page.wait_for_selector("#quiz:not([open])", state="attached")
            # 原生 dialog.close 事件异步派发；等待焦点和滚动锁的实际收尾，而非只等待 open 属性移除。
            page.wait_for_function("document.activeElement.id === 'quiz-btn' && !document.documentElement.classList.contains('lock')", timeout=2000)
            assert page.evaluate("document.activeElement.id") == "quiz-btn", how
            assert not page.evaluate("document.documentElement.classList.contains('lock')"), how


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_search_row_is_compact_and_fits_viewport(browser, built, size):
    d, _ = built
    with open_page(browser, d, *size) as page:
        _open(page)
        page.fill("#lib-q", "不存在的画风zzqq")
        r = page.eval_on_selector("#lib-q", "e=>{const b=e.getBoundingClientRect();return [b.left,b.right,b.height,innerWidth]}")
        assert r[0] >= 0 and r[1] <= r[3] and r[2] >= 36, r
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        bar = page.eval_on_selector("#lib-bar", "e=>e.getBoundingClientRect().height")
        assert bar <= size[1] * 0.4, bar               # 吸顶筛选条加上搜索行后仍然紧凑
        assert page.is_visible("#lib-empty")


# ---------------- 数据当文字显示、缺项有明确回退 ----------------

def test_data_with_markup_is_shown_as_text_not_executed(browser, tmp_path, monkeypatch):
    payload = '<img src=x onerror="window.__xss=1">'
    orig = BD.registry

    def evil(private=False):
        r = orig(private)
        for s in r["styles"]:
            s["zh"] = payload
        return r

    monkeypatch.setattr(BD, "registry", evil)
    monkeypatch.setattr(BD, "_github_stars", lambda: payload)
    BD.picker(tmp_path)
    with open_page(browser, tmp_path) as page:
        _open(page)
        page.wait_for_timeout(300)
        assert page.evaluate("window.__xss") is None
        assert page.locator(".sc-name img, #star-count img, .pal-shot-cap img").count() == 0
        assert page.locator(".sc-name").first.inner_text() == payload
        assert page.inner_text("#star-count") == payload


def test_missing_optional_data_has_explicit_fallbacks(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(BD, "_scene_image", lambda sample, default, pool, have: ("", ""))
    monkeypatch.setattr(BD, "_github_stars", lambda: "")
    monkeypatch.setattr(BD, "_package_version", lambda: "")
    BD.picker(tmp_path)
    meta = _data(tmp_path / "index.html")
    assert all(s["image"] == "" and s["representative"] == "" for s in meta["scenes"])
    with open_page(browser, tmp_path) as page:
        _open(page)
        assert page.locator("#github-btn .star").is_hidden()                          # 没有星数快照就只显示 GitHub
        assert page.locator("#pkg-ver-wrap").is_hidden()                              # 读不到版本就不写版本
        assert page.locator("#product-version").is_hidden()
        page.click("#releases-link")
        assert "暂无已发布的更新记录" in page.locator("#release-panel").inner_text()
        page.keyboard.press("Escape")


# ---------------- 动态降级与减少动态效果（真实浏览器） ----------------

# 页面脚本启动时读到的 requestAnimationFrame / cancelAnimationFrame / IntersectionObserver 都是 undefined，
# 并记录读取次数；文档解析完后恢复原值，避免影响测试驱动自身。页面已经按「读到什么」选好了回退路径。
DEGRADE_SCRIPT = r"""
(() => {
  const keys = ['requestAnimationFrame', 'cancelAnimationFrame', 'IntersectionObserver'];
  const saved = {}, reads = {};
  keys.forEach((k) => {
    saved[k] = Object.getOwnPropertyDescriptor(window, k);
    reads[k] = 0;
    Object.defineProperty(window, k, { configurable: true, get() { reads[k] += 1; return undefined; }, set() {} });
  });
  window.__degradeReads = reads;
  document.addEventListener('DOMContentLoaded', () => keys.forEach((k) => {
    delete window[k];
    if (saved[k]) Object.defineProperty(window, k, saved[k]);
  }));
})();
"""

FLOATS_SETTLED = """() => {
  const f = [...document.querySelectorAll('#floaters .float')];
  return f.length > 0 && f.every(x => x.style.transform || (x.getAttribute('src') && x.complete && !x.naturalWidth));
}"""
FLOAT_TRANSFORMS = "[...document.querySelectorAll('#floaters .float')].map(f => f.style.transform)"
FLOAT_STYLES = "[...document.querySelectorAll('#floaters .float')].map(f => f.style.transform + '|' + f.style.opacity)"
SKY_HASH = """(() => {
  const c = document.getElementById('sky');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let h = 0;
  for (let i = 0; i < d.length; i += 7) h = (h * 31 + d[i]) | 0;
  return h;
})()"""
STAGE_STATE = """() => ({
  count: document.getElementById('stage-count').textContent,
  active: [...document.querySelectorAll('#stage-frame .slide.is-active .art')].map(e => ({tag: e.tagName, src: e.getAttribute('src'), alt: e.getAttribute('alt'), paused: e.tagName === 'VIDEO' ? e.paused : null})),
  toggle: [document.getElementById('stage-toggle').disabled, document.getElementById('stage-toggle').textContent]
})"""
CSS_PROBE = """() => {
  const cs = (e) => getComputedStyle(e);
  return {
    slide: cs(document.querySelector('#stage-frame .slide.is-active')).transitionDuration,
    thumb: cs(document.querySelector('#lib-grid .thumb img')).transitionDuration,
    btn: cs(document.getElementById('install-copy')).transitionDuration,
    scroll: cs(document.documentElement).scrollBehavior,
    reduce: matchMedia('(prefers-reduced-motion: reduce)').matches
  };
}"""


def _longest(duration: str) -> float:
    return max(float(x.strip().rstrip("s")) for x in duration.split(","))


def _wait_floats_settled(page):
    page.wait_for_function(FLOATS_SETTLED, timeout=30000)
    page.wait_for_timeout(400)


def test_page_works_without_animation_frame_and_intersection_observer(browser, built):
    d, html = built
    meta = _data(html)
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    with open_page(browser, d, init_script=DEGRADE_SCRIPT) as page:
        _open(page)
        reads = page.evaluate("window.__degradeReads")
        assert all(reads[k] >= 1 for k in ("requestAnimationFrame", "cancelAnimationFrame", "IntersectionObserver")), reads
        assert page.is_hidden("#load-fail")
        assert page.locator("#lib-grid > li").count() == len(meta["cands"])
        assert page.inner_text("[data-metric=styles]").replace(",", "") == str(meta["metrics"]["styles"])
        # 没有 IntersectionObserver：不做懒加载，所有画风库图片立即请求
        assert page.eval_on_selector_all("#lib-grid img", "e=>e.every(i=>i.getAttribute('src'))")
        # 没有 requestAnimationFrame：背景改走定时器，浮图仍然在动
        _wait_floats_settled(page)
        before = page.evaluate(FLOAT_TRANSFORMS)
        page.wait_for_timeout(700)
        after = page.evaluate(FLOAT_TRANSFORMS)
        moved = [a != b for a, b in zip(before, after) if a and b]
        assert moved and any(moved), (before[:2], after[:2])
        # 展台手动切换可用
        if meta["carousel"]:
            page.click("#stage-next")
            assert page.inner_text("#stage-count") == f"2 / {len(meta['carousel'])}"
        # 搜索可用
        tok = cands[0]["tone"][0]
        page.fill("#lib-q", tok)
        assert _visible(page) == {c["id"] for c in cands if _match(c, tok, meta["scenes"])}
        page.fill("#lib-q", "")
        assert len(_visible(page)) == len(cands)
        # 复制可用
        _first_card(page).click()
        page.wait_for_selector("#dlg[open]")
        page.evaluate(STUB_CLIPBOARD)
        page.click("#dlg-copy")
        copied = page.evaluate("window.__copied")
        assert copied == page.inner_text("#dlg-copyline") and SC.validate(SC.parse(SC.find(copied)[0])) == []
        assert page.inner_text("#dlg-copy") == "已复制"


def test_motion_is_alive_without_reduce_preference(browser, built):
    """对照组：没有「减少动态效果」时，背景在动、展台自动切换、过渡动画开启、图片懒加载。"""
    d, html = built
    meta = _data(html)
    with open_page(browser, d) as page:
        _open(page)
        css = page.evaluate(CSS_PROBE)
        assert css["reduce"] is False and css["scroll"] == "smooth"
        assert _longest(css["slide"]) > 0 and _longest(css["thumb"]) > 0 and _longest(css["btn"]) > 0, css
        state = page.evaluate(STAGE_STATE)
        assert state["toggle"] == [False, "暂停"], state
        assert len(state["active"]) == 1 and state["active"][0]["tag"] == "IMG", state
        assert state["active"][0]["src"].endswith(meta["carousel"][0]["image"]), state
        loaded = page.eval_on_selector_all("#lib-grid img", "e=>e.filter(i=>i.getAttribute('src')).length")
        assert loaded < len(meta["cands"]), "有 IntersectionObserver 时画风库图片应该按需加载"
        _wait_floats_settled(page)
        t1, h1 = page.evaluate(FLOAT_TRANSFORMS), page.evaluate(SKY_HASH)
        page.wait_for_timeout(700)
        t2, h2 = page.evaluate(FLOAT_TRANSFORMS), page.evaluate(SKY_HASH)
        assert any(a != b for a, b in zip(t1, t2) if a and b), "浮图应该在动"
        assert h1 != h2, "星空应该在闪烁"
        if meta["carousel"]:
            page.wait_for_function("document.getElementById('stage-count').textContent.startsWith('2 /')", timeout=15000, polling=200)


def test_reduced_motion_freezes_stage_background_and_transitions(browser, built):
    d, html = built
    meta = _data(html)
    with open_page(browser, d, reduced_motion="reduce") as page:
        _open(page)
        css = page.evaluate(CSS_PROBE)
        assert css == {"slide": "0s", "thumb": "0s", "btn": "0s", "scroll": "auto", "reduce": True}, css
        _wait_floats_settled(page)
        stage0, styles0, sky0 = page.evaluate(STAGE_STATE), page.evaluate(FLOAT_STYLES), page.evaluate(SKY_HASH)
        assert stage0["toggle"] == [True, "播放"], stage0               # 自动切换按系统设置关闭
        assert len(stage0["active"]) == 1 and stage0["active"][0]["tag"] == "IMG", stage0  # 首张为静态样图，不再使用动画 Logo
        assert stage0["active"][0]["src"].endswith(meta["carousel"][0]["image"]), stage0
        assert stage0["count"].startswith("1 /") and any(s.split("|")[0] for s in styles0), "浮图应已就位（静止，不是不存在）"
        # 超过一轮作品自动切换间隔：位置、状态、画布都不能变
        page.wait_for_timeout(7000)
        stage1, styles1, sky1 = page.evaluate(STAGE_STATE), page.evaluate(FLOAT_STYLES), page.evaluate(SKY_HASH)
        assert stage1 == stage0, (stage0, stage1)
        assert styles1 == styles0, "浮图位置或透明度变化了"
        assert sky1 == sky0, "星空/极光/流星画布变化了"
        assert page.inner_text("#stage-toggle") == "播放" and page.locator("#stage-toggle").is_disabled()
        # 手动切换仍然可用（只是不自动播）
        if meta["carousel"]:
            page.click("#stage-next")
            assert page.inner_text("#stage-count") == f"2 / {len(meta['carousel'])}"


@pytest.mark.parametrize("canvas_mode", ["null", "throw"])
@pytest.mark.parametrize("reduced_motion", ["reduce", "no-preference"])
def test_canvas_unavailable_keeps_search_detail_and_copy(browser, built, canvas_mode, reduced_motion):
    d, html = built
    meta = _data(html)
    canvas_script = """HTMLCanvasElement.prototype.getContext = function() {
      %s
    };""" % ("return null;" if canvas_mode == "null" else "throw new Error('Canvas unavailable');")
    with open_page(browser, d, 390, 844, reduced_motion=reduced_motion,
                   init_script=DEGRADE_SCRIPT + canvas_script) as page:
        _open(page)
        page.fill("#lib-q", "C01")
        assert _visible(page) == {"C01"}
        _first_card(page).click()
        page.wait_for_selector("#dlg[open]")
        page.evaluate(STUB_CLIPBOARD)
        page.click("#dlg-copy")
        copied = page.evaluate("window.__copied")
        assert copied == page.inner_text("#dlg-copyline")
        parsed = SC.parse(SC.find(copied)[0])
        assert parsed.style == "C01" and SC.validate(parsed) == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def test_hero_prefers_registered_hd_and_hd_stays_out_of_library(tmp_path, monkeypatch):
    """展台高清版 samples/cv-hd.* 只给展台用：登记且文件存在才采用，否则退回原图；不能变成画风库里的一种样图。"""
    cp = tmp_path / "styles" / "C99" / "contract.json"
    (cp.parent / "samples").mkdir(parents=True)
    contract = {"samples": [{"file": "samples/cv.webp", "ratio": 2.35}, {"file": "samples/cv-hd.webp", "ratio": 2.35}]}
    monkeypatch.setattr(BD.CT, "load", lambda code: contract)
    assert BD._hero_hd("C99", cp, "cv") is None, "登记了但文件不在，必须退回原图"
    (cp.parent / "samples" / "cv-hd.webp").write_bytes(b"x")
    assert BD._hero_hd("C99", cp, "cv") == cp.parent / "samples" / "cv-hd.webp"
    assert BD._hero_hd("C99", cp, "au") is None, "只认同种类的高清版"
    assert set(BD._sample_files("C99", cp)) == {"cv"}, "cv-hd 不能成为画风库样图种类"


CARD_STATE = """() => [...document.querySelectorAll('#lib-grid > li:not([hidden])')].map(li => {
  const im = li.querySelector('.thumb img'), r = li.getBoundingClientRect();
  return {id: li.dataset.style, src: (im.getAttribute('src') || im.dataset.src || ''), l: r.left, t: r.top, rgt: r.right, b: r.bottom};
})"""


def _card_kind(src: str, sid: str) -> str:
    return src.rsplit("/", 1)[1].split("?")[0][len(sid) + 1:-len(".webp")]


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_library_cards_use_real_scene_images_in_a_masonry(browser, built, size):
    """画风库默认封面样图，切换用途后展示对应样图；瀑布流不重叠、从左到右读。"""
    d, html = built
    meta = _data(html)
    cands = {c["id"]: c for c in meta["cands"]}
    same = {"s1", "s2", "s3"}
    with open_page(browser, d, *size) as page:
        _open(page)
        cards = page.evaluate(CARD_STATE)
        cover = next(sc for sc in meta["scenes"] if sc["id"] == "wxcover")
        assert {c["id"] for c in cards} == set(cover["pool"])
        for c in cards:
            k = _card_kind(c["src"], c["id"])
            assert k in cands[c["id"]]["imgs"], c
            assert k not in same or not (set(cands[c["id"]]["imgs"]) - same - {"anchor", "bd"}), ("用了同题图作默认", c)
        kinds = [_card_kind(c["src"], c["id"]) for c in cards]
        assert all(k.split("-")[0] == cover["sample"].split("-")[0] for k in kinds), "默认应使用封面样图"
        # 瀑布流：互不重叠；第一行从左到右就是前几种画风
        for i, a in enumerate(cards):
            for b in cards[i + 1:]:
                assert not (a["l"] < b["rgt"] - 1 and b["l"] < a["rgt"] - 1 and a["t"] < b["b"] - 1 and b["t"] < a["b"] - 1), (a["id"], b["id"])
        cols = 4 if size[0] > 900 else 2
        first_row = sorted(cards[:cols], key=lambda c: c["l"])
        assert [c["id"] for c in first_row] == [c["id"] for c in cards[:cols]] and len({round(c["t"]) for c in first_row}) == 1
        for scene in meta["scenes"]:
            page.click(f'#lib-scenes .chip[data-scene="{scene["id"]}"]')
            group = scene["sample"].split("-")[0]
            for c in page.evaluate(CARD_STATE):
                k = _card_kind(c["src"], c["id"])
                assert k in cands[c["id"]]["imgs"], (scene["id"], c)
                if any(i.split("-")[0] == group for i in cands[c["id"]]["imgs"]):
                    assert k.split("-")[0] == group, (scene["id"], c["id"], k)
