# 原创动作库：导出到新工程并接进 HyperFrames

只在 [motion-scenes.md](motion-scenes.md) 的第 4～5 步需要“程序化的小动作”时读本页，例如光扫、粒子、弹簧、逐字、数字滚动。不用动作库也能做动态版，这一页不是必经步骤。

动作库是一个纯 ES module：`motion-primitives.mjs`，版本 `0.1.0`。它**只提供 `seek(t)`**：同一个 `t` 永远得到同一个画面，可以任意顺序、往回 seek；库里没有时钟、没有定时器、没有 `requestAnimationFrame`，什么时候 seek 完全由工程里的时间线决定。不联网、不动态求值、不往页面插 HTML 字符串。

## 1. 先把库复制出来（只是准备）

```sh
python3 scripts/sb.py animate assets --out ./motion-assets
```

- `--out` 必须是**尚不存在**的新目录，且它的上一级目录已经存在（不会帮你建多级目录）。目录、文件、符号链接（包括指向不存在位置的断链）已存在都会拒绝，不覆盖；落在 Skill 包内也会拒绝。
- 只接受 `--out`，其它参数一律退出码 2，不要往后面加 `--hyperframes-cli` 之类的参数。
- 成功后目录里有两个文件：`motion-primitives.mjs` 和 `motion-assets-receipt.json`；同样的 JSON（多一个 `out` 字段）也打印在标准输出：

```json
{"schema": "sansheng-image/motion-assets-receipt/v1", "version": "0.1.0", "sha256": "…",
 "files": [{"path": "motion-primitives.mjs", "sha256": "…", "bytes": 12345}]}
```

- 这一步不装依赖、不起外部进程、不联网、不加载渲染底座，**也不渲染、不验收**。它只是把文件放到你指定的位置，不能当成 `render` 或 `qa` 的结果。
- 复制到工程后可以用 receipt 里的 `sha256` 核对文件没被改过：`shasum -a 256 <工程>/motion-primitives.mjs`。

再把 `.mjs` 放进你**已有的**新 HyperFrames 工程（工程内的本地文件，不引用远程地址）：

```sh
cp -n ./motion-assets/motion-primitives.mjs ./motion-project/motion-primitives.mjs
```

旧工程不强行替换；已经渲染过的旧作品不会因为本库而失效或需要重做。

## 2. 通用约定（来自库的实际行为）

每个 `createXxx(...)` 返回同一种“动作对象”：

| 成员 | 含义 |
|---|---|
| `kind` | 名称，如 `'tween'`、`'lightSweep'` |
| `duration` | 秒；`createLoop` 为 `Infinity` |
| `seek(t)` | 把画面设为 `t` 秒时的状态，返回该状态对象；`t` 小于 0 取 0、大于 `duration` 取 `duration`；非有限数抛错 |
| `dispose()` | 还原它改过的样式、移除它插入的节点、释放占用；可重复调用 |
| `disposed` | 是否已 dispose；dispose 后再 `seek` 会抛错 |

- 出错一律抛 `MotionError`（`e.name === 'MotionError'`），消息是中文，说明哪个参数不合法；未知字段同样会抛错。
- 时间单位全是秒。所有可选项有默认值，下文括号里是默认值与范围。
- **占用**：同一元素的同一属性槽只能有一个动作占用——`transform`（含 `transform-origin`）、`opacity`、`clip`（`clip-path`）、`dash`（描线）、`text`（数字滚动）。冲突会抛错，请换元素或加一层包装。
- 缓动（`ease`）可写名称、函数或 `cubicBezier(...)` / `steps(n)` 的返回值。名称有：`linear quadIn quadOut quadInOut cubicIn cubicOut cubicInOut quartOut expoOut sineInOut smoothstep backOut backInOut elasticOut bounceOut`，端点精确为 0 和 1。

### 文字、人脸、产品保护

