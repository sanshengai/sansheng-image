/*
 * 叁笙原创动作库（0.1.0）
 *
 * 约定：
 * - 时间单位一律为秒；所有原语只暴露 seek(t)，同一 t 总得到同一结果，可任意顺序、往回 seek。
 * - 库内不自带时钟、不启动定时器，由调用方（HyperFrames 渲染器）决定何时 seek。
 * - 只作用于调用者显式传入的元素；目标不存在、参数越界一律抛 MotionError。
 * - 受保护元素（文字/人脸/产品）：opts.protected === true，或元素/祖先带 data-mp-protected 属性。
 *   形变类（挤压、翻转、非等比缩放）拒绝受保护元素；覆盖类（粒子、光扫）在受保护区域挖洞避让；
 *   遮空类（遮罩揭示、数字滚动、路径描线）直接拒绝。
 *   覆盖类的避让区是创建时的布局快照，不会随重排自动跟随；覆盖层存活期间，避让链路上的元素拒绝占用 transform，
 *   文档内的流内 numberRoll / charText 与覆盖层双向互斥（会让受保护元素重排；只放行不包含该容器的 absolute/fixed 布局孤岛内的目标），
 *   容器创建时不得有滚动偏移、存活期间不得滚动，也不得与另一覆盖层互相改变避让链路上 static 祖先的 position；
 *   容器及其祖先路径上的 absolute 节点，其直接父元素必须已定位（非 static），路径上不得有 fixed（否则包含块会被切换）。
 * - 逐字文字只生成 aria 隐藏的装饰副本，源文字不动。
 * - 本文件不含动态求值、HTML 字符串注入、网络访问。
 */

export const VERSION = '0.1.0';

export class MotionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'MotionError';
  }
}

function fail(message) {
  throw new MotionError(message);
}

/* ------------------------------------------------------------------ 校验工具 */

export function num(name, v, min, max) {
  if (typeof v !== 'number' || !Number.isFinite(v)) fail(`${name} 必须是有限数字`);
  if (v < min || v > max) fail(`${name} 超出范围 [${min}, ${max}]`);
  return v;
}

function numOr(name, v, def, min, max) {
  return v === undefined ? def : num(name, v, min, max);
}

function intOr(name, v, def, min, max) {
  const x = numOr(name, v, def, min, max);
  if (!Number.isInteger(x)) fail(`${name} 必须是整数`);
  return x;
}

function boolOr(name, v, def) {
  if (v === undefined) return def;
  if (typeof v !== 'boolean') fail(`${name} 必须是布尔值`);
  return v;
}

function oneOf(name, v, list, def) {
  if (v === undefined && def !== undefined) return def;
  if (!list.includes(v)) fail(`${name} 必须是 ${list.join(' / ')} 之一`);
  return v;
}

function plain(opts, keys, name = 'opts') {
  if (opts === undefined) return {};
  if (opts === null || typeof opts !== 'object' || Array.isArray(opts)) fail(`${name} 必须是对象`);
  for (const k of Object.keys(opts)) {
    if (!keys.includes(k)) fail(`${name} 含未知字段 ${k}`);
  }
  return opts;
}

function pick(opts, keys) {
  const out = {};
  for (const k of keys) if (opts[k] !== undefined) out[k] = opts[k];
  return out;
}

function pairOr(name, v, def, min, max) {
  if (v === undefined) return def;
  if (!Array.isArray(v) || v.length !== 2) fail(`${name} 必须是 [最小, 最大]`);
  const a = num(`${name}[0]`, v[0], min, max);
  const b = num(`${name}[1]`, v[1], min, max);
  if (a > b) fail(`${name} 的最小值不能大于最大值`);
  return [a, b];
}

const COLOR_RE = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,24}|rgba?\(\s*\d{1,3}(\.\d+)?%?\s*(,\s*\d{1,3}(\.\d+)?%?\s*){2}(,\s*(0|1|0?\.\d+)\s*)?\))$/;

function colorOr(name, v, def) {
  if (v === undefined) return def;
  if (typeof v !== 'string' || !COLOR_RE.test(v)) fail(`${name} 不是受支持的颜色写法`);
  return v;
}

const r4 = (v) => {
  const x = Math.round(v * 1e4) / 1e4;
  return x === 0 ? 0 : x;
};
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const lerp = (a, b, t) => a + (b - a) * t;

/** 时间归一：非有限数拒绝；小于 0 取 0；大于 duration 取 duration。 */
export function normalizeTime(t, duration) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  return t < 0 ? 0 : t > duration ? duration : t;
}

/* ------------------------------------------------------------------ 元素、占用、保护 */

function requireElement(el, name) {
  if (el === null || typeof el !== 'object' || el.nodeType !== 1 || el.style === null || typeof el.style !== 'object') {
    fail(`${name} 不存在或不是元素`);
  }
  return el;
}

const PROT_ATTR = 'data-mp-protected';
const PROT_SEL = `[${PROT_ATTR}]`;

/** 元素自身、或其祖先被标记为受保护。 */
export function isProtected(el, opts) {
  if (opts && opts.protected === true) return true;
  if (typeof el.hasAttribute === 'function' && el.hasAttribute(PROT_ATTR)) return true;
  if (typeof el.closest === 'function' && el.closest(PROT_SEL)) return true;
  return false;
}

/** 元素的后代中含受保护元素。 */
export function containsProtected(el) {
  return typeof el.querySelector === 'function' && el.querySelector(PROT_SEL) !== null;
}

/** 形变类原语的统一闸门：自身、祖先、后代任一受保护都拒绝。 */
export function assertDeformable(el, opts, what) {
  if (isProtected(el, opts) || containsProtected(el)) {
    fail(`${what} 拒绝形变受保护元素（文字/人脸/产品）`);
  }
}

function assertNotHidable(el, opts, what) {
  if (isProtected(el, opts) || containsProtected(el)) {
    fail(`${what} 会遮蔽内容，拒绝作用于受保护元素`);
  }
}

const claims = new WeakMap();
/** 覆盖层（粒子/光扫）的避让链路登记：节点 → 存活覆盖层引用数。链路上的元素不能再占用 transform。 */
const guards = new WeakMap();

/**
 * 同一元素的同一属性槽只能被一个原语占用；返回释放函数。
 * transform 槽含 transform 与 transform-origin；opacity 槽只含 opacity。
 * 要占用 transform 的元素若位于存活覆盖层的避让链路上，也拒绝（避让区是创建时的静态快照，不能被移动）。
 * 单次调用原子：任一槽冲突则一个都不登记。
 */
function claim(el, slots, owner) {
  let m = claims.get(el);
  if (!m) {
    m = new Map();
    claims.set(el, m);
  }
  for (const s of slots) {
    if (m.has(s)) fail(`元素的 ${s} 已被 ${m.get(s).owner} 占用，请改用不同元素或嵌套包装`);
  }
  if (slots.includes('transform') && (guards.get(el) ?? 0) > 0) {
    fail(`${owner} 拒绝占用 transform：该元素位于存活的粒子/光扫避让链路上（受保护或 avoid 元素及其到容器之间的祖先），移动或缩放会让避让区失效；请先 dispose 覆盖层`);
  }
  const token = { owner };
  for (const s of slots) m.set(s, token);
  return () => {
    for (const s of slots) if (m.get(s) === token) m.delete(s);
  };
}

/** 一次登记多个元素的占用；中途冲突则回滚已取得的占用后再抛错。 */
function claimMany(entries, owner) {
  const releases = [];
  try {
    for (const [el, slots] of entries) releases.push(claim(el, slots, owner));
  } catch (e) {
    for (const r of releases.reverse()) r();
    throw e;
  }
  return () => {
    for (const r of releases) r();
  };
}

/** 避让链路：受保护/avoid 节点自身，以及它们到容器（不含容器）之间的全部祖先。 */
function guardChain(container, nodes) {
  const chain = new Set();
  for (const n of nodes) {
    for (let a = n; a && a !== container; a = a.parentElement) chain.add(a);
  }
  return [...chain];
}

/** 只读检查：链路上若已有元素占用 transform，覆盖层拒绝创建。 */
function assertGuardable(chain, what) {
  for (const n of chain) {
    const m = claims.get(n);
    if (m && m.has('transform')) {
      fail(`${what} 拒绝创建：避让链路上的元素的 transform 已被 ${m.get('transform').owner} 占用，避让区会随之失效`);
    }
  }
}

/** 存活且有避让链路的覆盖层容器 → 引用数。numberRoll / charText 在其子树内改文字会让受保护元素重排，故互斥。 */
const overlayRoots = new WeakMap();

/** 计算样式 position；无样式环境（Node 桩）返回 ''，视为不可判定、不拦截。创建期读取，不逐帧调用。 */
function positionOf(el) {
  const view = el.ownerDocument && el.ownerDocument.defaultView;
  if (!view || typeof view.getComputedStyle !== 'function') return '';
  return view.getComputedStyle(el).position;
}

function isOutOfFlow(el) {
  const p = positionOf(el);
  return p === 'absolute' || p === 'fixed';
}

