#!/usr/bin/env python3
"""叁笙生图命令行入口：python3 scripts/sb.py <命令> ...

命令：
  compile  <清单.json>                 编译出提示词（不出图）
  cover-flow <清单.json>              S02 方形母版与横向扩图的两段提示词
  cover-check <方形.png>              S02 方形母版高对比内容边距预检
  generate <清单.json> -o 输出.png      编译并出图（自动重试、繁忙切备用线路）
  doctor [--deep]                      自检：各家「已配置 / 能连通 / 能出图」
  setup  [--provider X --model Y]      首次配置向导（不写密钥）
  inbox  <清单.json>... -d 目录         没有可用服务时，生成待出图清单
  qa     <图> --style C31 [--text ...]  出图验收：像素 + 独立看图 + 文字逐字比对
  qa-contrast <问题图> <候选图> --criterion ...  匿名双图换序比较单一缺陷（仅相对改进）
  qa-focus <局部裁图> --criterion ...       两次独立判断局部是否仍有已知缺陷
  deck budget|check <文件> --mode talk-read  PPT 页预算（由论点数推）与页规格卡检查
  comic-book <分镜.json>                     整册漫画分镜检查（页数档位、对白、钩子、概念道具）
  motion <图> --template glow --region x,y,w,h  局部动效 GIF（可选子板块，见 references/motion.md）
  qa-leak <成图> <参考图>                      串味报告：参考图局部是否被原样搬进成图（只报告）
  qa-palette <图> --family sea               色板偏差报告（只报告，不拒绝）
  sheet  <定妆图> <图>... -o 输出.png    一致性对照网格（--thumbs 看缩略图可辨认）
  matrix <风格码> [--only T1,T2]        8 道标准题测试矩阵（断点续跑、成本记账）
  build  [--private] [--gallery]        由风格合同生成 registry.json；--gallery 生成选择器（index.html + img/）；--advanced 生成旧完整画廊
  plan   <计划.json> [--manifests 目录]  检查配图计划、打印确认表并存成 <计划名>.plan-card.md、生成每张图的编译清单
  board  <镜头表.json> -o 目录 [--locations]  分镜：先出地点设定图与定调图，再一次出整场竖屏与横屏首帧
  batch  <计划.json> -o 目录             一口气做完：出图 → 导出 → 验收 → 单张返修 → 报告（可续跑）
  pptx   <计划.json> --images 目录 -o 文件.pptx [--mode illustration|full]   组装 PPT（插画 + 可编辑文字 / 整页图）
  style-for <场景> [--explicit C31]      按「本次指定 > 项目锁定 > 偏好 > 作者档案 > 出厂默认」定样式
  selection <recommend|normalize|apply|impact>  统一选择记录、局部修改与受影响图片
  preferences <show|set|clear|record|undo|forget|pause|resume|export|import>  管理本地偏好
  plan-review <计划.json> --article 原文 [--backend ...]  独立复核配图计划是否合理（逐张给结论）
  export <图> --format 格式 -o 输出       裁到格式比例并缩放到平台像素（公众号头条等另存方形裁切）
  sticker-split <透明宫格.png> --grid 3 -d 目录  检查格线并切出 2×2 至 4×4 独立透明 PNG
  sticker-check <目录> [--count 9]          检查逐张导出的透明表情包
  character sheet <角色.json> --style X   生成角色设定网格（之后每张图拿它当身份参考）
  character check <角色.json> <设定图> <图>... --style X   一致性对照网格 + 逐条核对辨识特征
  layers   <brief.json> -o 新目录 --prepare | --import-results <json> --prepared <json>   分件式图层（不代调出图）
  animate  <doctor|plan|render|qa> ...   四场景动态版（本机渲染与验收）
  animate  assets --out 新目录          导出原创动作库
  animate  dialect --style 画风码       读取该画风的动作语言建议
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import first_run as FR  # noqa: E402
from stylebook import compile as CP  # noqa: E402


def _manifest(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_compile(a) -> int:
    c = CP.compile_manifest(_manifest(a.manifest))
    print(json.dumps(c.to_dict(), ensure_ascii=False, indent=2) if a.json else c.prompt)
    return 0


def cmd_cover_flow(a) -> int:
    from stylebook.cover_flow import compile_flow
    print(json.dumps(compile_flow(_manifest(a.manifest)), ensure_ascii=False, indent=2))
    return 0


def cmd_cover_check(a) -> int:
    from stylebook.cover_flow import check_square_master
    result = check_square_master(Path(a.image))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def cmd_generate(a) -> int:
    from stylebook import backends as B
    if Path(a.out).exists() and not a.force:
        print(f"{a.out} 已存在，不覆盖（可能是验收过的成品）。换一个输出路径，或加 --force。", file=sys.stderr)
        return 2
    m = _manifest(a.manifest)
    c = CP.compile_manifest(m)
    base = Path(a.manifest).resolve().parent
    refs = [(base / r["path"]).resolve() if not Path(r["path"]).is_absolute() else Path(r["path"]) for r in c.references]
    try:
        r = B.generate(c.prompt, Path(a.out), size=c.size, aspect=c.aspect, refs=refs, provider=a.provider,
                       model=a.model or (None if c.model == "gpt-image-2" else c.model), quality=a.quality, tag=c.style)
    except B.BackendError as e:
        print(f"出图失败（{e.kind}）：{e}", file=sys.stderr)
        return 2
    print(json.dumps({"out": a.out, "provider": r.provider, "model": r.model, "attempts": r.attempts, "seconds": r.seconds,
                      "est_usd": r.est_usd, "style": c.style, "size": list(c.size)}, ensure_ascii=False))
    return 0


def cmd_doctor(a) -> int:
    from stylebook import setup as S
    print(S.doctor(deep=a.deep, only=a.only))
    return 0


def cmd_setup(a) -> int:
    from stylebook import setup as S
    if a.env_template:
        print(f"密钥模板：{S.env_template()}（权限 600，只在本机）")
    elif a.provider:
        print(S.configure(a.provider, a.model, a.quality or "normal", test=not a.no_test))
    else:
        out = S.interactive()
        if out:
            print(out)
    return 0


def cmd_inbox(a) -> int:
    from stylebook import backends as B
    tasks = []
    for m in a.manifests:
        c = CP.compile_manifest(_manifest(m))
        tasks.append({"id": Path(m).stem, "prompt": c.prompt, "aspect": c.aspect, "references": c.references})
    print(f"待出图清单：{B.inbox(tasks, Path(a.dir))}")
    return 0


def cmd_qa(a) -> int:
    from stylebook import contract as CT
    from stylebook.backends.base import load_dotenv
    from stylebook.qa import judge, write_report
    load_dotenv()  # 看图后端的密钥只写在 ~/.config/sansheng-image/.env 时也要读到
    if a.contract:
        contract_path = Path(a.contract)
        c = json.loads(contract_path.read_text(encoding="utf-8"))
        problems = CT.validate(c)
        if problems:
            raise CT.ContractError(c.get("code", a.style), problems)
        if c["code"] != a.style.split("@")[0]:
            print(f"验收合同风格码须为 {a.style.split('@')[0]}，实际为 {c['code']}", file=sys.stderr)
            return 2
        c["_path"] = str(contract_path)
    else:
        c = CT.load(a.style)
    manifest = _manifest(a.manifest) if a.manifest else None
    if manifest:
        pinned = f"{c['code']}@r{c['revision']}"
        if manifest.get("style") not in (c["code"], pinned):
            print(f"验收清单的样式须为 {pinned}，实际为 {manifest.get('style')}", file=sys.stderr)
            return 2
        content = manifest.get("content") or {}
        if not str(content.get("subject", "")).strip():
            print("验收清单缺少 content.subject，无法核对内容事实", file=sys.stderr)
            return 2
        from stylebook.comic import validate_content as validate_comic_content
        comic_problems = validate_comic_content(a.format or manifest.get("format"), content)
        if comic_problems:
            print("；".join(comic_problems), file=sys.stderr)
            return 2
        from stylebook.qa import content_expectations
        c = {**c, **content_expectations(content)}
    manifest_text = (manifest or {}).get("text") or {}
    expected = ([t for t in a.text.split("|") if t] if a.text is not None else
                [x["text"] for x in manifest_text.get("items", []) if x.get("text")])
    mode = a.text_mode or (("native" if expected else "none") if a.text is not None else
                           manifest_text.get("mode") or ("native" if expected else "none"))
    fmt = a.format or (manifest or {}).get("format")
    from stylebook import data as D
    format_def = D.formats().get(fmt, {}) if fmt else {}
    if (format_def.get("safe_zone") or {}).get("center_square"):
        from stylebook.export import center_square_preview
        source = Path(a.image)
        square = center_square_preview(source, source.with_name(f"{source.stem}.qa-square.png"))
        c = {**c, "_square_crop_expectation": str(square)}
    thumbnail_px = format_def.get("thumbnail_px")
    if thumbnail_px:
        from PIL import Image
        px = int(thumbnail_px)
        source = Path(a.image)
        thumb = source.with_name(f"{source.stem}.thumb-{px}.png")
        with Image.open(source) as im:
            im.convert("RGB").resize((px, px), Image.Resampling.LANCZOS).save(thumb)
        c = {**c, "_thumbnail_expectation": str(thumb)}
    from stylebook.qa.binding import snapshot, verify
    binding_files = {"image": Path(a.image)}
    if a.manifest:
        binding_files["manifest"] = Path(a.manifest)
    if c.get("_path"):
        binding_files["contract"] = Path(c["_path"])
    for key in ("_square_crop_expectation", "_thumbnail_expectation"):
        if c.get(key):
            binding_files[key] = Path(c[key])
    if a.review_json:
        binding_files["supplied_review"] = Path(a.review_json)
    binding_values = {"contract": c, "expected_text": expected, "text_mode": mode, "format": fmt,
                      "review_mode": "supplied" if a.review_json else "independent_call"}
    binding = snapshot("image", files=binding_files, values=binding_values)
    if a.review_json:
        rv = json.loads(Path(a.review_json).read_text(encoding="utf-8"))
    else:
        from stylebook.qa.reviewer import review, ReviewerIncompleteResult
        try:
            rv = review(Path(a.image), c, expected, mode)
        except ReviewerIncompleteResult as exc:
            # judge 仍拒绝漏答；保留原回答与绑定，转为补审核而非重生。
            rv = exc.review
    v = judge(Path(a.image), c, rv, expected, mode, fmt, manifest)
    verify(binding, kind="image", files=binding_files, values=binding_values)
    v.binding = binding
    if a.report:
        write_report([v], Path(a.report))
    print(json.dumps({"image": a.image, "passed": v.passed, "problems": v.problems, "pixel": v.pixel,
                      "transcribed_text": rv.get("transcribed_text")}, ensure_ascii=False, indent=2))
    return 0 if v.passed else 1


def cmd_qa_contrast(a) -> int:
    from stylebook.qa.contrast import compare
    mode = "forbidden" if a.defect is not None or a.defect_file is not None else "desired"
    source = a.defect_file if a.defect_file is not None else a.criterion_file
    if source is not None:
        criterion = Path(source).read_text(encoding="utf-8")
    else:
        criterion = a.defect if mode == "forbidden" else a.criterion
    report = Path(a.report) if a.report else Path(a.candidate).with_name(Path(a.candidate).stem + "-contrast.json")
    if report.exists():
        print(f"对照报告已存在，不覆盖：{report}", file=sys.stderr)
        return 2
    result = compare(Path(a.reference), Path(a.candidate), criterion, criterion_mode=mode)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "report": str(report), "scope": result["scope"]}, ensure_ascii=False))
    return 0 if result["verdict"] == "candidate_better" else 1


def cmd_qa_focus(a) -> int:
    from stylebook.qa.focus import check
    criterion = Path(a.criterion_file).read_text(encoding="utf-8") if a.criterion_file else a.criterion
    report = Path(a.report) if a.report else Path(a.crop).with_name(Path(a.crop).stem + "-focus.json")
    if report.exists():
        print(f"局部复核报告已存在，不覆盖：{report}", file=sys.stderr)
        return 2
    result = check(Path(a.crop), criterion, original=Path(a.original) if a.original else None)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "report": str(report), "scope": result["scope"]}, ensure_ascii=False))
    return 0 if result["passed"] else 1


def cmd_qa_palette(a) -> int:
    from stylebook import palette as PL
    from stylebook.qa.palette_check import check
    hexes = [c["hex"] for c in PL.resolve(a.family, a.light, a.sat, a.hex or None)] if a.family != "orig" else list(a.hex or [])
    if not hexes:
        print("需要 --family 色系，或用 --hex 给出至少一个颜色", file=sys.stderr)
        return 2
    print(json.dumps(check(Path(a.image), hexes), ensure_ascii=False, indent=2))
    return 0


def cmd_motion(a) -> int:
    from stylebook import motion as MO
    try:
        if a.template == "labels":
            if a.layout:
                region = MO.boxes_from_layout(Path(a.layout))
            elif a.boxes:
                region = [[tuple(float(x) for x in b.split(",")) for b in grp.split("+")] for grp in a.boxes.split(";") if grp.strip()]
                if any(len(r) != 4 for g in region for r in g):
                    raise ValueError
            else:
                print("labels 模板要给 --boxes \"x,y,w,h;x,y,w,h\" 或 --layout 排字配置", file=sys.stderr)
                return 2
        else:
            region = tuple(float(x) for x in a.region.split(","))
            if len(region) != 4:
                raise ValueError
    except (ValueError, AttributeError, OSError, KeyError):
        print("--region 要写成 x,y,w,h 四个 0–1 的比例，例如 0.55,0.1,0.4,0.5；--boxes 用分号分隔多个框", file=sys.stderr)
        return 2
    out = Path(a.output)
    if out.exists():
        print(f"输出已存在，不覆盖：{out}", file=sys.stderr)
        return 2
    try:
        print(json.dumps(MO.make(Path(a.image), a.template, region, a.target, out, a.format, not a.no_loop,
                                    tuple(int(a.color.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)) if a.color else None), ensure_ascii=False))
    except MO.MotionError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_deck(a) -> int:
    from stylebook import deck as DK
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    try:
        if a.action == "budget":
            print(json.dumps(DK.page_budget(data["ledger"], a.mode, int(a.chars), user_pages=a.pages, user_minutes=a.minutes),
                             ensure_ascii=False, indent=2))
            return 0
        problems = DK.check_cards(data["cards"] if isinstance(data, dict) else data, a.mode)
    except DK.DeckError as e:
        print(str(e), file=sys.stderr)
        return 2
    for item in problems:
        print(item)
    return 1 if problems else 0


def cmd_comic_book(a) -> int:
    from stylebook import comic_book as CB
    problems = CB.check(json.loads(Path(a.file).read_text(encoding="utf-8")))
    for item in problems:
        print(item)
    return 1 if problems else 0


def cmd_qa_leak(a) -> int:
    from stylebook.qa.leak_check import check
    try:
        r = check(Path(a.image), Path(a.reference))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_sheet(a) -> int:
    from stylebook.qa.sheet import consistency_sheet, thumb_sheet
    if a.thumbs:
        print(thumb_sheet(Path(a.images[0]), Path(a.out)))
    else:
        print(consistency_sheet(Path(a.images[0]), [Path(x) for x in a.images[1:]], Path(a.out)))
    return 0


def cmd_matrix(a) -> int:
    from stylebook import matrix as MX
    st = MX.run(a.style, only=a.only.split(",") if a.only else None, out_dir=Path(a.out) if a.out else None,
                provider=a.provider, model=a.model, quality=a.quality, max_usd=a.max_usd, jobs=a.jobs,
                do_review=not a.no_review, regen=a.regen, reviews=a.reviews, log=lambda m: print(m, file=sys.stderr, flush=True))
    qs = st["questions"]
    print(json.dumps({"style": st["style"], "passed": [k for k, v in qs.items() if v.get("result") == "pass"],
                      "failed": [k for k, v in qs.items() if v.get("result") == "fail"],
                      "split": [k for k, v in qs.items() if v.get("result") == "split"],
                      "pending": [k for k, v in qs.items() if v.get("result") == "pending"],
                      "admission": st.get("admission", {}).get("verdict"),
                      "matrix_line": st.get("admission", {}).get("matrix_verdict"),
                      "abilities": st["abilities"], "est_usd_total": st["est_usd_total"],
                      "spent_this_run": st["spent_this_run"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_matrix_evidence(a) -> int:
    from stylebook.matrix_evidence import import_evidence
    state = import_evidence(a.bundle, a.contract, a.out)
    print(json.dumps({"style": state["style"], "admission": state["admission"],
                      "new_generations": 0, "new_model_calls": 0,
                      "output": str(Path(a.out).resolve())}, ensure_ascii=False, indent=2))
    return 0


def cmd_build(a) -> int:
    from stylebook import build as BD
    if a.check:
        reg = BD.registry(a.private)
        problems = BD.check(reg)
        on_disk = None if a.private else json.loads(BD.REGISTRY_PATH.read_text(encoding="utf-8")) if BD.REGISTRY_PATH.is_file() else {}
        if on_disk is not None and on_disk != json.loads(json.dumps(reg, ensure_ascii=False)):
            problems.append("registry.json 与风格合同 / 目录不一致：运行 sb.py build 重新生成")
        print("\n".join(problems) or "注册表一致")
        return 1 if problems else 0
    print(f"注册表：{BD.write_registry(private=a.private)}")
    if a.gallery and not a.advanced:
        print(f"选择器：{BD.picker(private=a.private)}")
    elif a.gallery:
        print(f"画廊：{BD.gallery(private=a.private, samples=[Path(x) for x in a.samples or []], legacy_prompts=Path(a.legacy_prompts) if a.legacy_prompts else None)}")
    return 0


def cmd_plan(a) -> int:
    from stylebook import plan as PL
    plan = _manifest(a.plan)
    errs, warns = PL.check(plan, base_path=Path(a.plan).resolve().parent)
    if errs:
        print("配图计划有问题，改完再出图：\n" + "\n".join(f"- {e}" for e in errs), file=sys.stderr)
        return 1
    print(PL.table(plan))
    for w in warns:
        print(f"提醒：{w}")
    from stylebook import plan_card as PC
    plan_path = Path(a.plan).resolve()
    card = PC.write(plan_path.parent, PL.table(plan), warns=warns, confirmed=plan.get("confirmed"),
                    name=f"{plan_path.stem}.plan-card.md")
    print(f"计划卡：{card}")
    if a.manifests:
        d = Path(a.manifests)
        d.mkdir(parents=True, exist_ok=True)
        for m in PL.manifests(plan, a.model or "gpt-image-2"):
            CP.compile_manifest(m)  # 先编译一遍，冲突词、换色等问题出图前就拦下
            (d / f"{m['_id']}.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"编译清单：{d}")
    return 0


def cmd_board(a) -> int:
    from stylebook import board as BD
    argv = [a.board] + (["-o", a.out] if a.out else []) + (["--locations"] if a.locations else []) + \
        (["--provider", a.provider] if a.provider else []) + (["--model", a.model] if a.model else []) + (["--force"] if a.force else [])
    return BD.main(argv)


def cmd_style_for(a) -> int:
    from stylebook import contract as CT
    from stylebook import plan as PL
    from stylebook import selection as S
    r = PL.resolve_style(a.scene, a.explicit, Path(a.project) if a.project else None)
    recommendation = S.recommend(a.scene, explicit=a.explicit, project=Path(a.project) if a.project else None)
    chosen_code = CT.canonical(r["code"])[0]
    chosen = next(item for item in recommendation["candidates"] if item["code"] == chosen_code)
    r["admission"] = chosen["status"]
    r["admission_note"] = ("已通过画风矩阵；具体用途与整组图片仍需逐次验收" if chosen["status"] == "admitted"
                           else "该画风尚未正式准入；可试用，但须逐张检查并保留失败证据")
    r["candidates"] = recommendation["candidates"]
    r["use"], r["pool"] = recommendation["use"], recommendation["pool"]
    print(json.dumps(r, ensure_ascii=False))
    return 0


def cmd_selection(a) -> int:
    from stylebook import selection as S
    if a.action == "recommend":
        result = S.recommend(a.scene, shapes=a.shapes.split(",") if a.shapes else [],
                             explicit=a.explicit, project=Path(a.project) if a.project else None)
    elif a.action == "normalize":
        raw = Path(a.input).read_text(encoding="utf-8").strip() if a.input else a.code
        if raw is None:
            raise S.SelectionError("normalize 需要 --input JSON 文件或 --code sb2:… / sb1:…")
        result = S.normalize(json.loads(raw) if raw.startswith("{") else raw, scene=a.scene)
    elif a.action == "apply":
        if not a.plan or not a.input:
            raise S.SelectionError("apply 需要 --plan 和 --input")
        plan_path = Path(a.plan).resolve()
        result = S.apply(_manifest(a.plan), _manifest(a.input), base_path=plan_path.parent)
    else:
        if not a.plan or not a.before or not a.after:
            raise S.SelectionError("impact 需要 --plan、--before、--after")
        result = {"affected_ids": S.affected(_manifest(a.before), _manifest(a.after), _manifest(a.plan),
                                               replace_master=a.propagate_master)}
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if a.out:
        target = Path(a.out)
        if target.exists():
            raise S.SelectionError(f"输出已存在，不覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
        print(target)
    else:
        print(output, end="")
    return 0


def cmd_preferences(a) -> int:
    from stylebook import profile as P
    if a.action == "show":
        data = P.read()
        result = {"paused": data["paused"], "event_count": len(data["events"]),
                  "effective": {field: P.lookup(field, scene=a.scene, project=a.project)
                                for field in sorted(P.FIELDS)}}
    elif a.action == "set":
        if a.value is None or a.field is None:
            raise P.ProfileError("set 需要 --field 和 --value")
        value = json.loads(a.value) if a.field == "palette" else a.value
        result = P.set_explicit(a.field, value, scene=a.scene, project=a.project, note=a.note or "")
    elif a.action == "clear":
        result = P.clear_explicit(a.field, scene=a.scene, project=a.project)
    elif a.action == "record":
        if a.value is None or a.field is None or not a.candidates or not a.scene or not a.task:
            raise P.ProfileError("record 需要 --scene、--field、--value、--task 与 --candidates JSON 数组")
        value = json.loads(a.value) if a.field == "palette" else a.value
        result = P.record_change(a.field, value, scene=a.scene, project=a.project, task_id=a.task,
                                 candidates=json.loads(a.candidates), reason=a.reason, note=a.note or "")
    elif a.action == "undo":
        result = P.revoke(a.event_id)
    elif a.action == "forget":
        P.forget(a.event_id)
        result = {"forgotten": a.event_id}
    elif a.action == "export":
        if not a.file:
            raise P.ProfileError("export 需要 --file")
        P.export_to(Path(a.file), force=a.force)
        result = {"exported": a.file}
    elif a.action == "import":
        if not a.file:
            raise P.ProfileError("import 需要 --file")
        result = P.import_from(Path(a.file), replace_conflicts=a.replace_conflicts)
    else:
        P.pause(a.action == "pause")
        result = {"paused": a.action == "pause"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _plan_review_call(PR, plan: dict, a):
    """调用独立复核；失败时给出可操作的指引并返回非零退出码（2 = 复核没能完成），不出 traceback。"""
    import os
    from stylebook.backends.base import load_dotenv
    from stylebook.qa.reviewer import QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable
    added = None
    try:
        before = set(os.environ)
        load_dotenv()  # 让 ~/.config/sansheng-image/.env 里的复核后端密钥生效
        added = set(os.environ) - before
        backend = getattr(a, "backend", None)
        return PR.review(plan, Path(a.article), a.model, **({"backend": backend} if backend else {}))
    except (QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable) as e:
        print(f"配图计划复核没能完成：{e}", file=sys.stderr)
        if isinstance(e, ReviewerAuthUnavailable):
            print(PR.backend_guidance(), file=sys.stderr)
        else:
            print("额度、限流或网络问题通常稍后重试即可；急用可用 --backend 换一个复核后端（对照见 references/backends.md「独立复核后端」）。", file=sys.stderr)
        return 2
    except PR.PlanReviewIncomplete as e:
        print(f"配图计划复核没能完成：{e}", file=sys.stderr)
        if a.report:
            Path(a.report).write_text(json.dumps(e.to_report(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"原始回答与逐条缺口已写入 {a.report}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:  # 未知后端、预算设置不合法、截断、服务端 HTTP 错误等
        print(f"配图计划复核没能完成：{e}", file=sys.stderr)
        return 2
    finally:
        for key in added or ():
            os.environ.pop(key, None)


def cmd_plan_review(a) -> int:
    from stylebook.qa import plan_review as PR
    from stylebook import plan as PL
    plan = _manifest(a.plan)
    errs, _ = PL.check(plan, base_path=Path(a.plan).resolve().parent)
    if errs:
        print("配图计划有问题：" + "；".join(errs), file=sys.stderr)
        return 1
    if plan.get("version") in (2, 3):
        import hashlib
        if hashlib.sha256(Path(a.article).read_bytes()).hexdigest() != plan["source"]["sha256"]:
            print("plan-review 的原文与计划 source.sha256 不同", file=sys.stderr)
            return 1
    from stylebook.qa.binding import snapshot, verify
    binding_files = {"article": Path(a.article), "plan": Path(a.plan)}
    binding_values = {"plan": plan}
    binding = snapshot("article-plan", files=binding_files, values=binding_values)
    r = _plan_review_call(PR, plan, a)
    if isinstance(r, int):
        return r
    verify(binding, kind="article-plan", files=binding_files, values=binding_values)
    r["binding"] = binding
    if a.report:
        Path(a.report).write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s = PR.summarize([(Path(a.article).parent.name, r)], allow_no_images=not plan["items"])
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0 if s["qualified"] else 1


def cmd_export(a) -> int:
    from stylebook import export as EX
    anchor = tuple(float(x) for x in a.anchor.split(",")) if a.anchor else (0.5, 0.5)
    overlay = _manifest(a.overlay) if a.overlay else None
    r = EX.export(Path(a.image), a.format, Path(a.out), anchor, overlay=overlay,
                  overlay_root=Path(a.overlay).resolve().parent if a.overlay else None)
    print(json.dumps({"out": str(r.main), "extras": [str(x) for x in r.extras], "warnings": r.warnings}, ensure_ascii=False))
    return 0


def cmd_sticker_split(a) -> int:
    from stylebook.sticker import split_grid
    try:
        paths = split_grid(Path(a.image), Path(a.dir), a.grid)
    except (ValueError, FileExistsError) as e:
        print(f"表情包切片被拦下：{e}", file=sys.stderr)
        return 2
    print(json.dumps({"grid": a.grid, "count": len(paths), "images": [str(p) for p in paths]}, ensure_ascii=False))
    return 0


def cmd_sticker_check(a) -> int:
    from stylebook.sticker import check_pack
    try:
        report = check_pack(Path(a.dir), count=a.count)
    except ValueError as exc:
        print(f"表情包检查被拦下：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


def cmd_character(a) -> int:
    from stylebook import backends as B
    from stylebook import character as CH
    from stylebook import contract as CT
    ch = CH.load(Path(a.character))
    if a.action == "sheet":
        c = CP.compile_manifest(CH.sheet_manifest(ch, a.style))
        out = Path(a.out or Path(ch["_dir"]) / f"{ch['id']}-sheet.png")
        if out.exists() and not a.force:
            print(f"{out} 已存在，不覆盖；加 --force 重出", file=sys.stderr)
            return 2
        r = B.generate(c.prompt, out, size=c.size, aspect=c.aspect, tag=f"character:{ch['id']}")
        print(json.dumps({"sheet": str(out), "style": c.style, "seconds": r.seconds, "est_usd": r.est_usd}, ensure_ascii=False))
        return 0
    from stylebook.qa import judge
    from stylebook.qa.reviewer import review
    from stylebook.qa.sheet import consistency_sheet
    imgs = [Path(x) for x in a.images]
    labels = [f"{i}" for i in range(2, len(imgs) + 1)]
    grid = consistency_sheet(imgs[0], imgs[1:], Path(a.out or Path(ch["_dir"]) / f"{ch['id']}-consistency.png"), labels=labels)
    cc = CH.consistency_contract(ch, CT.load(a.style))
    from stylebook.qa.sheet import _font
    expected = ["定妆图" if _font()[1] else "REF"] + labels  # 网格上的标签是我们自己加的字，按应有文字比对
    v = judge(grid, cc, review(grid, cc, expected, "native"), expected, "native")
    print(json.dumps({"grid": str(grid), "passed": v.passed, "problems": v.problems}, ensure_ascii=False, indent=2))
    return 0 if v.passed else 1


def cmd_batch(a) -> int:
    from stylebook import batch as BT
    st = BT.run(_manifest(a.plan), Path(a.out), provider=a.provider, model=a.model, quality=a.quality, retries=a.retries,
                jobs=a.jobs, do_review=not a.no_review, log=lambda m: print(m, file=sys.stderr, flush=True),
                base_path=Path(a.plan).resolve().parent)
    print(json.dumps(st["summary"], ensure_ascii=False, indent=2))
    print(f"报告：{Path(a.out) / 'report.md'}")
    produced = [item for item in st.get("items", {}).values() if item.get("status") in {"passed", "generated"}]
    FR.maybe_show_star(len(produced), complete=len(produced) == st["summary"]["total"] and not st["summary"]["failed"])
    return 0 if not st["summary"]["failed"] else 1


def cmd_make(a) -> int:
    from stylebook import make as MK
    from stylebook.backends.base import BackendError
    try:
        if a.import_results:
            if not a.prepared or a.prepare:
                raise MK.BriefError("导回须同时提供 --prepared，不能与 --prepare 同用")
            report = MK.import_host(_manifest(a.brief), Path(a.prepared), Path(a.import_results), Path(a.out),
                                    base_path=Path(a.brief).resolve().parent)
        else:
            if a.prepared:
                raise MK.BriefError("--prepared 只用于 --import-results")
            report = MK.run(_manifest(a.brief), Path(a.out), base_path=Path(a.brief).resolve().parent,
                            provider=a.provider, model=a.model, quality=a.quality, jobs=a.jobs,
                            prepare_only=a.prepare)
    except (MK.BriefError, BackendError, FileExistsError) as exc:
        print(f"轻量出图停止：{exc}", file=sys.stderr)
        return 2
    if report.get("status") != "pending_host":
        from stylebook import flywheel as FW
        report["flywheel"] = FW.after_make(_manifest(a.brief), report, Path(a.out))
        (Path(a.out) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for q in report["flywheel"]["pending"]:
            print(f"【待问】{q['question']}", file=sys.stderr)
        for note in report["flywheel"]["notes"]:
            print(f"【飞轮】{note}", file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    FR.maybe_show_star(len(report.get("items", [])), complete=report.get("status") == "pending_visual_review")
    return 1 if report["status"] == "failed" else 0


def cmd_layers(a) -> int:
    from stylebook import layers as LY
    try:
        brief = LY.load_json(a.brief)
        base = Path(a.brief).resolve().parent
        if a.import_results:
            if not a.prepared or a.prepare:
                raise LY.LayersError("导回须同时提供 --prepared，不能与 --prepare 同用")
            report = LY.import_results(brief, Path(a.prepared), Path(a.import_results), Path(a.out), base_path=base)
        else:
            if a.prepared or not a.prepare:
                raise LY.LayersError("只支持 --prepare 或 --import-results <json> --prepared <json>；Codex 内置生图不能由命令行代调")
            report = LY.prepare_run(brief, Path(a.out), base_path=base)
    except (LY.LayersError, FileExistsError) as exc:
        print(f"分件出图停止：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_animate(a) -> int:
    from stylebook import animate_adapter as AA
    return AA.main(a.rest)


def cmd_raw_generate(a) -> int:
    """按已编译好的提示词直接出一张原图（供写作 Skill 等上游调用；不编译、不验收、不导出）。"""
    from stylebook import backends as B
    from stylebook.backends import providers as PV
    from stylebook.backends.base import BackendError
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    w, h = (int(x) for x in a.size.lower().split("x"))
    try:
        r = B.generate(prompt, Path(a.out), size=(w, h), aspect=a.aspect, refs=[Path(p) for p in a.ref or []],
                       provider=a.provider, quality=a.quality, tag=a.tag or "raw-generate")
    except BackendError as exc:
        print(json.dumps({"ok": False, "kind": exc.kind, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "out": a.out, "provider": r.provider, "model": r.model, "seconds": r.seconds,
                      "attempts": r.attempts, "est_usd": r.est_usd, "tokens": getattr(PV.META, "tokens", None)}, ensure_ascii=False))
    return 0


def cmd_accept(a) -> int:
    from stylebook import flywheel as FW
    recs = FW.accept(Path(a.out_dir), a.ids.split(",") if a.ids else None)
    print(json.dumps({"accepted": [r["detail"] for r in recs], "style": recs[0]["style"] if recs else None}, ensure_ascii=False))
    return 0 if recs else 1


def cmd_flywheel(a) -> int:
    from stylebook import flywheel as FW
    if a.action == "status":
        print(json.dumps(FW.status(a.scene), ensure_ascii=False, indent=2))
    elif a.action == "questions":
        print(json.dumps(FW.pending(a.scene), ensure_ascii=False, indent=2))
    elif a.action == "answer":
        if not (a.field and a.value and a.choice):
            print("answer 需要 --field --value --choice(default/project/no)", file=sys.stderr)
            return 2
        value = json.loads(a.value) if a.value.startswith("{") else a.value
        print(json.dumps(FW.answer({k: v for k, v in (("scene", a.scene), ("project", a.project)) if v}, a.field, value, a.choice), ensure_ascii=False))
    else:
        print("未知动作", file=sys.stderr)
        return 2
    return 0


def cmd_pptx(a) -> int:
    import shutil
    import subprocess
    try:
        import pptx  # noqa: F401
        from stylebook import pptx_build as PX
        print(PX.build(_manifest(a.plan), Path(a.images), Path(a.out), a.mode))
        return 0
    except ImportError:
        uv = shutil.which("uv")
        if not uv:
            print("需要 python-pptx：pip install python-pptx，或装 uv 后重试（会临时带上，不改系统 Python）", file=sys.stderr)
            return 2
        script = Path(__file__).resolve().parent / "stylebook" / "pptx_build.py"
        cp = subprocess.run([uv, "run", "--quiet", "--no-project", "--with", "python-pptx", "python3", str(script),
                             a.plan, a.images, a.out, a.mode], capture_output=True, text=True)
        print(cp.stdout.strip() or cp.stderr[-400:])
        return cp.returncode


def main(argv=None) -> int:
    actual_argv = sys.argv[1:] if argv is None else argv
    # 动态与分件入口只检查现有环境，绝不沿用静态入口的自动 pip 安装。
    if (not actual_argv or actual_argv[0] not in {"animate", "layers"}) and FR.ensure_dependencies(actual_argv):
        return 2
    ap = argparse.ArgumentParser(prog="sb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compile"); p.add_argument("manifest"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_compile)
    p = sub.add_parser("cover-flow"); p.add_argument("manifest"); p.set_defaults(fn=cmd_cover_flow)
    p = sub.add_parser("cover-check"); p.add_argument("image"); p.set_defaults(fn=cmd_cover_check)
    p = sub.add_parser("generate"); p.add_argument("manifest"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--quality")
    p.add_argument("--force", action="store_true", help="允许覆盖已存在的输出文件"); p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("doctor"); p.add_argument("--deep", action="store_true"); p.add_argument("--only"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("raw-generate"); p.add_argument("--prompt-file", required=True); p.add_argument("--ref", action="append")
    p.add_argument("--aspect", default="1:1"); p.add_argument("--size", default="1024x1024"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--provider"); p.add_argument("--quality"); p.add_argument("--tag"); p.set_defaults(fn=cmd_raw_generate)
    p = sub.add_parser("accept"); p.add_argument("out_dir"); p.add_argument("--ids"); p.set_defaults(fn=cmd_accept)
    p = sub.add_parser("flywheel"); p.add_argument("action", choices=["status", "questions", "answer"]); p.add_argument("--scene"); p.add_argument("--project")
    p.add_argument("--field"); p.add_argument("--value"); p.add_argument("--choice", choices=["default", "project", "no"]); p.set_defaults(fn=cmd_flywheel)
    p = sub.add_parser("setup"); p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--quality")
    p.add_argument("--no-test", action="store_true"); p.add_argument("--env-template", action="store_true"); p.set_defaults(fn=cmd_setup)
    p = sub.add_parser("inbox"); p.add_argument("manifests", nargs="+"); p.add_argument("-d", "--dir", required=True); p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("qa"); p.add_argument("image"); p.add_argument("--style", required=True); p.add_argument("--text", help="应有文字，用 | 分隔")
    p.add_argument("--contract", help="本地候选合同 JSON；按此修订版验收，不读取正式样式合同")
    p.add_argument("--manifest", help="本张图的编译清单；核对内容主体与空间关系，并沿用文字和格式")
    p.add_argument("--text-mode", choices=["native", "overlay", "hybrid", "none"]); p.add_argument("--format"); p.add_argument("--review-json", help="宿主模型写好的看图结论（manual 模式）")
    p.add_argument("--report"); p.set_defaults(fn=cmd_qa)
    p = sub.add_parser("qa-contrast"); p.add_argument("reference", help="已知缺陷图，评审时会匿名")
    p.add_argument("candidate", help="返修候选图，评审时会匿名")
    criterion = p.add_mutually_exclusive_group(required=True)
    criterion.add_argument("--criterion", help="希望画面满足的特征；缺少它算缺陷")
    criterion.add_argument("--criterion-file", help="UTF-8 文件：希望画面满足的特征；缺少它算缺陷")
    criterion.add_argument("--defect", help="不希望出现的特征；出现它算缺陷")
    criterion.add_argument("--defect-file", help="UTF-8 文件：不希望出现的特征；出现它算缺陷")
    p.add_argument("--report", help="保存两次换序复核及图片哈希的 JSON 路径")
    p.set_defaults(fn=cmd_qa_contrast)
    p = sub.add_parser("qa-focus"); p.add_argument("crop", help="已裁出的局部图片")
    p.add_argument("--original", help="对应整图，只记录路径和哈希；须另行验收整图")
    criterion = p.add_mutually_exclusive_group(required=True)
    criterion.add_argument("--criterion", help="一个可观察的禁止特征")
    criterion.add_argument("--criterion-file", help="UTF-8 文本文件，含一个可观察的禁止特征")
    p.add_argument("--report", help="保存两次独立复核及图片哈希的 JSON 路径")
    p.set_defaults(fn=cmd_qa_focus)
    p = sub.add_parser("deck"); p.add_argument("action", choices=["budget", "check"])
    p.add_argument("file", help="budget：含 ledger 的 JSON；check：含 cards 的 JSON 或卡片数组")
    p.add_argument("--mode", choices=["talk", "talk-read", "read"], default="talk-read")
    p.add_argument("--chars", type=int, default=0, help="budget：源文汉字数")
    p.add_argument("--pages", type=int, help="用户指定页数"); p.add_argument("--minutes", type=float, help="用户指定时长（分钟）")
    p.set_defaults(fn=cmd_deck)
    p = sub.add_parser("comic-book"); p.add_argument("file", help="分镜文档 JSON（见 references/scenes/comic.md）")
    p.set_defaults(fn=cmd_comic_book)
    p = sub.add_parser("qa-leak"); p.add_argument("image", help="成图"); p.add_argument("reference", help="当时挂的参考图")
    p.set_defaults(fn=cmd_qa_leak)
    p = sub.add_parser("motion"); p.add_argument("image", help="已通过验收的静态图")
    p.add_argument("--template", choices=["glow", "particles", "breathe", "labels"], required=True)
    p.add_argument("--region", help="作用区域 x,y,w,h（占画面比例）；labels 模板改用 --boxes / --layout")
    p.add_argument("--boxes", help="labels：按出现顺序的标签框 \"x,y,w,h;x,y,w,h\"（占画面比例），同时出现的几个框用 + 连接")
    p.add_argument("--layout", help="labels：overlay 排字配置 JSON，按 items 顺序取 box")
    p.add_argument("--target", choices=["wechat-article", "wechat-sticker", "x", "web"], default="wechat-article")
    p.add_argument("--format", choices=["gif", "webp"], help="缺省按输出后缀")
    p.add_argument("--color", help="glow / particles 的颜色，如 #F0BE6E；浅底图要用深一点的颜色才看得见")
    p.add_argument("--no-loop", action="store_true", help="播完停在最后一帧")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_motion)
    p = sub.add_parser("qa-palette"); p.add_argument("image")
    p.add_argument("--family", default="orig", help="色系 id；orig 时用 --hex")
    p.add_argument("--light", type=int, default=0); p.add_argument("--sat", type=int, default=0)
    p.add_argument("--hex", nargs="*", help="直接给色板 hex")
    p.set_defaults(fn=cmd_qa_palette)
    p = sub.add_parser("sheet"); p.add_argument("images", nargs="+"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--thumbs", action="store_true", help="单图缩成 46/66/128 像素检查可辨认"); p.set_defaults(fn=cmd_sheet)
    p = sub.add_parser("matrix"); p.add_argument("style"); p.add_argument("--only", help="只跑这些题，逗号分隔")
    p.add_argument("-o", "--out"); p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--quality")
    p.add_argument("--max-usd", type=float, help="本轮新增花费上限（美元）"); p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--no-review", action="store_true"); p.add_argument("--regen", action="store_true", help="忽略旧图全部重出")
    p.add_argument("--reviews", type=int, default=1, help="每题独立看图几次（准入用 2；不一致记为分歧交人复核）")
    p.set_defaults(fn=cmd_matrix)
    p = sub.add_parser("matrix-evidence", help="核验现有内置原图、Coding Plan意见与Codex裁决；不生图、不调用模型、不正式登记")
    p.add_argument("bundle", help="完整外部证据包JSON")
    p.add_argument("--contract", required=True, help="当前实际候选合同JSON")
    p.add_argument("-o", "--out", required=True, help="必须不存在的输出目录")
    p.set_defaults(fn=cmd_matrix_evidence)
    p = sub.add_parser("build"); p.add_argument("--private", action="store_true", help="叠加私有 profile（只写到 gallery/build/）")
    p.add_argument("--gallery", action="store_true"); p.add_argument("--advanced", action="store_true", help="生成旧的完整画廊（内嵌全部样图、含偏好页）"); p.add_argument("--samples", nargs="*", help="样图目录（<码>-T3.png 或 <码>-q1.png）")
    p.add_argument("--legacy-prompts", help="兼容旧命令；选择器已停用独立提示词模板，此参数不再生效")
    p.add_argument("--check", action="store_true", help="只检查 registry.json 是否与合同一致，不写文件"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("plan"); p.add_argument("plan"); p.add_argument("--manifests"); p.add_argument("--model"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("board", help="按镜头表一次出整场竖屏与横屏首帧（--locations 先出地点设定图与定调图）")
    p.add_argument("board"); p.add_argument("-o", "--out"); p.add_argument("--locations", action="store_true")
    p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_board)
    p = sub.add_parser("style-for"); p.add_argument("scene"); p.add_argument("--explicit"); p.add_argument("--project"); p.set_defaults(fn=cmd_style_for)
    p = sub.add_parser("selection"); p.add_argument("action", choices=["recommend", "normalize", "apply", "impact"])
    p.add_argument("--scene"); p.add_argument("--shapes"); p.add_argument("--explicit"); p.add_argument("--project")
    p.add_argument("--input"); p.add_argument("--code"); p.add_argument("--plan"); p.add_argument("--before"); p.add_argument("--after")
    p.add_argument("--propagate-master", action="store_true", help="替换系列母版并重画所有引用它的图")
    p.add_argument("-o", "--out"); p.set_defaults(fn=cmd_selection)
    p = sub.add_parser("preferences"); p.add_argument("action", choices=["show", "set", "clear", "record", "undo", "forget", "pause", "resume", "export", "import"])
    p.add_argument("--scene"); p.add_argument("--project"); p.add_argument("--field", choices=["style", "palette", "form", "structure"])
    p.add_argument("--value"); p.add_argument("--task"); p.add_argument("--candidates"); p.add_argument("--reason", default="preference")
    p.add_argument("--note"); p.add_argument("--event-id"); p.add_argument("--file"); p.add_argument("--force", action="store_true")
    p.add_argument("--replace-conflicts", action="store_true"); p.set_defaults(fn=cmd_preferences)
    p = sub.add_parser("plan-review"); p.add_argument("plan"); p.add_argument("--article", required=True)
    p.add_argument("--model"); p.add_argument("--report")
    p.add_argument("--backend", choices=["ark_coding_plan", "ark_agent_plan", "claude_cli"],
                   help="复核后端；缺省读 SANSHENG_IMAGE_PLAN_REVIEW_BACKEND，再读 SANSHENG_IMAGE_QA_BACKEND，默认 ark_coding_plan")
    p.set_defaults(fn=cmd_plan_review)
    p = sub.add_parser("export"); p.add_argument("image"); p.add_argument("--format", required=True); p.add_argument("-o", "--out", required=True)
    p.add_argument("--anchor", help="裁切焦点，相对坐标 x,y（默认 0.5,0.5）"); p.set_defaults(fn=cmd_export)
    p.add_argument("--overlay", help="叠字 JSON：items[].text、box=[x,y,w,h]、font_px、min_px")
    p = sub.add_parser("sticker-split"); p.add_argument("image"); p.add_argument("--grid", type=int, required=True)
    p.add_argument("-d", "--dir", required=True); p.set_defaults(fn=cmd_sticker_split)
    p = sub.add_parser("sticker-check"); p.add_argument("dir"); p.add_argument("--count", type=int, default=9)
    p.set_defaults(fn=cmd_sticker_check)
    p = sub.add_parser("character"); p.add_argument("action", choices=["sheet", "check"]); p.add_argument("character")
    p.add_argument("images", nargs="*"); p.add_argument("--style", required=True); p.add_argument("-o", "--out")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_character)
    p = sub.add_parser("make"); p.add_argument("brief"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--quality")
    p.add_argument("--jobs", type=int, default=4); p.add_argument("--prepare", action="store_true")
    p.add_argument("--import-results"); p.add_argument("--prepared")
    p.set_defaults(fn=cmd_make)
    p = sub.add_parser("layers"); p.add_argument("brief"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--prepare", action="store_true"); p.add_argument("--import-results"); p.add_argument("--prepared")
    p.set_defaults(fn=cmd_layers)
    p = sub.add_parser("animate"); p.add_argument("rest", nargs=argparse.REMAINDER, help="doctor|plan|render|qa|assets|dialect 及其参数")
    p.set_defaults(fn=cmd_animate)
    p = sub.add_parser("batch"); p.add_argument("plan"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--quality"); p.add_argument("--retries", type=int, default=2)
    p.add_argument("--jobs", type=int, default=3); p.add_argument("--no-review", action="store_true"); p.set_defaults(fn=cmd_batch)
    p = sub.add_parser("pptx"); p.add_argument("plan"); p.add_argument("--images", required=True); p.add_argument("-o", "--out", required=True)
    p.add_argument("--mode", choices=["illustration", "full"], default="illustration"); p.set_defaults(fn=cmd_pptx)
    a = ap.parse_args(argv)
    from stylebook import contract as CT
    from stylebook import stylecode as SC
    from stylebook import profile as PRF
    from stylebook import selection as SEL
    from stylebook.qa.reviewer import QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable
    try:
        return a.fn(a)
    except (QuotaExhausted, ReviewerAuthUnavailable, ReviewerNetworkUnavailable) as e:
        print(f"独立看图已停止：{e}", file=sys.stderr)
        return 2
    except CP.CompileError as e:
        print(f"编译被拦下：{e}", file=sys.stderr)
        return 3
    except CT.ContractError as e:
        print(f"风格合同不合格：{e}", file=sys.stderr)
        return 3
    except SC.StyleCodeError as e:
        print(f"风格码不对：{e}", file=sys.stderr)
        return 3
    except PRF.ProfileError as e:
        print(f"偏好记录有问题：{e}", file=sys.stderr)
        return 3
    except SEL.SelectionError as e:
        print(f"选择记录有问题：{e}", file=sys.stderr)
        return 3
    except KeyError as e:  # 找不到风格、格式等
        print(f"找不到：{e.args[0] if e.args else e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"文件不存在：{e.filename or e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"JSON 格式有误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
