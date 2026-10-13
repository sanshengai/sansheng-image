(function () {
  'use strict';

  var DATA_URL = '/tools/image/motion-data.json';
  var MEDIA_PREFIX = '/tools/image/motion/';
  var MAX_ITEMS = 12;
  var SCROLL_PLAY_RATIO = 0.6;   // 手机：露出这么多才开始播
  var SCROLL_STOP_RATIO = 0.25;  // 低于这个比例视为离开，停止

  var hasIO = typeof window.IntersectionObserver === 'function';
  var mqReduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  var mqCoarse = window.matchMedia('(pointer: coarse)');

  var $ = function (s, r) { return (r || document).querySelector(s); };
  function onMq(mq, fn) { if (mq.addEventListener) mq.addEventListener('change', fn); else if (mq.addListener) mq.addListener(fn); }

  function h(tag, props, kids) {
    var e = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v == null || v === false) return;
        if (k === 'class') e.className = v;
        else if (k === 'text') e.textContent = v;
        else e.setAttribute(k, v === true ? '' : v);
      });
    }
    (kids || []).forEach(function (c) {
      if (c == null) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  /* ---------- 数据校验：媒体只认本域 /tools/image/motion/<id>/ 下固定的 poster.png 与 share.mp4 ---------- */
  function mediaPath(v, name) {
    if (typeof v !== 'string' || v.length > 300 || v.indexOf('\\') !== -1) return null;
    if (v.indexOf(MEDIA_PREFIX) !== 0) return null;
    var u;
    try { u = new URL(v, location.href); } catch (e) { return null; }
    if (u.origin !== location.origin || u.search || u.hash) return null;
    if (u.pathname !== v) return null;   // 含 ../、%2e 等会被规范化改写的路径一律拒绝
    if (v.slice(-(name.length + 1)) !== '/' + name) return null;
    return u.pathname;
  }
  function str(v, min, max) { return typeof v === 'string' && v.length >= min && v.length <= max; }
  function dim(v) { return typeof v === 'number' && isFinite(v) && Math.floor(v) === v && v >= 16 && v <= 8000; }

  function parseItems(d) {
    var out = [], seen = {};
    if (!d || typeof d !== 'object' || !Array.isArray(d.items)) return out;
    d.items.forEach(function (o) {
      if (out.length >= MAX_ITEMS || !o || typeof o !== 'object') return;
      if (typeof o.id !== 'string' || !/^[A-Za-z0-9_-]{1,32}$/.test(o.id) || seen[o.id]) return;
      if (!str(o.title, 1, 60) || !str(o.scene, 1, 32) || !str(o.scene_label, 1, 20)) return;
      if (o.note != null && !(str(o.note, 1, 240) && o.note.trim() !== '')) return;
      if (o.style_code != null && !(typeof o.style_code === 'string' && /^C\d{1,3}$/.test(o.style_code))) return;
      if (o.homepage_scene != null && !(typeof o.homepage_scene === 'string' && /^[a-z0-9_-]{1,32}$/.test(o.homepage_scene))) return;
      if (!dim(o.width) || !dim(o.height)) return;
      if (!(typeof o.duration === 'number' && o.duration > 0 && o.duration <= 60)) return;
      var poster = mediaPath(o.poster, 'poster.png');
      var video = mediaPath(o.video, 'share.mp4');
      if (!poster || !video) return;
      if (poster !== MEDIA_PREFIX + o.id + '/poster.png' || video !== MEDIA_PREFIX + o.id + '/share.mp4') return;
      seen[o.id] = 1;
      out.push({
        id: o.id, title: o.title, styleCode: o.style_code || null,
        scene: o.scene, homeScene: o.homepage_scene || null, sceneLabel: o.scene_label,
        width: o.width, height: o.height, poster: poster, video: video,
        note: (typeof o.note === 'string' ? o.note : null)
      });
    });
    return out;
  }

  var items = [];

  /* ---------- 播放器：全局最多一个在播 ---------- */
  var players = [];
  var active = null;

  function updateBtn(p) {
    var busy = p.state === 'loading' || p.state === 'playing';
    var label = busy ? '暂停' : (p.state === 'failed' ? '重试播放' : '播放动态');
    p.btn.textContent = label;
    p.btn.setAttribute('aria-label', label + (p.item ? '：' + p.item.title : ''));
  }
  function setStatus(p, msg) { p.status.textContent = msg || ''; }

  function releaseSrc(p) {
    var v = p.v;
    try { v.pause(); } catch (e) { /* 忽略 */ }
    if (v.getAttribute('src') !== null) {
      v.removeAttribute('src');
      try { v.load(); } catch (e2) { /* 忽略 */ }
    }
  }
  function stop(p) {
    p.gen++;
    releaseSrc(p);
    p.state = 'idle'; p.mode = null;
    if (active === p) active = null;
    setStatus(p, '');
    updateBtn(p);
  }
  function stopAll() { players.slice().forEach(function (p) { if (p.state !== 'idle') stop(p); }); }
  function fail(p, msg) {
    p.gen++;
    releaseSrc(p);
    p.state = 'failed'; p.mode = null;
    if (active === p) active = null;
    setStatus(p, msg);
    updateBtn(p);
  }

  function play(p, mode) {
    if (!p.item) return;
    if (mode === 'auto' && (mqReduce.matches || document.hidden)) return;
    if (active && active !== p) stop(active);
    active = p;
    var gen = ++p.gen;
    p.mode = mode; p.state = 'loading';
    setStatus(p, '');
    updateBtn(p);
    var v = p.v;
    if (v.getAttribute('src') !== p.item.video) v.setAttribute('src', p.item.video);
    var r;
    try { r = v.play(); } catch (e) { fail(p, '播放没有成功，点按钮可重试。'); return; }
    if (r && typeof r.then === 'function') {
      r.then(function () {
        if (gen !== p.gen) return;
        p.state = 'playing'; updateBtn(p);
      }, function () {
        if (gen !== p.gen) return;
        fail(p, '播放没有成功，点按钮可重试。');
      });
    } else { p.state = 'playing'; updateBtn(p); }
  }

  function onBtn(p) {
    if (!p.item) return;
    if (p.state === 'loading' || p.state === 'playing') { p.suppressed = true; stop(p); return; }
    p.suppressed = false;
    play(p, 'manual');
  }

  function desktopTry(p) {
    if (mqCoarse.matches || !hasIO || !p.item || p.suppressed || p.state !== 'idle') return;
    if (!(p.hover || p.focus) || p.ratio < SCROLL_STOP_RATIO) return;
    if (mqReduce.matches || document.hidden) return;
    play(p, 'auto');
  }
  function mobileTry(p) {
    if (!mqCoarse.matches || !hasIO || !p.mobileAuto || !p.item || p.suppressed || p.state !== 'idle') return;
    if (mqReduce.matches || document.hidden) return;
    if (p.ratio >= SCROLL_PLAY_RATIO) play(p, 'auto');
  }
  function leaveCheck(p) {
    if (p.hover || p.focus) return;
    p.suppressed = false;
    if (p.mode === 'auto') stop(p);
  }

  var pio = hasIO ? new IntersectionObserver(function (es) {
    es.forEach(function (e) {
      var p = null;
      for (var i = 0; i < players.length; i++) if (players[i].area === e.target) { p = players[i]; break; }
      if (!p) return;
      p.ratio = e.isIntersecting ? e.intersectionRatio : 0;
      if (p.ratio < SCROLL_STOP_RATIO) {
        p.suppressed = false;
        if (p.state !== 'idle') stop(p);
      } else { mobileTry(p); desktopTry(p); }
    });
  }, { threshold: [0, SCROLL_STOP_RATIO, SCROLL_PLAY_RATIO, 1] }) : null;

  function makePlayer(o) {
    var v = o.video;
    v.muted = true; v.defaultMuted = true; v.loop = true; v.controls = false;
    v.setAttribute('muted', ''); v.setAttribute('loop', '');
    v.setAttribute('playsinline', ''); v.setAttribute('webkit-playsinline', '');
    v.playsInline = true;
    v.setAttribute('preload', 'none'); v.preload = 'none';
    v.setAttribute('disablepictureinpicture', '');
    var p = {
      v: v, btn: o.btn, status: o.status, area: o.area, mobileAuto: !!o.mobileAuto,
      item: null, state: 'idle', mode: null, gen: 0,
      hover: false, focus: false, suppressed: false, ratio: 0
    };
    v.addEventListener('error', function () {
      if (v.getAttribute('src') === null || p.state === 'idle') return;
      fail(p, '视频没有加载出来，点按钮可重试。');
    });
    o.btn.addEventListener('click', function () { onBtn(p); });
    var autoArea = o.autoArea || o.video.parentNode;
    if (autoArea.tagName !== 'BUTTON') { autoArea.tabIndex = 0; autoArea.setAttribute('aria-label', '动态预览'); }
    autoArea.addEventListener('mouseenter', function () { p.hover = true; desktopTry(p); });
    autoArea.addEventListener('mouseleave', function () { p.hover = false; leaveCheck(p); });
    autoArea.addEventListener('focusin', function () { p.focus = true; desktopTry(p); });
    autoArea.addEventListener('focusout', function (e) {
      if (e.relatedTarget && autoArea.contains(e.relatedTarget)) return;
      p.focus = false; leaveCheck(p);
    });
    players.push(p);
    if (pio) pio.observe(o.area);
    updateBtn(p);
    return p;
  }
  function destroyPlayer(p) {
    stop(p);
    var i = players.indexOf(p);
    if (i !== -1) players.splice(i, 1);
    if (pio) pio.unobserve(p.area);
  }
  function setItem(p, it) {
    stop(p);
    p.item = it; p.suppressed = false;
    p.v.setAttribute('aria-label', '动态示例：' + it.title);
    updateBtn(p);
  }

  function frameNode(it, tagText) {
    var f = h('div', { class: 'motion-frame' });
    f.style.aspectRatio = it.width + ' / ' + it.height;
    return { frame: f, tag: h('span', { class: 'motion-tag', text: tagText }) };
  }
  function itemNote(it) {
    if (it.note) return it.note;
    if (it.scene === 'brand') return '品牌示范：用来展示动态效果，不代表文章配图功能。';
    if (/动态示范$/.test(it.sceneLabel)) return '画风动态示范：展示静态首帧与动态示范；尚未纳入该场景的静态画风筛选。';
    return it.sceneLabel + '示例：静态首帧与动态示范，仅作展示。';
  }
  function hintText() {
    if (mqReduce.matches) return '系统开启了「减少动态效果」：不会自动播放，也不预加载视频；需要时点「播放动态」手动观看。';
    if (!hasIO) return '点「播放动态」按钮观看。';
    if (mqCoarse.matches) return '滚动到画面中会自动播放，离开后停止；也可以点按钮。';
    return '鼠标移上去或用键盘聚焦会播放，移开就停；也可以点按钮。';
  }

  /* ---------- 「动态版」区块（画风库之后） ---------- */
  var secEl = null;
  function insertSection(bodyKids) {
    var lib = $('#library');   // 动态版入口放在画风库之后、色系图鉴之前
    if (!lib || !lib.parentNode) return null;
    var old = document.getElementById('motion-showcase');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var body = h('div', { class: 'motion-body' }, bodyKids);
    var sec = h('section', { class: 'sec motion-sec', id: 'motion-showcase', 'aria-labelledby': 'motion-title' }, [
      h('div', { class: 'wrap' }, [
        h('h2', { class: 'sec-title', id: 'motion-title', text: '动态版' }),
        body
      ])
    ]);
    lib.parentNode.insertBefore(sec, lib.nextSibling);
    return sec;
  }

  function buildFallback() {
    cleanupWall();
    players.slice().forEach(destroyPlayer);
    secEl = insertSection([h('p', { class: 'motion-note', role: 'status', text: '动态示例暂未加载' })]);
  }

  function buildShowcase() {
    var first = items[0];
    var s = frameNode(first, '静态首帧');
    var d = frameNode(first, '动态示范');

    var img = h('img', { alt: '', decoding: 'async' });
    img.addEventListener('error', function () { img.classList.add('broken'); });
    s.frame.appendChild(img);
    s.frame.appendChild(s.tag);

    var video = h('video', { class: 'motion-video' });
    d.frame.appendChild(video);
    d.frame.appendChild(d.tag);

    var btn = h('button', { class: 'btn btn-small motion-play', type: 'button', text: '播放动态' });
    var status = h('p', { class: 'motion-status', role: 'status' });
    var dynFig = h('figure', { class: 'motion-fig motion-fig-dyn' }, [
      d.frame,
      h('figcaption', { class: 'motion-cap' }, [btn])
    ]);
    var staticFig = h('figure', { class: 'motion-fig' }, [
      s.frame,
      h('figcaption', { class: 'motion-cap', text: '静态首帧' })
    ]);

    var tabsBox = h('div', { class: 'motion-tabs', role: 'group', 'aria-label': '选择示例' });
    var sceneEl = h('span', { class: 'motion-scene' });
    var titleEl = h('span', { class: 'motion-title-text' });
    var noteEl = h('p', { class: 'motion-note' });
    var hintEl = h('p', { class: 'motion-hint', text: hintText() });
    var tabs = items.map(function (it, i) {
      var b = h('button', { class: 'chip', type: 'button', 'aria-pressed': 'false', text: it.title });
      b.addEventListener('click', function () { show(i); });
      tabsBox.appendChild(b);
      return b;
    });

    secEl = insertSection([
      h('p', { class: 'sec-sub', text: '左边是静态首帧，右边是动态示范，仅作展示。' }),
      tabsBox,
      h('p', { class: 'motion-meta' }, [sceneEl, titleEl]),
      h('div', { class: 'motion-compare' }, [staticFig, dynFig]),
      status,
      noteEl,
      hintEl
    ]);
    if (!secEl) return;

    var sp = makePlayer({ video: video, btn: btn, status: status, area: dynFig, mobileAuto: true });
    var nearSeen = !hasIO;

    function showPoster() {
      if (!nearSeen || !sp.item) return;
      img.classList.remove('broken');
      img.src = sp.item.poster;
      video.setAttribute('poster', sp.item.poster);
    }
    function show(i) {
      var it = items[i];
      tabs.forEach(function (b, j) { b.setAttribute('aria-pressed', j === i ? 'true' : 'false'); });
      sceneEl.textContent = it.sceneLabel;
      titleEl.textContent = it.title;
      noteEl.textContent = itemNote(it);
      s.frame.style.aspectRatio = d.frame.style.aspectRatio = it.width + ' / ' + it.height;
      img.alt = '静态首帧：' + it.title;
      setItem(sp, it);
      showPoster();
      mobileTry(sp); desktopTry(sp);
    }
    show(0);

    if (hasIO) {
      var near = new IntersectionObserver(function (es) {
        for (var i = 0; i < es.length; i++) {
          if (es[i].isIntersecting) { nearSeen = true; near.disconnect(); showPoster(); return; }
        }
      }, { rootMargin: '400px 0px' });
      near.observe(s.frame);
    }
    onMq(mqReduce, function () { hintEl.textContent = hintText(); });
    onMq(mqCoarse, function () { hintEl.textContent = hintText(); });
  }

  /* ---------- 场景墙可动卡：只处理真实文章场景，选该场景第一个 article 示例 ----------
     结构：li > button.scene-card（原按钮，点击筛选逻辑不动）+ 兄弟 button.motion-wall-play + p.motion-wall-status
     动态 video 放在 button 内的 .thumb 里（无 controls，不是交互元素），播放控制不嵌进 button。 */
  var wall = [];
  var wallMO = null;

  function rmNode(n) { if (n && n.parentNode) n.parentNode.removeChild(n); }

  function decorateCard(card, it) {
    var li = card.parentNode, th = card.querySelector('.thumb');
    if (!li || li.nodeName !== 'LI' || !th) return;
    if (li.classList.contains('motion-wall-item')) return;

    var img = th.querySelector('img'), made = false;
    var orig = img ? {
      alt: img.getAttribute('alt'), title: img.getAttribute('title'),
      src: img.getAttribute('src'), dsrc: img.getAttribute('data-src')
    } : null;
    if (img) {
      img.removeAttribute('data-src');   // 阻止页面的懒加载再把该场景的静态代表图设回来
    } else {
      img = h('img', { alt: '', decoding: 'async' });
      img.addEventListener('load', function () { img.classList.add('is-in'); });
      img.addEventListener('error', function () { img.classList.add('broken'); });
      th.appendChild(img);
      made = true;
    }
    var nameEl = card.querySelector('.scene-name');
    var sceneName = (nameEl && nameEl.textContent) || it.sceneLabel;
    img.setAttribute('alt', sceneName + '样图，示例：' + it.title + '（动态示范的静态首帧）');
    img.setAttribute('title', it.title);

    var video = h('video', { class: 'motion-video motion-wall-video', 'aria-hidden': 'true' });
    var badge = h('span', { class: 'motion-badge', text: '可动 · ' + (it.styleCode || '示例') });
    th.appendChild(video);
    th.appendChild(badge);

    var ctl = h('button', { class: 'btn btn-small motion-wall-play', type: 'button', text: '播放动态' });
    var status = h('p', { class: 'motion-status motion-wall-status', role: 'status' });
    li.classList.add('motion-wall-item');
    li.appendChild(ctl);
    li.appendChild(status);

    var player = makePlayer({ video: video, btn: ctl, status: status, area: li, autoArea: card, mobileAuto: true });
    setItem(player, it);

    var w = { li: li, img: img, made: made, orig: orig, video: video, badge: badge, ctl: ctl, status: status, player: player, near: null, shown: false };
    function showPoster() {
      if (w.shown) return;
      w.shown = true;
      if (w.near) { w.near.disconnect(); w.near = null; }
      img.classList.remove('is-in', 'broken');
      img.src = it.poster;
      video.setAttribute('poster', it.poster);
    }
    wall.push(w);

    // poster 按需：卡片图已被页面加载过说明卡片已在附近，立刻换；否则靠近视口 400px 内才设置
    if (!hasIO || (orig && orig.src)) showPoster();
    else {
      w.near = new IntersectionObserver(function (es) {
        for (var i = 0; i < es.length; i++) if (es[i].isIntersecting) { showPoster(); return; }
      }, { rootMargin: '400px 0px' });
      w.near.observe(th);
    }
  }

  function cleanupWall() {
    if (wallMO) { wallMO.disconnect(); wallMO = null; }
    wall.forEach(function (w) {
      if (w.near) w.near.disconnect();
      destroyPlayer(w.player);
      [w.video, w.badge, w.ctl, w.status].forEach(rmNode);
      w.li.classList.remove('motion-wall-item');
      if (w.made) rmNode(w.img);
      else if (w.orig) {
        var o = w.orig, im = w.img;
        if (o.alt == null) im.removeAttribute('alt'); else im.setAttribute('alt', o.alt);
        if (o.title == null) im.removeAttribute('title'); else im.setAttribute('title', o.title);
        im.classList.remove('broken');
        if (o.src != null) im.setAttribute('src', o.src);
        else if (o.dsrc != null) im.setAttribute('src', o.dsrc);
        else im.removeAttribute('src');
      }
    });
    wall = [];
  }

  function markScenes() {
    var ids = [], picks = [];
    items.forEach(function (it) {
      if (it.scene === 'article' && it.homeScene && ids.indexOf(it.homeScene) === -1) { ids.push(it.homeScene); picks.push(it); }
    });
    if (!ids.length) return;
    var grid = $('#scene-grid');
    if (!grid) return;
    function apply() {
      var done = 0;
      ids.forEach(function (sid, i) {
        var card = grid.querySelector('.scene-card[data-scene="' + sid + '"]');
        if (!card) return;
        done++;
        decorateCard(card, picks[i]);
      });
      return done === ids.length;
    }
    if (apply() || !window.MutationObserver) return;
    wallMO = new MutationObserver(function () { if (apply() && wallMO) { wallMO.disconnect(); wallMO = null; } });
    wallMO.observe(grid, { childList: true });
  }

  /* ---------- 详情页动态缩略图：只读 dialog 的现有 DOM（#dlg-id 文本 / .dlg-text），不改页面全局函数 ---------- */
  var dlgBox = null, dlgPlayer = null;
  function destroyDlgBox() {
    if (dlgPlayer) { destroyPlayer(dlgPlayer); dlgPlayer = null; }
    if (dlgBox && dlgBox.parentNode) dlgBox.parentNode.removeChild(dlgBox);
    dlgBox = null;
  }
  function syncDialog() {
    destroyDlgBox();
    var dlg = $('#dlg'), idEl = $('#dlg-id');
    if (!dlg || !idEl || !dlg.open) return;
    var m = /^(C\d{1,3})(?!\d)/.exec(idEl.textContent || '');
    if (!m) return;
    var it = null;
    for (var i = 0; i < items.length; i++) if (items[i].styleCode === m[1]) { it = items[i]; break; }
    if (!it) return;

    var f = frameNode(it, '动态示范');
    var video = h('video', { class: 'motion-video', poster: it.poster });
    f.frame.appendChild(video);
    f.frame.appendChild(f.tag);
    var btn = h('button', { class: 'btn btn-small motion-play', type: 'button', text: '播放动态' });
    var status = h('p', { class: 'motion-status', role: 'status' });
    dlgBox = h('div', { class: 'motion-dlg' }, [
      h('p', { class: 'motion-dlg-title', text: '动态示范' }),
      f.frame,
      h('div', { class: 'motion-cap' }, [btn]),
      h('p', { class: 'motion-note', text: itemNote(it) }),
      status
    ]);
    var text = $('.dlg-text', dlg);
    if (!text) { dlgBox = null; return; }
    var usenote = $('#dlg-usenote', text), facts = $('.dlg-facts', text);
    if (usenote && usenote.parentNode === text) text.insertBefore(dlgBox, usenote.nextSibling);   // 放在「① 用在哪」之后、「② 色系」之前
    else if (facts && facts.parentNode === text) text.insertBefore(dlgBox, facts);
    else text.appendChild(dlgBox);
    dlgPlayer = makePlayer({ video: video, btn: btn, status: status, area: dlgBox, mobileAuto: false });
    setItem(dlgPlayer, it);
  }
  function watchDialog() {
    var dlg = $('#dlg'), idEl = $('#dlg-id');
    if (!dlg || !idEl) return;
    dlg.addEventListener('close', destroyDlgBox);
    if (!window.MutationObserver) return;
    new MutationObserver(syncDialog).observe(idEl, { childList: true, characterData: true, subtree: true });
    new MutationObserver(function () { if (!dlg.open) destroyDlgBox(); }).observe(dlg, { attributes: true, attributeFilter: ['open'] });
    if (dlg.open) syncDialog();
  }

  /* ---------- 全局：隐藏页面、减少动态、指针类型变化 ---------- */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stopAll();
    else players.forEach(mobileTry);
  });
  onMq(mqReduce, stopAll);
  onMq(mqCoarse, stopAll);

  /* ---------- 启动 ---------- */
  function start() {
    if (!window.fetch || !window.Promise) { buildFallback(); return; }
    var req;
    try { req = fetch(DATA_URL, { credentials: 'same-origin' }); } catch (e) { buildFallback(); return; }
    req.then(function (r) {
      if (!r.ok) throw new Error('http');
      return r.json();
    }).then(function (d) {
      items = parseItems(d);
      if (!items.length) throw new Error('empty');
      buildShowcase();
      if (!secEl) throw new Error('no-slot');
      markScenes();
      watchDialog();
    }).catch(function () {
      items = [];
      destroyDlgBox();
      buildFallback();
    });
  }
  start();
})();
