"""分件式图层（stylebook.layers）测试。

运行（在 Skill 根目录）：
    python3 -I -m unittest discover -s tests -p "test_layers.py" -v
默认测试当前仓；要测另一份副本，设 SANSHENG_IMAGE_ROOT=<sansheng-image 根目录>。
被测模块、vendor 与 SOURCE.json 都取自 <根>/scripts/stylebook；夹具在 tests/fixtures。
全部临时文件写在 tempfile 临时目录里；不联网、不出图。缺少正式模块直接报错，不会跳过。
"""
from __future__ import annotations

import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("SANSHENG_IMAGE_ROOT") or Path(__file__).resolve().parents[1]).resolve()
SCRIPTS = ROOT / "scripts"
HERE = SCRIPTS / "stylebook"  # 被测模块与 vendor 所在目录
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRIEF_FIXTURE = FIXTURES / "layers-bookstore-brief.json"
# 已审核的 vendor/cutlib.py 原始 SHA；与 vendor/SOURCE.json、实际文件三方必须一致
REVIEWED_CUTLIB_SHA = "e37acab57143b58923db047e67bbadbdd1e923e0b22ea51b4e36c7b28f10b5bc"

if sys.path[:1] != [str(SCRIPTS)]:
    sys.path.insert(0, str(SCRIPTS))

from stylebook import layers as L  # noqa: E402  缺少正式模块时直接 ImportError，不跳过

SIZE = (320, 180)  # 与 brief 画布 1600x900 同为 16:9


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def find_skill_md(root) -> list:
    return sorted(Path(root).rglob("SKILL.md"))


def setUpModule():
    if not (ROOT / "scripts" / "stylebook" / "compile.py").is_file():
        raise RuntimeError(f"找不到 sansheng-image 根：{ROOT}（设 SANSHENG_IMAGE_ROOT）；不允许静默跳过")
    if Path(L.__file__).resolve() != HERE / "layers.py":
        raise RuntimeError(f"导入的 stylebook.layers 不是 {HERE / 'layers.py'}，而是 {L.__file__}；请检查 sys.path / SANSHENG_IMAGE_ROOT")
    if not BRIEF_FIXTURE.is_file():
        raise RuntimeError(f"缺少测试夹具：{BRIEF_FIXTURE}")
    L.use_root(ROOT)


def save_background(path, tint):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", SIZE, tint)
    ImageDraw.Draw(im).rectangle((10, 10, 120, 80), fill=(120, 90, 60))
    im.save(path, "PNG")