在要保护的元素（或它的外层容器）上写 `data-mp-protected`，或在调用时传 `protected: true`（只对该次调用的目标生效）。库按类型处理：

| 类型 | 包含 | 对受保护元素 |
|---|---|---|
| 形变类 | `createSquash`、`createFlip`，以及 `createKeyframes` / `createTween` / `createSpring` 用到 `scaleX`、`scaleY` 通道时 | 自身、祖先或后代任一受保护就**拒绝** |
| 覆盖类 | `createParticles`、`createLightSweep` | 在受保护后代（以及 `avoid` 里的元素）所在区域**挖洞避让**；容器或目标本身受保护则拒绝 |
| 遮空类 | `createMaskReveal`、`createNumberRoll`、`createPathDraw` | **拒绝**（会遮住或改写内容） |
| 装饰副本 | `createCharText` | 不动源文字，只在 `host` 里生成 aria 隐藏的副本；`host` 不得在受保护元素内 |

读这张表时要注意三点：

1. 避让区是**创建那一刻的布局快照**。创建后不要再移动、缩放受保护元素及其到容器之间的祖先（库会拒绝它们再占用 `transform`），也不要滚动容器、改字号或换行。字体和图片没加载完就创建，快照会是错的，所以要先等字体就绪（见第 4 节的例子）。
2. 库**不拦**位移、旋转、等比 `scale`、`opacity`：这些通道对文字、人脸、产品不会自动拒绝。文字不抖、不闪、不被盖住，要靠你不对它们用这些通道，并在人工验收里逐帧看（见 [motion-scenes.md](motion-scenes.md) 的人工验收项）。
3. 数字、专名、引语一律用代码字，不让模型在素材里写；`createNumberRoll` 的终值就是 `to`，请保证它是原文里的真实数字。

## 3. 十二类动作：函数、适用、限制

