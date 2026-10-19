"""Optional, pinned lemo-wake runtime. Upstream code lives outside this MIT package."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads((ROOT / "references/wake-upstream.json").read_text(encoding="utf-8"))


class WakeError(ValueError):
    pass


def config_dir() -> Path:
    return Path(os.environ.get("SANSHENG_IMAGE_CONFIG_DIR", Path.home() / ".config/sansheng-image")).expanduser().resolve()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WakeError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WakeError(f"JSON 顶层须为对象：{path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(f.read()).hexdigest()


def verify(skill: Path) -> Path:
    skill = skill.expanduser().resolve()
    for rel, expected in LOCK["files"].items():
        p = skill / rel
        if p.is_symlink() or not p.is_file() or sha(p) != expected:
            raise WakeError(f"上游版本或文件不符：{rel}；需要 {LOCK['revision']}，不静默执行其他版本")
    return skill


def settings() -> dict:
    path = config_dir() / "motion-wake.json"
    data = read_json(path) if path.exists() else {}
    if data.get("enabled") is not True:
        raise WakeError("动态模块尚未启用；由 Agent 执行 sb.py animate wake setup --install，或 setup --wake-skill <已有目录>")
    if data.get("revision") != LOCK["revision"]:
        raise WakeError("动态运行环境版本已变化，请重新运行 animate wake setup")
    verify(Path(data["wake_skill"]))
    return data


def environment(config: dict) -> dict:
    env = os.environ.copy()
    env["LEMO_WAKE_MODELS"] = config["models_dir"]
    if config.get("chrome"):
        env["CHROME"] = config["chrome"]
    return env


def run(command: list[str], *, cwd: Path, log: Path, config: dict) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as f:
        result = subprocess.run(command, cwd=cwd, env=environment(config), stdout=f, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise WakeError(f"命令退出 {result.returncode}，保留日志：{log}")


def python_command(config: dict, name: str, *args) -> list[str]:
    return ["uv", "run", "--quiet", "--script", str(Path(config["wake_skill"]) / "scripts" / name), *map(str, args)]


def install_runtime() -> Path:
    """Fetch a fixed public source archive, never into a Skill discovery directory."""
    parent = config_dir() / "runtime/lemo-wake"
    dest = parent / LOCK["revision"]
    if dest.exists():
        return verify(dest / "skills/wake")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=parent) as temporary:
        staging = Path(temporary)
        archive = staging / "source.tar.gz"
        url = f"https://codeload.github.com/lemomo-ai/lemo-wake/tar.gz/{LOCK['revision']}"
        with urlopen(url, timeout=60) as response, archive.open("wb") as output:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 64 * 1024 * 1024:
                    raise WakeError("上游源码归档超过 64 MiB，停止安装")
                output.write(chunk)
        extracted = staging / "source"
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if not parts or ".." in parts or member.name.startswith("/"):
                    raise WakeError("上游归档包含不安全路径")
                rel = PurePosixPath(*parts[1:])
                if not rel.parts:
                    continue
                if not (str(rel).startswith("skills/wake/") or str(rel) in {"LICENSE", "README.md", "CITATION.cff"}):
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    raise WakeError("上游归档包含非常规文件")
                if member.size > 32 * 1024 * 1024:
                    raise WakeError("上游单文件过大")
                target = extracted.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        verify(extracted / "skills/wake")
        if not (extracted / "LICENSE").is_file():
            raise WakeError("上游归档缺少 LICENSE")
        # A concurrent installer may have completed first. Never overwrite it.
        if not dest.exists():
            extracted.rename(dest)
        return verify(dest / "skills/wake")


def setup(*, skill: Path | None = None, install: bool = False, models: Path | None = None,
          chrome: Path | None = None, node_deps: bool = False, disable: bool = False) -> dict:
    path = config_dir() / "motion-wake.json"
    if disable:
        write_json(path, {"enabled": False})
        return {"state": "disabled", "config": str(path)}
    if install == (skill is not None):
        raise WakeError("setup 须指定 --install 或 --wake-skill 其中之一")
    actual = install_runtime() if install else verify(skill)
    config = {"enabled": True, "revision": LOCK["revision"], "wake_skill": str(actual),
              "models_dir": str((models or config_dir() / "runtime/models/lemo-wake").expanduser().resolve()),
              "upstream_license": LOCK["license"], "license_url": LOCK["license_url"]}
    if chrome:
        if not chrome.expanduser().is_file():
            raise WakeError("--chrome 须指向实际浏览器可执行文件")
        config["chrome"] = str(chrome.expanduser().resolve())
    if install or node_deps:
        if not shutil.which("npm"):
            raise WakeError("缺少 npm，未启用动态模块")
        run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=actual / "scripts",
            log=config_dir() / "runtime/lemo-wake-npm.log", config=config)
    write_json(path, config)
    return {"state": "configured-needs-doctor", "config": str(path), **config,
            "next": "python3 scripts/sb.py animate wake doctor",
            "license_note": "上游独立保存，保留 CC BY-NC 4.0；外部调用不改变其许可条件。"}


def doctor() -> dict:
    config = settings()
    missing = [name for name in ("node", "uv", "ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise WakeError(f"缺少依赖：{', '.join(missing)}")
    result = subprocess.run(["node", str(Path(config["wake_skill"]) / "scripts/doctor.mjs")],
                            env=environment(config), capture_output=True, text=True, check=False)
    return {"state": "ready" if result.returncode == 0 else "missing-dependencies", "exit_code": result.returncode,
            "revision": LOCK["revision"], "report": result.stdout + result.stderr,
            "note": "doctor 不下载模型、不证明成片审美；完整制作仍需逐片审片。"}