/** 布局孤岛：自身或最近的 absolute/fixed 祖先。孤岛内部的尺寸变化不影响孤岛外的布局；没有则返回 null（落在根文档流里）。 */
function layoutIsland(el) {
  for (let a = el; a; a = a.parentElement) if (isOutOfFlow(a)) return a;
  return null;
}

function docAll(el, selector) {
  const doc = el.ownerDocument;
  return doc && typeof doc.querySelectorAll === 'function' ? [...doc.querySelectorAll(selector)] : [];
}

const containsNode = (a, b) => a === b || (typeof a.contains === 'function' && a.contains(b));

/**
 * 文字改写 / 副本插入点 t 相对存活覆盖层容器 r 是否会让受保护元素重排：
 * - r 包含 t（或 t 就是 r）：只放行自身脱离文档流（absolute/fixed）且不在避让链路上的 t（onChain 为 t 是否在避让链路上）；
 * - 旁系（t 在 r 之外）：只放行布局孤岛存在且不包含 r 的 t；t 落在根文档流、或孤岛把 r 包在里面，
 *   t 变宽变窄会改变 r 的宽度（flex / grid / shrink-to-fit），拒绝。
 */
function reflowHazard(t, r, onChain) {
  if (containsNode(r, t)) return t === r || onChain || !isOutOfFlow(t);
  const island = layoutIsland(t);
  return island === null || containsNode(island, r);
}

/** 文档内存活且有避让链路的覆盖层容器，外加 el 自身祖先链上的（容器已脱离文档时仍有效）。 */
function liveOverlayRoots(el) {
  const roots = new Set();
  for (const layer of docAll(el, '[data-mp-decor="sweep"],[data-mp-decor="particles"]')) {
    const r = layer.parentElement;
    if (r && (overlayRoots.get(r) ?? 0) > 0) roots.add(r);
  }
  for (let a = el; a; a = a.parentElement) if ((overlayRoots.get(a) ?? 0) > 0) roots.add(a);
  return roots;
}

/**
 * numberRoll / charText 的创建前检查（正向）：对文档里每个存活覆盖层容器按 reflowHazard 判定，
 * 容器内放行脱流且不在避让链路上的目标；旁系只放行不包含该容器的布局孤岛内的目标。其余会让受保护元素重排移位，拒绝。
 */
function assertNoOverlayReflow(el, what) {
  const onChain = (guards.get(el) ?? 0) > 0;
  for (const r of liveOverlayRoots(el)) {
    if (reflowHazard(el, r, onChain)) {
      fail(`${what} 拒绝：存活的粒子/光扫容器与目标处于同一流内布局（目标在容器内且在文档流中、是避让节点/容器，或在容器外但不在不含该容器的 absolute/fixed 孤岛里），改写文字或插入副本会让受保护元素重排移位，避让区失效；请先 dispose 覆盖层，或把目标放进 position:absolute/fixed 的孤岛盒子（该盒子不得包含覆盖层容器）`);
    }
  }
}

/**
 * 容器与其祖先路径的包含块检查（只读，零 DOM 改动）：避让区是容器坐标里的快照，
 * 容器自身或祖先里的 absolute 盒子 W，其包含块是最近的已定位祖先；若 W 的直接父元素是 static，
 * 本库（另一覆盖层把它改成 relative）会切换 W 的包含块，W 的尺寸随之改变，受保护元素在容器坐标里移位。
 * 所以 absolute 节点的直接父元素必须已定位（非 static）；已定位的父元素被平移/等比缩放不改变包含块，不拦截。
 * 路径上出现 fixed 直接拒绝（包含块是视口，任何祖先加 transform 都会切换）。
 * 返回这些已定位父元素，调用方在创建成功后对其中被本库临时改成 relative 的取一份 posRefs 引用。
 */
function assertContainingBlockStable(el, what) {
  const parents = new Set();
  for (let a = el; a; a = a.parentElement) {
    const p = positionOf(a);
    if (p === 'fixed') {
      fail(`${what} 拒绝创建：容器或其祖先 <${String(a.localName)}> 是 position:fixed，祖先一旦出现 transform 其包含块会切换，避让区失效；请改用 position:absolute 并给其父元素写 position:relative`);
    }
    if (p === 'absolute') {
      const up = a.parentElement;
      if (!up) continue;
      if (positionOf(up) === 'static') {
        fail(`${what} 拒绝创建：容器路径上的 absolute 节点 <${String(a.localName)}> 的父元素 <${String(up.localName)}> 是 static，其被改成 relative 或加 transform 会切换包含块、改变尺寸，避让区失效；请给该父元素写 position:relative（使用已定位的父元素）`);
      }
      parents.add(up);
    }
  }
  return [...parents];
}

/**
 * 覆盖层创建前的只读检查（零 DOM 改动），返回需要保留 posRefs 引用的已定位父元素：
 * 容器不得有滚动偏移（洞是视觉坐标、图层是滚动原点，滚动后错位）；
 * 有避让链路时，链路节点不得是 position:fixed（R6），容器路径的包含块必须稳定（见 assertContainingBlockStable）；
 * 文档内不得有存活的、会与该容器同流重排的 numberRoll 目标或 charText 副本（按布局孤岛判定，见 reflowHazard）。
 */
function assertOverlayReady(el, chain, what) {
  if ((el.scrollLeft || 0) !== 0 || (el.scrollTop || 0) !== 0) {
    fail(`${what} 拒绝：容器有滚动偏移（scrollLeft/scrollTop 非 0），避让区会与图层错位；请先滚回 0，且覆盖层存活期间不得滚动容器`);
  }
  if (chain.length === 0) return [];
  for (const n of chain) {
    if (positionOf(n) === 'fixed') {
      fail(`${what} 拒绝创建：避让节点或其到容器之间的祖先 <${String(n.localName)}> 是 position:fixed，容器或其祖先一旦被本库写 transform 就会切换它的包含块，避让区失效；请改用 position:absolute`);
    }
  }
  const posParents = assertContainingBlockStable(el, what);
  const inChain = new Set(chain);
  const scan = new Set([el, ...el.querySelectorAll('*'), ...docAll(el, '*')]);
  for (const n of scan) {
    const m = claims.get(n);
    const tok = m && m.get('text');
    if (tok && tok.owner === 'numberRoll' && reflowHazard(n, el, inChain.has(n))) {
      fail(`${what} 拒绝创建：文档里有存活的流内 numberRoll 目标，其文字宽度随 seek 变化会让受保护元素移位；请先 dispose numberRoll，或把它放进不包含该容器的 position:absolute/fixed 孤岛盒子`);
    }
  }
  for (const c of docAll(el, '[data-mp-decor="chars"]')) {
    const host = c.parentElement;
    if (!host || reflowHazard(host, el, inChain.has(host))) {
      fail(`${what} 拒绝创建：文档里有存活的流内 charText 副本，其插入/移除会让受保护元素移位；请先 dispose charText，或把它的 host 放进不包含该容器的 position:absolute/fixed 孤岛盒子`);
    }
  }
  return posParents;
}

/**
 * 覆盖层创建成功后登记：链路节点 guards +1；链路节点与容器路径上已定位父元素中，已被别的覆盖层临时改成 relative 的，
 * 再取一份 posRefs 引用（保证本覆盖层存活期间它们不被还原为 static；引用到最后一个覆盖层 dispose 才放）；
 * 有链路时登记容器为 overlayRoots。返回幂等释放函数。
 */
function registerGuards(chain, root, posParents = []) {
  for (const n of chain) guards.set(n, (guards.get(n) ?? 0) + 1);
  const posReleases = [];
  for (const n of [...chain, ...posParents]) {
    const ref = posRefs.get(n);
    if (ref) {
      ref.count += 1;
      posReleases.push(releasePos(n, ref));
    }
  }
  if (chain.length > 0) overlayRoots.set(root, (overlayRoots.get(root) ?? 0) + 1);
  let done = false;
  return () => {
    if (done) return;
    done = true;
    for (const n of chain) {
      const c = (guards.get(n) ?? 0) - 1;
      if (c > 0) guards.set(n, c);
      else guards.delete(n);
    }
    if (chain.length > 0) {
      const r = (overlayRoots.get(root) ?? 0) - 1;
      if (r > 0) overlayRoots.set(root, r);
      else overlayRoots.delete(root);
    }
    for (const r of posReleases) r();
  };
}

function saveProps(el, props) {
  const hadStyleAttr = typeof el.hasAttribute === 'function' ? el.hasAttribute('style') : true;
  const saved = props.map((p) => [p, el.style.getPropertyValue(p)]);
  return () => {
    for (const [p, v] of saved) {
      if (v === '') el.style.removeProperty(p);
      else el.style.setProperty(p, v);
    }
    // 原本没有 style 属性、还原后只剩空壳时一并去掉，避免留下 style=""
    if (!hadStyleAttr && typeof el.getAttribute === 'function' && typeof el.removeAttribute === 'function' && el.getAttribute('style') === '') {
      el.removeAttribute('style');
    }
  };
}

function setProp(el, prop, value) {
  el.style.setProperty(prop, String(value));
}

function makeItem(kind, duration, apply, cleanups) {
  let disposed = false;
  return {
    kind,
    duration,
    seek(t) {
      if (disposed) fail(`${kind} 已 dispose`);
      return apply(normalizeTime(t, duration));
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const c of cleanups) c();
    },
    get disposed() {
      return disposed;
    },
  };
}

