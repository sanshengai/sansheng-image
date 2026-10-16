(function () {
  'use strict';

  var DATA_URL = '/tools/image/motion-data.json';
  var MEDIA_PREFIX = '/tools/image/motion/';
  var MAX_ITEMS = 12;
  var SCROLL_PLAY_RATIO = 0.6;   // 展示区进入视口即播放
  var SCROLL_STOP_RATIO = 0.25;  // 低于这个比例视为离开，停止

  var hasIO = typeof window.IntersectionObserver === 'function';
  var mqReduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  var mqCoarse = window.matchMedia('(pointer: coarse)');
  var NO_MOTION_SCENES = ['wxcover', 'board'];   // 公众号封面、视频分镜首帧：成品本身是静图，不展示动态版

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
  function playbackPath(path) {
    return location.protocol === 'file:' ? new URL('motion/' + path.slice(MEDIA_PREFIX.length), location.href).href : path;
  }
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
      if (NO_MOTION_SCENES.indexOf(o.homepage_scene) !== -1) return;
      if (!dim(o.width) || !dim(o.height)) return;
      if (!(typeof o.duration === 'number' && o.duration > 0 && o.duration <= 60)) return;
      var poster = mediaPath(o.poster, 'poster.png');
      var video = mediaPath(o.video, 'share.mp4');
      if (!poster || !video) return;
      if (poster !== MEDIA_PREFIX + o.id + '/poster.png' || video !== MEDIA_PREFIX + o.id + '/share.mp4') return;
      var source = o.source == null ? null : mediaPath(o.source, 'source.png');
      if (o.source != null && source !== MEDIA_PREFIX + o.id + '/source.png') return;
      seen[o.id] = 1;
      out.push({
        id: o.id, title: o.title, styleCode: o.style_code || null,
        scene: o.scene, homeScene: o.homepage_scene || null, sceneLabel: o.scene_label,
        width: o.width, height: o.height, poster: playbackPath(poster), video: playbackPath(video), source: source ? playbackPath(source) : null,
        note: (typeof o.note === 'string' ? o.note : null)
      });
    });
    return out;
  }

  var items = [];

  /* 展示区同时播放可见卡片；画风库仍沿用单项预览。 */
  var players = [];
  var active = null;

  function updateBtn(p) {
    p.area.classList.toggle('is-moving', p.state === 'playing');
    if (!p.btn) return;
    var busy = p.state === 'loading' || p.state === 'playing';
    var label = busy ? '暂停' : (p.state === 'failed' ? '重试播放' : '播放动态');
    p.btn.textContent = label;
    p.btn.setAttribute('aria-label', label + (p.item ? '：' + p.item.title : ''));
  }
  function setStatus(p, msg) { if (p.status) p.status.textContent = msg || ''; }

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
    if (!p.item || (mode === 'auto' && mqReduce.matches)) return;
    if (mode === 'auto' && document.hidden) return;
    if (active && active !== p) stop(active);
    if (!p.viewportAuto) active = p;
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
    if (p.viewportAuto) { viewportTry(p); return; }
    if (mqCoarse.matches || !hasIO || !p.item || p.suppressed || p.state !== 'idle') return;
    if (!(p.hover || p.focus) || p.ratio < SCROLL_STOP_RATIO) return;
    if (mqReduce.matches || document.hidden) return;
    play(p, 'auto');
  }
  function viewportTry(p) {
    if (p.viewportAuto && hasIO && p.item && !p.suppressed && p.state === 'idle' && p.ratio >= SCROLL_PLAY_RATIO && !mqReduce.matches && !document.hidden) play(p, 'auto');
  }
  function mobileTry(p) {
    if (!mqCoarse.matches || !hasIO || !p.mobileAuto || !p.item || p.suppressed || p.state !== 'idle') return;
    if (mqReduce.matches || document.hidden) return;
    if (p.ratio >= SCROLL_PLAY_RATIO) play(p, 'auto');
  }
  function leaveCheck(p) {
    if (p.viewportAuto) return;
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
      v: v, btn: o.btn, status: o.status, area: o.area, mobileAuto: !!o.mobileAuto, viewportAuto: !!o.viewportAuto,
      item: null, state: 'idle', mode: null, gen: 0,
      hover: false, focus: false, suppressed: false, ratio: 0
    };
    v.addEventListener('error', function () {
      if (v.getAttribute('src') === null || p.state === 'idle') return;
      fail(p, '视频没有加载出来，点按钮可重试。');
    });
    if (o.btn) o.btn.addEventListener('click', function () { onBtn(p); });
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
    cleanupLibrary();
    players.slice().forEach(destroyPlayer);
    secEl = insertSection([h('p', { class: 'motion-note', role: 'status', text: '动态示例暂未加载' })]);
  }

  function buildShowcase() {
    var grid = h('div', { class: 'motion-gallery' });
    secEl = insertSection([grid]);
    if (!secEl) return;
    items.forEach(function (it) {
      var source = h('img', { src: it.source || it.poster, alt: '静态图：' + it.title, loading: 'lazy', decoding: 'async' });
      var original = h('div', { class: 'motion-original' }, [source, h('span', { class: 'motion-tag', text: it.source ? '原图' : '静态图' })]);
      var video = h('video', { class: 'motion-video', poster: it.poster });
      var frame = h('div', { class: 'motion-frame' }, [video, h('span', { class: 'motion-tag motion-tag-live', text: '动态版' })]);
      frame.style.aspectRatio = it.width + ' / ' + it.height;
      var btn = h('button', { class: 'motion-toggle', type: 'button' });
      var enlarge = h('button', { class: 'motion-enlarge', type: 'button', text: '放大', 'aria-label': '放大观看：' + it.title });
      var status = h('p', { class: 'motion-status', role: 'status' });
      var card = h('article', { class: 'motion-card' }, [
        h('div', { class: 'motion-pair' }, [original, frame]),
        h('div', { class: 'motion-card-foot' }, [h('h3', { text: it.title }), h('div', { class: 'motion-actions' }, [btn, enlarge])]), status
      ]);
      grid.appendChild(card);
      var player = makePlayer({ video: video, btn: btn, status: status, area: frame, viewportAuto: true });
      setItem(player, it);
      enlarge.addEventListener('click', function () { openMotion(it); });
    });
  }

  function openMotion(it) {
    stopAll();
    var video = h('video', { src: it.video, poster: it.poster, controls: true, muted: true, playsinline: true, loop: true });
    video.muted = true;
    var close = h('button', { class: 'btn', type: 'button', text: '关闭' });
    var dlg = h('dialog', { class: 'motion-lightbox', 'aria-label': it.title }, [close, video]);
    document.body.appendChild(dlg);
    function cleanup() { video.pause(); video.removeAttribute('src'); video.load(); dlg.remove(); players.forEach(viewportTry); }
    close.addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('close', cleanup, { once: true });
    dlg.showModal();
    if (!mqReduce.matches) { var playing = video.play(); if (playing && playing.catch) playing.catch(function () {}); }
  }

  /* ---------- 画风库卡片「会动」角标：按当前场景挑该画风的动态示例；
     桌面鼠标划入或键盘聚焦就播，手机滚到画面里自动播；公众号封面、分镜场景不出 ----------
     视频叠在卡片缩略图上，平时透明、预加载为 none，只有在播时才显示，卡片原图与点击逻辑不动。 */
  var libDeco = [];
  var libMO = null;
  function rmNode(n) { if (n && n.parentNode) n.parentNode.removeChild(n); }
  function libPicks(scene) {
    var by = {};
    if (NO_MOTION_SCENES.indexOf(scene) !== -1) return by;
    items.forEach(function (it) {
      if (!it.styleCode || !it.homeScene) return;
      if (scene !== 'all' && it.homeScene !== scene) return;
      if (!by[it.styleCode]) by[it.styleCode] = it;
    });
    return by;
  }
  function undecorate(w) {
    destroyPlayer(w.player);
    rmNode(w.video); rmNode(w.badge);
    w.li.classList.remove('motion-lib-item', 'is-moving');
  }
  function syncLibrary() {
    var grid = $('#lib-grid');
    if (!grid) return;
    var by = libPicks(grid.getAttribute('data-scene') || 'all');
    libDeco = libDeco.filter(function (w) {
      var ok = w.li.parentNode === grid && by[w.li.getAttribute('data-style')] === w.player.item;
      if (!ok) undecorate(w);
      return ok;
    });
    Object.keys(by).forEach(function (code) {
      var li = grid.querySelector('li[data-style="' + code + '"]');
      if (!li || li.classList.contains('motion-lib-item')) return;
      var card = li.querySelector('.style-card'), th = card && card.querySelector('.thumb');
      if (!th) return;
      var it = by[code];
      var video = h('video', { class: 'motion-video motion-lib-video', 'aria-hidden': 'true' });
      var badge = h('span', { class: 'motion-badge', text: '会动' });
      th.appendChild(video);
      th.appendChild(badge);
      li.classList.add('motion-lib-item');
      var player = makePlayer({ video: video, area: li, autoArea: card, mobileAuto: true });
      setItem(player, it);
      libDeco.push({ li: li, video: video, badge: badge, player: player });
    });
  }
  function watchLibrary() {
    var grid = $('#lib-grid');
    if (!grid) return;
    syncLibrary();
    if (!window.MutationObserver || libMO) return;
    libMO = new MutationObserver(syncLibrary);
    libMO.observe(grid, { attributes: true, attributeFilter: ['data-scene'], childList: true });
  }
  function cleanupLibrary() {
    if (libMO) { libMO.disconnect(); libMO = null; }
    libDeco.forEach(undecorate);
    libDeco = [];
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
    else players.forEach(function (p) { mobileTry(p); viewportTry(p); });
  });
  onMq(mqReduce, function () { stopAll(); if (!mqReduce.matches) players.forEach(viewportTry); });
  onMq(mqCoarse, stopAll);

  /* ---------- 启动 ---------- */
  function showItems(d) {
    items = parseItems(d);
    if (!items.length) throw new Error('empty');
    buildShowcase();
    if (!secEl) throw new Error('no-slot');
    watchLibrary();
    watchDialog();
  }
  function start() {
    if (location.protocol === 'file:') {
      try {
        var data = JSON.parse(document.getElementById('page-data').textContent);
        showItems(data.motionPreview);
      } catch (e) { items = []; buildFallback(); }
      return;
    }
    if (!window.fetch || !window.Promise) { buildFallback(); return; }
    var req;
    try { req = fetch(DATA_URL, { credentials: 'same-origin' }); } catch (e) { buildFallback(); return; }
    req.then(function (r) {
      if (!r.ok) throw new Error('http');
      return r.json();
    }).then(function (d) {
      showItems(d);
    }).catch(function () {
      items = [];
      destroyDlgBox();
      buildFallback();
    });
  }
  start();
})();
