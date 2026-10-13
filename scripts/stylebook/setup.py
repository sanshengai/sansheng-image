"""配置引导与自检。原则：不代写密钥、不让用户把密钥贴进对话、先用最低成本测试出图再保存。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from . import backends as B
from .backends.base import CONFIG_DIR, BackendError, load_dotenv, redact
from .backends.providers import PROVIDERS

# 国内用户排序：已有中转 → 大陆直连 → 境外 → 暂时没有 Key
ORDER = ["codex", "openai", "seedream", "dashscope", "gemini", "openrouter"]
ENV_HELP = {
    "codex": ["不需要密钥：安装 Codex CLI 后运行 codex login，用 ChatGPT 账号登录即可"],
    "openai": ["OPENAI_API_KEY=你的密钥", "OPENAI_BASE_URL=https://api.openai.com/v1   # 用中转就换成中转地址"],
    "seedream": ["ARK_API_KEY=你的密钥"],
    "dashscope": ["DASHSCOPE_API_KEY=你的密钥"],
    "gemini": ["GEMINI_API_KEY=你的密钥"],
    "openrouter": ["OPENROUTER_API_KEY=你的密钥"],
}


def review_env_help() -> list[str]:
    """独立复核（plan-review 配图计划复核、qa 看图）的后端与凭证；三选一，默认 ark_coding_plan。"""
    from .qa import coding_plan as CPL
    from .qa.reviewer import ARK_PLAN_URL
    return [
        "独立复核后端（给配图计划/成图做第二双眼睛；三选一，不设置时默认 ark_coding_plan）",
        "SANSHENG_IMAGE_PLAN_REVIEW_BACKEND=ark_coding_plan   # 配图计划复核；可改 ark_agent_plan 或 claude_cli",
        "SANSHENG_IMAGE_QA_BACKEND=ark_coding_plan            # 成图看图验收；上一项没设时配图计划复核也读它",
        "# 方式一：火山方舟 Coding Plan（默认）",
        f"ARK_CODING_PLAN_BASE_URL={CPL.BASE_URL}",
        "ARK_CODING_PLAN_API_KEY=你的密钥",
        "# 方式二：火山方舟 Agent Plan",
        f"ARK_AGENT_PLAN_BASE_URL={ARK_PLAN_URL}",
        "ARK_AGENT_PLAN_API_KEY=你的密钥",
        "# 方式三：本机 Claude Code 命令行，不需要上面的 Key，只需在终端登录过 claude",
        "# SANSHENG_IMAGE_QA_CLAUDE=/path/to/claude   # 可选：claude 不在 PATH 时指定",
        "# 可选：配图计划复核的输出预算（2000–32000，claude_cli 不适用）",
        "SANSHENG_IMAGE_PLAN_REVIEW_MAX_OUTPUT_TOKENS=12000",
    ]


def menu() -> str:
    inbox = len(ORDER) + 1
    lines = [f"选一家生图服务（有 ChatGPT / Codex 订阅就选 1，不按张付费；没有 Key 也能用：选 {inbox}，Skill 会给出逐张提示词，你在即梦、豆包等网页里免费出图后导回验收）：", ""]
    for i, name in enumerate(ORDER, 1):
        p = PROVIDERS[name]
        state = "已配置" if B.configured(name) else "未配置"
        lines.append(f"{i}. {p['zh']}（{state}）— {p['blurb']}。申请：{p['apply']}")
    lines += [f"{inbox}. 暂时没有 Key：走收件箱", "",
              f"设置密钥：打开 {CONFIG_DIR / '.env'}（可先运行 `setup --env-template` 生成模板），按注释填入，"
              "**不要把密钥发到对话里**。填好后运行 `setup --provider <名字>`，会先出一张最便宜的测试图再保存设置。"]
    return "\n".join(lines)


def env_template() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = CONFIG_DIR / ".env"
    if f.exists():
        return f
    body = ["# 叁笙生图的密钥文件：只在本机，不进任何仓库。去掉行首 # 并填入你的密钥。", ""]
    for name in ORDER:
        body.append(f"# {PROVIDERS[name]['zh']}")
        body += [f"# {x}" for x in ENV_HELP[name]] + [""]
    body += [f"# {x}" if not x.startswith("#") else x for x in review_env_help()] + [""]
    f.write_text("\n".join(body), encoding="utf-8")
    os.chmod(f, 0o600)
    return f


def _deep_test(name: str) -> tuple[bool, str]:
    """最低成本的真实出图：低质量、最小允许尺寸。"""
    try:
        with tempfile.TemporaryDirectory() as d:
            r = B.generate("A single small green leaf on a plain white background. No text.", Path(d) / "t.png",
                           size=(1024, 1024), provider=name, quality="low", max_attempts=2, tag="doctor")
        return True, f"测试出图成功（{r.model}，{r.seconds} 秒）"
    except BackendError as e:
        return False, f"{e.kind}：{e}"


def doctor(deep: bool = False, only: str | None = None) -> str:
    load_dotenv()
    cfg = B.config()
    rows = ["| 服务 | 已配置 | 能连通 | 能出图 | 实测过 |", "|---|---|---|---|---|"]
    for name in ORDER:
        if only and name != only:
            continue
        p = PROVIDERS[name]
        conf = B.configured(name)
        reach, ready = "—", "—"
        if conf and p.get("ping"):
            try:
                ok, msg = p["ping"]()
                reach = ("✓ " if ok else "✗ ") + msg
            except BackendError as e:
                reach = f"✗ {e.kind}：{e}"
        elif conf:
            reach = "未探测（该服务无免费探测接口）"
        if conf and deep:
            ok, msg = _deep_test(name)
            ready = ("✓ " if ok else "✗ ") + msg
        rows.append(f"| {p['zh']} | {'✓' if conf else '未配置'} | {reach} | {ready} | {'是' if p['verified'] else '否（按官方文档实现）'} |")
    base = os.environ.get("OPENAI_BASE_URL")
    head = [f"当前设置：服务 {cfg.get('provider', '自动选择')}，模型 {cfg.get('model', '默认')}，质量 {cfg.get('quality', 'normal')}",
            f"配置目录：{CONFIG_DIR}（密钥文件 .env {'存在' if (CONFIG_DIR / '.env').exists() else '不存在'}）"]
    if base:
        head.append(f"OpenAI 兼容地址：{redact(base)}")
    tail = [] if deep else ["", "加 `--deep` 会对已配置的服务各出一张低质量测试图；实际费用以服务商当前计价为准。"]
    return "\n".join(head + [""] + rows + tail)


def configure(provider: str, model: str | None = None, quality: str = "normal", test: bool = True) -> str:
    load_dotenv()
    if provider not in PROVIDERS:
        raise SystemExit(f"未知服务 {provider}；可选 {ORDER}")
    if not B.configured(provider):
        return (f"还没有 {PROVIDERS[provider]['zh']} 的密钥。请在 {env_template()} 里填入："
                + "；".join(ENV_HELP[provider]) + "。填好再运行一次本命令。")
    if test:
        ok, msg = _deep_test(provider)
        if not ok:
            return f"测试出图没通过，设置未保存。原因：{msg}"
    cfg = {"provider": provider, "model": model or PROVIDERS[provider]["default_model"], "quality": quality}
    f = B.save_config(cfg)
    return f"已保存设置到 {f}：{cfg}" + ("" if test else "（跳过了测试出图）")


def interactive() -> str:
    print(menu())
    if not sys.stdin.isatty():
        return ""
    choice = input("输入编号：").strip()
    if choice == "6":
        return "好的，出图时会走收件箱。"
    try:
        name = ORDER[int(choice) - 1]
    except (ValueError, IndexError):
        return "没有识别这个编号。"
    return configure(name)