/* ------------------------------------------------------------------ 1/2 缓动与关键帧 */

const E = (f) => (x) => (x <= 0 ? 0 : x >= 1 ? 1 : f(x));
const C1 = 1.70158;
const C3 = C1 + 1;
const C2 = C1 * 1.525;
const bounceRaw = (x) => {
  const n1 = 7.5625;
  const d1 = 2.75;
  if (x < 1 / d1) return n1 * x * x;
  if (x < 2 / d1) {
    x -= 1.5 / d1;
    return n1 * x * x + 0.75;
  }
  if (x < 2.5 / d1) {
    x -= 2.25 / d1;
    return n1 * x * x + 0.9375;
  }
  x -= 2.625 / d1;
  return n1 * x * x + 0.984375;
};

/** 命名缓动，端点精确为 0 与 1。 */
export const easings = Object.freeze({
  linear: E((x) => x),
  quadIn: E((x) => x * x),
  quadOut: E((x) => 1 - (1 - x) * (1 - x)),
  quadInOut: E((x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2)),
  cubicIn: E((x) => x * x * x),
  cubicOut: E((x) => 1 - Math.pow(1 - x, 3)),
  cubicInOut: E((x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)),
  quartOut: E((x) => 1 - Math.pow(1 - x, 4)),
  expoOut: E((x) => (1 - Math.pow(2, -10 * x)) / (1 - Math.pow(2, -10))),
  sineInOut: E((x) => -(Math.cos(Math.PI * x) - 1) / 2),
  smoothstep: E((x) => x * x * (3 - 2 * x)),
  backOut: E((x) => 1 + C3 * Math.pow(x - 1, 3) + C1 * Math.pow(x - 1, 2)),
  backInOut: E((x) =>
    x < 0.5
      ? (Math.pow(2 * x, 2) * ((C2 + 1) * 2 * x - C2)) / 2
      : (Math.pow(2 * x - 2, 2) * ((C2 + 1) * (2 * x - 2) + C2) + 2) / 2,
  ),
  elasticOut: E((x) => Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1),
  bounceOut: E(bounceRaw),
});

/** 三次贝塞尔缓动（同 CSS cubic-bezier，x1/x2 须在 [0,1]）。 */
export function cubicBezier(x1, y1, x2, y2) {
  num('x1', x1, 0, 1);
  num('x2', x2, 0, 1);
  num('y1', y1, -3, 4);
  num('y2', y2, -3, 4);
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sx = (s) => ((ax * s + bx) * s + cx) * s;
  const sy = (s) => ((ay * s + by) * s + cy) * s;
  const dx = (s) => (3 * ax * s + 2 * bx) * s + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let s = x;
    for (let i = 0; i < 8; i++) {
      const e = sx(s) - x;
      if (Math.abs(e) < 1e-7) return sy(s);
      const d = dx(s);
      if (Math.abs(d) < 1e-6) break;
      s -= e / d;
    }
    let lo = 0;
    let hi = 1;
    s = x;
    for (let i = 0; i < 50; i++) {
      const v = sx(s);
      if (Math.abs(v - x) < 1e-7) break;
      if (v < x) lo = s;
      else hi = s;
      s = (lo + hi) / 2;
    }
    return sy(s);
  };
}

/** 阶梯缓动（末端跳变）。 */
export function steps(n) {
  if (!Number.isInteger(n) || n < 1 || n > 64) fail('steps 的 n 必须是 1..64 的整数');
  return (x) => (x <= 0 ? 0 : x >= 1 ? 1 : Math.floor(x * n) / n);
}

/** 缓动解析：名称 / 函数 / 缺省线性。自定义函数的端点会被强制为 0 与 1。 */
export function resolveEase(e, name = 'ease') {
  if (e === undefined) return easings.linear;
  if (typeof e === 'string') {
    if (!Object.hasOwn(easings, e)) fail(`${name} 未知缓动 ${e}`);
    return easings[e];
  }
  if (typeof e === 'function') {
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      const v = e(x);
      if (typeof v !== 'number' || !Number.isFinite(v)) fail(`${name} 返回了非有限值`);
      return v;
    };
  }
  return fail(`${name} 必须是缓动名称或函数`);
}

function normalizeFrames(frames, name, range, scalar) {
  if (!Array.isArray(frames) || frames.length < 1 || frames.length > 64) fail(`${name} 必须是 1..64 个关键帧`);
  let prev = -Infinity;
  let dim = null;
  return frames.map((f, i) => {
    if (f === null || typeof f !== 'object') fail(`${name}[${i}] 必须是对象`);
    plain(f, ['t', 'v', 'ease'], `${name}[${i}]`);
    num(`${name}[${i}].t`, f.t, 0, 3600);
    if (f.t <= prev) fail(`${name} 的 t 必须严格递增`);
    prev = f.t;
    const [lo, hi] = range || [-1e6, 1e6];
    let v;
    if (Array.isArray(f.v)) {
      if (scalar) fail(`${name}[${i}].v 必须是数字`);
      if (f.v.length < 1 || f.v.length > 4) fail(`${name}[${i}].v 长度须为 1..4`);
      if (dim !== null && dim !== f.v.length) fail(`${name} 各帧 v 的维度必须一致`);
      dim = f.v.length;
      v = f.v.map((x, j) => num(`${name}[${i}].v[${j}]`, x, lo, hi));
    } else {
      if (dim !== null && dim !== 0) fail(`${name} 各帧 v 的维度必须一致`);
      dim = 0;
      v = num(`${name}[${i}].v`, f.v, lo, hi);
    }
    return { t: f.t, v, ease: resolveEase(f.ease, `${name}[${i}].ease`) };
  });
}

function evalFrames(frames, t) {
  const n = frames.length;
  const first = frames[0];
  const last = frames[n - 1];
  if (t <= first.t) return Array.isArray(first.v) ? first.v.slice() : first.v;
  if (t >= last.t) return Array.isArray(last.v) ? last.v.slice() : last.v;
  let i = 0;
  while (i < n - 2 && frames[i + 1].t <= t) i++;
  const a = frames[i];
  const b = frames[i + 1];
  const e = a.ease((t - a.t) / (b.t - a.t));
  return Array.isArray(a.v) ? a.v.map((x, j) => lerp(x, b.v[j], e)) : lerp(a.v, b.v, e);
}

/** 纯函数：关键帧在 t 秒的值（数字或定长数组）；段缓动由起点帧的 ease 决定。 */
export function keyframeValue(frames, t) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  return evalFrames(normalizeFrames(frames, 'frames'), t);
}

const RANGES = Object.freeze({
  x: [-1e5, 1e5],
  y: [-1e5, 1e5],
  scale: [0.01, 100],
  scaleX: [0.01, 100],
  scaleY: [0.01, 100],
  rotate: [-3600, 3600],
  opacity: [0, 1],
});
const TRANSFORM_CH = new Set(['x', 'y', 'scale', 'scaleX', 'scaleY', 'rotate']);
const ORIGINS = ['center', 'center bottom', 'center top', 'left center', 'right center', 'left top', 'right top', 'left bottom', 'right bottom'];

function transformString(s) {
  const parts = [];
  if (s.x !== undefined || s.y !== undefined) parts.push(`translate(${r4(s.x ?? 0)}px, ${r4(s.y ?? 0)}px)`);
  if (s.rotate !== undefined) parts.push(`rotate(${r4(s.rotate)}deg)`);
  if (s.scale !== undefined || s.scaleX !== undefined || s.scaleY !== undefined) {
    const k = s.scale ?? 1;
    parts.push(`scale(${r4(k * (s.scaleX ?? 1))}, ${r4(k * (s.scaleY ?? 1))})`);
  }
  return parts.join(' ');
}

function applyChannels(el, s) {
  if (Object.keys(s).some((k) => TRANSFORM_CH.has(k))) setProp(el, 'transform', transformString(s));
  if (s.opacity !== undefined) setProp(el, 'opacity', r4(s.opacity));
}

function slotsFor(chans) {
  const slots = [];
  if (chans.some((c) => TRANSFORM_CH.has(c))) slots.push('transform');
  if (chans.includes('opacity')) slots.push('opacity');
  return slots;
}

/** 通道类原语的公共构造：校验 → 占用 → 设置原点 → 生成 item。 */
function buildChannelItem(kind, el, opts, chans, duration, sample) {
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  if (chans.includes('scaleX') || chans.includes('scaleY')) assertDeformable(el, opts, kind);
  const origin = opts.origin === undefined ? undefined : oneOf('origin', opts.origin, ORIGINS);
  const slots = slotsFor(chans);
  if (origin && !slots.includes('transform')) fail(`${kind} 的 origin 只用于 transform 类通道；仅 opacity 时不占用 transform-origin`);
  const cleanups = [claim(el, slots, kind)];
  // 只保存/还原本原语占用的槽对应的属性，dispose 不会改写别的原语正占用的属性
  const owned = [];
  if (slots.includes('transform')) owned.push('transform');
  if (origin) owned.push('transform-origin');
  if (slots.includes('opacity')) owned.push('opacity');
  cleanups.push(saveProps(el, owned));
  if (origin) setProp(el, 'transform-origin', origin);
  return makeItem(kind, duration, (t) => {
    const state = sample(t);
    // 缓动过冲/自定义缓动不得越过通道范围（例如 scale 变负而镜像）
    for (const ch of Object.keys(state)) {
      const [lo, hi] = RANGES[ch];
      state[ch] = r4(Math.min(hi, Math.max(lo, state[ch])));
    }
    applyChannels(el, state);
    return state;
  }, cleanups.reverse());
}

