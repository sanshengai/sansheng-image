"""叁笙生图「动态版」原创动作库资源导出：sb.py animate assets --out <尚不存在的新目录>。

只做一件事：把包内的 assets/motion-primitives.mjs 原样复制到用户指定的新目录，并写一份 JSON receipt。
它是准备步骤，不是 render / qa，不证明任何作品已经出片或验收。

边界：
- 不装依赖、不起子进程、不访问网络、不加载渲染底座（motion_pipeline），不读 manifest。
- 参数只接受 `--out <目录>`（或 `--out=<目录>`）与 `-h/--help`，其余一律拒绝；不接受缩写。
- 输出目录必须尚不存在（含断开的符号链接也算已存在）、其父目录必须是已有目录；不会 mkdir -p。
  输出位置不得落在本 Skill 包内或包内动作库所在目录内（保护源）。
- 用 os.mkdir 原子创建新目录，文件用 O_EXCL 新建；中途失败只清理本次自己写出的文件，
  最后才尝试 rmdir 空目录，绝不 rmtree，不删别人并发放进来的文件。
- 源文件必须是普通文件且不是符号链接，内容过基本完整性检查（版本行与全部公开函数），读到的字节就是写出的字节。
- 失败：stderr 中文说明、无 traceback、退出码 2；成功：stdout 打印可解析的 JSON receipt，退出码 0。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Sequence

SCHEMA = "sansheng-image/motion-assets-receipt/v1"
MODULE_FILE = "motion-primitives.mjs"
RECEIPT_FILE = "motion-assets-receipt.json"
EXPECTED_VERSION = "0.1.0"
# 随已验模块更新；仅检查导出名不能发现保持导出名不变的损坏。
EXPECTED_SHA256 = "b0af7e647b1eff63d2f33d4c23d1f863e072d85096147c2ea06de15e589334dc"
MAX_BYTES = 1 << 20

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path(__file__).resolve().parent / "assets" / MODULE_FILE

# 文档与调用示例依赖的公开函数；缺任何一个说明包内文件被截断或替换
REQUIRED_EXPORTS = (
    "normalizeTime", "createKeyframes", "createTween", "createSpring", "createSquash", "createParticles",
    "createLightSweep", "createMaskReveal", "createFlip", "createCharText", "createNumberRoll",
    "createPathDraw", "createLoop", "createTimeline", "closedKeyframes", "loopTime",
)
_VERSION_RE = re.compile(r"^export const VERSION = '([^'\n]+)';$", re.MULTILINE)

USAGE = "用法：sb.py animate assets --out <尚不存在的新目录>\n" \
        "把内置原创动作库 motion-primitives.mjs 复制到新目录，并写 motion-assets-receipt.json；只是准备资源，不渲染、不验收。"


class AssetsError(Exception):
    """可预期的拒绝；消息直接给用户看。"""


def _parse_args(argv: Sequence[str]):
    """返回 --out 的值；请求帮助时返回 None。"""
    args = list(argv)
    if len(args) == 1 and args[0] in ("-h", "--help"):
        return None
    value = None
    if len(args) == 2 and args[0] == "--out":
        value = args[1]
    elif len(args) == 1 and args[0].startswith("--out="):
        value = args[0][len("--out="):]
    else:
        raise AssetsError(f"不支持的参数 {args!r}；assets 只接受 --out <尚不存在的新目录>")
    if value == "":
        raise AssetsError("--out 不能为空")
    if value.startswith("-"):
        raise AssetsError(f"--out 的值 {value!r} 像是另一个选项；目录名不要以 - 开头")
    if "\0" in value:
        raise AssetsError("--out 含非法字符")
    return value


def _read_source(source: Path):
    """读源文件并校验；返回 (字节, 版本号)。读到的字节就是之后写出的字节。"""
    try:
        before = os.lstat(source)
    except FileNotFoundError:
        raise AssetsError(f"包内动作库缺失：{source}") from None
    except OSError as exc:
        raise AssetsError(f"无法检查包内动作库 {source}：{exc.strerror}") from None
    if stat.S_ISLNK(before.st_mode):
        raise AssetsError(f"包内动作库是符号链接，拒绝复制：{source}")
    if not stat.S_ISREG(before.st_mode):
        raise AssetsError(f"包内动作库不是普通文件：{source}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise AssetsError(f"无法读取包内动作库 {source}：{exc.strerror}") from None
    try:
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise AssetsError(f"包内动作库在读取时被替换：{source}")
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise AssetsError(f"读取包内动作库失败 {source}：{exc.strerror}") from None
    if not data or len(data) > MAX_BYTES:
        raise AssetsError(f"包内动作库大小异常（{len(data)} 字节），疑似损坏：{source}")
    if b"\0" in data:
        raise AssetsError(f"包内动作库含空字节，疑似损坏：{source}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise AssetsError(f"包内动作库不是有效的 UTF-8，疑似损坏：{source}") from None
    found = _VERSION_RE.findall(text)
    if len(found) != 1:
        raise AssetsError(f"包内动作库缺少唯一的 VERSION 行，疑似损坏：{source}")
    if found[0] != EXPECTED_VERSION:
        raise AssetsError(f"包内动作库版本是 {found[0]!r}，本入口只认 {EXPECTED_VERSION!r}：{source}")
    if "export class MotionError" not in text:
        raise AssetsError(f"包内动作库缺少 MotionError，疑似损坏：{source}")
    missing = [name for name in REQUIRED_EXPORTS if not re.search(rf"^export function {name}\(", text, re.MULTILINE)]
    if missing:
        raise AssetsError(f"包内动作库缺少公开函数 {', '.join(missing)}，疑似损坏：{source}")
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise AssetsError(f"包内动作库校验不符，疑似损坏：{source}")
    return data, found[0]


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _check_out(raw: str, source: Path) -> Path:
    """只读检查输出位置；通过时输出目录一定尚不存在，且父目录是已有目录。"""
    out = Path(os.path.abspath(raw))
    if out.name == "":
        raise AssetsError(f"输出位置不合法：{raw!r}")
    parent = out.parent
    if not parent.is_dir():
        raise AssetsError(f"输出目录的父目录不存在或不是目录：{parent}（不会自动创建上级目录）")
    if os.path.lexists(out):
        raise AssetsError(f"输出已存在，不覆盖：{out}（含空目录、文件、符号链接；换一个新目录名）")
    real_out = Path(os.path.realpath(parent)) / out.name
    protected = {Path(os.path.realpath(PACKAGE_ROOT)),
                 Path(os.path.realpath(os.path.dirname(os.path.abspath(source))))}
    for root in protected:
        if _is_within(real_out, root) or _is_within(root, real_out):
            raise AssetsError(f"输出位置不能在 Skill 包内或其祖先：{out}（保护源文件）")
    return out


def _write_new_file(path: str, data: bytes):
    """用 O_EXCL 新建并写入；返回 (dev, ino)。失败时只删除自己刚创建的这个文件。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o644)  # 失败（含已存在）时什么都没创建，直接上抛
    ident = None
    try:
        st = os.fstat(fd)
        ident = (st.st_dev, st.st_ino)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if ident is not None:
            _unlink_if_ours(path, ident)
        raise
    return ident


