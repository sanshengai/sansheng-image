"""Editorial intent and source accounting; never a semantic quality score."""
from __future__ import annotations

import copy


def validate(value: object, *, source: str | None, item_ids: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != {"audience", "context", "goal", "coverage"}:
        raise ValueError("editorial 须含 audience/context/goal/coverage 四项")
    for field in ("audience", "context", "goal"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"editorial.{field} 不得为空")
    rows = value["coverage"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("editorial.coverage 不得为空；至少说明一个内容单元的取舍")
    if not source or not source.strip():
        raise ValueError("editorial.coverage 需要非空 source 原文")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"quote", "decision", "targets", "reason"}:
            raise ValueError("coverage 每项须含 quote/decision/targets/reason")
        if not isinstance(row["quote"], str) or not row["quote"].strip() or row["quote"] not in source:
            raise ValueError("coverage.quote 必须是原文中的非空准确引用")
        if row["decision"] not in ("show", "body", "omit"):
            raise ValueError("coverage.decision 只能是 show/body/omit")
        targets = row["targets"]
        if not isinstance(targets, list) or any(not isinstance(t, str) or t not in item_ids for t in targets):
            raise ValueError("coverage.targets 只能引用本次图片 id")
        if (row["decision"] == "show") != bool(targets):
            raise ValueError("show 须指定图片；body/omit 不得指定图片")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("coverage.reason 必须说明呈现或省略的理由")
    return copy.deepcopy(value)


def card(value: dict) -> str:
    lines = [f"读者：{value['audience']}", f"阅读环境：{value['context']}", f"目标：{value['goal']}",
             "", "内容取舍（只核对来源与去向，不代表语义完整性已验收）："]
    labels = {"show": "入图", "body": "留在正文", "omit": "本次不呈现"}
    for row in value["coverage"]:
        lines.append(f"- {row['quote']} → {labels[row['decision']]} {', '.join(row['targets'])}；{row['reason']}")
    return "\n".join(lines)