| # | 类别 | 实际函数 | 什么时候选 | 保护与限制 |
|---|---|---|---|---|
| 1 | 缓动 | `easings`、`cubicBezier(x1,y1,x2,y2)`、`steps(n)`、`resolveEase(e)` | 给其它动作配节奏 | 纯函数；`cubicBezier` 的 `x1/x2` 须在 0–1；`steps` 的 n 为 1–64 |
| 2 | 关键帧与补间 | `createKeyframes(target,{tracks,origin})`、`createTween(target,{channel,from,to,duration,ease,origin})`、纯函数 `keyframeValue(frames,t)` | 平移、淡入淡出、缩放、旋转 | 通道只有 `x y scale scaleX scaleY rotate opacity`；`scaleX/scaleY` 属形变类；通道取值超范围会被钳制 |
| 3 | 弹簧 | `createSpring(target,{channel,from,to,stiffness,damping,mass,velocity,duration})`，辅助 `springProgress`、`springSettleTime` | 弹入、回弹落位 | `from` 不能等于 `to`；不给 `duration` 时取弹簧收敛时间（最长 20 秒） |
| 4 | 挤压回弹 | `createSquash(target,{duration,amount,cycles,decay,origin})` | 无文字的物件落地、弹跳 | 形变类；保体积；**0 秒不是原样**（起点就是被挤压的状态），所以不要放进 `createTimeline` 里靠 `at` 延后开始，否则开始前一直停在被挤压的样子 |
| 5 | 粒子 | `createParticles(container,{count,seed,origin,angle,arc,speed,gravity,drag,life,emitWindow,size,spin,colors,shape,avoid,avoidPadding})` | 庆祝、星尘、气泡 | 覆盖类；`count` ≤ 120；`seed` 固定则每次一样；`origin` 是容器宽高的 0–1 比例；容器必须已布局出尺寸；`duration = emitWindow + life` |
| 6 | 光扫 | `createLightSweep(target,{duration,band,angle,color,ease,direction,avoid,avoidPadding})` | 卡片、徽章、产品图上扫过一道高光 | 覆盖类；目标不能是 `img/svg/canvas/video` 等替换元素或空元素，请用外层 `div`；`angle` 0–45 |
| 7 | 遮罩揭示 | `createMaskReveal(target,{mode,duration,ease,origin})`；`mode`：`left right down up center-x center-y circle` | 色块、背景形状、插画元素的出现 | 遮空类，**不能用于文字、人脸、产品**；起点是完全隐藏，所以不满足“第 0 帧完整”的元素不要用它 |
| 8 | 翻卡 | `createFlip(card,{front,back,axis,perspective,duration,ease})` | 正反两面的卡片 | 形变类；`front`、`back` 必须是 `card` 内两个不同的后代；翻卡全程文字在转，要显示可读文字请用人工逐帧确认 |
| 9 | 逐字文字 | `createCharText(source,{host,className,mode,stagger,charDuration,amplitude,ease,firstFrameReadable})`；`mode`：`wave pulse reveal` | 标题的装饰性逐字波动 | 只做装饰副本，源文字不改；文字 1–120 个字符；`reveal` 首帧不可读，必须显式 `firstFrameReadable:false`，默认请用 `wave` 或 `pulse`；源文字**不会被隐藏**，副本放哪里、是否重影由你的版式决定 |
| 10 | 数字滚动 | `createNumberRoll(target,{from,to,duration,ease,decimals,separator,prefix,suffix})` | 一个数字从 A 数到 B | 遮空类；目标只能含文本、不能有子元素；会改写目标的文字；结束时精确等于 `to`；`prefix/suffix` ≤ 12 个纯文本字符 |
| 11 | 路径描线 | `createPathDraw(target,{mode,segment,duration,ease,reverse})`；`mode`：`draw erase trail` | 连线、箭头、轮廓描画 | 遮空类；目标须是带 `getTotalLength` 的 SVG 图形元素；起点隐藏 |
| 12 | 首尾循环 | `createLoop(item,{period,mode})`、`createTimeline([{item,at}])`、纯函数 `loopTime`、`pingPongTime`、`closedKeyframes`、`crossfadeWeight`、`stateDistance`、`assertLoopClosed(item,period,tol)` | 无缝循环、把多个动作排进一条时间线 | `createLoop` 的 `period` 不得超过 `item.duration`；`mode` 为 `wrap` 或 `pingpong`；`assertLoopClosed` 会真的 seek，调用后请重新 seek 到你需要的时间 |

另有校验与辅助导出：`MotionError`、`VERSION`、`num`、`normalizeTime`、`isProtected`、`containsProtected`、`assertDeformable` 以及几个纯函数（`particleStates`、`charStates`、`rollValue` 等）。一般不需要直接用。

`createTimeline` 的行为（常被忽略）：每个条目按 `t - at` 钳制后 seek；**开始前停在该动作的首状态，结束后停在末状态**。所以放在 `at: 2.0` 的光扫在 0～2 秒是“光带不可见”的首状态，没问题；但放在 `at` 之后的挤压回弹在 0～2 秒是被挤压的样子，这就是第 4 行的提醒。

### 画风合同里的“十二类别”不是函数名

风格合同的 `motion_dialect.primitives` 用的是上表的十二个**建议类别**：`keyframes`、`easing`、`spring`、`squash`、`particles`、`light_sweep`、`mask_reveal`、`card_flip`、`char_text`、`number_roll`、`path_draw`、`loop`，与本表“类别”列一一对应，但它们不是库里的函数名，也不会被自动调用。查某个画风的动作语言和类别到函数名的对应：

```sh
python3 scripts/sb.py animate dialect --style C24
```

这条命令只读合同；用哪个函数、作用在哪个元素，仍按上面的保护与限制和文章内容自己写进工程（流程见 [motion-scenes.md](motion-scenes.md)）。

## 4. 接进 HyperFrames 工程：最小可运行连接例

### 先讲清楚异步