/** 多通道关键帧：tracks = { x:[{t,v,ease?}], opacity:[...], ... }。 */
export function createKeyframes(target, opts) {
  const el = requireElement(target, 'target');
  opts = plain(opts, ['tracks', 'protected', 'origin']);
  const tracks = opts.tracks;
  if (tracks === null || typeof tracks !== 'object' || Array.isArray(tracks)) fail('tracks 必须是对象');
  const chans = Object.keys(tracks);
  if (chans.length === 0) fail('tracks 不能为空');
  const nf = {};
  let duration = 0;
  for (const ch of chans) {
    if (!Object.hasOwn(RANGES, ch)) fail(`tracks 含未知通道 ${ch}`);
    nf[ch] = normalizeFrames(tracks[ch], `tracks.${ch}`, RANGES[ch], true);
    duration = Math.max(duration, nf[ch][nf[ch].length - 1].t);
  }
  return buildChannelItem('keyframes', el, opts, chans, duration, (t) => {
    const s = {};
    for (const ch of chans) s[ch] = evalFrames(nf[ch], t);
    return s;
  });
}

/** 单通道缓动补间：from → to，缓动可为名称、函数或 cubicBezier/steps 的返回值。 */
export function createTween(target, opts) {
  const el = requireElement(target, 'target');
  opts = plain(opts, ['channel', 'from', 'to', 'duration', 'ease', 'protected', 'origin']);
  const channel = oneOf('channel', opts.channel, Object.keys(RANGES));
  const [lo, hi] = RANGES[channel];
  const from = num('from', opts.from, lo, hi);
  const to = num('to', opts.to, lo, hi);
  const duration = numOr('duration', opts.duration, 1, 0.01, 3600);
  const ease = resolveEase(opts.ease);
  return buildChannelItem('tween', el, opts, [channel], duration, (t) => ({
    [channel]: lerp(from, to, ease(t / duration)),
  }));
}

/* ------------------------------------------------------------------ 3 弹簧 */

export function springConfig(o) {
  o = plain(o, ['stiffness', 'damping', 'mass', 'velocity']);
  return {
    stiffness: numOr('stiffness', o.stiffness, 170, 1, 5000),
    damping: numOr('damping', o.damping, 26, 0, 500),
    mass: numOr('mass', o.mass, 1, 0.01, 100),
    velocity: numOr('velocity', o.velocity, 0, -1e4, 1e4),
  };
}

function springEval(t, c) {
  if (t <= 0) return 0;
  const w0 = Math.sqrt(c.stiffness / c.mass);
  const z = c.damping / (2 * Math.sqrt(c.stiffness * c.mass));
  const v0 = c.velocity;
  let e;
  if (Math.abs(z - 1) < 1e-9) {
    e = (-1 + (v0 - w0) * t) * Math.exp(-w0 * t);
  } else if (z < 1) {
    const wd = w0 * Math.sqrt(1 - z * z);
    e = Math.exp(-z * w0 * t) * (-Math.cos(wd * t) + ((v0 - z * w0) / wd) * Math.sin(wd * t));
  } else {
    const s = Math.sqrt(z * z - 1);
    const r1 = -w0 * (z - s);
    const r2 = -w0 * (z + s);
    const k2 = (v0 + r1) / (r2 - r1);
    const k1 = -1 - k2;
    e = k1 * Math.exp(r1 * t) + k2 * Math.exp(r2 * t);
  }
  return 1 + e;
}

/** 阻尼弹簧的解析解：0 → 1，初速度 velocity 为归一化单位/秒。 */
export function springProgress(t, cfg) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  return springEval(t, springConfig(cfg));
}

/** 弹簧收敛到 ±eps 之内且不再越界的时间（最长扫描 20 秒）。 */
export function springSettleTime(cfg, eps = 0.001) {
  const c = springConfig(cfg);
  num('eps', eps, 1e-6, 0.5);
  const dt = 1 / 240;
  let last = 0;
  for (let t = 0; t <= 20; t += dt) {
    if (Math.abs(springEval(t, c) - 1) > eps) last = t + dt;
  }
  return Math.min(20, last);
}

/** 弹簧驱动单个通道：from + (to - from) * 弹簧进度。velocity 为通道单位/秒。 */
export function createSpring(target, opts) {
  const el = requireElement(target, 'target');
  opts = plain(opts, ['channel', 'from', 'to', 'stiffness', 'damping', 'mass', 'velocity', 'duration', 'protected', 'origin']);
  const channel = oneOf('channel', opts.channel, Object.keys(RANGES));
  const [lo, hi] = RANGES[channel];
  const from = num('from', opts.from, lo, hi);
  const to = num('to', opts.to, lo, hi);
  if (from === to) fail('from 与 to 不能相等');
  const rawV = numOr('velocity', opts.velocity, 0, -1e5, 1e5);
  const cfg = springConfig({ ...pick(opts, ['stiffness', 'damping', 'mass']), velocity: rawV / (to - from) });
  const duration = numOr('duration', opts.duration, undefined, 0.01, 3600) ?? Math.max(0.01, springSettleTime(cfg));
  return buildChannelItem('spring', el, opts, [channel], duration, (t) => ({
    [channel]: Math.min(hi, Math.max(lo, from + (to - from) * springEval(t, cfg))),
  }));
}

/* ------------------------------------------------------------------ 4 挤压回弹 */

export function squashConfig(o) {
  o = plain(o, ['duration', 'amount', 'cycles', 'decay', 'origin', 'protected']);
  boolOr('protected', o.protected, false);
  return {
    duration: numOr('duration', o.duration, 0.8, 0.05, 10),
    amount: numOr('amount', o.amount, 0.35, 0, 0.6),
    cycles: numOr('cycles', o.cycles, 2.5, 0.5, 8),
    decay: numOr('decay', o.decay, 3, 0, 10),
    origin: oneOf('origin', o.origin, ORIGINS, 'center bottom'),
  };
}

/** 保体积的挤压回弹：sy = 1 - s，sx = 1 / sy；p = 1 时精确回到 1。 */
export function squashProfile(t, cfg) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  const p = clamp01(t / cfg.duration);
  const env = Math.exp(-cfg.decay * p) * (1 - p);
  const s = cfg.amount * env * Math.cos(2 * Math.PI * cfg.cycles * p);
  const sy = 1 - s;
  return { sx: 1 / sy, sy };
}

export function createSquash(target, opts) {
  const el = requireElement(target, 'target');
  const cfg = squashConfig(opts);
  assertDeformable(el, opts, 'squash');
  const cleanups = [claim(el, ['transform'], 'squash'), saveProps(el, ['transform', 'transform-origin'])];
  setProp(el, 'transform-origin', cfg.origin);
  return makeItem('squash', cfg.duration, (t) => {
    const { sx, sy } = squashProfile(t, cfg);
    const state = { sx: r4(sx), sy: r4(sy) };
    setProp(el, 'transform', `scale(${state.sx}, ${state.sy})`);
    return state;
  }, cleanups.reverse());
}

/* ------------------------------------------------------------------ 5 粒子 */

