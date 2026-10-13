"""对已裁出的局部做两次独立绝对复核；不替代整图 QA 或样式准入。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from .reviewer import review, source


def _file(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def check(crop: Path, criterion: str, *, original: Path | None = None,
          ask: Callable[[Path, dict], dict] | None = None) -> dict:
    """两次均确认局部不存在所述缺陷才通过；任何漏答、含糊或分歧都拒绝。"""
    crop = Path(crop).resolve()
    criterion = criterion.strip()
    if not criterion:
        raise ValueError("局部复核必须写清一个可观察的禁止特征")
    image = _file(crop)
    original_image = _file(Path(original)) if original else None
    contract = {"code": "FOCUS", "revision": 1,
                "qa": {"must_see": [], "must_not_see": [criterion]}}
    independent = ask is None
    ask = ask or (lambda path, spec: review(path, spec, text_mode="none", samples=1))  # 本函数自己做两次独立复核
    reviews = []
    for _ in range(2):
        answer = ask(crop, contract)
        entries = answer.get("must_not_see") if isinstance(answer, dict) else None
        matches = [x for x in entries if isinstance(x, dict) and x.get("item") == criterion] if isinstance(entries, list) else []
        entry = matches[0] if len(matches) == 1 else {}
        valid = (type(entry.get("present")) is bool and isinstance(entry.get("why"), str)
                 and bool(entry["why"].strip()))
        reviews.append({"passed": bool(valid and entry["present"] is False),
                        "problem": ("缺少明确的逐条判断" if not valid else
                                    "出现禁止特征" if entry["present"] else None),
                        "answer": answer})
    return {"passed": all(r["passed"] for r in reviews),
            "scope": "focused crop defect check only; not full-image QA or style admission",
            "criterion": criterion, "crop": image, "original": original_image,
            "review_source": source() if independent else "injected",
            "reviews": reviews}
