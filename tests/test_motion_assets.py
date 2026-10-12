"""sb.py animate assets：把内置原创动作库复制到新目录。

走真实入口：`python3 scripts/sb.py animate assets ...` 子进程，以及 `stylebook.motion_assets.main` 本体。
只需要本地 Python，不联网、不启动渲染底座。夹具与输出都放系统临时目录，不在 Skill 扫描树内，也不含 SKILL.md。
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SB = SCRIPTS / "sb.py"
SOURCE = SCRIPTS / "stylebook" / "assets" / "motion-primitives.mjs"
CJK = re.compile(r"[一-鿿]")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stylebook import animate_adapter as AA  # noqa: E402
from stylebook import motion_assets as M  # noqa: E402

# 子进程里用真实模块、注入包内文件路径（命令行本身不暴露 source）
INJECT = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from stylebook import motion_assets as M\n"
    "src = Path(sys.argv[2]) if sys.argv[2] else None\n"
    "sys.exit(M.main(sys.argv[3:], source=src))\n"
)
NO_PIPELINE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from stylebook import animate_adapter as AA\n"
    "rc = AA.main(['assets', '--out', sys.argv[2]])\n"
    "assert 'stylebook.motion_pipeline' not in sys.modules, '加载了渲染底座'\n"
    "sys.exit(rc)\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(path: Path):
    """目录树快照：相对路径 → (类型, 内容/链接目标)，不跟随符号链接。"""
    out = {}
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            p = Path(base) / name
            rel = str(p.relative_to(path))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("dir", None)
            else:
                out[rel] = ("file", p.read_bytes())
    return out


def run_sb(*args: str):
    return subprocess.run([sys.executable, str(SB), "animate", "assets", *args], capture_output=True, text=True)


def run_injected(source, *args: str):
    return subprocess.run([sys.executable, "-I", "-c", INJECT, str(SCRIPTS), str(source or ""), *args],
                          capture_output=True, text=True)


def call_main(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = M.main(argv, **kw)
    return rc, out.getvalue(), err.getvalue()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="motion-assets-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.real = SOURCE.read_bytes()

    def fixture(self, data: bytes | None = None, name: str = "motion-primitives.mjs") -> Path:
        d = self.tmp / "fixture"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_bytes(self.real if data is None else data)
        return p

    def assertRejected(self, proc_or_tuple, *, before=None):
        if isinstance(proc_or_tuple, tuple):
            rc, stdout, stderr = proc_or_tuple
        else:
            rc, stdout, stderr = proc_or_tuple.returncode, proc_or_tuple.stdout, proc_or_tuple.stderr
        self.assertEqual(rc, 2, stderr)
        self.assertEqual(stdout.strip(), "", "拒绝时 stdout 不应有 receipt")
        self.assertIn("错误：", stderr)
        self.assertRegex(stderr, CJK)
        self.assertNotIn("Traceback", stderr)
        if before is not None:
            self.assertEqual(snapshot(self.tmp), before, "拒绝后临时目录发生了变化")


class PackagedSourceTests(unittest.TestCase):
    def test_source_is_formal_release(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("export const VERSION = '0.1.0';", text)
        self.assertNotIn("candidate", text)
        self.assertNotIn("候选", text)
        self.assertFalse(SOURCE.is_symlink())


class RealEntryCopyTests(TmpCase):
    def test_sb_animate_assets_copies_identical_bytes(self):
        out = self.tmp / "assets-out"
        proc = run_sb("--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.strip(), "")
        receipt = json.loads(proc.stdout)
        copied = (out / "motion-primitives.mjs").read_bytes()
        self.assertEqual(copied, SOURCE.read_bytes())
        self.assertEqual(sha(copied), sha(SOURCE.read_bytes()))
        self.assertEqual(receipt["schema"], M.SCHEMA)
        self.assertEqual(receipt["version"], "0.1.0")
        self.assertEqual(receipt["sha256"], sha(copied))
        self.assertEqual(receipt["files"], [{"path": "motion-primitives.mjs", "sha256": sha(copied), "bytes": len(copied)}])
        self.assertEqual(Path(receipt["out"]).resolve(), out)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["motion-assets-receipt.json", "motion-primitives.mjs"])
        on_disk = json.loads((out / "motion-assets-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, {k: receipt[k] for k in ("schema", "version", "sha256", "files")})
        self.assertFalse((out / "motion-primitives.mjs").is_symlink())

    def test_equals_form_and_help(self):
        out = self.tmp / "eq-form"
        proc = run_sb(f"--out={out}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((out / "motion-primitives.mjs").read_bytes(), SOURCE.read_bytes())
        before = snapshot(self.tmp)
        helped = run_sb("--help")
        self.assertEqual(helped.returncode, 0)
        self.assertIn("--out", helped.stdout)
        self.assertEqual(snapshot(self.tmp), before)

    def test_second_run_to_same_target_is_refused_and_untouched(self):
        out = self.tmp / "twice"
        self.assertEqual(run_sb("--out", str(out)).returncode, 0)
        before = snapshot(self.tmp)
        self.assertRejected(run_sb("--out", str(out)), before=before)

    def test_unknown_or_malformed_args_rejected_without_writes(self):
        target = str(self.tmp / "never")
        cases = [
            [], ["--force"], ["--out"], ["--out", target, "extra"], ["--out", target, "--force"],
            ["--ou", target], ["--output", target], [target], ["--out", ""], ["--out", "-x"], ["--out=", ],
            ["--out", target, "--out", target + "2"], ["--hyperframes-cli", "x"],
        ]
        before = snapshot(self.tmp)
        for args in cases:
            with self.subTest(args=args):
                self.assertRejected(run_sb(*args), before=before)
        self.assertFalse(Path(target).exists())

    def test_existing_targets_are_never_overwritten(self):
        d_empty = self.tmp / "empty-dir"
        d_empty.mkdir()
        f = self.tmp / "a-file"
        f.write_bytes(b"keep me")
        d_full = self.tmp / "full-dir"
        d_full.mkdir()
        (d_full / "motion-primitives.mjs").write_bytes(b"old")
        dangling = self.tmp / "dangling"
        os.symlink(self.tmp / "nowhere", dangling)
        to_dir = self.tmp / "to-dir"
        os.symlink(d_empty, to_dir)
        before = snapshot(self.tmp)
        for target in (d_empty, f, d_full, dangling, to_dir):
            with self.subTest(target=target.name):
                self.assertRejected(run_sb("--out", str(target)), before=before)
        self.assertFalse((self.tmp / "nowhere").exists())
        self.assertTrue(dangling.is_symlink())

    def test_parent_must_be_existing_directory(self):
        a_file = self.tmp / "plain-file"
        a_file.write_bytes(b"x")
        before = snapshot(self.tmp)
        for target in (self.tmp / "missing-parent" / "new", self.tmp / "missing" / "deeper" / "new", a_file / "new"):
            with self.subTest(target=str(target.relative_to(self.tmp))):
                self.assertRejected(run_sb("--out", str(target)), before=before)
        self.assertFalse((self.tmp / "missing-parent").exists())

    def test_output_inside_package_or_at_ancestor_rejected(self):
        names = [ROOT / "__motion_assets_must_not_exist__",
                 SCRIPTS / "stylebook" / "assets" / "__motion_assets_must_not_exist__",
                 SOURCE.parent.parent / "__motion_assets_must_not_exist__"]
        link = self.tmp / "link-to-package"
        os.symlink(ROOT, link)
        names.append(link / "__motion_assets_must_not_exist__")
        for target in names:
            self.assertFalse(target.exists())
        try:
            for target in names:
                with self.subTest(target=str(target)):
                    proc = run_sb("--out", str(target))
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertIn("错误：", proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertFalse((ROOT / "__motion_assets_must_not_exist__").exists())
                    self.assertFalse((SCRIPTS / "stylebook" / "assets" / "__motion_assets_must_not_exist__").exists())
                    self.assertFalse((SCRIPTS / "stylebook" / "__motion_assets_must_not_exist__").exists())
        finally:
            for target in names:
                if target.exists() and not target.is_symlink():
                    shutil.rmtree(target)
        # 祖先目录本身已存在，同样被拒
        for ancestor in (ROOT, ROOT.parent, SCRIPTS):
            with self.subTest(ancestor=str(ancestor)):
                proc = run_sb("--out", str(ancestor))
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_assets_does_not_load_render_base(self):
        out = self.tmp / "no-pipeline"
        proc = subprocess.run([sys.executable, "-I", "-c", NO_PIPELINE, str(SCRIPTS), str(out)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((out / "motion-primitives.mjs").read_bytes(), SOURCE.read_bytes())


class AdapterRoutingTests(TmpCase):
    def test_assets_branch_never_touches_pipeline_module(self):
        class Boom:
            def __getattr__(self, name):
                raise AssertionError(f"assets 不应访问底座：{name}")

        out = self.tmp / "routed"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = AA.main(["assets", "--out", str(out)], pipeline_module=Boom())
        self.assertEqual(rc, 0)
        self.assertTrue((out / "motion-primitives.mjs").is_file())

    def test_four_scene_subcommands_still_go_through_pipeline_validator(self):
        self.assertEqual(AA.SUBCOMMANDS, ("doctor", "plan", "render", "qa"))
        self.assertEqual(AA.SUPPORTED_SCENES, ("brand", "article", "infographic", "xiaohongshu"))
        calls = []

        class ManifestError(Exception):
            exit_code = 2

        class Pipe:
            pass

            @staticmethod
            def build_parser():
                return SimpleNamespace(parse_args=lambda argv: SimpleNamespace(cmd=argv[0]))

            @staticmethod
            def main(argv, *, manifest_validator=None):
                calls.append((list(argv), manifest_validator))
                return 0

        Pipe.ManifestError = ManifestError
        for sub in AA.SUBCOMMANDS:
            self.assertEqual(AA.main([sub, "m.json"], pipeline_module=Pipe), 0)
        self.assertEqual([c[0][0] for c in calls], list(AA.SUBCOMMANDS))
        self.assertTrue(all(callable(c[1]) for c in calls))
        validator = calls[0][1]
        validator(SimpleNamespace(scene="article", presets=["preview-gif"]))
        with self.assertRaises(ManifestError):
            validator(SimpleNamespace(scene="ppt", presets=[]))
        with self.assertRaises(ManifestError):
            validator(SimpleNamespace(scene="article", presets=["ppt-mp4-candidate"]))
        # 前缀不同的写法不会误入 assets
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = AA.main(["assets2", "--out", str(self.tmp / "x")], pipeline_module=Pipe)
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / "x").exists())


class SourceGuardTests(TmpCase):
    def test_injected_good_source_copies_exactly(self):
        src = self.fixture()
        out = self.tmp / "out-good"
        proc = run_injected(src, "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((out / "motion-primitives.mjs").read_bytes(), self.real)
        self.assertEqual(json.loads(proc.stdout)["sha256"], sha(self.real))

    def test_missing_symlink_directory_and_corrupt_sources_rejected(self):
        text = self.real.decode("utf-8")
        real_copy = self.fixture(name="real-copy.mjs")
        link = self.tmp / "fixture" / "link.mjs"
        os.symlink(real_copy, link)
        a_dir = self.tmp / "fixture" / "dir.mjs"
        a_dir.mkdir()
        corrupt = {
            "empty": b"",
            "broken-syntax-with-exports": self.real + b"\n}",
            "changed-body-with-exports": self.real.replace(b"const lo =", b"const lo = 0; const broken =", 1) if b"const lo =" in self.real else self.real + b"\n// unauthorized byte change",
            "truncated": self.real[: len(self.real) // 2],
            "nul": self.real + b"\0",
            "bad-utf8": self.real + b"\xff\xfe",
            "candidate-version": text.replace("'0.1.0'", "'0.1.0-candidate'", 1).encode(),
            "other-version": text.replace("'0.1.0'", "'9.9.9'", 1).encode(),
            "no-version-line": text.replace("export const VERSION", "const VERSION", 1).encode(),
            "missing-function": text.replace("export function createTimeline(", "function createTimeline(", 1).encode(),
            "missing-error-class": text.replace("export class MotionError", "class MotionError", 1).encode(),
            "oversize": self.real + b"\n" * (M.MAX_BYTES + 1),
        }
        sources = {"missing": self.tmp / "fixture" / "absent.mjs", "symlink": link, "directory": a_dir}
        for name, data in corrupt.items():
            sources[name] = self.fixture(data, name=f"{name}.mjs")
        before = snapshot(self.tmp)
        for name, src in sources.items():
            with self.subTest(source=name):
                out = self.tmp / f"out-{name}"
                proc = run_injected(src, "--out", str(out))
                self.assertRejected(proc, before=before)
                self.assertFalse(out.exists())

    def test_output_inside_source_directory_rejected(self):
        src = self.fixture()
        before = snapshot(self.tmp)
        self.assertRejected(run_injected(src, "--out", str(src.parent / "new")), before=before)
        self.assertRejected(run_injected(src, "--out", str(src.parent)), before=before)


class WriteFaultInjectionTests(TmpCase):
    """在真实 main 里注入写故障，证明失败时不留半成品、不覆盖、不误删别人的文件。"""

    def setUp(self):
        super().setUp()
        self.src = self.fixture()
        self.out = self.tmp / "fault-out"
        self.real_write = M._write_new_file

    def run_main(self):
        return call_main(["--out", str(self.out)], source=self.src)

    def test_second_file_failure_removes_first_and_directory(self):
        calls = []

        def flaky(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")
            return self.real_write(path, data)

        with mock.patch.object(M, "_write_new_file", flaky):
            result = self.run_main()
        self.assertEqual(len(calls), 2)
        self.assertRejected(result)
        self.assertFalse(self.out.exists(), "半成品目录应被清理")

    def test_first_file_failure_leaves_nothing(self):
        def boom(path, data):
            raise OSError(errno.EIO, "I/O error")

        with mock.patch.object(M, "_write_new_file", boom):
            result = self.run_main()
        self.assertRejected(result)
        self.assertFalse(self.out.exists())

    def test_low_level_fsync_failure_cleans_partial_file(self):
        with mock.patch.object(M.os, "fsync", side_effect=OSError(errno.EIO, "fsync failed")):
            result = self.run_main()
        self.assertRejected(result)
        self.assertFalse(self.out.exists())

    def test_readback_mismatch_cleans_everything(self):
        with mock.patch.object(M, "_verify_written", side_effect=M.AssetsError("写出后回读校验失败：输出文件与源不一致")):
            result = self.run_main()
        self.assertRejected(result)
        self.assertFalse(self.out.exists())

    def test_foreign_file_is_not_deleted_on_cleanup(self):
        calls = []
        foreign = self.out / "someone-elses.txt"

        def flaky(path, data):
            calls.append(path)
            if len(calls) == 2:
                foreign.write_bytes(b"not ours")
                raise OSError(errno.ENOSPC, "No space left on device")
            return self.real_write(path, data)

        with mock.patch.object(M, "_write_new_file", flaky):
            result = self.run_main()
        self.assertRejected(result)
        self.assertEqual(snapshot(self.out), {"someone-elses.txt": ("file", b"not ours")})

    def test_foreign_replacement_of_our_file_is_not_deleted(self):
        """清理前自己的文件被别人换成了另一个文件（inode 不同）：不能误删。"""
        calls = []

        def flaky(path, data):
            calls.append(path)
            if len(calls) == 2:
                first = Path(calls[0])
                os.link(first, first.with_name("kept-original"))  # 占住旧 inode，避免新文件复用同一个 inode 号
                first.unlink()
                first.write_bytes(b"replaced by someone else")
                raise OSError(errno.ENOSPC, "No space left on device")
            return self.real_write(path, data)

        with mock.patch.object(M, "_write_new_file", flaky):
            result = self.run_main()
        self.assertRejected(result)
        self.assertEqual(snapshot(self.out), {"motion-primitives.mjs": ("file", b"replaced by someone else"),
                                              "kept-original": ("file", self.real)})

    def test_directory_created_by_race_is_not_overwritten(self):
        real_read = M._read_source

        def racing(source):
            self.out.mkdir()
            (self.out / "sentinel.txt").write_bytes(b"mine")
            (self.out / "motion-primitives.mjs").write_bytes(b"pre-existing")
            return real_read(source)

        with mock.patch.object(M, "_read_source", racing):
            result = self.run_main()
        self.assertRejected(result)
        self.assertEqual(snapshot(self.out), {"sentinel.txt": ("file", b"mine"), "motion-primitives.mjs": ("file", b"pre-existing")})

    def test_dangling_symlink_created_by_race_is_not_followed(self):
        real_read = M._read_source
        target = self.tmp / "victim"

        def racing(source):
            os.symlink(target, self.out)
            return real_read(source)

        with mock.patch.object(M, "_read_source", racing):
            result = self.run_main()
        self.assertRejected(result)
        self.assertTrue(self.out.is_symlink())
        self.assertFalse(target.exists())

    def test_success_still_works_after_patches_are_removed(self):
        rc, stdout, stderr = self.run_main()
        self.assertEqual(rc, 0, stderr)
        self.assertEqual((self.out / "motion-primitives.mjs").read_bytes(), self.real)
        self.assertEqual(json.loads(stdout)["sha256"], sha(self.real))


if __name__ == "__main__":
    unittest.main()
