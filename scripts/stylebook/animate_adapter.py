"""叁笙生图动态入口：wake 为原图到动态流程，其余子命令兼容已有工程。

显式 `wake` 请求才延迟加载 wake_workflow，由该模块管理独立外部运行环境。
下面的 v2 协议与无网络/无写入约束指已有 HyperFrames 适配部分，不适用于 wake 制作流程。

v1 的缺口：适配器先 load_manifest 校验 scene，再交 pipeline.main()，main 内部又读一次 manifest；
两次读取之间磁盘文件可被换成 ppt / music，白名单被绕过。v2 的做法：

1. 用 pipeline 自己的 build_parser() 解析实参，未知参数、缺参数、非法取值由它拒绝；
2. 适配器不再自己读 manifest。它把“四场景白名单 + 禁用 preset”做成一个校验函数，
   通过 pipeline.main(argv, manifest_validator=...) 交给底座；底座在 load_manifest 之后、
   任何 plan / render / qa 之前调用它，校验的对象就是后面执行所用的同一个已解析 Manifest，全程只读一次；
3. 校验函数只检查、不返回新 Manifest、不重新解析；不通过就抛 ManifestError（PipelineError 子类，exit_code=2），
   由底座 main 统一打印并返回 2。

边界：
- 白名单只有 brand / article / infographic / xiaohongshu。底座本身还认 ppt / music，这里不对外开放；
  四场景 manifest 里也不接受 ppt-mp4-candidate / music-loop-mp4-candidate 两个 preset。
- 需要底座已打上 motion_pipeline.py.patch（main 带 keyword-only 的 manifest_validator）。
  缺少该参数时直接拒绝（退出码 2），不会退回“先读后转发”的旧流程。
- 不 import 任意路径、不起子进程、不碰网络、不写文件；默认模块是 stylebook.motion_pipeline，
  部署位置 scripts/stylebook/motion_pipeline.py。pipeline_module 参数只给调用方（含测试）注入已有模块对象。
- argv 只接受由字符串组成的 list / tuple；其他类型直接退出码 2。
- 另有只读的 `dialect --style <码>`：读取风格合同里的 motion_dialect 建议，同样在加载底座之前分流，
  不触碰下面四个子命令的 manifest 校验与白名单（stylebook.motion_dialects）。
- 另有 `assets --out <新目录>`：只把内置原创动作库复制出来（stylebook.motion_assets），在加载底座之前分流，
  不触碰上面四个子命令的 manifest 校验与白名单。
"""
from __future__ import annotations

import inspect
import sys
from typing import Sequence

SUPPORTED_SCENES = ("brand", "article", "infographic", "xiaohongshu")
SUBCOMMANDS = ("doctor", "plan", "render", "qa")
# 第五个入口 assets 不属于底座子命令：只把内置原创动作库复制到新目录，见 motion_assets.py
ASSETS_COMMAND = "assets"
# 第六个入口 dialect 同样不属于底座子命令：只读风格合同里的动作语言建议，见 motion_dialects.py
DIALECT_COMMAND = "dialect"
# 这两个 preset 属于 PPT / 音乐封面候选格式；四场景不开放，普通 PPT 与音乐封面仍出静态图
UNSUPPORTED_PRESETS = ("ppt-mp4-candidate", "music-loop-mp4-candidate")


def _load_default_pipeline():
    from stylebook import motion_pipeline
    return motion_pipeline


def _exit_code(code) -> int:
    if code is None:
        return 0
    return code if isinstance(code, int) else 2


def _fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return 2


def _accepts_validator(main_fn) -> bool:
    try:
        params = inspect.signature(main_fn).parameters
    except (TypeError, ValueError):
        return False
    if "manifest_validator" in params:
        return params["manifest_validator"].kind is inspect.Parameter.KEYWORD_ONLY
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def make_manifest_validator(pm):
    """返回交给 pipeline.main 的校验函数：只读已解析的 Manifest，通过返回 None，不通过抛 exit_code=2 的 PipelineError。"""

    def validate(manifest) -> None:
        if manifest.scene not in SUPPORTED_SCENES:
            raise pm.ManifestError(f"动态版只支持场景 {'/'.join(SUPPORTED_SCENES)}，当前 scene={manifest.scene!r}；"
                                   "普通 PPT 与音乐封面仍出静态图")
        blocked = [name for name in manifest.presets if name in UNSUPPORTED_PRESETS]
        if blocked:
            raise pm.ManifestError(f"四场景不开放 preset {blocked}，当前 scene={manifest.scene!r}（PPT / 音乐封面候选格式）")

    return validate


def main(argv: Sequence[str], *, pipeline_module=None) -> int:
    if not isinstance(argv, (list, tuple)):
        return _fail(f"argv 必须是字符串参数列表，不能是 {type(argv).__name__}")
    bad = [i for i, a in enumerate(argv) if not isinstance(a, str)]
    if bad:
        return _fail(f"argv 的每个元素都必须是字符串，第 {bad[0]} 个是 {type(argv[bad[0]]).__name__}")
    args_list = list(argv)
    if args_list and args_list[0] == "wake":
        # Full image-to-motion workflow is opt-in; static commands never import or configure it.
        from stylebook import wake_workflow
        return wake_workflow.main(args_list[1:])
    if args_list and args_list[0] == ASSETS_COMMAND:
        # 资源导出只复制包内动作库，不读 manifest、不加载渲染底座，也不经过四场景校验
        from stylebook import motion_assets
        return motion_assets.main(args_list[1:])
    if args_list and args_list[0] == DIALECT_COMMAND:
        # 画风动作语言只读合同，不读 manifest、不加载渲染底座，也不经过四场景校验
        from stylebook import motion_dialects
        return motion_dialects.main(args_list[1:])
    if pipeline_module is None:
        try:
            pipeline_module = _load_default_pipeline()
        except ImportError as exc:
            return _fail(f"找不到动态版渲染模块 stylebook.motion_pipeline（应部署在 scripts/stylebook/motion_pipeline.py）：{exc}")
    pm = pipeline_module
    try:
        args = pm.build_parser().parse_args(args_list)
    except SystemExit as exc:  # argparse 对未知参数 / 缺参数 / -h 的退出
        return _exit_code(exc.code)
    if args.cmd not in SUBCOMMANDS:
        return _fail(f"不支持的子命令 {args.cmd!r}，只有 {'/'.join(SUBCOMMANDS)}")
    if not _accepts_validator(pm.main):
        return _fail("底座 motion_pipeline.main 不支持 manifest_validator，请先应用 motion_pipeline.py.patch；"
                     "为避免先读后换绕过白名单，不退回旧流程")
    return pm.main(args_list, manifest_validator=make_manifest_validator(pm))
