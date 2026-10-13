#!/usr/bin/env python3
"""样图维护：样图只在本机（styles/*/samples/）和官网，不进 Git 仓库的任何分支。

  status [--strict]       工作区样图数；与合同登记对比（登记了但缺文件、文件在但没登记）；
                          样图与合同修订是否对得上（当前 / 待重出 / 历史样图）。--strict：已准入的画风
                          有「待重出」样图就返回 1
  backup --dest <目录>    把 styles/*/samples 原样同步到仓库外的备份目录（只增不删，内容变了的覆盖备份）
  restore --src <目录>    从备份目录还原到工作区（默认不覆盖已存在且内容不同的文件，--overwrite 才覆盖）

不做 Git 操作，不联网。锚点图（styles/<码>/anchor.*）和出图用的范例图不在 samples/ 下，属于仓库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stylebook.contract import sample_state  # noqa: E402


class SyncError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sample_files(base: Path) -> list[str]:
    """base/styles/<码>/samples/ 下的普通文件（相对 base 的 POSIX 路径，排序）。"""
    out = []
    for d in sorted((base / "styles").glob("*/samples")):
        if d.is_dir():
            out += [p.relative_to(base).as_posix() for p in sorted(d.rglob("*"))
                    if p.is_file() and not p.is_symlink() and not p.name.startswith(".")]
    return out


def registered(base: Path) -> set[str]:
    """合同 samples 里登记的文件（相对 base 的路径）。"""
    out = set()
    for cj in sorted((base / "styles").glob("*/contract.json")):
        for s in json.loads(cj.read_text(encoding="utf-8")).get("samples", []):
            out.add((cj.parent / s["file"]).relative_to(base).as_posix())
    return out


def revisions(base: Path) -> dict:
    """样图与合同修订的对应：各状态计数，以及待重出的清单（已准入画风的单列）。"""
    counts = {"current": 0, "stale": 0, "historical": 0}
    stale, stale_admitted = [], []
    for cj in sorted((base / "styles").glob("*/contract.json")):
        c = json.loads(cj.read_text(encoding="utf-8"))
        admitted = (c.get("evidence") or {}).get("admission") == "admitted"
        for s in c.get("samples", []):
            state = sample_state(c, s)
            counts[state] += 1
            if state == "stale":
                row = f"{cj.parent.name}/{s['file']}（r{s['generation']['revision']} 生成，合同已到 r{c.get('revision')}）"
                stale.append(row)
                if admitted:
                    stale_admitted.append(row)
    return {**counts, "stale_list": stale, "stale_admitted": stale_admitted}


def status(base: Path = ROOT) -> dict:
    have, reg = set(sample_files(base)), registered(base)
    res = {"files": len(have), "registered": len(reg),
           "missing": sorted(reg - have), "unregistered": sorted(have - reg), "revisions": revisions(base)}
    print(f"工作区样图 {res['files']} 个；合同登记 {res['registered']} 个；"
          f"登记了但缺文件 {len(res['missing'])}、文件在但没登记 {len(res['unregistered'])}")
    rv = res["revisions"]
    print(f"按合同修订：当前修订生成 {rv['current']}、待重出 {rv['stale']}（其中已准入画风 {len(rv['stale_admitted'])}）、"
          f"历史样图（无生成记录）{rv['historical']}")
    for n in rv["stale_list"][:10]:
        print(f"  待重出：{n}")
    for label, key in (("缺文件", "missing"), ("没登记", "unregistered")):
        for n in res[key][:10]:
            print(f"  {label}：{n}")
        if len(res[key]) > 10:
            print(f"  …另有 {len(res[key]) - 10} 个{label}")
    return res


def _copy(src_base: Path, dst_base: Path, overwrite: bool) -> dict:
    names = sample_files(src_base)
    if not names:
        raise SyncError(f"{src_base} 里没有样图（styles/*/samples/ 为空）")
    added = updated = same = skipped = 0
    for n in names:
        s, d = src_base / n, dst_base / n
        if d.exists():
            if _sha(s) == _sha(d):
                same += 1
                continue
            if not overwrite:
                skipped += 1
                continue
            updated += 1
        else:
            added += 1
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    return {"added": added, "updated": updated, "same": same, "skipped": skipped}


def _inside(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or b in a.parents


def backup(dest: Path, base: Path = ROOT) -> dict:
    if _inside(dest, base):
        raise SyncError(f"备份目录必须在仓库外：{dest}")
    r = _copy(base, dest, overwrite=True)
    print(f"备份到 {dest}：新增 {r['added']}、内容变化覆盖 {r['updated']}、已一致 {r['same']}（不删除备份里多出的文件）")
    return r


def restore(src: Path, base: Path = ROOT, overwrite: bool = False) -> dict:
    if not src.is_dir():
        raise SyncError(f"备份目录不存在：{src}")
    r = _copy(src, base, overwrite=overwrite)
    print(f"从 {src} 还原：新增 {r['added']}、覆盖 {r['updated']}、已一致 {r['same']}、内容不同而跳过 {r['skipped']}")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=ROOT, help="仓库目录（默认本脚本所在仓库）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="工作区样图与合同登记对比")
    st.add_argument("--strict", action="store_true", help="已准入画风有待重出样图时返回 1")
    b = sub.add_parser("backup", help="工作区样图 -> 仓库外备份目录")
    b.add_argument("--dest", type=Path, required=True)
    r = sub.add_parser("restore", help="备份目录 -> 工作区")
    r.add_argument("--src", type=Path, required=True)
    r.add_argument("--overwrite", action="store_true", help="覆盖已存在且内容不同的文件")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "status":
            res = status(a.repo)
            if a.strict and res["revisions"]["stale_admitted"]:
                print("错误：已准入的画风有待重出的样图，先按当前修订重出", file=sys.stderr)
                return 1
        elif a.cmd == "backup":
            backup(a.dest, a.repo)
        else:
            restore(a.src, a.repo, a.overwrite)
    except SyncError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
