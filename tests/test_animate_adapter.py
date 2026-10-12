"""stylebook.animate_adapter 行为测试（四场景白名单、manifest 只读一次、拒绝 PPT / 音乐封面）。

运行（在 Skill 根目录）：
    python3 -I -m unittest discover -s tests -p "test_animate_adapter.py" -v
默认测试当前仓；要测另一份副本，设 SANSHENG_IMAGE_ROOT=<sansheng-image 根目录>。
（-I 下 cwd 不在 sys.path，所以用 discover；本文件自己把 <根>/scripts 放到 sys.path 最前。）

被测的 animate_adapter 与 motion_pipeline 都取自 <根>/scripts/stylebook，缺少正式模块直接报错，不会跳过；
找到的 motion_pipeline 没有 manifest_validator 时，test_pipeline_under_test_has_validator_hook 失败。
所有用例都走真实 pipeline.main()；只有 run_render / run_qa（会调用 HyperFrames 与 ffmpeg）和 doctor 的 run_doctor 被
临时 mock 成记录器。load_manifest 以“包一层、仍调用真实实现”的方式观测，不替换其行为。
node / ffmpeg / ffprobe 用 sys.executable 占位，只为通过 resolve_tools 的存在性检查。
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("SANSHENG_IMAGE_ROOT") or Path(__file__).resolve().parents[1]).resolve()
SCRIPTS = ROOT / "scripts"
HERE = SCRIPTS / "stylebook"  # 被测模块所在目录

if sys.path[:1] != [str(SCRIPTS)]:
    sys.path.insert(0, str(SCRIPTS))

from stylebook import animate_adapter as AA  # noqa: E402  缺少正式模块时直接 ImportError，不跳过
from stylebook import motion_pipeline as PM  # noqa: E402

PM_SOURCE = "formal"
HAS_HOOK = "manifest_validator" in inspect.signature(PM.main).parameters


def setUpModule():
    for mod, name in ((AA, "animate_adapter.py"), (PM, "motion_pipeline.py")):
        if Path(mod.__file__).resolve() != HERE / name:
            raise RuntimeError(f"导入的 {name} 不是 {HERE / name}，而是 {mod.__file__}；请检查 sys.path / SANSHENG_IMAGE_ROOT")


class Recording:
    """真实模块的代理：除 main 外全部取自真实模块；main 只记录实参，用于“默认模块”和“底座缺失”类用例。"""

    def __init__(self, real, ret: int = 0):
        self._real = real
        self.ret = ret
        self.main_calls: list = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def main(self, argv, **kwargs):
        self.main_calls.append((list(argv), kwargs))
        return self.ret


class UnpatchedBase:
    """模拟没有 manifest_validator 的旧底座：main 的签名里没有该参数。"""

    def __init__(self, real):
        self._real = real
        self.main_calls: list = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def main(self, argv=None, *, runner=None, environ=None):
        self.main_calls.append(list(argv))
        return 0


class Outcome:
    def __init__(self, rc, out, err, rec):
        self.rc, self.out, self.err, self.rec = rc, out, err, rec


def call_adapter(argv, pipeline_module):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = AA.main(argv, pipeline_module=pipeline_module)
    return rc, out.getvalue(), err.getvalue()


def sha_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_snapshot(root: Path) -> dict:
    snap = {}
    for base, dirs, files in os.walk(root):
        for d in dirs:
            snap[os.path.relpath(os.path.join(base, d), root)] = "dir"
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, root)
            snap[rel] = ("link", os.readlink(p)) if os.path.islink(p) else sha_of(Path(p))
    return snap


# 白名单之外的各种写法；真实拒绝测试与“变异后必须漏过”测试共用
UNSUPPORTED_CASES = {
    "ppt_scene": {"scene": "ppt"},
    "music_scene_with_music_preset": {"scene": "music", "presets": ["music-loop-mp4-candidate"]},
    "music_scene_plain_preset": {"scene": "music"},
    "ppt_preset_in_article": {"presets": ["ppt-mp4-candidate"]},
    "music_preset_in_brand": {"scene": "brand", "presets": ["music-loop-mp4-candidate"]},
    "ppt_preset_mixed_with_legal": {"presets": ["preview-gif", "ppt-mp4-candidate"]},
}
MUTATION_CMDS = ("plan", "render", "qa")


def is_rejected(o: Outcome) -> bool:
    """同一个判据：退出码 2、没有进入 render / qa 执行、没有任何 plan 正常输出。"""
    return o.rc == 2 and not o.rec.render and not o.rec.qa and o.out.strip() == ""


class AnimateAdapterTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(os.path.realpath(self._td.name))
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.cli = self.tmp / "hyperframes.mjs"
        self.cli.write_text("// 占位：plan 与 mock 的 render 不执行它\n", encoding="utf-8")
        self.tools = ["--node", sys.executable, "--ffmpeg", sys.executable, "--ffprobe", sys.executable]

    # ── 夹具 ──
    def write_project(self, w=1280, h=720, duration=5):
        (self.proj / "index.html").write_text(
            f'<!doctype html><html><body><div data-composition-id="main" data-width="{w}" '
            f'data-height="{h}" data-duration="{duration}"></div></body></html>', encoding="utf-8")

    def manifest_data(self, **override) -> dict:
        scene = override.get("scene", "article")
        data = {"version": 1, "project": "proj", "scene": scene, "style_code": None if scene == "brand" else "C24",
                "width": 1280, "height": 720, "duration": 5, "fps": 30, "loop": True, "presets": ["preview-gif"]}
        data.update(override)
        for key in [k for k, v in override.items() if v is ...]:  # 用 ... 表示删掉该字段
            data.pop(key)
        return data

    def manifest(self, name="m.json", **override) -> Path:
        self.write_project()
        path = self.tmp / name
        path.write_text(json.dumps(self.manifest_data(**override), ensure_ascii=False), encoding="utf-8")
        return path

    def raw_manifest(self, text: str, name="raw.json") -> Path:
        self.write_project()
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def argv(self, cmd, manifest, out_name="out", extra=()):
        base = [cmd, str(manifest), "--out", str(self.tmp / out_name)]
        cli = [] if cmd == "qa" else ["--hyperframes-cli", str(self.cli)]   # 真实 parser：qa 不接受 --hyperframes-cli
        return base + cli + self.tools + list(extra)

    def doctor_argv(self):
        return ["doctor", "--hyperframes-cli", str(self.cli), *self.tools]

    # ── 观测：包住真实 load_manifest / main，mock 掉会调用外部程序的 run_render / run_qa ──
    @contextlib.contextmanager
    def instrumented(self, after_load=None, drop_validator=False):
        rec = types.SimpleNamespace(events=[], loaded=[], validated=[], render=[], qa=[], main_calls=[])
        real_load, real_main = PM.load_manifest, PM.main

        def load(path):
            m = real_load(path)
            rec.events.append("load")
            rec.loaded.append(m)
            if after_load is not None:
                after_load(m)
            return m

        def run_render(t, m, out_arg, opts):
            rec.events.append("render")
            rec.render.append(m)
            return 0, {"status": "ok", "output_dir": str(out_arg), "artifacts": []}

        def run_qa(t, m, out, **kwargs):
            rec.events.append("qa")
            rec.qa.append(m)
            return 0, {"status": "auto-pass-needs-manual-review", "problems": [], "note": "mock"}

        def main(argv, **kwargs):
            rec.main_calls.append(list(argv))
            validator = kwargs.get("manifest_validator")
            if drop_validator:                      # 变异：等价于底座从不调用 manifest_validator
                kwargs["manifest_validator"] = None
            elif validator is not None:
                def spy(m):
                    rec.events.append("validate")
                    rec.validated.append(m)
                    return validator(m)
                kwargs["manifest_validator"] = spy
            return real_main(argv, **kwargs)

        with mock.patch.object(PM, "load_manifest", load), mock.patch.object(PM, "run_render", run_render), \
                mock.patch.object(PM, "run_qa", run_qa), mock.patch.object(PM, "main", main):
            yield rec

    def invoke(self, argv, **inst) -> Outcome:
        with self.instrumented(**inst) as rec:
            rc, out, err = call_adapter(argv, PM)
        return Outcome(rc, out, err, rec)

    # ── 底座钩子本身 ──
    def test_pipeline_under_test_has_validator_hook(self):
        self.assertTrue(HAS_HOOK, f"加载到的 motion_pipeline（来源 {PM_SOURCE}，{getattr(PM, '__file__', '?')}）没有 manifest_validator；"
                                  "正式 stylebook.motion_pipeline 必须带 keyword-only 的 manifest_validator")
        param = inspect.signature(PM.main).parameters["manifest_validator"]
        self.assertIs(param.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(param.default)

    def test_real_main_calls_validator_right_after_load_and_before_any_work(self):
        events: list = []
        real_load, real_resolve, real_inspect = PM.load_manifest, PM.resolve_out, PM.inspect_project

        def load(path):
            events.append("load")
            return real_load(path)

        def resolve(*a, **k):
            events.append("resolve_out")
            return real_resolve(*a, **k)

        def insp(*a, **k):
            events.append("inspect_project")
            return real_inspect(*a, **k)

        def boom(*a, **k):
            events.append("executor")
            raise AssertionError("校验失败后不应进入 render / qa")

        seen: list = []

        def validator(m):
            events.append("validate")
            seen.append(m)
            raise PM.ManifestError("测试：一律拒绝")

        m = self.manifest()
        with mock.patch.object(PM, "load_manifest", load), mock.patch.object(PM, "resolve_out", resolve), \
                mock.patch.object(PM, "inspect_project", insp), mock.patch.object(PM, "run_render", boom), \
                mock.patch.object(PM, "run_qa", boom):
            for cmd in MUTATION_CMDS:
                with self.subTest(cmd=cmd):
                    events.clear()
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                        rc = PM.main(self.argv(cmd, m), manifest_validator=validator)
                    self.assertEqual(rc, 2)
                    self.assertIn("一律拒绝", err.getvalue())
                    self.assertEqual(events, ["load", "validate"])
        self.assertTrue(all(isinstance(x, PM.Manifest) for x in seen))

    def test_validator_return_value_is_ignored_and_executor_gets_loaded_manifest(self):
        m = self.manifest()
        with self.instrumented() as rec:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = PM.main(self.argv("render", m), manifest_validator=lambda mf: "不是新的 Manifest")
        self.assertEqual(rc, 0)
        self.assertEqual(len(rec.loaded), 1)
        self.assertIs(rec.render[0], rec.loaded[0])

    def test_default_none_keeps_base_behaviour_unchanged(self):
        # 底座本身仍认 ppt / music（白名单只在适配器这一层）；不传 validator 时行为与打补丁前一致
        for scene in ("ppt", "music"):
            with self.subTest(scene=scene):
                m = self.manifest(scene=scene)
                with self.instrumented() as rec:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        rc = PM.main(self.argv("render", m))
                self.assertEqual(rc, 0)
                self.assertEqual(rec.render[0].scene, scene)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = PM.main(self.argv("plan", self.manifest()))
        self.assertEqual(rc, 0)

    def test_doctor_never_calls_validator(self):
        calls: list = []
        checks = [{"name": "x", "required": True, "ok": True, "detail": "d"}]
        with mock.patch.object(PM, "run_doctor", return_value=checks):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = PM.main(self.doctor_argv(), manifest_validator=lambda m: calls.append(m))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    # ── 合法四场景：同一个 Manifest 贯穿校验与执行 ──
    def test_four_scenes_validate_and_execute_the_same_single_read_manifest(self):
        for scene in AA.SUPPORTED_SCENES:
            for cmd in ("plan", "render", "qa"):
                with self.subTest(scene=scene, cmd=cmd):
                    argv = self.argv(cmd, self.manifest(scene=scene))
                    o = self.invoke(argv)
                    self.assertEqual(o.rc, 0, o.err)
                    self.assertEqual(o.rec.main_calls, [argv])
                    self.assertEqual(len(o.rec.loaded), 1, "manifest 只允许被读一次")
                    loaded = o.rec.loaded[0]
                    self.assertEqual(loaded.scene, scene)
                    self.assertEqual(len(o.rec.validated), 1)
                    self.assertIs(o.rec.validated[0], loaded)
                    if cmd == "render":
                        self.assertEqual(len(o.rec.render), 1)
                        self.assertIs(o.rec.render[0], loaded)
                        self.assertEqual(o.rec.qa, [])
                    elif cmd == "qa":
                        self.assertEqual(len(o.rec.qa), 1)
                        self.assertIs(o.rec.qa[0], loaded)
                        self.assertEqual(o.rec.render, [])
                    else:
                        self.assertEqual(o.rec.render + o.rec.qa, [])

    def test_forward_returns_pipeline_exit_code(self):
        def run_render(t, m, out_arg, opts):
            return 3, {"status": "partial", "output_dir": str(out_arg), "artifacts": []}
        m = self.manifest()
        with mock.patch.object(PM, "run_render", run_render):
            rc, _, err = call_adapter(self.argv("render", m), PM)
        self.assertEqual(rc, 3, err)

    def test_allowlist_is_exactly_four_and_subset_of_pipeline(self):
        self.assertEqual(set(AA.SUPPORTED_SCENES), {"brand", "article", "infographic", "xiaohongshu"})
        self.assertTrue(set(AA.SUPPORTED_SCENES) <= set(PM.SCENES))
        self.assertTrue(set(AA.UNSUPPORTED_PRESETS) <= set(PM.PRESETS))

    # ── doctor 不需要 manifest ──
    def test_doctor_runs_without_reading_any_manifest(self):
        checks = [{"name": "x", "required": True, "ok": True, "detail": "d"}]
        argv = self.doctor_argv()
        with mock.patch.object(PM, "run_doctor", return_value=checks):
            o = self.invoke(argv)
        self.assertEqual(o.rc, 0, o.err)
        self.assertEqual(o.rec.main_calls, [argv])
        self.assertEqual(o.rec.loaded, [])
        self.assertEqual(o.rec.validated, [])

    def test_doctor_rejects_stray_positional(self):
        o = self.invoke(["doctor", "m.json", "--hyperframes-cli", str(self.cli)])
        self.assertEqual(o.rc, 2)
        self.assertEqual(o.rec.main_calls, [])

    # ── help ──
    def test_help_exits_zero_without_running_pipeline(self):
        for argv in (["-h"], ["--help"], ["doctor", "-h"], ["plan", "-h"], ["render", "--help"], ["qa", "-h"]):
            with self.subTest(argv=argv):
                o = self.invoke(argv)
                self.assertEqual(o.rc, 0, o.err)
                self.assertIn("usage", o.out.lower())
                self.assertEqual(o.rec.main_calls, [])

    # ── 不支持的 scene / preset（真实 main 路径） ──
    def test_unsupported_scene_and_presets_rejected_on_real_path(self):
        for label, override in UNSUPPORTED_CASES.items():
            for cmd in MUTATION_CMDS:
                with self.subTest(case=label, cmd=cmd):
                    o = self.invoke(self.argv(cmd, self.manifest(**override)))
                    self.assertTrue(is_rejected(o), (o.rc, o.out, o.err))
                    self.assertEqual(len(o.rec.loaded), 1)
                    self.assertEqual(len(o.rec.validated), 1)
                    self.assertTrue(o.err.strip())
                    if "scene" in override:
                        self.assertIn(override["scene"], o.err)
                    else:
                        self.assertIn(override["presets"][-1], o.err)

    def test_blank_missing_or_unknown_scene_rejected_by_real_manifest_check(self):
        cases = {"blank": {"scene": ""}, "missing": {"scene": ...}, "unknown": {"scene": "video"}, "not_string": {"scene": 3}}
        for label, override in cases.items():
            for cmd in MUTATION_CMDS:
                with self.subTest(case=label, cmd=cmd):
                    o = self.invoke(self.argv(cmd, self.manifest(**override)))
                    self.assertEqual(o.rc, 2)
                    self.assertTrue(o.err.strip())
                    self.assertEqual(o.rec.validated, [])
                    self.assertEqual(o.rec.render + o.rec.qa, [])

    def test_schema_violations_rejected_by_real_load_manifest(self):
        good = self.manifest_data()
        dup = json.dumps(good)[:-1] + ', "scene": "ppt"}'
        raws = {
            "unknown_field": json.dumps({**good, "extra": 1}),
            "missing_field": json.dumps({k: v for k, v in good.items() if k != "fps"}),
            "bad_fps": json.dumps({**good, "fps": 31}),
            "bad_version": json.dumps({**good, "version": 2}),
            "unknown_preset": json.dumps({**good, "presets": ["no-such-preset"]}),
            "duplicate_key": dup,
            "not_json": "{",
            "top_level_list": "[]",
        }
        for label, text in raws.items():
            for cmd in MUTATION_CMDS:
                with self.subTest(case=label, cmd=cmd):
                    o = self.invoke(self.argv(cmd, self.raw_manifest(text)))
                    self.assertEqual(o.rc, 2)
                    self.assertTrue(o.err.strip())
                    self.assertEqual(o.rec.validated, [])
                    self.assertEqual(o.rec.render + o.rec.qa, [])

    def test_blank_missing_directory_and_symlink_manifest_rejected(self):
        real = self.manifest()
        link = self.tmp / "link.json"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            link = None
        paths = {"blank": "", "missing": str(self.tmp / "nope.json"), "directory": str(self.proj)}
        if link is not None:
            paths["symlink"] = str(link)
        for label, p in paths.items():
            with self.subTest(case=label):
                o = self.invoke(["plan", p, "--out", str(self.tmp / "o"), "--hyperframes-cli", str(self.cli), *self.tools])
                self.assertEqual(o.rc, 2)
                self.assertTrue(o.err.strip())
                self.assertEqual(o.rec.validated, [])

    # ── 未知参数：由真实 parser 拒绝 ──
    def test_unknown_args_rejected_by_real_parser(self):
        m = self.manifest()
        for extra in (["--evil", "x"], ["--scene", "ppt"], ["--hyperframes-cli-extra", "x"], ["--presets", "ppt-mp4-candidate"]):
            for cmd in MUTATION_CMDS:
                with self.subTest(extra=extra, cmd=cmd):
                    o = self.invoke(self.argv(cmd, m, extra=extra))
                    self.assertEqual(o.rc, 2)
                    self.assertTrue(o.err.strip())
                    self.assertEqual(o.rec.main_calls, [])

    def test_qa_does_not_accept_hyperframes_cli_in_real_parser(self):
        # 早期版本的 4 个失败就是测试把 --hyperframes-cli 塞给 qa；这里钉死“真实 parser 就是不接受”，适配器照实退出 2
        m = self.manifest()
        argv = ["qa", str(m), "--out", str(self.tmp / "out"), "--hyperframes-cli", str(self.cli), *self.tools]
        o = self.invoke(argv)
        self.assertEqual(o.rc, 2)
        self.assertIn("unrecognized arguments", o.err)
        self.assertEqual(o.rec.main_calls, [])

    def test_missing_subcommand_and_bad_subcommand_rejected(self):
        for argv in ([], ["bogus"], ["ppt"], [""]):
            with self.subTest(argv=argv):
                o = self.invoke(argv)
                self.assertEqual(o.rc, 2)
                self.assertEqual(o.rec.main_calls, [])

    # ── argv 类型 ──
    def test_bad_argv_types_exit_two_without_traceback(self):
        cases = {
            "str": "plan m.json", "bytes": b"plan m.json", "none": None, "int": 3, "dict": {"plan": 1},
            "generator": (x for x in ["doctor"]), "bytes_element": [b"plan"], "none_element": ["plan", None],
            "int_element": ["plan", "m.json", 1], "nested_list": [["plan"]],
        }
        for label, argv in cases.items():
            with self.subTest(case=label):
                rec = Recording(PM)
                rc, out, err = call_adapter(argv, rec)
                self.assertEqual(rc, 2)
                self.assertTrue(err.strip())
                self.assertNotIn("Traceback", err)
                self.assertEqual(rec.main_calls, [])

    def test_tuple_of_strings_is_accepted(self):
        m = self.manifest()
        o = self.invoke(tuple(self.argv("plan", m)))
        self.assertEqual(o.rc, 0, o.err)

    # ── 底座缺钩子：失败即关闭 ──
    def test_base_without_validator_hook_is_refused(self):
        base = UnpatchedBase(PM)
        rc, out, err = call_adapter(self.argv("plan", self.manifest()), base)
        self.assertEqual(rc, 2)
        self.assertIn("manifest_validator", err)
        self.assertEqual(base.main_calls, [])

    # ── 先读后换（TOCTOU）：实际被测路径 ──
    def _swap_after_first_load(self, cmd, first_scene, second_scene):
        path = self.manifest(scene=first_scene)
        original_sha = sha_of(path)
        swapped_text = json.dumps(self.manifest_data(scene=second_scene), ensure_ascii=False)

        def swap(_m):
            path.write_text(swapped_text, encoding="utf-8")

        o = self.invoke(self.argv(cmd, path, extra=["--json"]), after_load=swap)
        self.assertNotEqual(sha_of(path), original_sha, "夹具自检：磁盘上的 manifest 应该已经被替换")
        return o, original_sha

    def test_swap_to_ppt_after_first_read_is_ignored_and_never_executed(self):
        for cmd in MUTATION_CMDS:
            with self.subTest(cmd=cmd):
                o, original_sha = self._swap_after_first_load(cmd, "article", "ppt")
                self.assertEqual(o.rc, 0, o.err)
                self.assertEqual(len(o.rec.loaded), 1, "真实 load_manifest 只允许调用一次")
                loaded = o.rec.loaded[0]
                self.assertEqual(loaded.scene, "article")
                self.assertEqual(loaded.sha256, original_sha)
                self.assertIs(o.rec.validated[0], loaded)
                if cmd == "plan":
                    report = json.loads(o.out)
                    self.assertEqual(report["manifest"]["scene"], "article")
                    self.assertEqual(report["manifest_sha256"], original_sha)
                    self.assertEqual(o.rec.render + o.rec.qa, [])
                else:
                    executed = o.rec.render + o.rec.qa
                    self.assertEqual(len(executed), 1)
                    self.assertIs(executed[0], loaded)
                    self.assertEqual(executed[0].scene, "article")
                    self.assertEqual(executed[0].sha256, original_sha)
                    self.assertNotIn("ppt", [x.scene for x in executed])

    def test_initial_ppt_is_rejected_even_if_disk_becomes_article_afterwards(self):
        for cmd in MUTATION_CMDS:
            with self.subTest(cmd=cmd):
                o, original_sha = self._swap_after_first_load(cmd, "ppt", "article")
                self.assertTrue(is_rejected(o), (o.rc, o.out, o.err))
                self.assertIn("ppt", o.err)
                self.assertEqual(len(o.rec.loaded), 1)
                self.assertEqual(o.rec.loaded[0].scene, "ppt")
                self.assertEqual(o.rec.loaded[0].sha256, original_sha)

    # ── 变异：取消 manifest_validator 调用，行为测试必须能抓到 ──
    def test_mutation_without_validator_lets_ppt_music_and_mixed_presets_through(self):
        # 与 test_unsupported_scene_and_presets_rejected_on_real_path 使用同一批用例和同一个 is_rejected 判据；
        # 变异只发生在本测试内的临时 mock（把传给底座的 validator 置 None），不改任何正式文件。
        for label, override in UNSUPPORTED_CASES.items():
            for cmd in MUTATION_CMDS:
                with self.subTest(case=label, cmd=cmd):
                    o = self.invoke(self.argv(cmd, self.manifest(**override)), drop_validator=True)
                    self.assertFalse(is_rejected(o), "变异后拒绝测试应当失败，说明测试确实依赖 validator 调用")
                    self.assertEqual(o.rc, 0, o.err)
                    self.assertEqual(o.rec.validated, [])
                    if cmd in ("render", "qa"):
                        executed = o.rec.render + o.rec.qa
                        self.assertEqual(len(executed), 1)
                        self.assertEqual(executed[0].scene, override.get("scene", "article"))
                        if "presets" in override:
                            self.assertEqual(list(executed[0].presets), override["presets"])

    # ── 输出目录防覆盖：由真实 pipeline 拒绝 ──
    def test_output_overwrite_rejected_by_real_pipeline(self):
        m = self.manifest()
        existing_dir = self.tmp / "exists"
        existing_dir.mkdir()
        (existing_dir / "keep.txt").write_text("旧成品", encoding="utf-8")
        existing_file = self.tmp / "exists.file"
        existing_file.write_text("旧文件", encoding="utf-8")
        dangling = self.tmp / "dangling"
        try:
            os.symlink(self.tmp / "no-target", dangling)
        except (OSError, NotImplementedError):
            dangling = None
        cases = {"dir": existing_dir, "file": existing_file, "inside_project": self.proj / "out"}
        if dangling is not None:
            cases["dangling_symlink"] = dangling
        before = tree_snapshot(self.tmp)
        for label, out in cases.items():
            with self.subTest(case=label):
                argv = ["plan", str(m), "--out", str(out), "--hyperframes-cli", str(self.cli), *self.tools]
                rc, _, err = call_adapter(argv, PM)
                self.assertEqual(rc, 2, err)
                self.assertTrue(err.strip())
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual((existing_dir / "keep.txt").read_text(encoding="utf-8"), "旧成品")

    # ── plan 无副作用（真实 plan，不 mock） ──
    def test_real_plan_has_no_side_effects(self):
        m = self.manifest()
        out = self.tmp / "fresh-out"
        before = tree_snapshot(self.tmp)
        argv = ["plan", str(m), "--out", str(out), "--hyperframes-cli", str(self.cli), *self.tools, "--json"]
        rc, stdout, err = call_adapter(argv, PM)
        self.assertEqual(rc, 0, err)
        self.assertFalse(out.exists())
        self.assertEqual(tree_snapshot(self.tmp), before)
        report = json.loads(stdout)
        self.assertEqual(report["manifest_sha256"], sha_of(m))
        self.assertEqual(report["manifest"]["scene"], "article")
        self.assertEqual(Path(report["out"]), out)

    # ── 默认模块与源码卫生 ──
    def test_default_module_is_stylebook_motion_pipeline(self):
        stub = Recording(PM, ret=0)
        pkg = types.ModuleType("stylebook")
        pkg.motion_pipeline = stub
        with mock.patch.dict(sys.modules, {"stylebook": pkg, "stylebook.motion_pipeline": stub}):
            rc, _, err = call_adapter(self.doctor_argv(), None)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(stub.main_calls), 1)
        argv, kwargs = stub.main_calls[0]
        self.assertEqual(argv, self.doctor_argv())
        self.assertTrue(callable(kwargs.get("manifest_validator")))

    def test_missing_default_module_reports_clearly(self):
        with mock.patch.dict(sys.modules, {"stylebook": None, "stylebook.motion_pipeline": None}):
            rc, _, err = call_adapter(["doctor", "--hyperframes-cli", str(self.cli)], None)
        self.assertEqual(rc, 2)
        self.assertIn("motion_pipeline", err)

    def test_adapter_source_has_no_host_path_or_path_import_backdoor(self):
        text = (HERE / "animate_adapter.py").read_text(encoding="utf-8")
        for needle in ("/Users/", "Cowork", "importlib", "subprocess", "os.system", "shell=True", "exec(", "eval("):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