`<script type="module">` 里的 `import` 是异步加载的，页面脚本跑到时模块可能还没就绪；而渲染是“跳到某一秒、截一帧”的 seek。如果在模块还没准备好时就有 seek 进来，会出现两种坏结果之一：画面悄悄停在没有动作的状态，或者每次渲染有的帧动了有的没动。这是**跑时竞争**，在录制出来的片子里表现为随机的错帧。

库本身的 seek 是同步的，没有竞争；竞争只来自“模块加载”这一段。下面的连接例这样处理：

1. 用一个**经典脚本**同步注册 `window.__timelines[...]`，渲染器随时都能找到时间线；
2. 在模块就绪**之前**，任何 seek 都会**显式抛错**，不会悄悄渲出错误画面；
3. 模块里先 `await document.fonts.ready`，再创建动作（保证避让区测的是最终版式），全部成功后才把“就绪”打开，并 resolve `window.__motionReady`。

它保证的是“**要么渲对，要么报错**”，**不保证**渲染器一定会等 `window.__motionReady`。渲染器是否等待、何时第一次 seek，取决于 HyperFrames 的实际行为，本页不替它断言，请用试渲染实测：

- 若 `render` 因“动作库尚未就绪”失败，说明渲染器在模块就绪前就 seek 了，不要把抛错改成静默忽略；应改成底座支持的等待方式后再渲染；
- 渲染成功后，对同一 manifest 渲到两个新目录，核对非首帧的联系表一致（人工验收项 4：重复 seek 画面一致）；
- 动作库不是靠“多等一会儿”稳定的，不要用固定延时当就绪判据。

### `index.html`

工程已有的根 composition 节点、属性和 `window.__timelines` 的键名**沿用你工程里原来的写法**；下面的 `main`、`stage` 只是占位名。`data-width`、`data-height`、`data-duration` 必须与 manifest 的 `width`、`height`、`duration` 完全一致（本例 1280×720、6 秒）。`gsap.min.js` 是工程里**已经有**的本地副本，由你自己负责合法取得；本页不提供也不安装。

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; background: #fff; }
  #stage { position: relative; width: 1280px; height: 720px; overflow: hidden; }
  #card  { position: absolute; left: 240px; top: 200px; width: 800px; height: 240px;
           border-radius: 24px; background: #f4efe6; }
  #title { position: absolute; left: 48px; top: 72px; margin: 0; font: 700 64px/1.2 sans-serif; color: #2b2b2b; }
  #dot   { position: absolute; left: 616px; top: 520px; width: 48px; height: 48px;
           border-radius: 50%; background: #e0a458; }
</style>
</head>
<body>
<div id="stage" data-composition-id="main" data-width="1280" data-height="720" data-duration="6">
  <!-- 文字是普通代码字；写 data-mp-protected 后，光扫会在它所在区域挖洞避让 -->
  <div id="card"><h1 id="title" data-mp-protected>三步复盘法</h1></div>
  <div id="dot"></div>
</div>

<script src="./gsap.min.js"></script>

<!-- 经典脚本：同步执行，先把时间线注册好，并设好“未就绪就报错”的开关 -->
<script>
(function () {
  const DURATION = 6;                       // 与 data-duration、manifest.duration 一致
  const S = { duration: DURATION, ready: false };
  S.apply = function () { throw new Error('动作库尚未就绪：模块 motion-primitives.mjs 还没加载完就收到了 seek'); };
  S.whenReady = new Promise(function (resolve, reject) { S.resolve = resolve; S.reject = reject; });
  S.whenReady.catch(function () {});        // 失败信息见 S.error，避免未处理的 Promise 噪声
  window.__motion = S;
  window.__motionReady = S.whenReady;

  const tl = gsap.timeline({ paused: true });
  // 用属性 setter 驱动画面；seek 禁用回调时也有效，不依赖 onUpdate。
  const driver = {
    _t: 0,
    get t() { return this._t; },
    set t(value) { this._t = value; S.apply(value); },
  };
  // 创建时间线时先不求值，模块就绪前的实际 seek 仍明确报错。
  tl.fromTo(driver, { t: 0 }, {
    t: DURATION, duration: DURATION, ease: 'none', immediateRender: false,
  }, 0);
  window.__timelines = window.__timelines || {};
  window.__timelines['main'] = tl;
})();
</script>

