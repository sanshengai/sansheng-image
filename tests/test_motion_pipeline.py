"""stylebook.motion_pipeline 单测。

- 第一部分用自有 FakeRunner 只验证「命令安全、环境隔离、失败传播、拒绝反例」，不产生真实视频，也不需要 Node / ffmpeg / 浏览器；
  这部分是普通行为测试，缺少正式模块或 Pillow 都直接报错，不会跳过。
- 第二部分 RealToolchainTest 用真实 HyperFrames CLI + 真实 ffmpeg 渲染一个很小的本地 HTML，是可选测试：
  HyperFrames CLI 取环境变量 HYPERFRAMES_CLI（hyperframes.mjs 的路径），否则取 <根>/node_modules/hyperframes/bin/hyperframes.mjs
  （项目自装）；缺 CLI、node、ffmpeg、ffprobe 或本机渲染浏览器时明确跳过并写明缺什么。

运行（在 Skill 根目录）：
    python3 -I -m unittest discover -s tests -p "test_motion_pipeline.py" -v
默认测试当前仓；要测另一份副本，设 SANSHENG_IMAGE_ROOT=<sansheng-image 根目录>。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("SANSHENG_IMAGE_ROOT") or Path(__file__).resolve().parents[1]).resolve()
SCRIPTS = ROOT / "scripts"
HERE = SCRIPTS / "stylebook"  # 被测模块所在目录

if sys.path[:1] != [str(SCRIPTS)]:
    sys.path.insert(0, str(SCRIPTS))

from stylebook import motion_pipeline as mp  # noqa: E402  缺少正式模块时直接 ImportError，不跳过
from PIL import Image  # noqa: E402  Pillow 是基线依赖，缺少即报错

ENCODERS = "Encoders:\n V..... = Video\n ------\n V....D libx264    libx264 H.264\n V....D libwebp_anim    WebP\n V....D gif    GIF\n"
SECRETS = {"GEMINI_API_KEY": "g-secret", "ANTHROPIC_API_KEY": "a-secret", "TAVILY_API_KEY": "t-secret", "OPENAI_API_KEY": "o-secret"}


def setUpModule():
    if Path(mp.__file__).resolve() != HERE / "motion_pipeline.py":
        raise RuntimeError(f"导入的 motion_pipeline 不是 {HERE / 'motion_pipeline.py'}，而是 {mp.__file__}；请检查 sys.path / SANSHENG_IMAGE_ROOT")


def good_report(**over):
    d = {"ok": True, "browserSkipped": False}
    for s in ("lint", "runtime", "layout", "motion", "contrast"):
        d[s] = {"ok": True, "errorCount": 0, "warningCount": 0, "infoCount": 0, "findings": []}
    d["lint"]["filesScanned"] = 1
    d.update(over)
    return d


def html(w=320, h=180, d="2", extra_head="", extra_body=""):
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0}}#root{{position:relative;width:{w}px;height:{h}px;background:#111}}'
            f'.l{{position:absolute;left:0;top:0;width:50%;height:100%;background:#fff}}</style>{extra_head}</head>'
            f'<body><div id="root" data-composition-id="main" data-start="0" data-width="{w}" data-height="{h}" data-duration="{d}" data-no-timeline>'
            f'<div class="l"></div>{extra_body}</div></body></html>')


def png_bytes(w, h):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


class FakeRunner:
    """只记录 argv/env/cwd 并按子命令返回预设结果；不执行任何真实命令。"""

    def __init__(self, node, cli):
        self.node, self.cli = node, cli
        self.calls, self.envs, self.cwds = [], [], []
        self.node_version, self.hf_version = "v22.3.0", "0.8.140"
        self.check_result = None
        self.render_rc = 0
        self.touch_project = False
        self.probe = {"w": 320, "h": 180, "frames": 60, "dur": "2.0", "fps": "30/1", "codec": "h264", "pix": "yuv420p"}
        self.gif_size = 11 * 1024 * 1024
        self.frame_fn = None
        self.encoders_text = ENCODERS
        self.webp_frames = None          # 覆盖 WebP 抽帧数
        self.webp_bad_index = None       # 第几帧写成坏文件
        self.webp_wrong_size_index = None
        self.webp_static = False         # 所有抽帧像素相同

    def __call__(self, argv, *, timeout, cwd, env):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), argv
        assert timeout <= 300
        self.calls.append(list(argv)), self.envs.append(env), self.cwds.append(cwd)
        r = lambda out="", rc=0: mp.CmdResult(list(argv), rc, out, "stderr-text")  # noqa: E731
        if argv[0] == self.node:
            if argv[1:] == ["--version"]:
                return r(self.node_version + "\n")
            assert argv[1] == self.cli
            if argv[2] == "--version":
                return r(self.hf_version + "\n")
            if argv[2] == "check":
                return self.check_result or r(json.dumps(good_report()))
            if argv[2] == "render":
                if self.render_rc:
                    return r("", self.render_rc)
                Path(argv[argv.index("-o") + 1]).write_bytes(b"fake-mp4")
                if self.touch_project:
                    (Path(argv[-1]) / "injected.txt").write_text("x")
                return r("rendered")
        name = os.path.basename(argv[0])
        if name == "ffmpeg":
            if argv[1:] == ["-version"]:
                return r("ffmpeg version 7.0 Copyright\n")
            if "-encoders" in argv:
                return r(self.encoders_text)
            dest = Path(argv[-1])
            if "%04d" in dest.name:                       # Pillow 路径的抽帧
                size = tuple(int(x) for x in re.search(r"scale=(\d+):(\d+)", " ".join(argv)).groups())
                count = self.webp_frames if self.webp_frames is not None else int(argv[argv.index("-frames:v") + 1])
                for i in range(1, count + 1):
                    fp = dest.parent / (dest.name % i)
                    if i == self.webp_bad_index:
                        fp.write_bytes(b"not a png")
                        continue
                    sz = (size[0] + 2, size[1]) if i == self.webp_wrong_size_index else size
                    color = (10, 40, 90) if self.webp_static else ((i * 4) % 256, 255 - (i * 3) % 256, 90)
                    Image.new("RGB", sz, color).save(fp)
            elif any("select=eq" in a for a in argv):
                idx = int(re.search(r"n\\,(\d+)", " ".join(argv)).group(1))
                (self.frame_fn(idx) if self.frame_fn else Image.new("RGB", (8, 8))).save(dest)
            elif dest.suffix == ".png":
                dest.write_bytes(png_bytes(self.probe["w"], self.probe["h"]))
            elif dest.suffix == ".gif":
                dest.write_bytes(b"GIF89a" + b"\0" * self.gif_size)
            else:
                dest.write_bytes(b"fake-out")
            return r()
        if name == "ffprobe":
            if argv[1:] == ["-version"]:
                return r("ffprobe version 7.0 Copyright\n")
            p = self.probe
            return r(json.dumps({"streams": [{"codec_type": "video", "codec_name": p["codec"], "width": p["w"], "height": p["h"], "pix_fmt": p["pix"],
                                              "r_frame_rate": p["fps"], "nb_read_frames": str(p["frames"])}], "format": {"duration": p["dur"]}}))
        raise AssertionError(f"意外的命令 {argv}")


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(mp, "SYSTEM_CHROME", ())     # 让浏览器探测只看测试里造的假缓存，不受本机 Chrome 影响
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.bin = self.tmp / "bin"
        (self.bin / "node_modules" / "hyperframes" / "bin").mkdir(parents=True)
        self.node, self.ffmpeg, self.ffprobe = (str(self.bin / n) for n in ("node", "ffmpeg", "ffprobe"))
        for p in (self.node, self.ffmpeg, self.ffprobe):
            Path(p).write_text("#!/bin/sh\n")
            os.chmod(p, 0o755)
        self.cli = str(self.bin / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs")
        Path(self.cli).write_text("//")
        (self.bin / "node_modules" / "hyperframes" / "package.json").write_text(json.dumps({"name": "hyperframes", "version": "0.8.140"}))
        home = self.tmp / "home"
        chrome = home / ".cache" / "puppeteer" / "chrome-headless-shell" / "152.0" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
        chrome.parent.mkdir(parents=True)
        chrome.write_text("#!/bin/sh\n")
        os.chmod(chrome, 0o755)
        self.chrome = str(chrome)
        self.environ = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", **SECRETS}
        self.project = self.work / "proj"
        self.project.mkdir()
        (self.project / "index.html").write_text(html())
        self.fake = FakeRunner(self.node, self.cli)

    def manifest(self, name="manifest.json", **over):
        d = {"version": 1, "project": "proj", "scene": "brand", "style_code": None, "width": 320, "height": 180, "duration": 2,
             "fps": 30, "loop": True, "presets": []}
        d.update(over)
        path = self.work / name
        path.write_text(json.dumps(d))
        return path

    def run_main(self, cmd, manifest, out, *extra, cli=True):
        argv = [cmd, str(manifest), "--out", str(out), "--node", self.node, "--ffmpeg", self.ffmpeg, "--ffprobe", self.ffprobe, *extra]
        if cli:
            argv += ["--hyperframes-cli", self.cli]
        return mp.main(argv, runner=self.fake, environ=self.environ)

    def report(self, out):
        return json.loads((Path(out) / "render_report.json").read_text(encoding="utf-8"))

    def tools(self):
        t = mp.resolve_tools(self.cli, node=self.node, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe, runner=self.fake, environ=self.environ)
        self.addCleanup(t.cleanup)
        return t


class ManifestTest(Base):
    def test_valid_manifest_loads(self):
        m = mp.load_manifest(self.manifest(scene="article", style_code="C24", presets=["preview-gif", "preview-webp"]))
        self.assertEqual((m.width, m.height, m.fps, m.loop, m.project_dir), (320, 180, 30, True, self.project))

    def test_rejections(self):
        bad = [
            ({"extra": 1}, "未知字段"), ({"fps": 20}, "fps"), ({"fps": 30.0}, "fps"), ({"duration": 16}, "duration"), ({"duration": 0.5}, "duration"),
            ({"width": 321}, "偶数"), ({"scene": "weibo"}, "scene"), ({"scene": "article", "style_code": None}, "style_code"),
            ({"style_code": "X1"}, "style_code"), ({"loop": 1}, "loop"), ({"presets": ["nope"]}, "未知 preset"),
            ({"presets": ["preview-gif", "preview-gif"]}, "不重复"), ({"presets": ["iphone-live-photo"]}, "pending"),
            ({"presets": ["xhs-3x4-mp4"]}, "3:4"), ({"loop": False, "presets": ["music-loop-mp4-candidate"]}, "loop=true"),
            ({"project": "/abs/proj"}, "相对"), ({"version": True}, "version"),
        ]
        for over, needle in bad:
            with self.subTest(over=over), self.assertRaises(mp.ManifestError) as cm:
                mp.load_manifest(self.manifest(**over))
            self.assertIn(needle, str(cm.exception))

    def test_missing_and_duplicate_keys_rejected(self):
        p = self.work / "m.json"
        p.write_text('{"version": 1}')
        with self.assertRaises(mp.ManifestError):
            mp.load_manifest(p)
        p.write_text('{"version": 1, "version": 1}')
        with self.assertRaises(mp.ManifestError):
            mp.load_manifest(p)
        p.write_text("")
        with self.assertRaises(mp.ManifestError):
            mp.load_manifest(p)

    def test_xhs_accepts_real_3x4_source(self):
        m = mp.load_manifest(self.manifest(scene="xiaohongshu", style_code="C24", width=240, height=320, presets=["xhs-3x4-mp4"]))
        self.assertEqual((m.width, m.height), (240, 320))

    def test_symlinked_project_rejected(self):
        (self.work / "link").symlink_to(self.project)
        with self.assertRaises(mp.PathError):
            mp.load_manifest(self.manifest(project="link"))


class PathTest(Base):
    def test_output_rejections(self):
        m = mp.load_manifest(self.manifest())
        (self.work / "empty").mkdir()
        (self.work / "symlink-out").symlink_to(self.work / "empty")
        (self.work / "viaproj").symlink_to(self.project)
        cases = {"existing empty dir": self.work / "empty", "same as project": self.project, "ancestor of project": self.work,
                 "dangling/dir symlink": self.work / "symlink-out", "inside project": self.project / "out",
                 "inside project via symlinked parent": self.work / "viaproj" / "out"}
        for label, out in cases.items():
            with self.subTest(label), self.assertRaises(mp.PathError):
                mp.resolve_out(str(out), m)

    def test_fresh_output_accepted(self):
        m = mp.load_manifest(self.manifest())
        self.assertEqual(mp.resolve_out(str(self.work / "fresh"), m), self.work / "fresh")


class ProjectTest(Base):
    def inspect(self, **man):
        return mp.inspect_project(mp.load_manifest(self.manifest(**man)))

    def test_valid_project_and_hash_changes(self):
        a = self.inspect()
        self.assertEqual(a, self.inspect())
        (self.project / "index.html").write_text(html() + "<!-- x -->")
        self.assertNotEqual(a["tree_sha256"], self.inspect()["tree_sha256"])

    def test_rejections(self):
        def write(text, name="index.html"):
            (self.project / name).write_text(text, encoding="utf-8")
        cases = {
            "宽度不符": lambda: write(html(w=321)), "时长不符": lambda: write(html(d="3")), "时长带单位": lambda: write(html(d="2s")),
            "缺根": lambda: write("<html><body></body></html>"),
            "两个根": lambda: write(html(extra_body='<div data-composition-id="b"></div>')),
            "第二个 html 含根": lambda: write(html(), "other.html") or (self.project / "other.html").write_text(html()),
            "远程脚本": lambda: write(html(extra_head='<script src="https://cdn.example.com/gsap.js"></script>')),
            "协议相对脚本": lambda: write(html(extra_head='<script src="//cdn.example.com/x.js"></script>')),
            "远程图片": lambda: write(html(extra_body='<img src="http://x.test/a.png">')),
            "远程字体 CSS": lambda: write(html(extra_head="<style>@font-face{font-family:a;src:url(https://f.test/a.woff2)}</style>")),
            "@import 远程": lambda: write(html(extra_head='<style>@import "https://f.test/a.css";</style>')),
            "越出工程": lambda: write(html(extra_body='<img src="../secret.png">')),
            "css 文件远程": lambda: write("@import url('https://f.test/x.css');", "a.css"),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                (self.project / "index.html").write_text(html())
                for extra in ("other.html", "a.css"):
                    (self.project / extra).unlink(missing_ok=True)
                mutate()
                with self.assertRaises(mp.ManifestError):
                    self.inspect()

    def test_missing_index_and_symlink_rejected(self):
        (self.project / "assets").mkdir()
        (self.project / "assets" / "ln.png").symlink_to(self.work / "outside.png")
        with self.assertRaises(mp.ManifestError):
            self.inspect()
        (self.project / "assets" / "ln.png").unlink()
        (self.project / "index.html").unlink()
        with self.assertRaises(mp.ManifestError):
            self.inspect()

    def test_local_assets_allowed(self):
        (self.project / "assets").mkdir()
        (self.project / "assets" / "a.js").write_text("//")
        (self.project / "index.html").write_text(html(extra_head='<script src="assets/a.js"></script>', extra_body='<img src="data:image/png;base64,AA==">'))
        self.assertEqual(self.inspect()["file_count"], 2 + 0)


class CheckEvaluationTest(unittest.TestCase):
    def res(self, stdout, rc=0, **kw):
        return mp.CmdResult(["x"], rc, stdout, "", **kw)

    def test_good_report(self):
        out = mp.evaluate_check(self.res(json.dumps(good_report())))
        self.assertTrue(out["ok"])

    def test_warnings_are_recorded_not_ignored(self):
        r = good_report()
        r["layout"].update(warningCount=1, findings=[{"code": "w1", "severity": "warning", "message": "m"}])
        out = mp.evaluate_check(self.res(json.dumps(r)))
        self.assertEqual(out["warnings"]["layout"], 1)
        self.assertEqual(out["warning_findings"][0]["code"], "w1")

    def test_rejections(self):
        noisy = "[hyperframes] loading\n" + json.dumps(good_report())
        bad_section = good_report()
        bad_section["layout"]["errorCount"] = 2
        no_section = good_report()
        del no_section["motion"]
        zero_files = good_report()
        zero_files["lint"]["filesScanned"] = 0
        cases = {
            "exit0 但 JSON 有 error": self.res(json.dumps(bad_section)), "ok=false": self.res(json.dumps(good_report(ok=False))),
            "浏览器被跳过": self.res(json.dumps(good_report(browserSkipped=True))), "缺分区": self.res(json.dumps(no_section)),
            "lint 零文件": self.res(json.dumps(zero_files)), "stdout 夹噪声": self.res(noisy), "空 stdout": self.res(""), "坏 JSON": self.res("{oops"),
            "JSON 不是对象": self.res("[1]"), "退出码 1": self.res(json.dumps(good_report()), rc=1),
            "超时": mp.CmdResult(["x"], None, "", "", True, "超时 300s"),
        }
        for label, res in cases.items():
            with self.subTest(label), self.assertRaises(mp.StageFailed):
                mp.evaluate_check(res)


class SafetyTest(Base):
    def test_env_drops_secrets_and_disables_telemetry(self):
        env = mp.safe_env(self.environ, browser=self.chrome)
        for k in SECRETS:
            self.assertNotIn(k, env)
        self.assertEqual((env["DO_NOT_TRACK"], env["HYPERFRAMES_NO_TELEMETRY"], env["HYPERFRAMES_BROWSER_PATH"]), ("1", "1", self.chrome))
        self.assertEqual(env["HOME"], self.environ["HOME"])

    def test_argv_shapes(self):
        t = self.tools()
        proj, dest = self.project, self.work / "o" / "master.mp4"
        self.assertEqual(mp.hf_check_argv(t, proj), [self.node, self.cli, "check", "--json", str(proj)])
        self.assertEqual(mp.hf_render_argv(t, proj, dest, 30, "draft"),
                         [self.node, self.cli, "render", "--fps=30", "--format=mp4", "--quality=draft", "--workers=1",
                          "--experimental-fast-capture=false", "-o", str(dest), str(proj)])
        for argv in (mp.hf_check_argv(t, proj), mp.hf_render_argv(t, proj, dest, 30, "draft")):
            self.assertTrue(os.path.isabs(argv[0]) and os.path.isabs(argv[1]))
            self.assertFalse({"npx", "npm", "sh", "bash", "-c"} & set(argv))

    def test_relative_cli_and_overlong_timeout_rejected(self):
        with self.assertRaises(mp.ToolError):
            mp.resolve_tools("node_modules/hyperframes/bin/hyperframes.mjs", node=self.node, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe, environ=self.environ)
        with self.assertRaises(SystemExit):
            mp.main(["doctor", "--hyperframes-cli", self.cli, "--timeout", "301"])

    def test_scaled_size_keeps_aspect_and_never_upscales(self):
        m = mp.load_manifest(self.manifest(width=1600, height=900))
        self.assertEqual(mp.scaled_size(m, 800), (800, 450))
        self.assertEqual(mp.scaled_size(m, 3000), (1600, 900))


class DoctorTest(Base):
    def names(self, checks, ok=None):
        return {c["name"] for c in checks if ok is None or c["ok"] == ok}

    def test_all_required_ok_and_no_optional_docker(self):
        checks = mp.run_doctor(self.tools())
        self.assertFalse([c for c in checks if c["required"] and not c["ok"]], checks)
        self.assertFalse([c for c in checks if "docker" in c["name"].lower()])

    def test_failures(self):
        self.fake.node_version = "v20.11.0"
        self.assertIn("node", self.names(mp.run_doctor(self.tools()), ok=False))
        self.fake.node_version, self.fake.hf_version = "v22.3.0", "0.8.106"
        self.assertIn("hyperframes-cli", self.names(mp.run_doctor(self.tools()), ok=False))
        self.fake.hf_version = "0.8.140"
        shutil.rmtree(Path(self.environ["HOME"]) / ".cache")
        t = self.tools()
        self.assertIn("render-browser", self.names(mp.run_doctor(t), ok=False))

    def test_missing_pillow_is_optional(self):
        checks = mp.run_doctor(self.tools())
        self.assertFalse(next(c for c in checks if c["name"] == "pillow")["required"])


class RenderFlowTest(Base):
    def render(self, name="out", manifest=None, *extra):
        out = self.work / name
        return out, self.run_main("render", manifest or self.manifest(), out, *extra)

    def test_success_order_env_and_report(self):
        before = mp.inventory_project(self.project)["tree_sha256"]
        out, code = self.render()
        self.assertEqual(code, 0)
        rep = self.report(out)
        self.assertEqual(rep["status"], "ok")
        self.assertEqual({a["name"] for a in rep["artifacts"]}, {"master", "poster"})
        subs = [c[2] for c in self.fake.calls if len(c) > 2 and c[0] == self.node and c[1] == self.cli]
        self.assertEqual([s for s in subs if s != "--version"], ["check", "render"])
        for env, cwd in zip(self.fake.envs, self.fake.cwds):
            self.assertFalse(set(SECRETS) & set(env))
            self.assertEqual(env["DO_NOT_TRACK"], "1")
            self.assertNotIn(str(self.project), cwd)
        self.assertTrue(any(e.get("HYPERFRAMES_BROWSER_PATH") == self.chrome for e in self.fake.envs))
        self.assertEqual(mp.inventory_project(self.project)["tree_sha256"], before)
        self.assertTrue(rep["manual_review_required"])

    def test_second_run_to_same_out_refused(self):
        out, code = self.render()
        self.assertEqual(code, 0)
        calls = len(self.fake.calls)
        self.assertEqual(self.render()[1], 2)
        self.assertEqual(len(self.fake.calls), calls)        # 拒绝发生在任何外部命令之前

    def test_check_error_with_exit0_stops_before_render(self):
        bad = good_report()
        bad["runtime"]["errorCount"] = 1
        self.fake.check_result = mp.CmdResult(["x"], 0, json.dumps(bad), "")
        out, code = self.render()
        self.assertEqual(code, 1)
        self.assertEqual(self.report(out)["status"], "failed")
        self.assertFalse([c for c in self.fake.calls if len(c) > 2 and c[2] == "render"])
        self.assertFalse((out / "master.mp4").exists())

    def test_render_exit_nonzero_not_success(self):
        self.fake.render_rc = 1
        out, code = self.render()
        self.assertEqual((code, self.report(out)["status"]), (1, "failed"))
        self.assertFalse((out / "master.mp4").exists())

    def test_master_probe_mismatch_marked_rejected(self):
        for key, val in (("w", 640), ("codec", "vp9"), ("fps", "24/1"), ("frames", 10), ("dur", "9.0")):
            with self.subTest(key):
                self.fake.probe = {"w": 320, "h": 180, "frames": 60, "dur": "2.0", "fps": "30/1", "codec": "h264", "pix": "yuv420p", key: val}
                out, code = self.render(f"out-{key}")
                self.assertEqual((code, self.report(out)["status"]), (1, "failed"))
                self.assertTrue((out / "master.rejected.mp4").is_file())
                self.assertFalse((out / "master.mp4").exists())

    def test_render_that_touches_project_fails(self):
        self.fake.touch_project = True
        out, code = self.render()
        self.assertEqual(code, 1)
        self.assertIn("输入工程", self.report(out)["error"])

    def test_oversize_gif_rejected_with_advice_master_kept(self):
        out, code = self.render("out", self.manifest(presets=["preview-gif"]))
        self.assertEqual(code, 3)
        rep = self.report(out)
        self.assertEqual(rep["status"], "partial")
        gif = next(a for a in rep["artifacts"] if a["name"] == "preview-gif")
        self.assertEqual(gif["status"], "rejected_oversize")
        self.assertIn("--gif-width", gif["reason"])
        self.assertTrue((out / "master.mp4").is_file())
        self.assertFalse((out / "preview.gif").exists())

    def test_timeout_is_failure(self):
        orig = self.fake.__call__

        def slow(argv, **kw):
            if len(argv) > 2 and argv[2] == "render":
                return mp.CmdResult(list(argv), None, "", "", True, "超时 300s")
            return orig(argv, **kw)
        self.fake = slow
        out, code = self.render()
        self.assertEqual((code, self.report(out)["status"]), (1, "failed"))
        self.assertIn("超时", self.report(out)["error"])

    def test_missing_encoder_for_preset_refused_before_work(self):
        self.fake.__class__ = type("NoWebp", (FakeRunner,), {})
        orig = FakeRunner.__call__

        def call(inst, argv, **kw):
            res = orig(inst, argv, **kw)
            if "-encoders" in argv:
                res.stdout = res.stdout.replace("libwebp_anim", "libwebp")
            return res
        self.fake.__class__.__call__ = call
        with mock.patch.object(mp, "pillow_webp_anim_probe", return_value=(False, "模拟：Pillow 无动画 WebP 编码")):
            out, code = self.render("out", self.manifest(presets=["preview-webp"]))
        self.assertEqual(code, 1)                         # ffmpeg 无 libwebp_anim 且 Pillow 无编码能力：两路皆无，在任何工作之前拒绝
        self.assertFalse(out.exists())
        self.assertFalse([c for c in self.fake.calls if len(c) > 2 and c[2] == "render"])

    def test_plan_creates_nothing_and_runs_nothing(self):
        out = self.work / "planned"
        self.assertEqual(self.run_main("plan", self.manifest(), out), 0)
        self.assertFalse(out.exists())
        self.assertEqual(self.fake.calls, [])

    def test_bad_project_rejected_with_code2_before_commands(self):
        (self.project / "index.html").write_text(html(extra_head='<script src="https://cdn.test/x.js"></script>'))
        out, code = self.render()
        self.assertEqual(code, 2)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(out.exists())


class ArtifactVerifyTest(Base):
    def test_webp_info_and_rejects(self):
        def chunk(tag, body):
            return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")
        vp8x = chunk(b"VP8X", bytes([0x02, 0, 0, 0]) + (319).to_bytes(3, "little") + (179).to_bytes(3, "little"))
        anim = chunk(b"ANIM", b"\0\0\0\0\0\0")
        frames = chunk(b"ANMF", b"x" * 16) * 2
        body = b"WEBP" + vp8x + anim + frames
        p = self.work / "a.webp"
        p.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        m = mp.load_manifest(self.manifest())
        self.assertEqual(mp.verify_webp(p, m), [])
        still = b"WEBP" + chunk(b"VP8 ", b"x" * 10)
        p.write_bytes(b"RIFF" + struct.pack("<I", len(still)) + still)
        self.assertTrue(mp.verify_webp(p, m))
        p.write_bytes(b"not webp")
        self.assertTrue(mp.verify_webp(p, m))

    def test_poster_dimension_check(self):
        m = mp.load_manifest(self.manifest())
        p = self.work / "p.png"
        p.write_bytes(png_bytes(320, 180))
        self.assertEqual(mp.verify_poster(p, m), [])
        p.write_bytes(png_bytes(321, 180))
        self.assertTrue(mp.verify_poster(p, m))

    def test_sample_indices(self):
        self.assertEqual(mp.sample_indices(3), [0, 1, 2])
        idx = mp.sample_indices(156)
        self.assertEqual((len(idx), idx[0], idx[-1]), (8, 0, 155))


NO_ANIM_ENCODERS = ENCODERS.replace("libwebp_anim", "libwebp")


def make_webp(path, n=60, dur=33, loop=0, mode="RGB", alpha=255, same=False):
    frames = [Image.new(mode, (64, 36), ((i * 4) % 256 if not same else 9, 80, 120) + ((alpha,) if mode == "RGBA" else ())) for i in range(n)]
    frames[0].save(path, format="WEBP", save_all=True, append_images=frames[1:], duration=dur, loop=loop, quality=70, lossless=True)


class WebpPillowRouteTest(Base):
    def setUp(self):
        super().setUp()
        self.fake.encoders_text = NO_ANIM_ENCODERS

    def render_webp(self, name="out", loop=True, *extra):
        out = self.work / name
        code = self.run_main("render", self.manifest(loop=loop, presets=["preview-webp"]), out, *extra)
        art = next((a for a in self.report(out)["artifacts"] if a["name"] == "preview-webp"), None)
        return out, code, art

    def test_probe_is_real_roundtrip_and_fails_when_encoder_breaks(self):
        ok, detail = mp.pillow_webp_anim_probe()
        self.assertTrue(ok, detail)
        with mock.patch("PIL.Image.Image.save", side_effect=OSError("no webp encoder")):
            ok, detail = mp.pillow_webp_anim_probe()
        self.assertFalse(ok)
        self.assertIn("自检失败", detail)

    def test_route_selection(self):
        t = self.tools()
        t.encoders = {"libwebp_anim", "libx264"}
        self.assertEqual(mp.select_webp_route(t)[0], mp.ROUTE_FFMPEG)
        t.encoders = {"libx264"}
        self.assertEqual(mp.select_webp_route(t)[0], mp.ROUTE_PILLOW)
        with mock.patch.object(mp, "pillow_webp_anim_probe", return_value=(False, "x")):
            self.assertIsNone(mp.select_webp_route(t)[0])
        self.assertTrue(any(c["name"] == "webp-route" and c["ok"] for c in mp.run_doctor(self.tools())))

    def test_render_via_pillow_verifies_behavior_and_cleans_up(self):
        out, code, art = self.render_webp()
        self.assertEqual((code, art["status"], art["webp_route"]), (0, "ok", mp.ROUTE_PILLOW))
        self.assertIn("未使用 ffprobe", art["verified_by"])
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
        self.assertTrue(art["frame_dir_cleaned"])
        self.assertFalse([c for c in self.fake.calls if "libwebp_anim" in c])
        with Image.open(out / "preview.webp") as im:
            self.assertEqual((im.size, im.info["loop"]), ((320, 180), 0))
            self.assertTrue(2 <= im.n_frames <= 60)
            total = 0
            for i in range(im.n_frames):
                im.seek(i)
                im.load()  # WebP duration 在实际解码该帧后才更新
                total += im.info["duration"]
            self.assertLessEqual(abs(total - 2000), 2)
        self.assertEqual(sorted(p.name for p in out.iterdir() if p.name.startswith(".")), [])

    def test_loop_false_writes_loop_1(self):
        out, code, art = self.render_webp("out", False)
        self.assertEqual(art["status"], "ok")
        with Image.open(out / "preview.webp") as im:
            self.assertEqual(im.info["loop"], 1)
        self.assertEqual(mp.webp_info(out / "preview.webp")["loop"], 1)

    def test_bad_extraction_rejected_and_cleaned(self):
        cases = {"帧数过少": {"webp_frames": 40}, "只有一帧": {"webp_frames": 1}, "坏帧": {"webp_bad_index": 7}, "尺寸错": {"webp_wrong_size_index": 3}}
        for label, attrs in cases.items():
            with self.subTest(label):
                for k in ("webp_frames", "webp_bad_index", "webp_wrong_size_index"):
                    setattr(self.fake, k, attrs.get(k))
                out, code, art = self.render_webp(f"out-{label}")
                self.assertEqual((code, art["status"]), (3, "failed"))
                self.assertFalse((out / "preview.webp").exists())
                self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
                self.assertTrue((out / "master.mp4").is_file())

    def test_limits_reject_before_extraction(self):
        with mock.patch.object(mp, "WEBP_MAX_RAW_BYTES", 1000):
            out, code, art = self.render_webp()
        self.assertEqual((code, art["status"]), (3, "rejected_limits"))
        self.assertIn("--webp-width", art["reason"])
        self.assertFalse([c for c in self.fake.calls if any("%04d" in a for a in c)])
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
        with mock.patch.object(mp, "WEBP_MAX_FRAMES", 10):
            out, code, art = self.render_webp("out2")
        self.assertEqual(art["status"], "rejected_limits")

    def test_oversize_rejected_and_file_removed(self):
        out, code, art = self.render_webp("out", True, "--webp-max-bytes", "100")
        self.assertEqual((code, art["status"]), (3, "rejected_oversize"))
        self.assertFalse((out / "preview.webp").exists())
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())

    def test_existing_output_file_never_overwritten(self):
        t = self.tools()
        mp.run_doctor(t, with_hf=False)
        m = mp.load_manifest(self.manifest(presets=["preview-webp"]))
        out = self.work / "out"
        out.mkdir()
        (out / "preview.webp").write_bytes(b"user-data")
        art = mp._make_derived(t, m, out / "master.mp4", out, "preview-webp", mp.default_opts())
        self.assertEqual(art["status"], "failed")
        self.assertEqual((out / "preview.webp").read_bytes(), b"user-data")
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
        self.assertFalse([c for c in self.fake.calls if any("%04d" in a for a in c)])

    def test_static_source_rejected_not_animated(self):
        self.fake.webp_static = True
        out, code, art = self.render_webp()
        self.assertEqual((code, art["status"]), (3, "rejected_static"))
        self.assertIn("not-animated", art["reason"])
        self.assertNotEqual(art["status"], "ok")
        self.assertFalse((out / "preview.webp").exists())
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
        self.assertTrue(art["frame_dir_cleaned"])
        self.assertEqual(self.report(out)["status"], "partial")
        self.assertTrue((out / "master.mp4").is_file())

    def test_no_single_frame_exception_left(self):
        self.assertFalse(hasattr(mp, "_master_is_static"))
        m = mp.load_manifest(self.manifest())
        p = self.work / "one.webp"
        make_webp(p, n=1, same=True)
        with self.assertRaises(TypeError):
            mp.verify_webp(p, m, behavior=True, static_source=True)
        with self.assertRaises(TypeError):
            mp.webp_behavior(p, m, static_source=True)
        for behavior in (True, False):
            with self.subTest(behavior=behavior):
                self.assertTrue(mp.verify_webp(p, m, behavior=behavior), "单帧 WebP 无论是否同色都必须拒绝")

    def test_verify_webp_behavior_counterexamples(self):
        m = mp.load_manifest(self.manifest())
        p = self.work / "v.webp"
        kw = dict(behavior=True, expect_size=(64, 36), expect_frames=60, fps=30)
        make_webp(p, dur=[round((i + 1) * 1000 / 30) - round(i * 1000 / 30) for i in range(60)])
        self.assertEqual(mp.verify_webp(p, m, **kw), [])
        make_webp(p, dur=100)
        self.assertTrue(any("时长" in b for b in mp.verify_webp(p, m, **kw)), "错误时长")
        make_webp(p, dur=33, loop=1)
        self.assertTrue(any("loop" in b for b in mp.verify_webp(p, m, **kw)), "loop 应为 0")
        make_webp(p, dur=33, loop=0)
        mf = mp.load_manifest(self.manifest(loop=False))
        self.assertTrue(any("loop" in b for b in mp.verify_webp(p, mf, **kw)), "loop=false 应为 1")
        make_webp(p, n=1)
        self.assertTrue(mp.verify_webp(p, m, behavior=True), "单帧")
        make_webp(p, n=60, dur=33)
        with Image.open(p) as fixture:
            self.assertEqual(fixture.n_frames, 60, "反例必须实际包含60帧，不能被有损编码合并")
        self.assertTrue(any("帧数" in b for b in mp.verify_webp(p, m, behavior=True, expect_size=(64, 36), expect_frames=30)), "帧数多于输入")
        self.assertTrue(any("尺寸" in b for b in mp.verify_webp(p, m, behavior=True, expect_size=(32, 18))), "尺寸")
        make_webp(p, mode="RGBA", alpha=128)
        self.assertTrue(any("透明" in b for b in mp.verify_webp(p, m, behavior=True)), "半透明")
        p.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\0" * 40)
        self.assertTrue(mp.verify_webp(p, m, behavior=True), "坏文件")
        make_webp(p, n=1, same=True)
        self.assertTrue(any("多帧" in b for b in mp.verify_webp(p, m, behavior=True)), "单帧必须拒绝")


class QaTest(Base):
    def rendered(self, **man):
        out = self.work / "out"
        self.assertEqual(self.run_main("render", self.manifest(**man), out), 0)
        return out

    def qa(self, out, *extra):
        return self.run_main("qa", self.manifest(), out, *extra, cli=False)

    def test_probe_only_is_never_reported_as_pass(self):
        out = self.rendered()
        self.assertEqual(self.qa(out, "--probe-only"), 0)
        rep = json.loads((out / "qa" / "qa_report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["status"], "probe-only-pixel-qa-NOT-run")
        self.assertFalse(rep["pixel"]["executed"])

    def test_qa_reprobes_real_file_not_report(self):
        out = self.rendered()
        self.fake.probe["w"] = 640           # 真实文件「变了」，即使 render_report.json 写着成功
        self.assertEqual(self.qa(out, "--probe-only"), 1)

    def test_qa_missing_master_or_existing_qa_dir_refused(self):
        with self.assertRaises(mp.PathError):
            mp.run_qa(self.tools(), mp.load_manifest(self.manifest()), self.work / "nope", probe_only=True, max_seam=4, min_std=2)
        out = self.rendered()
        (out / "qa").mkdir()
        self.assertEqual(self.qa(out, "--probe-only"), 2)

    def test_missing_preset_file_fails_qa(self):
        out = self.rendered(presets=[])
        m = self.manifest(presets=["share-mp4"])
        self.assertEqual(self.run_main("qa", m, out, "--probe-only", cli=False), 1)

    def test_single_frame_webp_fails_qa_even_if_sampled_frames_identical(self):
        out = self.rendered()
        make_webp(out / "preview.webp", n=1, same=True)
        m = self.manifest(presets=["preview-webp"])
        self.fake.frame_fn = lambda idx: Image.new("RGB", (64, 36), (200, 40, 40))     # 抽样 8 帧完全相同也不能救
        for extra in ((), ("--probe-only",)):
            with self.subTest(extra=extra):
                if (out / "qa").exists():
                    shutil.rmtree(out / "qa")
                self.assertEqual(self.run_main("qa", m, out, *extra, cli=False), 1)
                probs = json.loads((out / "qa" / "qa_report.json").read_text(encoding="utf-8"))["problems"]
                self.assertTrue(any("preview-webp" in p and "多帧" in p for p in probs), probs)

    def test_pixel_qa_pass_and_seam_reject(self):
        def img(inverted=False):
            im = Image.new("RGB", (64, 36), (255, 255, 255) if not inverted else (0, 0, 0))
            im.paste((0, 0, 0) if not inverted else (255, 255, 255), (32, 0, 64, 36))
            return im
        out = self.rendered()
        self.fake.frame_fn = lambda idx: img()
        self.assertEqual(self.qa(out), 0)
        rep = json.loads((out / "qa" / "qa_report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["status"], "auto-pass-needs-manual-review")
        self.assertEqual(rep["pixel"]["seam_mean_abs_diff"], 0)
        self.assertTrue((out / "qa" / "contact.png").is_file())
        shutil.rmtree(out / "qa")
        self.fake.frame_fn = lambda idx: img(inverted=idx == 59)
        self.assertEqual(self.qa(out), 1)
        rep = json.loads((out / "qa" / "qa_report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["status"], "fail")
        self.assertTrue(any("接缝" in p for p in rep["problems"]))
        shutil.rmtree(out / "qa")
        self.fake.frame_fn = lambda idx: Image.new("RGB", (64, 36), (10, 10, 10))
        self.assertEqual(self.qa(out), 1)               # 空白首帧
        self.assertTrue(any("空白" in p for p in json.loads((out / "qa" / "qa_report.json").read_text(encoding="utf-8"))["problems"]))


def _hyperframes_cli_path():
    """HYPERFRAMES_CLI 环境变量优先；否则取项目自装的 <根>/node_modules/hyperframes/bin/hyperframes.mjs。"""
    env = os.environ.get("HYPERFRAMES_CLI")
    if env:
        p = Path(env).expanduser()
        return p.resolve() if p.is_file() else None
    p = ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    return p.resolve() if p.is_file() else None


def _real_toolchain_missing():
    """返回 (缺什么的说明, 工具链)；工具链齐全时说明为 None。"""
    cli = _hyperframes_cli_path()
    if cli is None:
        where = os.environ.get("HYPERFRAMES_CLI") or str(ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs")
        return f"找不到 HyperFrames CLI（{where}）；设 HYPERFRAMES_CLI=<hyperframes.mjs 路径> 或在项目内自装", None
    exes = {n: shutil.which(n) for n in ("node", "ffmpeg", "ffprobe")}
    absent = [n for n, v in exes.items() if not v]
    if absent:
        return f"缺少 {'/'.join(absent)}", None
    browser, _ = mp.find_render_browser(dict(os.environ))
    if not browser:
        return "缺少本机渲染浏览器（Chrome / chrome-headless-shell）", None
    return None, (str(cli), exes["node"], exes["ffmpeg"], exes["ffprobe"])


_TOOLCHAIN_MISSING, _TOOLCHAIN = _real_toolchain_missing()


def _gsap_candidates():
    cands = [ROOT / "node_modules" / "gsap" / "dist" / "gsap.min.js"]
    if os.environ.get("GSAP_JS_PATH"):
        cands.insert(0, Path(os.environ["GSAP_JS_PATH"]).expanduser().resolve())
    if _TOOLCHAIN:
        cli = Path(_TOOLCHAIN[0])
        if len(cli.parents) > 2:  # <node_modules>/hyperframes/bin/hyperframes.mjs → <node_modules>/gsap
            cands.insert(0, cli.parents[2] / "gsap" / "dist" / "gsap.min.js")
    return cands


def moving_html(use_gsap: bool, w=320, h=180, d=2) -> str:
    """真实动画 fixture：白块 0→200px→0 往复位移（正弦缓动，首尾同位置，可无缝循环），内联 window.__timelines['main']。
    时间线 paused、纯函数 posAt(t) 决定姿态、根 composition 带 data-composition-id。
    有本地 gsap.min.js 时用真 gsap 时间线（tl.set 逐帧），否则用内联的最小 seek 型时间线，不引用任何远程脚本。"""
    gsap_tag = '<script src="assets/gsap.min.js"></script>' if use_gsap else ""
    return f'''<!doctype html><html><head><meta charset="utf-8">{gsap_tag}
<style>html,body{{margin:0;width:{w}px;height:{h}px;background:#111}}#root{{position:relative;width:{w}px;height:{h}px;overflow:hidden;background:#111}}
#box{{position:absolute;left:0;top:0;width:100px;height:{h}px;background:#fff;will-change:transform}}</style></head>
<body><div id="root" data-composition-id="main" data-start="0" data-width="{w}" data-height="{h}" data-duration="{d}">
<div id="box"></div></div>
<script>
(function () {{
  const D = {d}, TRAVEL = 200, STEP = 1 / 30;
  const posAt = t => TRAVEL * (1 - Math.cos(2 * Math.PI * Math.min(Math.max(t, 0), D) / D)) / 2;   // t=0 与 t=D 同位置
  const box = document.getElementById('box');
  const apply = t => {{ box.style.transform = 'translateX(' + posAt(t).toFixed(3) + 'px)'; }};
  let tl;
  if (window.gsap) {{
    tl = gsap.timeline({{ paused: true }});
    for (let i = 0; i <= D / STEP + 1e-6; i++) {{ const t = i * STEP; tl.set('#box', {{ x: posAt(t) }}, t); }}
    tl.set({{}}, {{}}, D);
  }} else {{
    let cur = 0;
    tl = {{ paused: true, duration: () => D, totalDuration: () => D, time: v => {{ if (v === undefined) return cur; cur = v; apply(v); return tl; }},
            seek: v => tl.time(v), pause: () => tl, play: () => tl, progress: v => tl.time(v * D) }};
  }}
  window.__timelines = window.__timelines || {{}};
  window.__timelines['main'] = tl;
  apply(0);
}})();
</script></body></html>'''


@unittest.skipIf(_TOOLCHAIN is None, f"跳过真实工具链测试：{_TOOLCHAIN_MISSING}")
class RealToolchainTest(unittest.TestCase):
    """可选：真实 CLI + 真实 ffmpeg；真实往复位移动画 fixture（首尾循环、内联 window.__timelines，无 data-no-timeline）。
    若所用版本的 lint 对此写法有意见，按报错调整 fixture，不要降低下面的断言。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        (self.tmp / "proj").mkdir()
        gsap = next((g for g in _gsap_candidates() if g.is_file()), None)
        if gsap is None:
            self.skipTest("真实动画测试需要本地 gsap.min.js；设 GSAP_JS_PATH，或在项目内安装 gsap；不降低 HyperFrames 检查")
        if gsap is not None:
            (self.tmp / "proj" / "assets").mkdir()
            shutil.copyfile(gsap, self.tmp / "proj" / "assets" / "gsap.min.js")
        (self.tmp / "proj" / "index.html").write_text(moving_html(gsap is not None), encoding="utf-8")
        self.cli, self.node, self.ffmpeg, self.ffprobe = _TOOLCHAIN
        self.tool_args = ["--node", self.node, "--ffmpeg", self.ffmpeg, "--ffprobe", self.ffprobe, "--hyperframes-cli", self.cli]

    def test_doctor_render_qa(self):
        m = self.tmp / "m.json"
        m.write_text(json.dumps({"version": 1, "project": "proj", "scene": "brand", "style_code": None, "width": 320, "height": 180,
                                 "duration": 2, "fps": 30, "loop": True, "presets": ["preview-gif", "preview-webp", "share-mp4"]}))
        self.assertEqual(mp.main(["doctor", *self.tool_args]), 0)
        out = self.tmp / "out"
        self.assertEqual(mp.main(["render", str(m), "--out", str(out), *self.tool_args]), 0)
        rep = json.loads((out / "render_report.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["status"], "ok")
        webp = next(a for a in rep["artifacts"] if a["name"] == "preview-webp")
        self.assertEqual(webp["status"], "ok")                       # 必须真实导出，不能因缺编码器而降级跳过
        encoders = mp._parse_encoders(mp.run_cmd([self.ffmpeg, "-hide_banner", "-encoders"], timeout=60, cwd=str(self.tmp), env=dict(os.environ)).stdout)
        self.assertEqual(webp["webp_route"], mp.ROUTE_FFMPEG if "libwebp_anim" in encoders else mp.ROUTE_PILLOW)
        self.assertIn("未使用 ffprobe", webp["verified_by"])
        self.assertTrue((out / "preview.webp").is_file())
        self.assertFalse((out / mp.WEBP_FRAME_DIR).exists())
        with Image.open(out / "preview.webp") as im:
            self.assertEqual(im.size, (320, 180))
            self.assertGreaterEqual(im.n_frames, 2)
            self.assertTrue(im.is_animated)
            self.assertEqual(im.info["loop"], 0)
            total, shots = 0, {}
            for i in range(im.n_frames):
                im.seek(i)
                im.load()  # WebP duration 在实际解码该帧后才更新
                total += im.info["duration"]
                if i in (0, im.n_frames // 2, im.n_frames - 1):
                    shots[i] = im.convert("RGB").tobytes()
            self.assertLessEqual(abs(total - 2000), 40)
            self.assertNotEqual(shots[0], shots[im.n_frames // 2], "WebP 中段必须与首帧不同：确有位移动画")
        self.assertTrue(webp.get("webp_verify", {}).get("timing_verifiable"))
        self.assertEqual(mp.main(["render", str(m), "--out", str(out), *self.tool_args]), 2)      # 不覆盖
        qa_args = ["qa", str(m), "--out", str(out), "--node", self.node, "--ffmpeg", self.ffmpeg, "--ffprobe", self.ffprobe]
        self.assertEqual(mp.main(qa_args), 0)


if __name__ == "__main__":
    unittest.main()
