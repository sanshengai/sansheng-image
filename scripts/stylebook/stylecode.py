"""风格码：网站选择器与 Skill 之间传递选择的一行文字。

语法（sb1）：
    sb1:<样式>[-<结构>-<密度>]-<色彩>
    样式  C31 或 C31@r2（锁定修订）
    结构  auto 或表达结构 id（flow、compare…），只在讲道理的场景出现
    密度  auto / sparse / balanced / dense，与结构成对出现
    色彩  orig，或 <色系 id>.L<深浅>S<鲜灰>，深浅与鲜灰取 -1 / 0 / 1
例：sb1:C01-orig　sb1:C31-flow-balanced-macaron.L1S0　sb1:C31@r2-auto-auto-orig

语法（sb2）：
    sb2:<用途>/<样式>[~<海报版式>][+<手法变体>][-<色系>[.L<深浅>S<鲜灰>]][-hex.<RRGGBB>…]
    ~版式  layouts/layouts.json 里的版式 id（可含连字符，如 corner-embrace）；紧随其后的 -色系 靠已知版式 id 切开
    +变体  合同 recipe.variants 的 id（小写字母、数字、下划线）
例：sb2:poster/C101~corner-embrace　sb2:poster/C103+mirror　sb2:poster/C101~corner-embrace-macaron.L1S0
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = "sb1:"
_STYLE = re.compile(r"^(?:C|S)\d{2,3}(?:@r\d+)?$")
_PAL = re.compile(r"^(?P<fam>[a-z]+)(?:\.L(?P<l>-?1|0)S(?P<s>-?1|0))?$")
DENSITIES = ("auto", "sparse", "balanced", "dense")


class StyleCodeError(ValueError):
    pass


@dataclass(frozen=True)
class StyleCode:
    style: str
    structure: str | None = None
    density: str | None = None
    palette: str = "orig"
    light: int = 0
    sat: int = 0
    scene: str | None = None
    custom: tuple[str, ...] = ()
    layout: str | None = None
    variant: str | None = None

    def format(self) -> str:
        if (self.layout or self.variant) and self.scene is None:
            raise StyleCodeError("海报版式与手法变体只能写在 sb2 码里")
        if self.scene is not None:
            result = f"sb2:{self.scene}/{self.style}"
            if self.layout:
                result += f"~{self.layout}"
            if self.variant:
                result += f"+{self.variant}"
            if self.palette not in ("orig", "brand"):
                result += f"-{self.palette}"
                if self.light or self.sat:
                    result += f".L{self.light}S{self.sat}"
            elif self.palette == "orig" and (self.light or self.sat):
                raise StyleCodeError("原色不带深浅鲜灰")
            if self.custom:
                result += "-hex." + ".".join(c.lstrip("#").upper() for c in self.custom)
            elif self.palette == "brand":
                raise StyleCodeError("品牌色需要具体 HEX")
            return result
        parts = [self.style]
        if self.structure is not None:
            parts += [self.structure, self.density or "auto"]
        parts.append("orig" if self.palette == "orig" else f"{self.palette}.L{self.light}S{self.sat}")
        return PREFIX + "-".join(parts)

    def manifest_fields(self) -> dict:
        """转成编译清单的字段。结构 auto 原样传给编译器（写入「按内容选最清楚的结构」）；密度 auto 不写，按格式默认。"""
        out: dict = {"style": self.style, "palette": {"family": self.palette, "light": self.light, "sat": self.sat}}
        if self.custom:
            out["palette"]["custom"] = list(self.custom)
        if self.structure:
            out["structure"] = self.structure
        if self.density and self.density != "auto":
            out["density"] = self.density
        if self.layout:
            out["poster_layout"] = self.layout
        if self.variant:
            out["variant"] = self.variant
        return out


def find(text: str) -> list[str]:
    """从一段话里找出所有风格码（用户可能连同整句「用叁笙生图 sb1:… 做成…」一起贴过来）。"""
    return re.findall(r"sb[12]:[A-Za-z0-9@./~+_\-]+", text or "")


def parse(code: str) -> StyleCode:
    raw = code.strip()
    if raw.startswith("sb2:"):
        return _parse_v2(raw)
    if not raw.startswith(PREFIX):
        raise StyleCodeError(f"风格码要以 {PREFIX} 开头：{raw!r}")
    parts = raw[len(PREFIX):].split("-")
    # 色彩段里的负号（L-1、S-1）会被 split 拆开：把 "L" / "S" 结尾的碎片与下一段拼回去
    merged: list[str] = []
    for p in parts:
        if merged and re.search(r"\.L$|\.L-?[01]S$|\.L0S$", merged[-1]):
            merged[-1] += "-" + p
        else:
            merged.append(p)
    parts = merged
    if len(parts) not in (2, 4):
        raise StyleCodeError(f"风格码段数不对（应为 2 或 4 段）：{raw!r}")
    style, pal = parts[0], parts[-1]
    if not _STYLE.match(style):
        raise StyleCodeError(f"样式码不对：{style!r}")
    m = _PAL.match(pal)
    if not m:
        raise StyleCodeError(f"色彩段不对：{pal!r}")
    fam = m.group("fam")
    if fam == "orig" and m.group("l") is not None:
        raise StyleCodeError("原色不带深浅鲜灰")
    light, sat = int(m.group("l") or 0), int(m.group("s") or 0)
    structure = density = None
    if len(parts) == 4:
        structure, density = parts[1], parts[2]
        if not re.fullmatch(r"[a-z]+", structure):
            raise StyleCodeError(f"结构段不对：{structure!r}")
        if density not in DENSITIES:
            raise StyleCodeError(f"密度段不对：{density!r}")
    return StyleCode(style, structure, density, fam, light, sat)


def validate(sc: StyleCode) -> list[str]:
    """与数据对照：样式、结构、色系是否存在。"""
    from . import data as D
    problems = []
    if sc.scene is not None and sc.scene not in D.scenes():
        problems.append(f"没有这个用途：{sc.scene}")
    if sc.style.split("@")[0] not in D.catalog():
        problems.append(f"没有这个样式：{sc.style}")
    if sc.structure and sc.structure != "auto" and sc.structure not in D.structures():
        problems.append(f"没有这个表达结构：{sc.structure}")
    if sc.palette not in {p["id"] for p in D.palettes()["palettes"]}:
        problems.append(f"没有这个色系：{sc.palette}")
    if sc.layout and sc.layout not in D.layouts():
        problems.append(f"没有这个海报版式：{sc.layout}")
    if sc.variant:
        from . import contract as CT
        try:
            variants = (CT.load(sc.style.split("@")[0]).get("recipe") or {}).get("variants") or {}
        except (KeyError, CT.ContractError):
            variants = None  # 样式本身不存在，上面已报
        if variants is not None and sc.variant not in variants:
            problems.append(f"{sc.style} 没有手法变体 {sc.variant}" + (f"；可选 {list(variants)}" if variants else "（该画风没有变体）"))
    return problems


def _split_layout(chain: str) -> str:
    """把「版式-色系」连写的片段切开：取最长的已知版式 id 前缀；都不认识就整段当版式（validate 会报未知）。"""
    from . import data as D
    tokens = chain.split("-")
    known = D.layouts()
    for n in range(len(tokens), 0, -1):
        if "-".join(tokens[:n]) in known:
            return "-".join(tokens[:n])
    return chain


def _parse_v2(raw: str) -> StyleCode:
    head = re.match(r"sb2:[a-z][a-z0-9-]*/[CS]\d{2,3}(?:@r[1-9]\d*)?", raw)
    layout = variant = None
    if head and raw[head.end():head.end() + 1] == "~":
        chain = re.match(r"~([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)", raw[head.end():])
        if not chain:
            raise StyleCodeError(f"海报版式不对：{raw!r}")
        layout = _split_layout(chain.group(1))
        raw = raw[:head.end()] + raw[head.end() + 1 + len(layout):]
    if head and raw[head.end():head.end() + 1] == "+":
        var = re.match(r"\+([a-z][a-z0-9]*(?:_[a-z0-9]+)*)", raw[head.end():])
        if not var:
            raise StyleCodeError(f"手法变体不对：{raw!r}")
        variant = var.group(1)
        raw = raw[:head.end()] + raw[head.end() + 1 + len(variant):]
    m = re.fullmatch(r"sb2:(?P<scene>[a-z][a-z0-9-]*)/(?P<style>[CS]\d{2,3}(?:@r[1-9]\d*)?)(?:-(?P<pal>[a-z]+)(?:\.L(?P<l>-?1|0)S(?P<s>-?1|0))?)?(?:-hex(?P<hex>(?:\.[0-9A-Fa-f]{6})+))?", raw)
    if not m:
        raise StyleCodeError(f"sb2 格式不对：{raw!r}")
    fam = m.group("pal") or "orig"
    # A lone -hex segment belongs to the HEX suffix, not the named palette.
    if fam == "orig" and m.group("l") is not None:
        raise StyleCodeError("原色不带深浅鲜灰")
    colors = tuple("#" + c.upper() for c in (m.group("hex") or "").split(".") if c)
    if colors and fam != "orig":
        raise StyleCodeError("HEX 品牌色不能同时指定另一色系")
    if fam in {"brand", "hex"} and not colors:
        raise StyleCodeError("品牌色需要具体 HEX")
    return StyleCode(m.group("style"), palette="brand" if colors else fam,
                     light=int(m.group("l") or 0), sat=int(m.group("s") or 0),
                     scene=m.group("scene"), custom=colors, layout=layout, variant=variant)
