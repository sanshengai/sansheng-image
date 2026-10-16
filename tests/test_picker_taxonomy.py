"""首页「地域 / 时代 / 朝代 / 风格分类」筛选的浏览器行为测试（当前 Skill 自带的页面模板 + 目录）。

- 缩略图用 2×2 假 WebP 代替真实编码（只用于交互测试；真实图库编码由发布前的整库构建验证），
  其余注册表、样图登记比例校验、清单与页面数据都走真实构建；
- 私有层显式指向不存在的目录（无 profile 模式），不读本机真实 profile；
- 预期值全部从页面嵌入数据 + 目录独立推算，不用被测页面的结果当尺子；
- 需要 playwright + Chromium，缺失时 skip 并写明「未验证页面行为」。
运行：python3 -m pytest -p no:cacheprovider tests/test_picker_taxonomy.py -q -rs
"""
from __future__ import annotations

import contextlib
import copy
import http.server
import io
import itertools
import json
import re
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import build as BD  # noqa: E402
from stylebook import taxonomy as TX  # noqa: E402

CATALOG = ROOT / "styles" / "catalog.json"
BOUNDARY = "需要 playwright + Chromium：筛选行为（交叉、清除、哈希、手机排版）在这里没有被验证，只验证了构建产物"
DATA_RE = re.compile(r'<script id="page-data" type="application/json">(.*?)</script>', re.S)
_buf = io.BytesIO()
Image.new("RGB", (2, 2), "white").save(_buf, "WEBP", lossless=True)
FAKE_WEBP = _buf.getvalue()
PENDING = "资料待定"


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _without_labels(doc: dict) -> dict:
    """由已带标签的目录机器派生旧版目录：去掉 taxonomy 与新增标签字段，legacyFamily 还原成 family。"""
    out = json.loads(json.dumps(doc))
    out.pop("taxonomy", None)
    out["styles"] = [{k: (s["legacyFamily"] if k == "family" else v) for k, v in s.items() if k not in TX.NEW_KEYS}
                     for s in out["styles"]]
    return out


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path_factory.getbasetemp() / "no-profile"))
    monkeypatch.delenv(BD.BRAND_DIR_ENV, raising=False)
    monkeypatch.setattr(BD, "_thumb_bytes", lambda p, m=BD.THUMB, q=60: FAKE_WEBP)


def _meta(html: Path) -> dict:
    return json.loads(DATA_RE.search(html.read_text(encoding="utf-8")).group(1))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("SANSHENG_IMAGE_PROFILE", str(tmp_path_factory.getbasetemp() / "no-profile"))
    mp.setattr(BD, "_thumb_bytes", lambda p, m=BD.THUMB, q=60: FAKE_WEBP)
    try:
        d = tmp_path_factory.mktemp("taxpicker")
        html = BD.picker(d)
        yield d, html, _meta(html)
    finally:
        mp.undo()


@contextlib.contextmanager
def serve(directory: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(directory), **k)

        def translate_path(self, path):
            if path.split("?")[0].rstrip("/") == "/tools/image":
                path = "/index.html"
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
    playwright = pytest.importorskip("playwright.sync_api", reason=BOUNDARY)
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium 不可用（{exc}）。{BOUNDARY}")
        yield b
        b.close()


@contextlib.contextmanager
def open_page(browser, directory: Path, width=1440, height=900):
    with serve(directory) as base:
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.base = base
        try:
            yield page
        finally:
            ctx.close()
        assert not errors, errors


def _open(page, frag=""):
    page.goto(f"{page.base}/tools/image{frag}")
    page.wait_for_selector("#lib-grid > li", state="attached")
    page.wait_for_load_state("networkidle")


def _visible(page) -> set[str]:
    return set(page.eval_on_selector_all("#lib-grid > li:not([hidden])", "e=>e.map(x=>x.dataset.style)"))


def _hash(page) -> dict:
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(page.evaluate("location.hash").lstrip("#")).items()}


def _sel(page, key, value):
    if page.is_hidden("#lib-tax-row"):   # 分类收在「更多筛选」里，先展开
        page.click("#lib-more")
    page.select_option(f"#lib-{key}", value)


# ---- 独立预期值：只用页面嵌入数据里的字段 ----

def _has(c, key, v):
    if key == "family":
        return c["family"] == v
    if v == PENDING:
        return not c[key]
    return v in c[key]