def _unlink_if_ours(path: str, ident) -> bool:
    try:
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == tuple(ident):
            os.unlink(path)
            return True
    except OSError:
        pass
    return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_written(path: str, expected: bytes) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        if _sha256(handle.read(MAX_BYTES + 1)) != _sha256(expected):
            raise AssetsError("写出后回读校验失败：输出文件与源不一致")


def _publish(out: Path, files: Sequence[tuple[str, bytes]]) -> None:
    """mkdir 原子占位 + 逐个新建文件；失败只清理本次自己创建的文件，不动别人放进来的。"""
    try:
        os.mkdir(out, 0o755)
    except FileExistsError:
        raise AssetsError(f"输出已存在，不覆盖：{out}（可能刚被别的进程创建）") from None
    made = os.lstat(out)
    created: list[tuple[str, tuple[int, int]]] = []
    try:
        for name, data in files:
            path = os.path.join(out, name)
            created.append((path, _write_new_file(path, data)))
        _verify_written(os.path.join(out, MODULE_FILE), files[0][1])
    except BaseException:
        for path, ident in reversed(created):
            _unlink_if_ours(path, ident)
        try:
            now = os.lstat(out)
            if stat.S_ISDIR(now.st_mode) and (now.st_dev, now.st_ino) == (made.st_dev, made.st_ino):
                os.rmdir(out)  # 只会删空目录；里面若有别人的文件就保留
        except OSError:
            pass
        raise


def _run(argv: Sequence[str], source) -> int:
    raw = _parse_args(argv)
    if raw is None:
        print(USAGE)
        return 0
    src = Path(source) if source is not None else DEFAULT_SOURCE
    out = _check_out(raw, src)
    data, version = _read_source(src)
    digest = _sha256(data)
    receipt = {"schema": SCHEMA, "version": version, "sha256": digest,
               "files": [{"path": MODULE_FILE, "sha256": digest, "bytes": len(data)}]}
    receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _publish(out, [(MODULE_FILE, data), (RECEIPT_FILE, receipt_bytes)])
    print(json.dumps({**receipt, "out": str(out), "receipt": RECEIPT_FILE}, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str], *, source=None) -> int:
    """argv 是 assets 之后的参数。source 仅供测试注入包内动作库路径，命令行不暴露。"""
    try:
        return _run(argv, source)
    except AssetsError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误：写入失败（{exc.strerror or exc}）；本次写出的文件已清理，未覆盖任何已有内容", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 不向用户抛 traceback
        print(f"错误：未预期的失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2
