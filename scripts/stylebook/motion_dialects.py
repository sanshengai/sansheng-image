"""叁笙生图「动态版」画风动作语言读取入口：sb.py animate dialect --style C24。

只读：用 contract.load 读取（并完整校验）当前风格合同里的 motion_dialect，原样输出给动态导演参考。

边界：
- 它是导演建议，不是自动成片：不渲染、不验收、不调用动作库，也不代表该画风的静态准入。
- 不写文件、不起子进程、不访问网络、不加载渲染底座；合同来自 contract.load（公开库，以及当前能读到的私有 profile）。
- 参数只接受 `--style <码>`（或 `--style=<码>`）与 `-h/--help`，其余一律拒绝，不接受缩写。
- 风格码写 `C24`，或 `C24@r5`；带修订号时必须与当前合同的 revision 完全一致，不一致就拒绝，不会悄悄忽略。
- 旧码并入别的画风时拒绝，不自动套用另一个画风的方言。
- 合同没有 motion_dialect 时输出「尚未编写」的 JSON 并退出码 2，不回落到其它画风或默认值。
- 成功：stdout 一份 JSON，退出码 0。缺方言：stdout 一份 JSON（ok=false），退出码 2。
  参数或合同有问题：stderr 中文说明、stdout 为空、无 traceback，退出码 2。
- 十二个类别是建议类别，不是函数名；类别与动作库公开函数的对应见 CATEGORY_FUNCTIONS 与 references/motion-primitives.md。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Sequence

from . import contract as CT

USAGE = ("用法：sb.py animate dialect --style <风格码>   例：--style C24，或 --style C24@r5（修订号须与当前合同一致）\n"
         "读取该画风合同里的动作语言（motion_dialect），始终输出 JSON；这是动态导演建议，不会自动成片，也不证明静态准入。")

SCOPE = ("动态导演建议：来自画风合同的 motion_dialect，只说明这个画风适合怎样运动。它不证明已自动调用动作库、"
         "已生成或通过动态成片，也不代表该画风的静态版已准入；原语类别是建议类别，具体函数见 references/motion-primitives.md，"
         "实际动作仍要按原文因果与受保护内容人工编排并逐帧验收。")

# 合同里的十二个建议类别 → 动作库 motion-primitives.mjs 的实际公开函数（见 references/motion-primitives.md 第 3 节）
CATEGORY_FUNCTIONS = {
    "keyframes": ["createKeyframes", "createTween", "keyframeValue"],
    "easing": ["easings", "cubicBezier", "steps", "resolveEase"],
    "spring": ["createSpring", "springProgress", "springSettleTime"],
    "squash": ["createSquash"],
    "particles": ["createParticles"],
    "light_sweep": ["createLightSweep"],
    "mask_reveal": ["createMaskReveal"],
    "card_flip": ["createFlip"],
    "char_text": ["createCharText"],
    "number_roll": ["createNumberRoll"],
    "path_draw": ["createPathDraw"],
    "loop": ["createLoop", "createTimeline", "loopTime", "pingPongTime", "closedKeyframes", "crossfadeWeight",
             "stateDistance", "assertLoopClosed"],
}

_STYLE_RE = re.compile(r"(?P<base>(?:C|S)[0-9]{2,3})(?:@r(?P<rev>[1-9][0-9]*))?")


class DialectError(Exception):
    """可预期的拒绝；消息直接给用户看。"""


def _parse_args(argv: Sequence[str]):
    """返回风格码字符串；请求帮助时返回 None。"""
    args = list(argv)
    if args in (["-h"], ["--help"]):
        return None
    if len(args) == 2 and args[0] == "--style":
        return args[1]
    if len(args) == 1 and args[0].startswith("--style="):
        return args[0][len("--style="):]
    raise DialectError("参数不对，只接受 --style <风格码>。\n" + USAGE)


def normalized_contract_sha256(contract: dict) -> str:
    """规范化合同的 SHA-256：排除 contract.load 附带的运行时字段（_path、_alias_from、_alias_note 等下划线开头的键）。"""
    clean = {k: v for k, v in contract.items() if not k.startswith("_")}
    blob = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _load(style: str) -> tuple[dict, str]:
    m = _STYLE_RE.fullmatch(style)
    if not m:
        raise DialectError(f"风格码 {style!r} 不合法：应写成 C24 或 C24@r5（C 开头为公开画风，S 开头为私有画风；不接受小写与空白）")
    base = m.group("base")
    try:
        contract = CT.load(base)
    except KeyError as exc:
        raise DialectError(f"找不到风格 {base}：{exc.args[0] if exc.args else exc}") from None
    except CT.ContractError as exc:
        raise DialectError(f"风格合同不合格，不读取其动作语言：{exc}") from None
    except (OSError, ValueError) as exc:
        raise DialectError(f"读取风格合同失败：{exc}") from None
    if contract.get("_alias_from") or contract.get("code") != base:
        raise DialectError(f"{base} 已并入 {contract.get('code')}；为避免误套其它画风，不自动转换，请直接用 {contract.get('code')} 查询")
    if m.group("rev") is not None and int(m.group("rev")) != contract["revision"]:
        raise DialectError(f"{base}@r{m.group('rev')} 与当前合同修订不符：当前是 r{contract['revision']}；"
                           "不会悄悄忽略修订号，请改用当前修订或去掉 @r")
    return contract, base


def describe(style: str) -> tuple[int, dict]:
    """返回（退出码, JSON 对象）。参数或合同问题抛 DialectError。"""
    contract, base = _load(style)
    head = {"style": contract["code"], "revision": contract["revision"], "pinned": f"{contract['code']}@r{contract['revision']}",
            "visibility": contract["visibility"], "name": contract["name"]}
    dialect = contract.get("motion_dialect")
    if dialect is None:
        return 2, {"ok": False, **head, "status": "motion_dialect_not_authored",
                   "message": f"画风 {contract['code']} 的合同尚未编写动作语言（motion_dialect）；不会套用其它画风或默认值。"
                              "动态编排仍可按审美原则与原文因果进行，但这里没有该画风专属的动作语言可读。",
                   "scope": SCOPE}
    functions = {}
    for category in dialect["primitives"]:
        if category not in CATEGORY_FUNCTIONS:
            raise DialectError(f"合同里的原语类别 {category!r} 不在已知十二类中")
        functions[category] = CATEGORY_FUNCTIONS[category]
    return 0, {"ok": True, **head, "motion_dialect": dialect, "primitive_functions": functions,
               "contract_sha256": normalized_contract_sha256(contract), "scope": SCOPE}


def main(argv: Sequence[str]) -> int:
    try:
        style = _parse_args(argv)
        if style is None:
            print(USAGE)
            return 0
        code, payload = describe(style)
    except DialectError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code
