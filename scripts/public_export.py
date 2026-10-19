"""Export the tracked public Skill files without private notes or Git history.

The source repository may contain maintainer-only files and commits. Always
publish a fresh repository made from this export, never push its source history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {
    ".claude-plugin/marketplace.json", ".claude-plugin/plugin.json",
    ".env.example", ".githooks/pre-commit", ".gitignore",
    ".github/ISSUE_TEMPLATE/bug.yml", ".github/ISSUE_TEMPLATE/recommend-style.yml",
    "ASSET_PROVENANCE.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "README.md",
    "README_EN.md", "SKILL.md", "THIRD_PARTY_NOTICES.md",
    "assets/C01-story-sample.png", "assets/C32-infographic-sample.png",
    "examples/book/plan.json", "examples/comic/plan.json",
    "examples/content-plan-v2/article.md", "examples/content-plan-v2/plan.json",
    "examples/quickstart/manifest.json", "gallery/template.html", "gallery/picker.html", "gallery/github-meta.json",
    "gallery/motion.js", "gallery/motion.css", "gallery/motion-data.json",  # 动态展示只放源码；poster/mp4 媒体不进 Git 也不进包
    "registry.json", "requirements-ppt.txt", "requirements-motion.txt", "requirements.txt",
    "scripts/sb.py", "scripts/first_run.py", "scripts/public_export.py", "scripts/samples_sync.py",
    "scripts/replay_wake.py",
}
PUBLIC_DIRS = ("formats/", "palettes/", "references/", "scenes/",
               "scripts/stylebook/", "structures/", "styles/", "tests/", "examples/motion/")
MOTION_FONT_FILES = {f"examples/motion/{case}/assets/serif.otf"
                     for case in ("city-light", "spring-album", "rainwater")}
MOTION_FONT_SHA256 = "435efadc1475f961c7a26eb2cec5d6a3e93cd2fe57e85aa14fcf35ce6558f60f"
REQUIRED = {"SKILL.md", "README.md", "LICENSE", "requirements.txt",
            "styles/anchor-provenance.json", "registry.json"}
FORBIDDEN = re.compile(
    r"(?:AIza[0-9A-Za-z_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}|"
    r"sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|"
    r"fc-[0-9a-f]{32}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"[Cc]:\\Users\\|/Us" + r"ers/[^/\s]+/|/ho" + r"me/[^/\s]+/)", re.I)


SAMPLE_RE = re.compile(r"styles/C\d{2,3}/samples/.+")


class ExportError(ValueError):
    pass


def select(entries: list[tuple[str, str]]) -> list[str]:
    """Only regular files from the explicit public surface may leave the repo."""
    selected = []
    for mode, name in entries:
        if SAMPLE_RE.fullmatch(name):
            continue  # 样图只在官网选择器里展示，不进下载包（出图只用合同与锚点，不读样图）
        if name in PUBLIC_FILES or name.startswith(PUBLIC_DIRS):
            if mode not in {"100644", "100755"}:
                raise ExportError(f"公开文件不是普通文件：{name}")
            selected.append(name)
    if not selected:
        raise ExportError("公开文件列表为空")
    missing = REQUIRED - set(selected)
    if missing:
        raise ExportError(f"公开包缺少必需文件：{sorted(missing)}")
    contracts = [p for p in selected if re.fullmatch(r"styles/C\d{2,3}/contract\.json", p)]
    expected = len(json.loads((ROOT / "styles/catalog.json").read_text(encoding="utf-8"))["styles"])
    if len(contracts) != expected:
        raise ExportError(f"公开画风合同数量应与目录一致（{expected}），实际 {len(contracts)}")
    if any(re.search(r"(?:^|/)S0[12](?:/|$)", p) for p in selected):
        raise ExportError("私有画风进入公开文件列表")
    return selected


def scan_text(name: str, raw: bytes) -> None:
    if name in MOTION_FONT_FILES:
        # Permit only the reviewed OFL subset, not arbitrary binary-looking uploads.
        if hashlib.sha256(raw).hexdigest() != MOTION_FONT_SHA256:
            raise ExportError(f"公开示例字体指纹不一致：{name}")
        return
    if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError(f"公开文本不是 UTF-8：{name}") from exc
    if name == ".githooks/pre-commit":
        # The hook declares the same detection expressions as data. Keep all
        # other lines in the scan, including executable commands and comments.
        value = "\n".join(line for line in value.splitlines() if not line.startswith("patterns="))
    if FORBIDDEN.search(value) or ("_work" + "space") in value:
        raise ExportError(f"公开文本含密钥形态或个人路径：{name}")


def tracked_entries() -> list[tuple[str, str]]:
    raw = subprocess.check_output(["git", "ls-tree", "-rz", "HEAD"], cwd=ROOT)
    result = []
    for record in raw.split(b"\0"):
        if record:
            header, path = record.split(b"\t", 1)
            mode, kind, _sha = header.decode("ascii").split(" ")
            if kind != "blob":
                raise ExportError(f"Git 条目类型不可公开：{path!r}")
            result.append((mode, path.decode("utf-8")))
    return result


def export(destination: Path) -> dict:
    if destination.exists():
        raise ExportError(f"输出位置已存在，不覆盖：{destination}")
    files = select(tracked_entries())
    payload: dict[str, bytes] = {}
    for name in files:
        raw = subprocess.check_output(["git", "show", f"HEAD:{name}"], cwd=ROOT)
        scan_text(name, raw)
        payload[name] = raw
    destination.mkdir(parents=True)
    for name, raw in payload.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        if name == ".githooks/pre-commit":
            target.chmod(0o755)
    receipt = {"schema_version": 1, "file_count": len(files),
               "files": {name: hashlib.sha256(payload[name]).hexdigest() for name in files}}
    (destination / "PUBLIC_EXPORT_MANIFEST.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def check_manifest(root: Path = ROOT) -> dict:
    """Check that the published file receipt matches the actual package."""
    manifest = root / "PUBLIC_EXPORT_MANIFEST.json"
    if not manifest.is_file():
        raise ExportError("公开包缺少 PUBLIC_EXPORT_MANIFEST.json")
    try:
        receipt = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ExportError("公开清单不是有效 JSON") from exc
    files = receipt.get("files")
    if receipt.get("schema_version") != 1 or not isinstance(files, dict) or \
            receipt.get("file_count") != len(files) or not files:
        raise ExportError("公开清单的版本或文件数不匹配")
    if any(Path(name).is_absolute() or ".." in Path(name).parts for name in files):
        raise ExportError("公开清单含无效路径")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and ".git" not in p.parts and
              "__pycache__" not in p.parts and ".pytest_cache" not in p.parts}
    actual.discard("PUBLIC_EXPORT_MANIFEST.json")
    # A local installation can contain generated output; the receipt only
    # promises that each exported file still has its published contents.
    missing = set(files) - actual
    changed = [name for name, digest in files.items()
               if name in actual and hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    if missing or changed:
        raise ExportError(f"公开清单与文件不符：缺失 {sorted(missing)}；变化 {sorted(changed)}")
    return receipt


def refresh_manifest(root: Path = ROOT) -> dict:
    """Refresh only hashes already listed by a vetted public export."""
    receipt = check_manifest_structure(root)
    receipt["files"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in receipt["files"]}
    (root / "PUBLIC_EXPORT_MANIFEST.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return check_manifest(root)


def check_manifest_structure(root: Path) -> dict:
    manifest = root / "PUBLIC_EXPORT_MANIFEST.json"
    if not manifest.is_file():
        raise ExportError("公开包缺少 PUBLIC_EXPORT_MANIFEST.json")
    receipt = json.loads(manifest.read_text(encoding="utf-8"))
    files = receipt.get("files")
    if receipt.get("schema_version") != 1 or not isinstance(files, dict) or \
            receipt.get("file_count") != len(files) or not files:
        raise ExportError("公开清单的版本或文件数不匹配")
    for name in files:
        if Path(name).is_absolute() or ".." in Path(name).parts or not (root / name).is_file():
            raise ExportError(f"公开清单含无效路径：{name}")
    return receipt


def check_staged(root: Path = ROOT) -> list[str]:
    """提交前钩子用：对暂存区里的文本文件跑与公开导出完全相同的 scan_text 规则（同一份 FORBIDDEN 与内部目录名）。"""
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
                           cwd=root, capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    problems = []
    for name in filter(None, names):
        if name.startswith(".githooks/"):
            continue
        raw = subprocess.run(["git", "show", f":{name}"], cwd=root, capture_output=True, check=True).stdout
        try:
            scan_text(name, raw)
        except ExportError as exc:
            problems.append(str(exc))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--check-manifest", action="store_true")
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument("--check-staged", action="store_true", help="提交前钩子：用公开导出的同一套规则检查暂存区")
    args = parser.parse_args()
    if sum(bool(x) for x in (args.output, args.check_manifest, args.refresh_manifest, args.check_staged)) != 1:
        parser.error("请选择 --output、--check-manifest、--refresh-manifest 或 --check-staged 其中一项")
    try:
        if args.check_staged:
            problems = check_staged()
            for item in problems:
                print(f"redact-guard: {item}", file=sys.stderr)
            return 1 if problems else 0
        if args.check_manifest:
            receipt = check_manifest()
            print(f"公开清单有效：{receipt['file_count']} 个文件")
            return 0
        if args.refresh_manifest:
            receipt = refresh_manifest()
            print(f"公开清单已更新：{receipt['file_count']} 个文件")
            return 0
        receipt = export(args.output.resolve())
    except (ExportError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"公开导出失败：{exc}\n")
    print(f"公开候选：{args.output.resolve()}（{receipt['file_count']} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