function hash01(seed, i, k) {
  let h = (seed ^ Math.imul(i + 1, 0x9e3779b1) ^ Math.imul(k + 1, 0x85ebca6b)) >>> 0;
  h = Math.imul(h ^ (h >>> 16), 0x7feb352d);
  h = Math.imul(h ^ (h >>> 15), 0x846ca68b);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** 粒子配置（origin 为容器内像素坐标）。 */
export function particleConfig(o) {
  o = plain(o, ['count', 'seed', 'origin', 'angle', 'arc', 'speed', 'gravity', 'drag', 'life', 'emitWindow', 'size', 'spin', 'colors', 'shape']);
  const origin = o.origin === undefined ? { x: 0, y: 0 } : plain(o.origin, ['x', 'y'], 'origin');
  const colors = o.colors === undefined ? ['#ffffff'] : o.colors;
  if (!Array.isArray(colors) || colors.length < 1 || colors.length > 8) fail('colors 必须是 1..8 个颜色');
  return {
    count: intOr('count', o.count, 24, 1, 120),
    seed: intOr('seed', o.seed, 1, 0, 2147483647),
    origin: { x: numOr('origin.x', origin.x, 0, -1e5, 1e5), y: numOr('origin.y', origin.y, 0, -1e5, 1e5) },
    angle: numOr('angle', o.angle, -90, -360, 360),
    arc: numOr('arc', o.arc, 360, 0, 360),
    speed: pairOr('speed', o.speed, [60, 180], 0, 5000),
    gravity: numOr('gravity', o.gravity, 0, -5000, 5000),
    drag: numOr('drag', o.drag, 0, 0, 20),
    life: numOr('life', o.life, 1, 0.05, 10),
    emitWindow: numOr('emitWindow', o.emitWindow, 0, 0, 10),
    size: pairOr('size', o.size, [3, 6], 1, 64),
    spin: numOr('spin', o.spin, 0, -3600, 3600),
    colors: colors.map((c, i) => colorOr(`colors[${i}]`, c)),
    shape: oneOf('shape', o.shape, ['circle', 'square'], 'circle'),
  };
}

/** 第 i 个粒子的出生时间与初速度，完全由 seed 与 i 决定。 */
export function particleLaunch(i, cfg) {
  const a = ((cfg.angle + (hash01(cfg.seed, i, 1) - 0.5) * cfg.arc) * Math.PI) / 180;
  const speed = lerp(cfg.speed[0], cfg.speed[1], hash01(cfg.seed, i, 2));
  return {
    birth: cfg.emitWindow * (i / cfg.count),
    vx: Math.cos(a) * speed,
    vy: Math.sin(a) * speed,
    size: lerp(cfg.size[0], cfg.size[1], hash01(cfg.seed, i, 3)),
    rot0: hash01(cfg.seed, i, 4) * 360,
    spin: (hash01(cfg.seed, i, 5) * 2 - 1) * cfg.spin,
    color: Math.min(cfg.colors.length - 1, Math.floor(hash01(cfg.seed, i, 6) * cfg.colors.length)),
  };
}

/** 纯函数：t 秒时全部粒子状态（带阻力的匀加速运动闭式解）。 */
export function particleStates(t, cfg) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  const out = [];
  const k = cfg.drag;
  const g = cfg.gravity;
  for (let i = 0; i < cfg.count; i++) {
    const L = particleLaunch(i, cfg);
    const age = t - L.birth;
    const alive = age >= 0 && age <= cfg.life;
    const a = clamp01(age / cfg.life) * cfg.life;
    let x;
    let y;
    if (k < 1e-6) {
      x = cfg.origin.x + L.vx * a;
      y = cfg.origin.y + L.vy * a + 0.5 * g * a * a;
    } else {
      const f = -Math.expm1(-k * a) / k;
      x = cfg.origin.x + L.vx * f;
      y = cfg.origin.y + (g / k) * a + (L.vy - g / k) * f;
    }
    const u = a / cfg.life;
    out.push({
      alive,
      x,
      y,
      alpha: alive ? Math.min(1, age / 0.06) * (1 - u) : 0,
      scale: 1 - 0.4 * u,
      rotate: L.rot0 + L.spin * a,
      size: L.size,
    });
  }
  return out;
}

function measureBox(el) {
  const w = el.clientWidth;
  const h = el.clientHeight;
  if (!(w > 0) || !(h > 0)) fail('容器尺寸为 0（需在布局完成后创建，且必须是 HTML 元素）');
  return { w, h };
}

const posRefs = new WeakMap();

function releasePos(el, ref) {
  let done = false;
  return () => {
    if (done) return;
    done = true;
    ref.count -= 1;
    if (ref.count === 0) {
      posRefs.delete(el);
      ref.restore();
    }
  };
}

/**
 * static 容器改成 relative（会让其绝对定位后代换包含块，调用方须知）。
 * 引用计数：同一容器上多个覆盖层共用，最后一个释放时才还原 position。
 */
function ensurePositioned(el) {
  const shared = posRefs.get(el);
  if (shared) {
    shared.count += 1;
    return releasePos(el, shared);
  }
  const view = el.ownerDocument && el.ownerDocument.defaultView;
  if (view && view.getComputedStyle(el).position === 'static') {
    if ((guards.get(el) ?? 0) > 0) {
      fail('覆盖层拒绝创建：容器是另一个存活粒子/光扫避让链路上的 static 祖先，改成 relative 会让其中绝对定位的受保护元素换包含块移位；请先 dispose 那个覆盖层，或自备已定位的容器');
    }
    const ref ={ count: 1, restore: saveProps(el, ['position']) };
    setProp(el, 'position', 'relative');
    posRefs.set(el, ref);
    return releasePos(el, ref);
  }
  return () => {};
}

function collectAvoid(el, avoid) {
  if (avoid === undefined) return [];
  if (!Array.isArray(avoid) || avoid.length > 16) fail('avoid 必须是不超过 16 个元素的数组');
  return avoid.map((n, i) => {
    requireElement(n, `avoid[${i}]`);
    if (n === el || !el.contains(n)) fail(`avoid[${i}] 必须是容器内部的元素`);
    return n;
  });
}

const rectsOverlap = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/**
 * 洞矩形合并：包含/重复/相交的洞反复合并成外接矩形，直到两两不相交（保守，多挖不少挖）。
 * evenodd 下重叠区域穿越数为奇数会重新变成可见，所以拼路径前必须先合并。仅边相接不算相交。
 */
export function mergeHoles(list) {
  const hs = list.map((h) => ({ x0: h.x0, y0: h.y0, x1: h.x1, y1: h.y1 }));
  let changed = true;
  while (changed) {
    changed = false;
    for (let i = 0; i < hs.length && !changed; i++) {
      for (let j = i + 1; j < hs.length; j++) {
        if (rectsOverlap(hs[i], hs[j])) {
          const a = hs[i];
          const b = hs[j];
          hs[i] = { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) };
          hs.splice(j, 1);
          changed = true;
          break;
        }
      }
    }
  }
  return hs;
}

/** 粒子旋转缩放后的轴对齐外框半边长（与浏览器 getBoundingClientRect 对变换后方块的外框一致）。 */
export function particleHalfExtent(size, scale, rotateDeg) {
  const a = (rotateDeg * Math.PI) / 180;
  return ((size * scale) / 2) * (Math.abs(Math.cos(a)) + Math.abs(Math.sin(a)));
}

/** 以 (x, y) 为中心、半边长 ext 的外框是否与洞相交（含相接）。 */
export function holeHit(h, x, y, ext) {
  return x + ext >= h.x0 && x - ext <= h.x1 && y + ext >= h.y0 && y - ext <= h.y1;
}

/**
 * 受保护后代 + 显式 avoid 元素 → 容器内（padding box）坐标系的避让矩形，创建时的静态快照。
 * 缩放系数 = 屏幕外框 / border-box 布局尺寸（offsetWidth 不受 transform 影响），再减去边框（clientLeft/Top）。
 * 只对平移+缩放的舞台成立，旋转/斜切舞台的轴对齐外框只是近似。
 */
function measureHoles(el, extra, pad, box) {
  const cr = el.getBoundingClientRect();
  const kx = el.offsetWidth > 0 && cr.width > 0 ? cr.width / el.offsetWidth : 1;
  const ky = el.offsetHeight > 0 && cr.height > 0 ? cr.height / el.offsetHeight : 1;
  const nodes = [...new Set([...el.querySelectorAll(PROT_SEL), ...extra])];
  const holes = [];
  for (const n of nodes) {
    const r = n.getBoundingClientRect();
    const x0 = Math.max(0, (r.left - cr.left) / kx - el.clientLeft - pad);
    const y0 = Math.max(0, (r.top - cr.top) / ky - el.clientTop - pad);
    const x1 = Math.min(box.w, (r.right - cr.left) / kx - el.clientLeft + pad);
    const y1 = Math.min(box.h, (r.bottom - cr.top) / ky - el.clientTop + pad);
    if (x1 > x0 && y1 > y0) holes.push({ x0, y0, x1, y1 });
  }
  return mergeHoles(holes);
}

function overlayLayer(doc, kind) {
  const layer = doc.createElement('div');
  layer.setAttribute('aria-hidden', 'true');
  layer.setAttribute('data-mp-decor', kind);
  for (const [p, v] of [['position', 'absolute'], ['left', '0'], ['top', '0'], ['right', '0'], ['bottom', '0'], ['overflow', 'hidden'], ['pointer-events', 'none'], ['border-radius', 'inherit']]) {
    layer.style.setProperty(p, v);
  }
  return layer;
}

/**
 * 粒子：在 container 内生成不超过 120 个装饰粒子（aria 隐藏、不接收指针）。
 * origin 为容器宽高的 0..1 比例；受保护后代与 avoid 元素所在区域按「粒子旋转缩放后的外框」避让，外框碰到洞即隐藏。
 * 避让区是创建时的布局快照：存活期间受保护/avoid 元素及其到容器的祖先不能再占用 transform（占用表双向拒绝）。
 * 顺序：先完成全部校验与占用检查（拒绝路径零改 DOM），再定位容器，再测量；定位后任何失败都回滚。
 */