def save_object(path, rect, color=(230, 140, 40)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", SIZE, (0, 255, 0))
    ImageDraw.Draw(im).rectangle(rect, fill=color)
    im.save(path, "PNG")


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.base = self.tmp / "base"
        self.base.mkdir()
        self.brief = json.loads(BRIEF_FIXTURE.read_text(encoding="utf-8"))
        self.host = self.base / "host"
        self.host.mkdir()

    def write_brief(self, brief=None):
        brief = brief or self.brief
        (self.base / "brief.json").write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        return brief

    def prep(self, brief=None, name="prep"):
        brief = self.write_brief(brief)
        out = self.tmp / name
        L.prepare_run(brief, out, base_path=self.base)
        return brief, out / "prepared.json", json.loads((out / "prepared.json").read_text(encoding="utf-8"))

    def make_images(self, prepared):
        paths = {}
        for i, t in enumerate(prepared["tasks"]):
            p = self.host / f"{t['id']}.png"
            if t["kind"] == "background":
                save_background(p, (200, 190, 170))
            else:
                save_object(p, (60 + 10 * i, 50, 120 + 10 * i, 120))
            paths[t["id"]] = p
        return paths

    def make_results(self, prepared, prepared_path, paths, *, historical=False, relative=False):
        images = {}
        for t in prepared["tasks"]:
            p = paths[t["id"]]
            shown = str(p.relative_to(self.base)) if relative else str(p)
            if historical:
                images[t["id"]] = {"path": shown, "sha256": sha(p)}
                continue
            exp_paths, exp_shas = [], []
            for r in t["references"]:
                if r.get("from_task"):
                    exp_paths.append(str(paths[r["from_task"]]))
                    exp_shas.append(sha(paths[r["from_task"]]))
                else:
                    exp_paths.append(r["path"])
                    exp_shas.append(r["sha256"])
            images[t["id"]] = {"path": shown, "sha256": sha(p), "prompt_sha256": t["prompt_sha256"],
                               "tool_arguments": {"prompt": t["compiled"]["prompt"], "referenced_image_paths": exp_paths,
                                                  "transparent_background": False},
                               "reference_sha256s": exp_shas, "provider": "codex_builtin", "model": None,
                               "est_usd": None, "seconds": 12.5}
        res = {"version": 1, "prepared_sha256": sha(prepared_path), "images": images}
        if historical:
            res["historical_import"] = True
        return res

    def do_import(self, brief, prepared_path, results, name="result"):
        rp = self.tmp / f"{name}-results.json"
        rp.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        out = self.tmp / name
        report = L.import_results(brief, prepared_path, rp, out, base_path=self.base)
        return out, report

    def assert_no_output(self, name):
        self.assertFalse((self.tmp / name).exists())
        self.assertEqual([p.name for p in self.tmp.glob(f".{name}.partial-*")], [])

    def happy(self):
        brief, pp, prepared = self.prep()
        paths = self.make_images(prepared)
        return brief, pp, prepared, paths


class PrepareTests(Base):
    def test_prepare_outputs_and_camera_lock(self):
        brief, pp, prepared = self.prep()
        self.assertEqual([t["id"] for t in prepared["tasks"]], ["background", "cart", "cat", "cat-asleep"])
        lock = L._camera_content(prepared["camera"])[1]
        for t in prepared["tasks"]:
            prompt = t["compiled"]["prompt"]
            self.assertIn(lock, prompt)  # 唯一 camera 描述逐字出现在每张提示词里
            self.assertIn("1600x900", prompt)
            self.assertNotIn("翻页书店", prompt)  # 文字只由代码排，不进提示词
            self.assertEqual(t["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
            self.assertFalse(t["transparent_background"])
        bg = prepared["tasks"][0]["compiled"]["prompt"]
        self.assertIn("no main subject", bg)
        self.assertIn("00FF00", prepared["tasks"][1]["compiled"]["prompt"])
        state = prepared["tasks"][3]
        self.assertEqual([r.get("from_task") for r in state["references"] if r.get("from_task")], ["cat"])
        self.assertTrue(all(t["compiled"]["style"].startswith("C64@r") for t in prepared["tasks"]))
        out = self.tmp / "prep"
        for name in ("brief.json", "prepared.json", "host-requests.json", "results.template.json", "layers-plan.json", "report.json",
                     "cart-prompt.txt", "cat-asleep-manifest.json", "cat-asleep-compiled.json"):
            self.assertTrue((out / name).is_file(), name)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["generated"], report["accepted"]), ("pending_host", False, False))
        self.assertEqual(report["prepared_sha256"], sha(out / "prepared.json"))
        self.assertEqual(prepared["plan"]["geometry"]["camera_visual_match"], "human_required_not_verified")
        self.assertEqual([x["kind"] for x in prepared["plan"]["layers"]], ["background", "object", "object", "state"])

    def test_background_does_not_leak_object_matte_instructions(self):
        brief = copy.deepcopy(self.brief)
        brief["objects"][0]["description"] += " OBJECT_ONLY_SENTINEL. All background is pure #00FF00."
        _, _, prepared = self.prep(brief)
        bg = prepared["tasks"][0]["compiled"]["prompt"]
        self.assertNotIn("OBJECT_ONLY_SENTINEL", bg)
        self.assertNotIn("#00FF00", bg)
        self.assertIn("OBJECT_ONLY_SENTINEL", prepared["tasks"][1]["compiled"]["prompt"])
        self.assertIn("cart", bg)

    def test_prepare_deterministic_and_template_is_not_importable(self):
        _, pp, p1 = self.prep(name="a")
        _, _, p2 = self.prep(name="b")
        self.assertEqual(p1, p2)
        tpl = json.loads((self.tmp / "a" / "results.template.json").read_text(encoding="utf-8"))
        rp = self.tmp / "tpl.json"
        rp.write_text(json.dumps(tpl), encoding="utf-8")
        with self.assertRaises(L.LayersError):
            L.import_results(self.brief, self.tmp / "a" / "prepared.json", rp, self.tmp / "tpl-out", base_path=self.base)
        self.assert_no_output("tpl-out")

    def test_existing_output_not_overwritten(self):
        brief, pp, prepared = self.prep()
        keep = self.tmp / "prep" / "marker.txt"
        keep.write_text("x", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            L.prepare_run(brief, self.tmp / "prep", base_path=self.base)
        self.assertEqual(keep.read_text(encoding="utf-8"), "x")

    def bad(self, mutate, pattern, exc=None):
        brief = copy.deepcopy(self.brief)
        mutate(brief)
        self.write_brief(brief)
        out = self.tmp / "bad"
        with self.assertRaisesRegex(exc or L.LayersError, pattern):
            L.prepare_run(brief, out, base_path=self.base)
        self.assert_no_output("bad")  # 先全部验证再写：失败时一个文件都不留

    def test_brief_rejections(self):
        self.bad(lambda b: b.update(objects=[]), "objects")
        self.bad(lambda b: b["objects"].append(dict(b["objects"][0])), "重复")
        self.bad(lambda b: b["objects"][1].update(id="CART"), "重复")  # 大小写不敏感
        self.bad(lambda b: b["objects"][0].update(id="background"), "重复")
        self.bad(lambda b: b["states"][0].update(id="cart"), "重复")
        self.bad(lambda b: b.update(extra=1), "未知字段")
        self.bad(lambda b: b["objects"][0].update(color="red"), "未知字段")
        self.bad(lambda b: b["camera"]["canvas"].update(width=True), "布尔")
        self.bad(lambda b: b["camera"]["canvas"].update(height=900.0), "整数")
        self.bad(lambda b: b["camera"]["canvas"].update(width="1600"), "整数")
        self.bad(lambda b: b["camera"]["canvas"].update(width=5000, height=900), "3:1")
        self.bad(lambda b: b.pop("camera"), "camera")
        self.bad(lambda b: b.update(camera=[b["camera"], b["camera"]]), "唯一")
        self.bad(lambda b: b["camera"].pop("lens"), "lens")
        self.bad(lambda b: b["camera"].update(position="  "), "position")
        self.bad(lambda b: b.update(version=True), "version")
        self.bad(lambda b: b.update(version=2), "version")
        self.bad(lambda b: b.update(style="X1"), "画风码")
        self.bad(lambda b: b.update(style="C9999"), "未知画风码")
        self.bad(lambda b: b.update(reason=""), "reason")
        self.bad(lambda b: b["background"].update(subject="a cat"), "未知字段")
        self.bad(lambda b: b["states"][0].update(object="nope"), "不是 objects")
        self.bad(lambda b: b["text"][0].update(box=[0.9, 0.9, 0.3, 0.3]), "画布内")
        self.bad(lambda b: b["text"][0].update(box=[True, 0, 0.2, 0.2]), "有限数字")
        self.bad(lambda b: b["objects"][0].update(description="a cart with a sign saying 翻页书店"), "不得写进生图描述")

    def test_brief_file_strict_json(self):
        p = self.base / "dup.json"
        for text, pat in (('{"version":1,"version":1}', "重复键"), ('{"version":NaN}', "NaN"), ("", "空文件"), ("{", "合法 JSON")):
            p.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(L.LayersError, pat):
                L.load_json(p)

    def test_source_and_reference_paths(self):
        def prep_with(**extra):
            b = copy.deepcopy(self.brief)
            b.update(extra)
            self.write_brief(b)
            L.prepare_run(b, self.tmp / "p2", base_path=self.base)

        (self.base / "story.txt").write_text("  \n", encoding="utf-8")
        with self.assertRaisesRegex(L.LayersError, "source 不得为空"):
            prep_with(source="story.txt")
        (self.base / "empty.txt").write_bytes(b"")
        with self.assertRaisesRegex(L.LayersError, "source 不得为空"):
            prep_with(source="empty.txt")
        with self.assertRaisesRegex(L.LayersError, "穿越"):
            prep_with(source="../story.txt")
        with self.assertRaisesRegex(L.LayersError, "绝对路径"):
            prep_with(source=str(self.base / "story.txt"))
        (self.base / "notimg.png").write_text("not an image", encoding="utf-8")
        with self.assertRaisesRegex(L.LayersError, "不是可读图片"):
            prep_with(references=[{"role": "style", "path": "notimg.png"}])
        (self.base / "zero.png").write_bytes(b"")
        with self.assertRaisesRegex(L.LayersError, "空文件"):
            prep_with(references=[{"role": "style", "path": "zero.png"}])
        real = self.tmp / "real.png"
        save_background(real, (1, 2, 3))
        os.symlink(real, self.base / "link.png")
        with self.assertRaisesRegex(L.LayersError, "符号链接"):
            prep_with(references=[{"role": "style", "path": "link.png"}])
        (self.base / "d").mkdir()
        os.symlink(self.tmp, self.base / "d" / "up")
        with self.assertRaisesRegex(L.LayersError, "符号链接"):
            prep_with(references=[{"role": "style", "path": "d/up/real.png"}])
        save_background(self.base / "ok.png", (9, 9, 9))
        with self.assertRaisesRegex(L.LayersError, "role"):
            prep_with(references=[{"role": "bogus", "path": "ok.png"}])
        self.assertFalse((self.tmp / "p2").exists())
        prep_with(references=[{"role": "style", "path": "ok.png"}])  # 合法对照：同一函数能成功
        self.assertTrue((self.tmp / "p2" / "prepared.json").is_file())


class ImportTests(Base):
    def test_import_happy_path_facts(self):
        brief, pp, prepared, paths = self.happy()
        before = {k: sha(v) for k, v in paths.items()}
        out, report = self.do_import(brief, pp, self.make_results(prepared, pp, paths))
        self.assertEqual((report["status"], report["accepted"], report["visual_review"]), ("pending_visual_review", False, "not_run"))
        self.assertEqual(report["camera_visual_match"], "human_required_not_verified")
        self.assertTrue(report["generated_by_this_prepared"])
        self.assertEqual({k: sha(v) for k, v in paths.items()}, before)  # 原输入未改
        self.assertEqual([p.name for p in self.tmp.glob(".result.partial-*")], [])
        from PIL import Image
        for rec in report["layers"]:
            raw = out / rec["raw_file"]
            self.assertEqual(sha(raw), rec["raw_sha256"])
            self.assertEqual(sha(raw), before[rec["id"]])  # 原始字节副本
            layer = out / rec["file"]
            self.assertEqual(sha(layer), rec["sha256"])
            self.assertEqual(layer.stat().st_size, rec["bytes"])
            with Image.open(layer) as im:
                self.assertEqual(im.size, SIZE)  # 完整 canvas，不裁剪
                self.assertEqual(rec["pixel_size"], list(im.size))
            self.assertIsNone(rec["generation"]["model"])
            self.assertIsNone(rec["generation"]["est_usd"])
            self.assertEqual(rec["generation"]["origin"], "this_prepared")
            if rec["kind"] != "background":
                with Image.open(layer) as im:
                    a = im.getchannel("A").getextrema()
                    self.assertEqual(a, (0, 255))
                k = rec["keying"]
                for key in ("green_spill_frac", "green_spill_px_on_edge", "spill_review_needed", "foreground_frac", "border_green_frac",
                            "border_alpha_clear_frac", "edge_stats_basis", "post_despill_max_green_excess"):
                    self.assertIn(key, k)
                # 溢色指标测的是去溢前原图颜色；标记与指标自洽，且不被当成视觉证明。去溢后的实际残留另记并 ≤6
                self.assertEqual(k["edge_stats_basis"], "original_rgb_before_despill_with_keyed_alpha")
                self.assertEqual(k["spill_review_needed"], k["green_spill_frac"] > L.SPILL_REVIEW_FRAC)
                self.assertTrue(k["spill_flag_not_visual_proof"])
                self.assertLessEqual(k["post_despill_max_green_excess"], 6)
                self.assertGreaterEqual(k["border_alpha_clear_frac"], L.BORDER_GREEN_MIN)
        for rel, meta in report["files"].items():
            self.assertEqual(sha(out / rel), meta["sha256"])
            self.assertEqual((out / rel).stat().st_size, meta["bytes"])
        self.assertTrue((out / "preview-base.png").is_file())
        self.assertEqual(sha(out / "host-results.json"), report["host_import"]["results_sha256"])
        manifest = json.loads((out / "layers.json").read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in manifest["layers"]], ["background", "cart", "cat", "cat-asleep"])
        self.assertEqual(manifest["text_overlays"][0]["render"], "code_overlay")
        self.assertTrue(manifest["generated_by_this_prepared"])
        self.assertEqual(manifest["generation_origin"], "codex_builtin_unknown_model")
        self.assertTrue(all(x["generation"]["origin"] == "this_prepared" for x in manifest["layers"]))
        self.assertTrue((out / "host-results.json").is_file() and not (out / "historical-results.json").exists())
        self.assertIn("host_import", report)
        self.assertIn("vendor", report)
        # 去绿溢：非透明像素里不应还有明显绿偏
        with Image.open(out / "layers" / "cart.png") as im:
            px = [p for p in im.getdata() if p[3] > 200]
        self.assertTrue(px)
        self.assertTrue(all(p[1] - max(p[0], p[2]) <= 6 for p in px))

    def test_relative_result_paths_resolve_against_base(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths, relative=True)
        out, report = self.do_import(brief, pp, res)
        self.assertEqual(report["status"], "pending_visual_review")

    def test_relative_result_path_traversal_and_symlink_and_nonimage(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        outside = self.tmp / "outside.png"
        shutil.copyfile(paths["cart"], outside)
        r = copy.deepcopy(res)
        r["images"]["cart"]["path"] = "../outside.png"
        with self.assertRaisesRegex(L.LayersError, "穿越"):
            self.do_import(brief, pp, r, "t1")
        self.assert_no_output("t1")
        link = self.host / "cart-link.png"
        os.symlink(paths["cart"], link)
        r = copy.deepcopy(res)
        r["images"]["cart"]["path"] = str(link)
        with self.assertRaisesRegex(L.LayersError, "符号链接"):
            self.do_import(brief, pp, r, "t2")
        self.assert_no_output("t2")
        bad = self.host / "cart-bad.png"
        bad.write_text("nope", encoding="utf-8")
        r = copy.deepcopy(res)
        r["images"]["cart"].update(path=str(bad), sha256=sha(bad))
        with self.assertRaisesRegex(L.LayersError, "不是可读图片"):
            self.do_import(brief, pp, r, "t3")
        empty = self.host / "cart-empty.png"
        empty.write_bytes(b"")
        r["images"]["cart"].update(path=str(empty), sha256=sha(empty))
        with self.assertRaisesRegex(L.LayersError, "空文件"):
            self.do_import(brief, pp, r, "t4")
        for n in ("t3", "t4"):
            self.assert_no_output(n)

    def test_missing_extra_and_empty_images(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        r = copy.deepcopy(res)
        del r["images"]["cat"]
        with self.assertRaisesRegex(L.LayersError, "缺：.*cat"):
            self.do_import(brief, pp, r, "m1")
        r = copy.deepcopy(res)
        r["images"]["ghost"] = copy.deepcopy(r["images"]["cat"])
        with self.assertRaisesRegex(L.LayersError, "多：.*ghost"):
            self.do_import(brief, pp, r, "m2")
        r = copy.deepcopy(res)
        r["images"] = {}
        with self.assertRaisesRegex(L.LayersError, "缺"):
            self.do_import(brief, pp, r, "m3")
        for n in ("m1", "m2", "m3"):
            self.assert_no_output(n)

    def test_existing_output_directory_not_overwritten(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        existing = self.tmp / "result"
        existing.mkdir()
        (existing / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.do_import(brief, pp, res)
        self.assertEqual([p.name for p in existing.iterdir()], ["keep.txt"])

    def test_binding_mismatches(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)

        def expect(mut, pattern, name):
            r = copy.deepcopy(res)
            mut(r)
            with self.assertRaisesRegex(L.LayersError, pattern):
                self.do_import(brief, pp, r, name)
            self.assert_no_output(name)

        expect(lambda r: r.update(prepared_sha256="0" * 64), "未绑定当前 prepared", "b1")
        expect(lambda r: r.update(version=True), "未绑定当前 prepared", "b2")
        expect(lambda r: r.update(extra=1), "字段限于", "b3")
        expect(lambda r: r["images"]["cart"].update(sha256="1" * 64), "摘要不一致", "b4")
        expect(lambda r: r["images"]["cart"]["tool_arguments"].update(prompt="other"), "工具参数", "b5")
        expect(lambda r: r["images"]["cart"]["tool_arguments"].update(transparent_background=True), "工具参数", "b6")
        expect(lambda r: r["images"]["cart"]["tool_arguments"].pop("transparent_background"), "工具参数", "b7")
        expect(lambda r: r["images"]["cart"].update(model="gpt-image-2"), "保持未知", "b8")
        expect(lambda r: r["images"]["cart"].update(est_usd=0.0), "保持未知", "b9")
        expect(lambda r: r["images"]["cart"].update(provider="openai"), "保持未知", "b10")
        expect(lambda r: r["images"]["cart"].update(seconds=-1), "耗时", "b11")
        expect(lambda r: r["images"]["cart"].update(seconds=True), "耗时", "b12")
        expect(lambda r: r["images"]["cart"].update(prompt_sha256="2" * 64), "工具参数", "b13")
        expect(lambda r: r["images"]["cart"].update(unknown=1), "未知字段", "b14")
        # 工具参数键集合必须恰为三项：多出的 model / size 不得被静默吞下，referenced_image_paths 缺了也不能按 [] 放行
        expect(lambda r: r["images"]["cart"]["tool_arguments"].update(model="gpt-image-1.5"), "字段必须恰为", "b15")
        expect(lambda r: r["images"]["cart"]["tool_arguments"].update(size="1024x1024"), "字段必须恰为", "b16")
        expect(lambda r: r["images"]["cart"]["tool_arguments"].pop("referenced_image_paths"), "字段必须恰为", "b17")
        expect(lambda r: r["images"]["cat-asleep"]["tool_arguments"].pop("referenced_image_paths"), "字段必须恰为", "b18")

    def test_state_must_lock_anchor_reference(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        state = next(t for t in prepared["tasks"] if t["id"] == "cat-asleep")
        idx = next(i for i, r in enumerate(state["references"]) if r.get("from_task"))
        r = copy.deepcopy(res)
        r["images"]["cat-asleep"]["reference_sha256s"][idx] = "0" * 64
        with self.assertRaisesRegex(L.LayersError, "未锁定基准"):
            self.do_import(brief, pp, r, "a1")
        r = copy.deepcopy(res)
        r["images"]["cat-asleep"]["reference_sha256s"][idx] = sha(paths["cart"])  # 挂了别的物件
        with self.assertRaisesRegex(L.LayersError, "未锁定基准"):
            self.do_import(brief, pp, r, "a2")
        r = copy.deepcopy(res)
        r["images"]["cat-asleep"]["tool_arguments"]["referenced_image_paths"][idx] = str(paths["cart"])
        with self.assertRaisesRegex(L.LayersError, "工具参数"):
            self.do_import(brief, pp, r, "a3")
        r = copy.deepcopy(res)
        r["images"]["cat-asleep"]["tool_arguments"]["referenced_image_paths"] = []
        with self.assertRaisesRegex(L.LayersError, "工具参数"):
            self.do_import(brief, pp, r, "a4")
        for n in ("a1", "a2", "a3", "a4"):
            self.assert_no_output(n)

    def test_inputs_changed_since_prepare(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        changed = copy.deepcopy(brief)
        changed["objects"][0]["description"] += " with a red ribbon"
        with self.assertRaisesRegex(L.LayersError, "已变化"):
            self.do_import(changed, pp, res, "c1")
        changed = copy.deepcopy(brief)
        changed["camera"]["lens"] = "85mm telephoto"
        with self.assertRaisesRegex(L.LayersError, "已变化"):
            self.do_import(changed, pp, res, "c2")
        self.assert_no_output("c1")
        self.assert_no_output("c2")

    def test_reference_image_sha_change_after_prepare(self):
        brief = copy.deepcopy(self.brief)
        save_background(self.base / "style.png", (30, 60, 90))
        brief["references"] = [{"role": "style", "path": "style.png"}]
        brief, pp, prepared = self.prep(brief)
        paths = self.make_images(prepared)
        res = self.make_results(prepared, pp, paths)
        save_background(self.base / "style.png", (31, 60, 90))  # 参考图被改
        with self.assertRaisesRegex(L.LayersError, "参考图 SHA"):
            self.do_import(brief, pp, res, "r1")
        self.assert_no_output("r1")
        save_background(self.base / "style.png", (30, 60, 90))  # 改回后同一份记录可导入：对照组
        out, report = self.do_import(brief, pp, res, "r2")
        self.assertEqual(report["status"], "pending_visual_review")

    def test_contract_change_detected(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        forged = json.loads(pp.read_text(encoding="utf-8"))
        forged["contract"]["sha256"] = "0" * 64  # 等价于合同在 prepare 之后变了
        fp = self.tmp / "forged-prepared.json"
        fp.write_text(json.dumps(forged, ensure_ascii=False), encoding="utf-8")
        res["prepared_sha256"] = sha(fp)
        with self.assertRaisesRegex(L.LayersError, "已变化.*contract"):
            self.do_import(brief, fp, res, "k1")
        self.assert_no_output("k1")

    def test_keying_gates_and_partial_failure_leaves_nothing(self):
        from PIL import Image
        brief, pp, prepared, paths = self.happy()
        for label, painter in (
                ("全绿空白", lambda p: Image.new("RGB", SIZE, (0, 255, 0)).save(p, "PNG")),
                ("无绿幕", lambda p: Image.new("RGB", SIZE, (230, 140, 40)).save(p, "PNG")),
                ("前景触边", lambda p: save_object(p, (0, 0, 319, 179)))):
            shutil.rmtree(self.host)
            self.host.mkdir()
            paths = self.make_images(prepared)  # cart/cat 在 cat-asleep 之前已成功抠完
            painter(paths["cat-asleep"])  # 最后一张出问题：前面都处理过了，目录仍不能留下
            res = self.make_results(prepared, pp, paths)
            name = "g" + str(abs(hash(label)))
            with self.assertRaises(L.LayersError, msg=label):
                self.do_import(brief, pp, res, name)
            self.assert_no_output(name)

    def test_duplicate_and_mismatched_sizes(self):
        from PIL import Image
        brief, pp, prepared, paths = self.happy()
        shutil.copyfile(paths["cart"], paths["cat"])
        with self.assertRaisesRegex(L.LayersError, "同一张原图"):
            self.do_import(brief, pp, self.make_results(prepared, pp, paths), "d1")
        paths = self.make_images(prepared)
        Image.new("RGB", (160, 90), (0, 255, 0)).save(paths["cat"], "PNG")
        with self.assertRaisesRegex(L.LayersError, "同一画布尺寸"):
            self.do_import(brief, pp, self.make_results(prepared, pp, paths), "d2")
        for n in ("d1", "d2"):
            self.assert_no_output(n)
        # 与 brief 画布比例不同（全部一致但不是 16:9）
        wide = {t["id"]: self.host / f"w-{t['id']}.png" for t in prepared["tasks"]}
        for i, p in enumerate(wide.values()):
            Image.new("RGB", (300, 200), (i * 20, 255, 0)).save(p, "PNG")
        with self.assertRaisesRegex(L.LayersError, "不同比例"):
            self.do_import(brief, pp, self.make_results(prepared, pp, wide), "d3")
        self.assert_no_output("d3")

    def test_historical_import_is_labelled_and_limited(self):
        b = copy.deepcopy(self.brief)
        b.pop("states")
        brief, pp, prepared = self.prep(b)
        paths = self.make_images(prepared)
        res = self.make_results(prepared, pp, paths, historical=True)
        out, report = self.do_import(brief, pp, res)
        self.assertEqual(report["status"], "keying_only_historical")
        self.assertFalse(report["generated_by_this_prepared"])
        self.assertEqual(report["generation_origin"], "historical_sample_not_from_this_prepared")
        self.assertTrue(all(x["generation"]["origin"].startswith("historical") for x in report["layers"]))
        self.assertIn("不是本 prepared 的出图证据", report["note"])
        # 消费者实际读的 layers.json 和文件名也不得冒认为本次宿主出图
        manifest = json.loads((out / "layers.json").read_text(encoding="utf-8"))
        self.assertIs(manifest["generated_by_this_prepared"], False)
        self.assertEqual(manifest["generation_origin"], "historical_sample_not_from_this_prepared")
        self.assertTrue(all(x["generation"]["origin"] == "historical_sample_not_from_this_prepared" and x["generation"]["provider"] is None
                            for x in manifest["layers"]))
        self.assertTrue((out / "historical-prepared.json").is_file() and (out / "historical-results.json").is_file())
        self.assertFalse(any(Path(n).name.startswith("host-") for n in report["files"]))
        self.assertFalse((out / "host-prepared.json").exists() or (out / "host-results.json").exists())
        self.assertNotIn("host_import", report)
        self.assertEqual(sha(out / "historical-results.json"), report["historical_import"]["results_sha256"])
        self.assertIn("historical-results.json", report["files"])
        # 含状态图时拒绝历史导入
        brief2, pp2, prepared2 = self.prep(name="prep2")
        paths2 = self.make_images(prepared2)
        res2 = self.make_results(prepared2, pp2, paths2, historical=True)
        with self.assertRaisesRegex(L.LayersError, "不能含状态图"):
            self.do_import(brief2, pp2, res2, "h2")
        self.assert_no_output("h2")
        # 历史记录里带工具字段（想冒充真实出图）也被拒
        res3 = self.make_results(prepared, pp, paths, historical=True)
        res3["images"]["cart"]["provider"] = "codex_builtin"
        with self.assertRaisesRegex(L.LayersError, "未知字段"):
            self.do_import(brief, pp, res3, "h3")


class KeyingGateTests(Base):
    def import_with(self, painter, name):
        """cart 用 painter 重画后整组导入；其余层保持默认夹具。"""
        brief, pp, prepared = self.prep(name=f"prep-{name}")
        paths = self.make_images(prepared)
        painter(paths["cart"])
        return self.do_import(brief, pp, self.make_results(prepared, pp, paths), name)

    @staticmethod
    def outlined(color, width):
        from PIL import Image, ImageDraw

        def paint(p):
            im = Image.new("RGB", SIZE, (0, 255, 0))
            d = ImageDraw.Draw(im)
            x0, y0, x1, y1 = 60, 50, 120, 120
            d.rectangle((x0 - width, y0 - width, x1 + width, y1 + width), fill=color)
            d.rectangle((x0, y0, x1, y1), fill=(230, 140, 40))
            im.save(p, "PNG")
        return paint

    def test_green_outline_sets_spill_flag_and_post_despill_is_clean(self):
        # 旧实现用 key_green 的输出测溢色，恒为 0；3px 绿描边必须在去溢前原图颜色上被量到
        _, plain = self.import_with(lambda p: save_object(p, (60, 50, 120, 120)), "s0")
        _, report = self.import_with(self.outlined((150, 220, 140), 3), "s1")
        base = next(x for x in plain["layers"] if x["id"] == "cart")["keying"]
        k = next(x for x in report["layers"] if x["id"] == "cart")["keying"]
        self.assertTrue(k["spill_review_needed"])
        self.assertGreater(k["green_spill_frac"], L.SPILL_REVIEW_FRAC)
        self.assertGreater(k["green_spill_px_on_edge"], 0)
        self.assertGreater(k["green_spill_frac"], base["green_spill_frac"])  # 描边确实比干净边缘多出溢色
        self.assertLessEqual(k["post_despill_max_green_excess"], 6)  # 去溢后残留是另一件事实，不能代替前者
        self.assertTrue(k["spill_flag_not_visual_proof"])
        # 对照：无绿描边的橙色物件 + 纯绿背景，被模糊抬高 alpha 的那圈纯幕像素（gd=255）不得再算溢色
        self.assertFalse(base["spill_review_needed"])
        self.assertEqual(base["green_spill_px_on_edge"], 0)
        self.assertEqual(base["green_spill_frac"], 0)
        self.assertGreater(base["edge_ring_px"], 0)  # 对照的 ring 不是空的：0 来自规则，不是来自没有边缘
        self.assertIn("gd>=85 pure screen excluded", base["spill_pixel_rule"])

    def test_wide_bright_green_outline_equals_pure_screen_not_flagged(self):
        # (0,255,0) 描边与背景同色：视为纯幕，物件边缘无绿偏，不应置标记；而 gd=70 的同形描边必须置标记
        _, same = self.import_with(self.outlined((0, 255, 0), 3), "s2")
        _, tinted = self.import_with(self.outlined((150, 220, 140), 3), "s3")
        a = next(x for x in same["layers"] if x["id"] == "cart")["keying"]
        b = next(x for x in tinted["layers"] if x["id"] == "cart")["keying"]
        self.assertFalse(a["spill_review_needed"])
        self.assertTrue(b["spill_review_needed"])

    def test_greyish_green_screen_film_is_rejected_but_pure_green_passes(self):
        from PIL import Image, ImageDraw

        def grey_green(p):
            im = Image.new("RGB", SIZE, (90, 160, 95))  # gd=65：过 gd>=60 预检，但 key_green 只给 alpha≈0.33 的薄膜
            ImageDraw.Draw(im).rectangle((60, 50, 120, 120), fill=(230, 140, 40))
            im.save(p, "PNG")

        brief, pp, prepared, paths = self.happy()
        grey_green(paths["cart"])
        self.assertGreaterEqual(L._border_green_fraction(paths["cart"]), L.BORDER_GREEN_MIN)  # 预检确实放行，拒的是抠后闸门
        with self.assertRaisesRegex(L.LayersError, "绿幕不够纯"):
            self.do_import(brief, pp, self.make_results(prepared, pp, paths), "film")
        self.assert_no_output("film")
        # 对照：同一物件、纯绿幕，抠后四周完全透明，通过
        save_object(paths["cart"], (60, 50, 120, 120))
        out, report = self.do_import(brief, pp, self.make_results(prepared, pp, paths), "pure")
        cart = next(x for x in report["layers"] if x["id"] == "cart")["keying"]
        self.assertGreaterEqual(cart["border_alpha_clear_frac"], L.BORDER_GREEN_MIN)


class FakeFn:
    """冒充 libc 函数：记录实参，按预设设 errno 并返回 -1（或 0）。"""

    def __init__(self, err=0):
        self.err, self.calls = err, []

    def __call__(self, *args):
        self.calls.append(args)
        if self.err:
            ctypes.set_errno(self.err)
            return -1
        return 0


class FakeLibc:
    pass


class ExclusiveRenameTests(Base):
    n_dirs = 0

    def make_dirs(self):
        self.n_dirs += 1  # 同一用例里会多次调用，目录名必须各不相同
        tmp = self.tmp / f"src-dir{self.n_dirs}"
        tmp.mkdir()
        (tmp / "payload.txt").write_text("p", encoding="utf-8")
        return tmp, self.tmp / f"dst-dir{self.n_dirs}"

    @staticmethod
    def race_then_real(real, made):
        """先在 _commit_dir 的 exists 检查之后、helper 调用之前建好目标空目录，再走真实 helper。"""
        def wrapper(tmp, out):
            Path(out).mkdir()
            made.append(os.stat(out).st_ino)
            return real(tmp, out)
        return wrapper

    def test_real_platform_helper_moves_without_plain_rename(self):
        tmp, out = self.make_dirs()
        if sys.platform == "win32":
            L._rename_dir_exclusive(tmp, out)
        else:
            with mock.patch.object(L.os, "rename", side_effect=AssertionError("不得走普通 os.rename")):
                L._rename_dir_exclusive(tmp, out)  # 真实 libc 调用（renamex_np / renameat2）
        self.assertFalse(tmp.exists())
        self.assertEqual((out / "payload.txt").read_text(encoding="utf-8"), "p")

    def test_real_helper_refuses_existing_empty_dir_even_if_exists_checks_lie(self):
        tmp, out = self.make_dirs()
        out.mkdir()
        ino = os.stat(out).st_ino
        # 把所有「先查」都骗成不存在：拒绝必须来自内核的排他改名，而不是二次 exists 检查
        with mock.patch.object(L.Path, "exists", return_value=False), mock.patch.object(L.Path, "is_symlink", return_value=False), \
                mock.patch.object(os.path, "exists", return_value=False), mock.patch.object(os.path, "lexists", return_value=False):
            with self.assertRaises(FileExistsError):
                L._rename_dir_exclusive(tmp, out)
        self.assertEqual(os.stat(out).st_ino, ino)  # 目标仍是原来那个 inode，没有被替换
        self.assertEqual(os.listdir(out), [])
        self.assertTrue((tmp / "payload.txt").is_file())  # helper 直接拒绝时 tmp 原样还在，清理是调用方的事

    def test_commit_dir_race_after_exists_check_does_not_replace_empty_dir(self):
        tmp, out = self.make_dirs()
        made = []
        with mock.patch.object(L, "_rename_dir_exclusive", self.race_then_real(L._rename_dir_exclusive, made)):
            with self.assertRaises(FileExistsError):
                L._commit_dir(tmp, out)
        self.assertEqual(len(made), 1)  # 竞态夹具确实触发了：_commit_dir 调用了 helper
        self.assertEqual(os.stat(out).st_ino, made[0])
        self.assertEqual(os.listdir(out), [])
        self.assertTrue((tmp / "payload.txt").is_file())

    def test_prepare_run_race_rejects_cleans_tmp_and_spares_target(self):
        brief = self.write_brief()
        out = self.tmp / "raced"
        made = []
        with mock.patch.object(L, "_rename_dir_exclusive", self.race_then_real(L._rename_dir_exclusive, made)):
            with self.assertRaises(FileExistsError):
                L.prepare_run(brief, out, base_path=self.base)
        self.assertEqual(os.stat(out).st_ino, made[0])
        self.assertEqual(os.listdir(out), [])  # 本次输出没有落进被抢先创建的目录
        self.assertEqual([p.name for p in self.tmp.glob(".raced.partial-*")], [])  # 失败后临时目录已清

    def test_import_run_race_rejects_cleans_tmp_and_spares_target(self):
        brief, pp, prepared, paths = self.happy()
        res = self.make_results(prepared, pp, paths)
        made = []
        with mock.patch.object(L, "_rename_dir_exclusive", self.race_then_real(L._rename_dir_exclusive, made)):
            with self.assertRaises(FileExistsError):
                self.do_import(brief, pp, res, "raced-import")
        out = self.tmp / "raced-import"
        self.assertEqual(os.stat(out).st_ino, made[0])
        self.assertEqual(os.listdir(out), [])
        self.assertEqual([p.name for p in self.tmp.glob(".raced-import.partial-*")], [])

    def test_success_path_goes_through_exclusive_helper(self):
        brief = self.write_brief()
        out = self.tmp / "viahelper"
        with mock.patch.object(L, "_rename_dir_exclusive", wraps=L._rename_dir_exclusive) as spy:
            L.prepare_run(brief, out, base_path=self.base)
        spy.assert_called_once()
        self.assertEqual(Path(spy.call_args.args[1]), out)
        self.assertTrue((out / "prepared.json").is_file())
        self.assertEqual([p.name for p in self.tmp.glob(".viahelper.partial-*")], [])

    def fake(self, plat, fn_name, err):
        libc = FakeLibc()
        fn = FakeFn(err)
        if fn_name:
            setattr(libc, fn_name, fn)
        return libc, fn

    def run_fake(self, plat, fn_name, err):
        tmp, out = self.make_dirs()
        libc, fn = self.fake(plat, fn_name, err)
        with mock.patch.object(L, "_platform", return_value=plat), mock.patch.object(L, "_libc", return_value=libc), \
                mock.patch.object(L.os, "rename", side_effect=AssertionError("失败时不得回退到普通 rename")):
            try:
                L._rename_dir_exclusive(tmp, out)
            except Exception as exc:
                return tmp, out, fn, exc
        return tmp, out, fn, None

    def test_darwin_call_shape_and_errno_mapping(self):
        tmp, out, fn, exc = self.run_fake("darwin", "renamex_np", 0)
        self.assertIsNone(exc)
        (src, dst, flags), = fn.calls
        self.assertEqual((src, dst, flags), (os.fsencode(tmp), os.fsencode(out), 0x4))
        self.assertIsInstance(src, bytes)
        self.assertIs(fn.restype, ctypes.c_int)
        self.assertEqual(fn.argtypes, [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint])
        for err in (errno.EEXIST, errno.ENOTEMPTY):
            _, _, _, exc = self.run_fake("darwin", "renamex_np", err)
            self.assertIsInstance(exc, FileExistsError, err)
        _, _, _, exc = self.run_fake("darwin", "renamex_np", errno.EIO)
        self.assertIsInstance(exc, L.LayersError)
        self.assertNotIsInstance(exc, FileExistsError)

    def test_linux_call_shape_and_enosys_fails_closed(self):
        tmp, out, fn, exc = self.run_fake("linux", "renameat2", 0)
        self.assertIsNone(exc)
        (a, src, b, dst, flags), = fn.calls
        self.assertEqual((a, src, b, dst, flags), (-100, os.fsencode(tmp), -100, os.fsencode(out), 1))
        self.assertEqual(fn.argtypes, [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint])
        _, _, _, exc = self.run_fake("linux", "renameat2", errno.EEXIST)
        self.assertIsInstance(exc, FileExistsError)
        for err in (errno.ENOSYS, errno.EINVAL):
            tmp, out, fn, exc = self.run_fake("linux", "renameat2", err)
            self.assertIsInstance(exc, L.LayersError, err)
            self.assertNotIsInstance(exc, FileExistsError)
            self.assertFalse(out.exists())  # 不回退，也不是「成功」
            self.assertTrue((tmp / "payload.txt").is_file())

    def test_missing_function_and_unsupported_platform_fail_closed(self):
        for plat, name in (("darwin", "renamex_np"), ("linux", "renameat2")):
            tmp, out, _, exc = self.run_fake(plat, None, 0)  # libc 里没有这个函数
            self.assertIsInstance(exc, L.LayersError, plat)
            self.assertIn(name, str(exc))
            self.assertFalse(out.exists())
        tmp, out, fn, exc = self.run_fake("freebsd14", "renameat2", 0)
        self.assertIsInstance(exc, L.LayersError)
        self.assertEqual(fn.calls, [])
        self.assertFalse(out.exists())

    def test_prepare_run_with_unsupported_platform_reports_failure_and_leaves_nothing(self):
        brief = self.write_brief()
        out = self.tmp / "unsup"
        with mock.patch.object(L, "_platform", return_value="plan9"):
            with self.assertRaisesRegex(L.LayersError, "不支持的平台"):
                L.prepare_run(brief, out, base_path=self.base)
        self.assert_no_output("unsup")


class VendorAndHygieneTests(unittest.TestCase):
    def test_vendor_copy_matches_reviewed_sha_and_source_record(self):
        # 不再依赖私有冲刺目录：以固定的已审核原始 SHA 为准，与 SOURCE.json、实际文件三方核对
        source = json.loads((HERE / "vendor" / "SOURCE.json").read_text(encoding="utf-8"))
        actual = sha(HERE / "vendor" / "cutlib.py")
        self.assertRegex(REVIEWED_CUTLIB_SHA, r"^[0-9a-f]{64}$")
        self.assertEqual(source["file"], "cutlib.py")
        self.assertEqual(source["sha256"], REVIEWED_CUTLIB_SHA)
        self.assertEqual(actual, REVIEWED_CUTLIB_SHA)
        tampered = hashlib.sha256((HERE / "vendor" / "cutlib.py").read_bytes() + b"\n# tampered\n").hexdigest()
        self.assertNotEqual(tampered, REVIEWED_CUTLIB_SHA)  # 对照：任何改动都会让核对失败

    def test_pin_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            copy_path = Path(td) / "cutlib.py"
            copy_path.write_bytes((HERE / "vendor" / "cutlib.py").read_bytes() + b"\n# tampered\n")
            good = sha(HERE / "vendor" / "cutlib.py")
            with self.assertRaisesRegex(L.LayersError, "不一致"):
                L.load_cutlib(copy_path, pin=good)
            mod, digest, pinned = L.load_cutlib(copy_path, pin=sha(copy_path))
            self.assertTrue(pinned)
            self.assertEqual(digest, sha(copy_path))
            with self.assertRaisesRegex(L.LayersError, "缺少 vendor"):
                L.load_cutlib(Path(td) / "missing.py")

    def test_vendor_loads_pinned_by_default_and_has_core(self):
        mod, digest, pinned = L.load_cutlib()
        self.assertEqual(digest, sha(HERE / "vendor" / "cutlib.py"))
        self.assertEqual(digest, REVIEWED_CUTLIB_SHA)
        self.assertEqual(pinned, json.loads((HERE / "vendor" / "SOURCE.json").read_text(encoding="utf-8"))["sha256"] is not None)
        self.assertTrue(callable(mod.key_green) and callable(mod.edge_stats))

    def test_no_network_subprocess_or_vision_in_layers(self):
        src = (HERE / "layers.py").read_text(encoding="utf-8")
        for needle in ("urllib", "import requests", "socket", "subprocess", "http.client", ".vision(", "cutout_bin", "/Users/sandy"):
            self.assertNotIn(needle, src, needle)
        self.assertFalse(re.search(r"^import (torch|cv2|rembg|onnx)", src, re.M))

    def test_no_skill_md_in_fixtures(self):
        # 范围只是测试夹具目录：仓根的真 SKILL.md 本来就合法，不能扫仓根
        self.assertTrue(FIXTURES.is_dir())
        self.assertTrue(BRIEF_FIXTURE.is_file())  # 空目录会让「没有 SKILL.md」空转，先确认夹具确实在
        self.assertEqual(find_skill_md(FIXTURES), [])
        with tempfile.TemporaryDirectory() as td:  # 对照：同一检测函数确实能发现混入的 SKILL.md
            (Path(td) / "sub").mkdir()
            (Path(td) / "sub" / "SKILL.md").write_text("x", encoding="utf-8")
            self.assertEqual(len(find_skill_md(td)), 1)


if __name__ == "__main__":
    unittest.main()