def _expect(cands, **f):
    return {c["id"] for c in cands if all(_has(c, k, v) for k, v in f.items() if v)}


def _options(meta, key):
    used = [v for v in meta["taxonomy"][key] if any(_has(c, key, v) for c in meta["cands"])]
    if key in ("region", "era") and any(not c[key] for c in meta["cands"]):
        used.append(PENDING)
    return used


# ---------------- 构建产物 ----------------

def test_embedded_data_carries_taxonomy_matching_the_catalog(built):
    d, html, meta = built
    doc = _catalog()
    cat = {s["code"]: s for s in doc["styles"]}
    assert html.stat().st_size < 250_000
    assert meta["taxonomy"] == {k: doc["taxonomy"][k] for k in TX.VOCAB_KEYS}
    assert meta["cands"] and all(c["id"] in cat for c in meta["cands"])
    for c in meta["cands"]:
        s = cat[c["id"]]
        assert (c["family"], c["legacyFamily"], c["region"], c["era"], c["dynasty"]) == (s["family"], s["legacyFamily"], s["region"], s["era"], s["dynasty"])
    assert "innerHTML" not in html.read_text(encoding="utf-8").split('<script id="page-data"')[1]
    assert not re.search(r"C2\b|审批|复审|冲突", json.dumps(meta["taxonomy"], ensure_ascii=False))   # 公共页面不带内部术语


# ---------------- 行为 ----------------

def test_row_sits_in_the_sticky_bar_and_options_follow_the_data(browser, built):
    d, html, meta = built
    with open_page(browser, d) as page:
        _open(page)
        # 默认收起在「更多筛选」里，点开才出现；吸顶栏上不直接摆四个下拉框
        assert page.is_visible("#lib-more") and page.is_hidden("#lib-tax-row")
        assert page.get_attribute("#lib-more", "aria-expanded") == "false"
        page.click("#lib-more")
        assert page.is_visible("#lib-tax-row") and page.is_hidden("#lib-tax-clear")
        assert page.get_attribute("#lib-more", "aria-expanded") == "true"
        assert page.eval_on_selector("#lib-bar", "b=>!!b.querySelector('#lib-tax-row, #lib-scenes') && b.querySelector('#lib-tax-row') && b.querySelector('#lib-palettes') && b.querySelector('#lib-q') !== null")
        for key, label in (("region", "全部地域"), ("era", "全部时代"), ("dynasty", "全部朝代"), ("family", "全部分类")):
            got = page.eval_on_selector_all(f"#lib-{key} option", "o=>o.map(x=>x.textContent)")
            assert got == [label, *_options(meta, key)], key
            assert page.input_value(f"#lib-{key}") == ""
        assert PENDING in _options(meta, "region") and PENDING in _options(meta, "era") and PENDING not in _options(meta, "dynasty")
        assert page.is_disabled("#lib-dynasty") and _visible(page) == set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))   # 默认＝封面
        page.evaluate("window.scrollTo({top: document.getElementById('library').getBoundingClientRect().top + scrollY + 600, behavior: 'instant'})")
        page.wait_for_timeout(200)
        top = page.eval_on_selector("#lib-bar", "b=>b.getBoundingClientRect().top")
        nav = page.eval_on_selector("#topnav", "n=>n.getBoundingClientRect().bottom")
        assert nav > 0 and abs(top - nav) < 1.5, (top, nav)      # 吸顶，紧贴在站点顶栏下方
        page.evaluate("window.dispatchEvent(new Event('scroll'))")
        assert "is-stuck" in page.get_attribute("#lib-bar", "class")  # 吸住时有阴影浮起
        assert page.is_visible("#lib-region")
        _open(page, "#g=" + _options(meta, "region")[0])          # 网址里带着分类筛选：自动展开，用户看得见是什么在生效
        page.reload(); page.wait_for_selector("#lib-grid > li", state="attached")
        assert page.is_visible("#lib-tax-row") and page.get_attribute("#lib-more", "aria-expanded") == "true"