export function createParticles(container, opts) {
  const el = requireElement(container, 'container');
  opts = plain(opts, ['count', 'seed', 'origin', 'angle', 'arc', 'speed', 'gravity', 'drag', 'life', 'emitWindow', 'size', 'spin', 'colors', 'shape', 'avoid', 'avoidPadding', 'protected']);
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  if (isProtected(el, opts)) fail('particles 的容器本身受保护，拒绝覆盖');
  const org = opts.origin === undefined ? { x: 0.5, y: 0.5 } : plain(opts.origin, ['x', 'y'], 'origin');
  const ox = numOr('origin.x', org.x, 0.5, 0, 1);
  const oy = numOr('origin.y', org.y, 0.5, 0, 1);
  const baseCfg = pick(opts, ['count', 'seed', 'angle', 'arc', 'speed', 'gravity', 'drag', 'life', 'emitWindow', 'size', 'spin', 'colors', 'shape']);
  particleConfig({ ...baseCfg, origin: { x: 0, y: 0 } });
  const pad = numOr('avoidPadding', opts.avoidPadding, 4, 0, 200);
  const avoidNodes = collectAvoid(el, opts.avoid);
  const chain = guardChain(el, [...el.querySelectorAll(PROT_SEL), ...avoidNodes]);
  assertGuardable(chain, 'particles');
  const posParents = assertOverlayReady(el, chain, 'particles');

  const doc = el.ownerDocument;
  const restorePos = ensurePositioned(el);
  let box;
  let cfg;
  let holes;
  try {
    box = measureBox(el);
    cfg = particleConfig({ ...baseCfg, origin: { x: ox * box.w, y: oy * box.h } });
    holes = measureHoles(el, avoidNodes, pad, box);
  } catch (e) {
    restorePos();
    throw e;
  }
  const releaseGuards = registerGuards(chain, el, posParents);
  const layer = overlayLayer(doc, 'particles');
  const launches = [];
  const nodes = [];
  for (let i = 0; i < cfg.count; i++) {
    const L = particleLaunch(i, cfg);
    launches.push(L);
    const n = doc.createElement('div');
    for (const [p, v] of [
      ['position', 'absolute'],
      ['left', '0'],
      ['top', '0'],
      ['width', `${r4(L.size)}px`],
      ['height', `${r4(L.size)}px`],
      ['background', cfg.colors[L.color]],
      ['border-radius', cfg.shape === 'circle' ? '50%' : '0'],
      ['opacity', '0'],
    ]) n.style.setProperty(p, v);
    layer.appendChild(n);
    nodes.push(n);
  }
  el.appendChild(layer);
  return makeItem('particles', cfg.emitWindow + cfg.life, (t) => {
    const states = particleStates(t, cfg);
    return states.map((s, i) => {
      const size = launches[i].size;
      const rot = r4(s.rotate);
      const sc = r4(s.scale);
      const ext = particleHalfExtent(size, sc, rot) + 0.01;
      const visible = s.alive && !holes.some((h) => holeHit(h, s.x, s.y, ext));
      const alpha = visible ? r4(s.alpha) : 0;
      const half = size / 2;
      nodes[i].style.setProperty('opacity', String(alpha));
      nodes[i].style.setProperty('transform', `translate(${r4(s.x - half)}px, ${r4(s.y - half)}px) rotate(${rot}deg) scale(${sc})`);
      return { x: r4(s.x), y: r4(s.y), alpha };
    });
  }, [
    () => {
      if (layer.parentNode) layer.parentNode.removeChild(layer);
    },
    restorePos,
    releaseGuards,
  ]);
}

/* ------------------------------------------------------------------ 6 光扫 */

/** 光带左缘 x：ltr 从 -band 扫到 width，rtl 反向。 */
export function sweepOffset(p, width, band, direction = 'ltr') {
  num('p', p, 0, 1);
  num('width', width, 1e-6, 1e6);
  num('band', band, 1e-6, 1e6);
  const x = -band + p * (width + band);
  return direction === 'rtl' ? width - p * (width + band) : x;
}

const VOID_TAGS = new Set(['img', 'input', 'br', 'hr', 'video', 'audio', 'canvas', 'svg', 'textarea', 'select', 'iframe', 'object', 'embed']);

/**
 * 光扫：在 target 内追加一层斜向高光，clip-path 把受保护后代与 avoid 元素挖空（相交的洞先合并，再拼 evenodd）。
 * target 本身或其祖先受保护时拒绝。避让区是创建时的布局快照，存活期间避让链路上的元素不能再占用 transform。
 */
export function createLightSweep(target, opts) {
  const el = requireElement(target, 'target');
  opts = plain(opts, ['duration', 'band', 'angle', 'color', 'ease', 'direction', 'avoid', 'avoidPadding', 'protected']);
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  if (isProtected(el, opts)) fail('lightSweep 的目标受保护，拒绝覆盖');
  if (VOID_TAGS.has(String(el.localName).toLowerCase())) fail('lightSweep 目标不能是替换元素或空元素，请改用外层容器');
  const duration = numOr('duration', opts.duration, 1, 0.05, 60);
  const bandFrac = numOr('band', opts.band, 0.35, 0.05, 1);
  const angle = numOr('angle', opts.angle, 20, 0, 45);
  const color = colorOr('color', opts.color, 'rgba(255, 255, 255, 0.55)');
  const direction = oneOf('direction', opts.direction, ['ltr', 'rtl'], 'ltr');
  const ease = resolveEase(opts.ease);
  const pad = numOr('avoidPadding', opts.avoidPadding, 2, 0, 200);
  const avoidNodes = collectAvoid(el, opts.avoid);
  const chain = guardChain(el, [...el.querySelectorAll(PROT_SEL), ...avoidNodes]);
  assertGuardable(chain, 'lightSweep');
  const posParents = assertOverlayReady(el, chain, 'lightSweep');

  const doc = el.ownerDocument;
  const restorePos = ensurePositioned(el);
  let box;
  let holes;
  try {
    box = measureBox(el);
    holes = measureHoles(el, avoidNodes, pad, box);
  } catch (e) {
    restorePos();
    throw e;
  }
  const bandPx = bandFrac * box.w;
  const releaseGuards = registerGuards(chain, el, posParents);
  const layer = overlayLayer(doc, 'sweep');
  if (holes.length) {
    let d = `M0 0H${r4(box.w)}V${r4(box.h)}H0Z`;
    for (const h of holes) d += `M${r4(h.x0)} ${r4(h.y0)}H${r4(h.x1)}V${r4(h.y1)}H${r4(h.x0)}Z`;
    layer.style.setProperty('clip-path', `path(evenodd, "${d}")`);
  }
  const band = doc.createElement('div');
  for (const [p, v] of [
    ['position', 'absolute'],
    ['left', '0'],
    ['top', '0'],
    ['height', '100%'],
    ['width', `${r4(bandPx)}px`],
    ['background', `linear-gradient(90deg, transparent 0%, ${color} 50%, transparent 100%)`],
    ['opacity', '0'],
  ]) band.style.setProperty(p, v);
  layer.appendChild(band);
  el.appendChild(layer);
  return makeItem('lightSweep', duration, (t) => {
    const p = ease(t / duration);
    const x = r4(sweepOffset(p, box.w, bandPx, direction));
    band.style.setProperty('transform', `translateX(${x}px) skewX(${r4(-angle)}deg)`);
    band.style.setProperty('opacity', t <= 0 || t >= duration ? '0' : '1');
    return { x };
  }, [
    () => {
      if (layer.parentNode) layer.parentNode.removeChild(layer);
    },
    restorePos,
    releaseGuards,
  ]);
}

/* ------------------------------------------------------------------ 7 遮罩揭示 */

const REVEAL_MODES = ['left', 'right', 'down', 'up', 'center-x', 'center-y', 'circle'];

/** 进度 p（0..1）对应的 clip-path 字符串；circle 的 origin 为百分比。 */
export function revealClip(mode, p, origin = { x: 50, y: 50 }) {
  oneOf('mode', mode, REVEAL_MODES);
  num('p', p, 0, 1);
  const rest = r4((1 - p) * 100);
  const half = r4((1 - p) * 50);
  switch (mode) {
    case 'left':
      return `inset(0% ${rest}% 0% 0%)`;
    case 'right':
      return `inset(0% 0% 0% ${rest}%)`;
    case 'down':
      return `inset(0% 0% ${rest}% 0%)`;
    case 'up':
      return `inset(${rest}% 0% 0% 0%)`;
    case 'center-x':
      return `inset(0% ${half}% 0% ${half}%)`;
    case 'center-y':
      return `inset(${half}% 0% ${half}% 0%)`;
    default:
      return `circle(${r4(p * 150)}% at ${r4(num('origin.x', origin.x, 0, 100))}% ${r4(num('origin.y', origin.y, 0, 100))}%)`;
  }
}

/** 遮罩揭示：用 clip-path 从隐藏到完整显示。会遮蔽内容，故拒绝受保护元素。 */
export function createMaskReveal(target, opts) {
  const el = requireElement(target, 'target');
  opts = plain(opts, ['mode', 'duration', 'ease', 'origin', 'protected']);
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  const mode = oneOf('mode', opts.mode, REVEAL_MODES, 'left');
  const duration = numOr('duration', opts.duration, 0.8, 0.05, 60);
  const ease = resolveEase(opts.ease);
  const origin = opts.origin === undefined ? { x: 50, y: 50 } : plain(opts.origin, ['x', 'y'], 'origin');
  revealClip(mode, 0, { x: numOr('origin.x', origin.x, 50, 0, 100), y: numOr('origin.y', origin.y, 50, 0, 100) });
  assertNotHidable(el, opts, 'maskReveal');
  const o = { x: origin.x ?? 50, y: origin.y ?? 50 };
  const cleanups = [claim(el, ['clip'], 'maskReveal'), saveProps(el, ['clip-path'])];
  return makeItem('maskReveal', duration, (t) => {
    const p = r4(ease(t / duration));
    const clip = revealClip(mode, p, o);
    setProp(el, 'clip-path', clip);
    return { p };
  }, cleanups.reverse());
}

