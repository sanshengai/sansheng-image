"""The public snapshot gate must reject empty input and private-looking text."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_export import ExportError, check_manifest, refresh_manifest, scan_text, select  # noqa: E402


def test_empty_public_selection_is_rejected():
    with pytest.raises(ExportError, match="为空"):
        select([])


@pytest.mark.parametrize("leak", ["/Users/" + "alice/private/file.json", "_work" + "space/notes",
                                   "sk-" + "A" * 30])
def test_private_content_is_rejected(leak):
    with pytest.raises(ExportError):
        scan_text("README.md", leak.encode())


def test_plain_public_text_is_allowed():
    scan_text("README.md", "Choose a style and export a JSON selection.".encode())


def test_reviewed_palette_selection_is_part_of_public_surface():
    from public_export import PUBLIC_FILES
    assert 'gallery/palette-showcase.json' in PUBLIC_FILES
    source = Path(__file__).resolve().parents[1] / 'gallery/palette-showcase.json'
    scan_text('gallery/palette-showcase.json', source.read_bytes())


@pytest.mark.parametrize("case", ["rainwater", "paper-diorama", "message-journey",
                                  "rain-bookshop", "travel-route"])
def test_only_reviewed_example_font_bytes_are_allowed(case):
    name = f"examples/motion/{case}/assets/serif.otf"
    raw = (Path(__file__).resolve().parents[1] / name).read_bytes()
    scan_text(name, raw)
    for changed in (b"", raw + b"changed", ("sk-" + "A" * 30).encode()):
        with pytest.raises(ExportError, match="字体指纹"):
            scan_text(name, changed)


def test_hook_pattern_definition_is_ignored_but_other_lines_are_scanned():
    scan_text(".githooks/pre-commit", ("patterns='" + "sk-" + "A" * 30 + "'\nexit 0\n").encode())
    with pytest.raises(ExportError):
        scan_text(".githooks/pre-commit", ("patterns=''\n" + "sk-" + "A" * 30 + "\n").encode())


def test_manifest_check_detects_changed_and_missing_files(tmp_path):
    import hashlib
    import json

    (tmp_path / "SKILL.md").write_text("original", encoding="utf-8")
    receipt = {"schema_version": 1, "file_count": 1,
               "files": {"SKILL.md": hashlib.sha256(b"original").hexdigest()}}
    (tmp_path / "PUBLIC_EXPORT_MANIFEST.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert check_manifest(tmp_path)["file_count"] == 1
    (tmp_path / "SKILL.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ExportError, match="变化"):
        check_manifest(tmp_path)
    refresh_manifest(tmp_path)
    assert check_manifest(tmp_path)["file_count"] == 1
    (tmp_path / "SKILL.md").unlink()
    with pytest.raises(ExportError, match="缺失"):
        check_manifest(tmp_path)


def test_check_staged_uses_the_same_rules_as_export(tmp_path):
    """提交前钩子与公开导出共用一套规则：内部目录名、密钥形态都要在暂存时被拦；干净文件与空暂存放行。"""
    import subprocess
    from scripts.public_export import check_staged

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    assert check_staged(tmp_path) == []                       # 空暂存
    (tmp_path / "ok.md").write_text("普通文档\n", encoding="utf-8")
    git("add", "ok.md")
    assert check_staged(tmp_path) == []
    (tmp_path / "leak.md").write_text("样例在 `_" + "workspace/assets/x.json`\n", encoding="utf-8")
    (tmp_path / "key.txt").write_text("sk-" + "B" * 30 + "\n", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"\x89PNG not text sk-" + b"C" * 30)
    git("add", "leak.md", "key.txt", "pic.png")
    found = check_staged(tmp_path)
    assert any("leak.md" in f for f in found) and any("key.txt" in f for f in found)
    assert not any("pic.png" in f for f in found)              # 图片不按文本扫


def test_motion_requirements_are_part_of_public_surface():
    from public_export import PUBLIC_FILES
    assert "requirements-motion.txt" in PUBLIC_FILES