def test_dimensions_intersect_and_match_independent_oracle(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    combos = [(r, e, f) for r, e, f in itertools.product(["", *_options(meta, "region")], ["", *_options(meta, "era")], ["", *_options(meta, "family")])]
    nonempty_multi = 0
    with open_page(browser, d) as page:
        _open(page)
        for r, e, f in combos:
            _sel(page, "region", r) if page.input_value("#lib-region") != r else None
            _sel(page, "era", e) if page.input_value("#lib-era") != e else None
            _sel(page, "family", f) if page.input_value("#lib-family") != f else None
            want = _expect(cands, region=r, era=e, family=f)
            assert _visible(page) == want, (r, e, f)
            if sum(bool(x) for x in (r, e, f)) >= 2 and want:
                nonempty_multi += 1
            assert page.is_visible("#lib-empty") == (not want)
        assert nonempty_multi >= 5, "数据里跨维度交集的非空例子太少，交集行为没被真正验证"


def test_era_is_a_field_of_its_own_not_guessed_from_dynasty(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    only_era = {c["id"] for c in cands if "传统语汇" in c["era"] and not c["dynasty"]}
    assert only_era, "需要有 era=传统语汇 但没有朝代的画风，才能证明时代不是按朝代推出来的"
    with open_page(browser, d) as page:
        _open(page)
        _sel(page, "era", "传统语汇")
        assert only_era <= _visible(page) == _expect(cands, era="传统语汇")
        _sel(page, "region", "中国")
        assert page.is_enabled("#lib-dynasty")
        china_dyn = [v for v in _options(meta, "dynasty") if any(v in c["dynasty"] for c in cands)]
        for v in china_dyn:
            _sel(page, "dynasty", v)
            assert _visible(page) == _expect(cands, region="中国", era="传统语汇") & {c["id"] for c in cands if v in c["dynasty"]}


def test_dynasty_only_for_china_and_switching_away_clears_it(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    dyn = next(v for v in _options(meta, "dynasty") if any(v in c["dynasty"] for c in cands))
    with open_page(browser, d) as page:
        _open(page)
        assert page.is_disabled("#lib-dynasty")
        _sel(page, "region", "中国")
        _sel(page, "dynasty", dyn)
        assert _hash(page).get("d") == dyn and _visible(page) == _expect(cands, region="中国") & {c["id"] for c in cands if dyn in c["dynasty"]}
        _sel(page, "region", next(v for v in _options(meta, "region") if v not in ("中国", PENDING)))
        assert page.input_value("#lib-dynasty") == "" and page.is_disabled("#lib-dynasty")
        assert "d" not in _hash(page)
        assert "朝代" not in page.inner_text("#lib-status")
        # 清掉地域后同样不能残留朝代
        _sel(page, "region", "中国")
        _sel(page, "dynasty", dyn)
        _sel(page, "region", "")
        assert page.input_value("#lib-dynasty") == "" and "d" not in _hash(page)
        assert _visible(page) == {c["id"] for c in cands}


def test_empty_result_can_be_cleared_from_the_message_and_from_the_row(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    empty = next(((r, e, f) for r, e, f in itertools.product(_options(meta, "region"), _options(meta, "era"), _options(meta, "family"))
                  if not _expect(cands, region=r, era=e, family=f)), None)
    assert empty, "数据里没有会得到空结果的组合，空结果提示无法验证"
    r, e, f = empty
    sc = max(meta["scenes"], key=lambda s: len(s["pool"]))
    with open_page(browser, d) as page:
        _open(page)
        for key, v in zip(("region", "era", "family"), empty):
            _sel(page, key, v)
        assert _visible(page) == set() and page.is_visible("#lib-empty") and page.is_visible("#lib-empty-clear")
        assert "没有同时符合" in page.inner_text("#lib-empty")
        page.click("#lib-empty-clear")
        assert len(_visible(page)) == len(cands) and page.is_hidden("#lib-empty") and page.is_hidden("#lib-tax-clear")
        assert all(page.input_value(f"#lib-{k}") == "" for k in ("region", "era", "family"))
        assert not {"g", "e", "d", "f"} & set(_hash(page))
        # 场景 + 搜索 + 筛选三者取交集；清除筛选只放开筛选，场景与搜索保留
        page.click(f'#lib-scenes .chip[data-scene="{sc["id"]}"]')
        pool = set(sc["pool"])
        tok = next(c["tone"][0] for c in cands if c["id"] in pool and c["tone"])
        page.fill("#lib-q", tok)
        base = {c["id"] for c in cands if c["id"] in pool and tok in c["tone"] + [c["name"], c["craft"], c["essence"]]} or None
        for key, v in zip(("region", "era", "family"), empty):
            _sel(page, key, v)
        assert _visible(page) == set() and "没有同时符合" in page.inner_text("#lib-empty") and tok in page.inner_text("#lib-empty")
        page.click("#lib-tax-clear")
        assert page.is_hidden("#lib-empty") and page.input_value("#lib-q") == tok and _visible(page)
        assert _visible(page) <= pool
        assert base is None or _visible(page) >= (base & _visible(page))


def test_status_line_mentions_filters_and_not_a_dating_claim(browser, built):
    d, html, meta = built
    with open_page(browser, d) as page:
        _open(page)
        _sel(page, "region", "日本")
        st = page.inner_text("#lib-status")
        assert "地域 日本" in st and "不是对具体文物年代的考证" not in st
        _sel(page, "era", "20世纪")
        st = page.inner_text("#lib-status")
        assert "时代 20世纪" in st and "不是对具体文物年代的考证" in st
        assert str(len(_visible(page))) in st


def test_pending_style_is_reachable_only_through_the_pending_option(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    pend = {c["id"] for c in cands if not c["region"]}
    assert pend == {c["id"] for c in cands if not c["era"]} and pend
    with open_page(browser, d) as page:
        _open(page)
        _sel(page, "region", PENDING)
        assert _visible(page) == pend
        _sel(page, "region", "中国")
        assert not (pend & _visible(page))
        _sel(page, "region", "")
        _sel(page, "era", PENDING)
        assert _visible(page) == pend
        assert page.is_disabled("#lib-dynasty")


def test_search_still_finds_legacy_family_and_finds_new_family(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    legacy = next(c["legacyFamily"] for c in cands if c["legacyFamily"] and c["legacyFamily"] not in {x["family"] for x in cands})
    newfam = cands[0]["family"]
    with open_page(browser, d) as page:
        _open(page)
        for q, field in ((legacy, "legacyFamily"), (newfam, "family")):
            page.fill("#lib-q", q)
            want = {c["id"] for c in cands if q.lower() in c[field].lower()} | {c["id"] for c in cands if q.lower() in " ".join([c["id"], c["name"], c["origin"], c["essence"], *c["tone"], c["craft"]]).lower()}
            assert want and _visible(page) >= {c["id"] for c in cands if c[field] == q}, q
            assert _visible(page) >= want - {i for i in want if not any(q.lower() in str(c.get(k, "")).lower() for c in cands if c["id"] == i for k in ("legacyFamily", "family", "name", "origin", "essence", "craft", "id")) and not any(q.lower() in t.lower() for c in cands if c["id"] == i for t in c["tone"])}
        # 搜索与新筛选取交集
        page.fill("#lib-q", legacy)
        fam = next(c["family"] for c in cands if c["legacyFamily"] == legacy)
        before = _visible(page)
        _sel(page, "family", fam)
        assert _visible(page) == before & _expect(cands, family=fam)


# ---------------- 哈希 ----------------

def test_old_hash_keys_still_parse_and_new_keys_are_independent(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    sc = meta["scenes"][0]
    pal = next(p for p in meta["pals"] if p["id"] not in ("orig", "brand") and p["colors"])
    with open_page(browser, d) as page:
        _open(page, f"#u={sc['id']}&p={pal['id']}&l=1&t=-1")
        assert _visible(page) == set(sc["pool"])
        assert page.get_attribute(f'#lib-palettes .chip[data-pal="{pal["id"]}"]', "aria-pressed") == "true"
        assert {k: v for k, v in _hash(page).items() if k in "uplt"} == {"u": sc["id"], "p": pal["id"], "l": "1", "t": "-1"}
        assert all(page.input_value(f"#lib-{k}") == "" for k in ("region", "era", "family"))
        # 品牌色 h 仍可解析
        page.goto(f"{page.base}/tools/image#h=0E926F.F2C57C")
        page.wait_for_load_state("networkidle")
        assert "品牌自定义" in page.inner_text("#lib-status")
        # 画风 s 仍然打开详情
        page.goto(f"{page.base}/tools/image#s={cands[0]['id']}")
        page.wait_for_selector("dialog[open]")
        assert page.inner_text("#dlg-id").startswith(cands[0]["id"])
        page.keyboard.press("Escape")
        page.wait_for_selector("dialog[open]", state="detached")      # 关闭事件会回写网址，等它落定再换网址，避免竞态
        page.wait_for_timeout(150)
        # 只带新键：独立回填，不连带别的状态
        e = "20世纪"
        page.goto(f"{page.base}/tools/image#e={e}")
        page.reload()
        page.wait_for_selector("#lib-grid > li", state="attached")
        assert page.input_value("#lib-era") == e and page.input_value("#lib-region") == ""
        assert _visible(page) == _expect(cands, era=e)
        # 新旧混用 + 多维
        r, f = "中国", next(c["family"] for c in cands if "中国" in c["region"])
        page.goto(f"{page.base}/tools/image#u={sc['id']}&g={r}&f={f}&e={e}")
        page.reload()
        page.wait_for_selector("#lib-grid > li", state="attached")
        assert _visible(page) == set(sc["pool"]) & _expect(cands, region=r, family=f, era=e)
        assert (page.input_value("#lib-region"), page.input_value("#lib-family"), page.input_value("#lib-era")) == (r, f, e)


def test_invalid_hash_values_are_dropped_and_never_written_back(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    dyn = next(v for v in _options(meta, "dynasty") if any(v in c["dynasty"] for c in cands))
    sc = meta["scenes"][1]
    with open_page(browser, d) as page:
        for frag in ("#g=火星&e=不存在&d=宋&f=%3Cimg%20src%3Dx%3E", f"#d={dyn}", "#g=&e=&f=", "#g=中国&d=%E4%B8%8D%E5%AD%98%E5%9C%A8"):
            page.goto(f"{page.base}/tools/image{frag}")
            page.reload()
            page.wait_for_selector("#lib-grid > li", state="attached")
            assert len(_visible(page)) == len(cands) or frag.startswith("#g=中国"), frag
            assert page.input_value("#lib-era") == "" and page.input_value("#lib-family") == ""
            assert page.input_value("#lib-dynasty") == ""
            if not frag.startswith("#g=中国"):
                assert page.input_value("#lib-region") == ""
            page.click(f'#lib-scenes .chip[data-scene="{sc["id"]}"]')          # 触发 writeHash
            assert not ({"e", "d", "f"} & set(_hash(page))), (frag, _hash(page))
            assert _hash(page).get("g", "中国") == "中国"
        # 朝代必须配合中国：#g=日本&d=<朝代> 里的朝代被丢弃
        page.goto(f"{page.base}/tools/image#g=日本&d={dyn}")
        page.reload()
        page.wait_for_selector("#lib-grid > li", state="attached")
        assert page.input_value("#lib-region") == "日本" and page.input_value("#lib-dynasty") == ""
        assert _visible(page) == _expect(cands, region="日本")


def test_selection_round_trips_through_reload_and_back_forward(browser, built):
    d, html, meta = built
    cover_pool = set(next(sc["pool"] for sc in meta["scenes"] if sc["id"] == "wxcover"))
    cands = [c for c in meta["cands"] if c["id"] in cover_pool]
    with open_page(browser, d) as page:
        _open(page)
        _sel(page, "region", "中国")
        dyn = next(v for v in _options(meta, "dynasty") if any(v in c["dynasty"] and "中国" in c["region"] for c in cands))
        _sel(page, "dynasty", dyn)
        _sel(page, "family", next(c["family"] for c in cands if dyn in c["dynasty"]))
        want, h = _visible(page), _hash(page)
        assert {"g", "d", "f"} <= set(h)
        page.reload()
        page.wait_for_selector("#lib-grid > li", state="attached")
        assert _visible(page) == want and page.input_value("#lib-dynasty") == dyn and page.is_enabled("#lib-dynasty")
        page.evaluate("location.hash = ''")
        page.wait_for_timeout(100)
        assert len(_visible(page)) == len(cands) and page.input_value("#lib-region") == ""


# ---------------- 排版 ----------------

@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_no_horizontal_overflow_and_controls_stay_inside_viewport(browser, built, size):
    d, html, meta = built
    w, h = size
    with open_page(browser, d, w, h) as page:
        _open(page)
        dyn_ok = any(v in c["dynasty"] for c in meta["cands"] for v in meta["taxonomy"]["dynasty"])
        _sel(page, "region", "中国")
        if dyn_ok:
            _sel(page, "dynasty", next(v for v in _options(meta, "dynasty")))
        _sel(page, "era", _options(meta, "era")[-1])
        page.evaluate("window.scrollTo({top: document.getElementById('library').getBoundingClientRect().top + scrollY, behavior: 'instant'})")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        rects = page.eval_on_selector_all("#lib-tax-row select:not([hidden]), #lib-tax-clear:not([hidden])", "e=>e.map(x=>{const r=x.getBoundingClientRect();return [r.left,r.right,r.height]})")
        assert rects and all(hh >= 34 for l, r, hh in rects), rects
        if w >= 600:
            assert all(0 <= l and r <= w + 0.5 for l, r, hh in rects), rects
        else:
            # 手机将分类控件放进独立的单行横向滚动区，每项必须可实际滚入和操作。
            row = page.locator(".lib-tax")
            box = row.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= w + 0.5
            assert row.evaluate("e=>e.scrollWidth > e.clientWidth")
            for control in page.locator("#lib-tax-row select:not([hidden])").all():
                control.evaluate("e=>e.scrollIntoView({block:'nearest',inline:'center'})")
                control.focus()
                r = control.bounding_box()
                assert r and box["x"] - 0.5 <= r["x"] and r["x"] + r["width"] <= box["x"] + box["width"] + 0.5
            assert page.eval_on_selector("#lib-region", "e=>parseFloat(getComputedStyle(e).fontSize)") >= 16
            page.locator("#lib-tax-clear").click()
            assert all(page.input_value("#lib-" + key) == "" for key in ("region", "era", "dynasty", "family"))
        bar = page.eval_on_selector("#lib-bar", "b=>b.getBoundingClientRect().height")
        assert bar < h * (0.25 if w < 600 else 0.3), bar


def test_option_text_comes_from_data_as_text_not_html(browser, tmp_path, monkeypatch):
    cat = _catalog()
    evil = "<img src=x onerror=\"window.__xss=1\">"
    cat["taxonomy"]["region"][cat["taxonomy"]["region"].index("日本")] = evil
    for s in cat["styles"]:
        s["region"] = [evil if v == "日本" else v for v in s["region"]]
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BD, "CATALOG", p)
    out = tmp_path / "out"
    BD.picker(out)
    with open_page(browser, out) as page:
        _open(page)
        assert page.evaluate("window.__xss") is None and page.locator("#lib-region img").count() == 0
        assert evil in page.eval_on_selector_all("#lib-region option", "o=>o.map(x=>x.textContent)")


def test_page_without_taxonomy_in_catalog_hides_the_row_and_keeps_everything_else(browser, tmp_path, monkeypatch):
    old = tmp_path / "old-catalog.json"
    old.write_text(json.dumps(_without_labels(_catalog()), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(BD, "CATALOG", old)
    BD.picker(tmp_path / "out")
    meta = _meta(tmp_path / "out" / "index.html")
    assert "taxonomy" not in meta and not any("region" in c for c in meta["cands"])
    with open_page(browser, tmp_path / "out") as page:
        _open(page, "#g=中国&e=20世纪")
        assert page.is_hidden("#lib-tax-row") and page.is_hidden("#lib-more") and len(_visible(page)) == len(meta["cands"])
        page.fill("#lib-q", meta["cands"][0]["id"])
        assert _visible(page) == {meta["cands"][0]["id"]}
        page.click(f'#lib-scenes .chip[data-scene="{meta["scenes"][0]["id"]}"]')       # 回写网址：没有词表时不认新键，也不会把它们传播下去
        assert not ({"g", "e", "d", "f"} & set(_hash(page)))


def test_other_page_features_survive(browser, built):
    """旧行为抽查：场景交集、色系、换色哈希、详情、三问入口、我的默认入口、Canvas 与浮动图仍在（完整行为由 full51 复用测试覆盖）。"""
    d, html, meta = built
    sc = meta["scenes"][0]
    with open_page(browser, d) as page:
        _open(page)
        page.click(f'#lib-scenes .chip[data-scene="{sc["id"]}"]')
        _sel(page, "era", _options(meta, "era")[0])
        assert _visible(page) == set(sc["pool"]) & _expect(meta["cands"], era=_options(meta, "era")[0])
        page.click("#quiz-btn")
        assert page.is_visible("#quiz")
        page.keyboard.press("Escape")
        assert page.is_visible("#tray-btn") and page.locator("#sky").count() == 1 and page.locator("#floaters").count() == 1
        page.locator("#lib-grid > li:not([hidden]) .style-card").first.click()
        page.wait_for_selector("dialog[open]")
        assert _hash(page).get("s") and _hash(page).get("u") == sc["id"]