<script type="module">
import * as MP from './motion-primitives.mjs';

const S = window.__motion;
try {
  await document.fonts.ready;               // 避让区是布局快照，必须等字体就绪后再创建

  const sweep = MP.createLightSweep(document.getElementById('card'), {
    duration: 1.2, band: 0.3, angle: 18, ease: 'cubicInOut',
  });
  const rise = MP.createSpring(document.getElementById('dot'), {
    channel: 'y', from: 0, to: -24, duration: 1.2,
  });
  const timeline = MP.createTimeline([
    { item: sweep, at: 0.5 },
    { item: rise, at: 2.0 },
  ]);
  if (timeline.duration > S.duration + 1e-6) {
    throw new Error('动作总时长 ' + timeline.duration + ' 秒超过 composition 的 ' + S.duration + ' 秒');
  }

  timeline.seek(0);                         // 先落到首状态；首帧本就是完整的一张图
  S.apply = function (t) { return timeline.seek(t); };
  S.ready = true;
  S.resolve();
} catch (e) {
  S.error = e;
  S.reject(e);
  throw e;
}
</script>
</body>
</html>
```

要点：

- 例中 `#title` 是受保护文字：光扫在它所在的矩形里留空；弹簧作用在无文字的小圆点上；没有对标题做位移、缩放或透明度。
- 动作只到约 3.2 秒，composition 是 6 秒：时间线结束后动作停在末状态，`S.duration` 校验保证动作不会超出 composition。
- 第 0 帧就是完整的一张图（光扫在开始前不可见，弹簧在 `from` 位置）。
- 对应 manifest（`project` 是相对 manifest 所在目录的子目录）：

```json
{"version": 1, "project": "motion-project", "scene": "article", "style_code": "C24",
 "width": 1280, "height": 720, "duration": 6, "fps": 30, "loop": false, "presets": ["preview-gif"]}
```

接着照 [motion-scenes.md](motion-scenes.md) 的流程：`plan` → `render` → `qa`，`plan` 和 `render` 指向同一个尚不存在的新输出目录，`qa` 指向已完成的渲染结果目录，改过 `index.html` 或 `.mjs` 就重新渲染到新目录，旧证据作废。

### 常见报错对照

| 报错（节选） | 原因 | 处理 |
|---|---|---|
| `…的 transform 已被 … 占用` | 同一元素同一槽被两个动作占用 | 换元素，或外面包一层 |
| `拒绝形变受保护元素` | 形变类动作碰到受保护的自身/祖先/后代 | 改作用在无文字的装饰元素上 |
| `会遮蔽内容，拒绝作用于受保护元素` | 遮罩、数字滚动、描线碰到受保护元素 | 不要对文字、人脸、产品使用 |
| `容器尺寸为 0` | 创建时还没布局出尺寸 | 等字体与图片就绪后再创建 |
| `容器有滚动偏移` | 覆盖层容器滚动过 | 先滚回 0，存活期间不要滚动 |
| `动作库尚未就绪` | 模块还没就绪就有 seek | 见上文“先讲清楚异步” |

## 5. 这一页没有证明什么

- 原库此前有 Node 44 项、浏览器 624 项、实渲染 46 项的验证记录，那是**库本身**的证据；它不等于你的新作品通过，也不是平台兼容结论。
- `animate assets` 只复制文件、写 receipt。新作品仍要走 `plan` → `render` → `qa`，`qa` 为 `auto-pass-needs-manual-review` 时必须人工审片；手机端试发（C5）未做，交付时写“待手机端试发”。
- 这里不对任何平台的体积、时长、比例、自动播放作承诺。