/* ------------------------------------------------------------------ 8 翻卡 */

/** 翻卡状态：旋转角 0..180°，转过 90° 起显示背面。 */
export function flipState(p) {
  num('p', p, 0, 1);
  const angle = 180 * p;
  return { angle, showBack: angle >= 90 };
}

/**
 * 翻卡：card 绕 y/x 轴转 180°，前后两面按角度切换可见性（不依赖 backface-visibility）。
 * 形变类，card、front、back 及其后代出现受保护元素即拒绝。
 */
export function createFlip(card, opts) {
  const el = requireElement(card, 'card');
  opts = plain(opts, ['front', 'back', 'axis', 'perspective', 'duration', 'ease', 'protected']);
  const front = requireElement(opts.front, 'front');
  const back = requireElement(opts.back, 'back');
  if (front === el || back === el || front === back || typeof el.contains !== 'function' || !el.contains(front) || !el.contains(back)) {
    fail('front 与 back 必须是 card 内两个不同的后代元素');
  }
  const axis = oneOf('axis', opts.axis, ['y', 'x'], 'y');
  const persp = numOr('perspective', opts.perspective, 1200, 200, 5000);
  const duration = numOr('duration', opts.duration, 0.9, 0.05, 60);
  const ease = resolveEase(opts.ease);
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  for (const n of [el, front, back]) assertDeformable(n, opts, 'flip');
  // 三个元素原子占用：中途冲突会回滚已取得的占用，不留泄漏
  const cleanups = [
    claimMany([[el, ['transform']], [front, ['opacity']], [back, ['transform', 'opacity']]], 'flip'),
    saveProps(el, ['transform']),
    saveProps(front, ['opacity', 'visibility']),
    saveProps(back, ['opacity', 'visibility', 'transform']),
  ];
  const AX = axis.toUpperCase();
  setProp(back, 'transform', `rotate${AX}(180deg)`);
  return makeItem('flip', duration, (t) => {
    const st = flipState(clamp01(ease(t / duration)));
    setProp(el, 'transform', `perspective(${persp}px) rotate${AX}(${r4(st.angle)}deg)`);
    setProp(front, 'opacity', st.showBack ? '0' : '1');
    setProp(front, 'visibility', st.showBack ? 'hidden' : 'visible');
    setProp(back, 'opacity', st.showBack ? '1' : '0');
    setProp(back, 'visibility', st.showBack ? 'visible' : 'hidden');
    return { angle: r4(st.angle), showBack: st.showBack };
  }, cleanups.reverse());
}

/* ------------------------------------------------------------------ 9 逐字文字 */

/** 逐字配置。reveal 模式会让首帧文字不可读，必须显式 firstFrameReadable:false。 */
export function charConfig(o, n) {
  o = plain(o, ['mode', 'stagger', 'charDuration', 'amplitude', 'ease', 'firstFrameReadable']);
  const mode = oneOf('mode', o.mode, ['wave', 'pulse', 'reveal'], 'wave');
  const firstFrameReadable = boolOr('firstFrameReadable', o.firstFrameReadable, true);
  if (mode === 'reveal' && firstFrameReadable) {
    fail('reveal 模式首帧文字不可读，需在场景配置中显式设置 firstFrameReadable:false；默认请用 wave 或 pulse');
  }
  if (!Number.isInteger(n) || n < 1 || n > 120) fail('字符数必须是 1..120');
  const stagger = numOr('stagger', o.stagger, 0.04, 0, 0.5);
  const charDuration = numOr('charDuration', o.charDuration, 0.5, 0.05, 5);
  return {
    mode,
    n,
    stagger,
    charDuration,
    amplitude: numOr('amplitude', o.amplitude, 10, 0, 80),
    ease: resolveEase(o.ease),
    firstFrameReadable,
    duration: (n - 1) * stagger + charDuration,
  };
}

/** 纯函数：t 秒时每个字符的 { y, scale, opacity }。wave/pulse 在首末帧回到原位且不透明度为 1。 */
export function charStates(t, cfg) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  const out = [];
  for (let i = 0; i < cfg.n; i++) {
    const local = t >= cfg.duration ? 1 : clamp01((t - i * cfg.stagger) / cfg.charDuration);
    const e = cfg.ease(local);
    if (cfg.mode === 'reveal') out.push({ y: cfg.amplitude * (1 - e), scale: 1, opacity: e });
    else if (cfg.mode === 'pulse') out.push({ y: 0, scale: 1 + 0.25 * Math.sin(Math.PI * e), opacity: 1 });
    else out.push({ y: -cfg.amplitude * Math.sin(Math.PI * e), scale: 1, opacity: 1 });
  }
  return out;
}

/**
 * 逐字文字：读取 source 的文本，在 host 内生成 aria 隐藏的装饰副本逐字运动。
 * 源文字不改、不遮、不占用；副本放在哪里由调用者的 host 与 className 决定。
 */
export function createCharText(source, opts) {
  const src = requireElement(source, 'source');
  opts = plain(opts, ['host', 'className', 'mode', 'stagger', 'charDuration', 'amplitude', 'ease', 'firstFrameReadable']);
  const host = requireElement(opts.host, 'host');
  if (host === src || (typeof src.contains === 'function' && src.contains(host))) {
    fail('host 不能是 source 本身或其内部元素（副本会插进源文字，改写源文字内容与排版）');
  }
  if (isProtected(host, undefined)) fail('host 位于受保护元素内部，装饰副本不得插入受保护区域');
  assertNoOverlayReflow(host, 'charText');
  const text = String(src.textContent ?? '').replace(/\s+/g, ' ').trim();
  if (text.length === 0) fail('source 没有可用的文字');
  const chars = Array.from(text);
  const cfg = charConfig(pick(opts, ['mode', 'stagger', 'charDuration', 'amplitude', 'ease', 'firstFrameReadable']), chars.length);
  if (opts.className !== undefined && (typeof opts.className !== 'string' || !/^[A-Za-z_][\w-]{0,63}$/.test(opts.className))) {
    fail('className 必须是单个合法的类名');
  }
  const doc = host.ownerDocument;
  const copy = doc.createElement('span');
  copy.setAttribute('aria-hidden', 'true');
  copy.setAttribute('data-mp-decor', 'chars');
  if (opts.className) copy.setAttribute('class', opts.className);
  const spans = chars.map((ch) => {
    const s = doc.createElement('span');
    s.style.setProperty('display', 'inline-block');
    s.textContent = ch === ' ' ? ' ' : ch;
    copy.appendChild(s);
    return s;
  });
  host.appendChild(copy);
  return makeItem('charText', cfg.duration, (t) => {
    const states = charStates(t, cfg);
    states.forEach((st, i) => {
      spans[i].style.setProperty('opacity', String(r4(st.opacity)));
      spans[i].style.setProperty('transform', `translate(0px, ${r4(st.y)}px) scale(${r4(st.scale)})`);
    });
    return states.map((st) => ({ y: r4(st.y), scale: r4(st.scale), opacity: r4(st.opacity) }));
  }, [
    () => {
      if (copy.parentNode) copy.parentNode.removeChild(copy);
    },
  ]);
}

/* ------------------------------------------------------------------ 10 数字滚动 */

export function numberConfig(o) {
  o = plain(o, ['from', 'to', 'duration', 'ease', 'decimals', 'separator', 'prefix', 'suffix']);
  const affix = (name, v) => {
    if (v === undefined) return '';
    if (typeof v !== 'string' || v.length > 12 || /[\u0000-\u001f<>]/.test(v)) fail(`${name} 必须是不超过 12 个字符的纯文本`);
    return v;
  };
  return {
    from: num('from', o.from, -1e12, 1e12),
    to: num('to', o.to, -1e12, 1e12),
    duration: numOr('duration', o.duration, 1.5, 0.05, 60),
    ease: resolveEase(o.ease),
    decimals: intOr('decimals', o.decimals, 0, 0, 6),
    separator: oneOf('separator', o.separator, ['', ',', ' ', "'"], ''),
    prefix: affix('prefix', o.prefix),
    suffix: affix('suffix', o.suffix),
  };
}

/** 纯函数：t 秒时的数值；t ≥ duration 时精确等于 to。 */
export function rollValue(t, cfg) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  if (t <= 0) return cfg.from;
  if (t >= cfg.duration) return cfg.to;
  return lerp(cfg.from, cfg.to, cfg.ease(t / cfg.duration));
}

export function formatNumber(v, fmt = {}) {
  num('v', v, -1e12, 1e12);
  const decimals = intOr('decimals', fmt.decimals, 0, 0, 6);
  const sep = fmt.separator ?? '';
  const s = Math.abs(v).toFixed(decimals);
  const [ip, fp] = s.split('.');
  const grouped = sep ? ip.replace(/\B(?=(\d{3})+(?!\d))/g, sep) : ip;
  const neg = v < 0 && Number(s) !== 0;
  return `${neg ? '-' : ''}${fmt.prefix ?? ''}${grouped}${fp ? `.${fp}` : ''}${fmt.suffix ?? ''}`;
}

/** 数字滚动：改写 target 自己的文本（target 须只含文本）。会改文字，故拒绝受保护元素。 */
export function createNumberRoll(target, opts) {
  const el = requireElement(target, 'target');
  const cfg = numberConfig(pick(plain(opts, ['from', 'to', 'duration', 'ease', 'decimals', 'separator', 'prefix', 'suffix', 'protected']), ['from', 'to', 'duration', 'ease', 'decimals', 'separator', 'prefix', 'suffix']));
  if (opts && opts.protected !== undefined) boolOr('protected', opts.protected);
  assertNotHidable(el, opts, 'numberRoll');
  if (el.children && el.children.length > 0) fail('numberRoll 的目标只能含文本，不能有子元素');
  assertNoOverlayReflow(el, 'numberRoll');
  const cleanups = [claim(el, ['text'], 'numberRoll')];
  const original = el.textContent;
  cleanups.push(() => {
    el.textContent = original;
  });
  return makeItem('numberRoll', cfg.duration, (t) => {
    const value = rollValue(t, cfg);
    const text = formatNumber(value, cfg);
    el.textContent = text;
    return { value: r4(value), text };
  }, cleanups.reverse());
}

/* ------------------------------------------------------------------ 11 路径描线 */

/** 描线窗口 [start, end]（占总长的比例）。draw 从头画，erase 从头擦，trail 是移动的线段。 */
export function pathWindow(p, mode = 'draw', segment = 0.3, reverse = false) {
  num('p', p, 0, 1);
  oneOf('mode', mode, ['draw', 'erase', 'trail']);
  num('segment', segment, 0.02, 1);
  let start;
  let end;
  if (mode === 'draw') {
    start = 0;
    end = p;
  } else if (mode === 'erase') {
    start = p;
    end = 1;
  } else {
    const head = p * (1 + segment);
    start = clamp01(head - segment);
    end = clamp01(head);
  }
  return reverse ? { start: 1 - end, end: 1 - start } : { start, end };
}

/** 路径描线：任何带 getTotalLength 的 SVG 图形元素；用 stroke-dasharray / dashoffset 实现，兼容 pathLength。 */
export function createPathDraw(target, opts) {
  const el = requireElement(target, 'target');
  if (typeof el.getTotalLength !== 'function') fail('目标不是 SVG 几何元素（缺少 getTotalLength）');
  opts = plain(opts, ['mode', 'segment', 'duration', 'ease', 'reverse', 'protected']);
  const mode = oneOf('mode', opts.mode, ['draw', 'erase', 'trail'], 'draw');
  const segment = numOr('segment', opts.segment, 0.3, 0.02, 1);
  const duration = numOr('duration', opts.duration, 1.2, 0.05, 60);
  const reverse = boolOr('reverse', opts.reverse, false);
  const ease = resolveEase(opts.ease);
  if (opts.protected !== undefined) boolOr('protected', opts.protected);
  assertNotHidable(el, opts, 'pathDraw');
  const real = el.getTotalLength();
  if (typeof real !== 'number' || !Number.isFinite(real) || real <= 0) fail('路径长度无效');
  const declared = Number.parseFloat(el.getAttribute('pathLength'));
  const unit = declared > 0 ? declared : real;
  const cleanups = [claim(el, ['dash'], 'pathDraw'), saveProps(el, ['stroke-dasharray', 'stroke-dashoffset', 'visibility'])];
  return makeItem('pathDraw', duration, (t) => {
    const w = pathWindow(clamp01(ease(t / duration)), mode, segment, reverse);
    const len = w.end - w.start;
    const hidden = len <= 1e-9;
    setProp(el, 'stroke-dasharray', `${r4(len * unit)} ${r4(unit * 2 + 1)}`);
    setProp(el, 'stroke-dashoffset', r4(-w.start * unit));
    setProp(el, 'visibility', hidden ? 'hidden' : 'visible');
    return { start: r4(w.start), end: r4(w.end) };
  }, cleanups.reverse());
}

/* ------------------------------------------------------------------ 12 首尾循环工具 */

/** 周期取模：负时间也落在 [0, period)。 */
export function loopTime(t, period) {
  if (typeof t !== 'number' || !Number.isFinite(t)) fail('t 必须是有限的秒数');
  num('period', period, 0.01, 3600);
  const u = t - Math.floor(t / period) * period;
  return u >= period || u < 0 ? 0 : u;
}

/** 往复（三角波）：周期为 2 * period，t = period 处为转折点。 */
export function pingPongTime(t, period) {
  num('period', period, 0.01, 3600);
  const u = loopTime(t, 2 * period);
  return u <= period ? u : 2 * period - u;
}

/** 把关键帧补成首尾闭合：缺 t=0 的首帧拒绝；末帧不到 period 则补一帧回到首值；超过 period 拒绝。 */
export function closedKeyframes(frames, period) {
  num('period', period, 0.01, 3600);
  const nf = normalizeFrames(frames, 'frames');
  const first = nf[0];
  if (first.t !== 0) fail('闭合循环要求首帧 t 为 0');
  const last = nf[nf.length - 1];
  if (last.t > period) fail('末帧 t 不能超过 period');
  const same = (a, b) => (Array.isArray(a) ? a.every((x, i) => x === b[i]) : a === b);
  if (last.t === period) {
    if (!same(first.v, last.v)) fail('末帧在 period 处必须与首帧同值');
    return nf;
  }
  return [...nf, { t: period, v: Array.isArray(first.v) ? first.v.slice() : first.v, ease: last.ease }];
}

/** 无缝交叉淡化权重：u < overlap 时 tail（片段尾部）渐隐、head（片段头部）渐显。 */
export function crossfadeWeight(t, period, overlap) {
  num('overlap', overlap, 0.01, 3600);
  const u = loopTime(t, period);
  if (overlap > period) fail('overlap 不能大于 period');
  const k = clamp01(u / overlap);
  const head = k * k * (3 - 2 * k);
  return { head, tail: 1 - head };
}

/** 两个 seek 返回值的最大数值差；类型或结构不一致为 Infinity。 */
export function stateDistance(a, b) {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b);
  if (a === b) return 0;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return Infinity;
  if (Array.isArray(a) !== Array.isArray(b)) return Infinity;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return Infinity;
  let d = 0;
  for (const k of ka) {
    if (!Object.hasOwn(b, k)) return Infinity;
    d = Math.max(d, stateDistance(a[k], b[k]));
  }
  return d;
}

/** 断言 item 在 0 与 period 的状态一致（会实际 seek，调用后请由消费者重新 seek）。 */
export function assertLoopClosed(item, period, tol = 1e-3) {
  if (!item || typeof item.seek !== 'function') fail('item 必须有 seek');
  num('period', period, 0.01, 3600);
  const a = item.seek(0);
  const b = item.seek(period);
  const d = stateDistance(a, b);
  if (!(d <= tol)) fail(`首尾不闭合：0 与 ${period} 秒的状态差 ${d}`);
  return d;
}

function requireItem(item, name) {
  if (!item || typeof item.seek !== 'function' || typeof item.duration !== 'number' || Number.isNaN(item.duration)) {
    fail(`${name} 必须是带 seek(t) 与 duration 的动作对象`);
  }
  return item;
}

/** 循环包装：mode 为 wrap（周期取模）或 pingpong（往复）。period 不能超过 item.duration。 */
export function createLoop(item, opts) {
  requireItem(item, 'item');
  opts = plain(opts, ['period', 'mode']);
  const period = numOr('period', opts.period, Number.isFinite(item.duration) ? item.duration : undefined, 0.01, 3600);
  if (period === undefined) fail('item 时长无限，必须显式指定 period');
  if (period > item.duration + 1e-9) fail('period 不能超过 item.duration');
  const mode = oneOf('mode', opts.mode, ['wrap', 'pingpong'], 'wrap');
  return makeItem('loop', Infinity, (t) => item.seek(mode === 'wrap' ? loopTime(t, period) : pingPongTime(t, period)), [
    () => {
      if (typeof item.dispose === 'function') item.dispose();
    },
  ]);
}

/** 时间线：entries = [{ item, at }]，每个 item 按 (t - at) 钳制后 seek；开始前停在首状态，结束后停在末状态。 */
export function createTimeline(entries) {
  if (!Array.isArray(entries) || entries.length < 1 || entries.length > 64) fail('entries 必须是 1..64 个条目');
  const list = entries.map((e, i) => {
    plain(e, ['item', 'at'], `entries[${i}]`);
    return { item: requireItem(e.item, `entries[${i}].item`), at: numOr(`entries[${i}].at`, e.at, 0, 0, 3600) };
  });
  const duration = Math.max(...list.map((e) => e.at + e.item.duration));
  return makeItem('timeline', duration, (t) => {
    return list.map((e) => {
      const local = Math.max(0, Math.min(t - e.at, e.item.duration));
      return e.item.seek(local);
    });
  }, [
    () => {
      for (const e of list) if (typeof e.item.dispose === 'function') e.item.dispose();
    },
  ]);
}
