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
  var NO_MOTION_SCENES = ['wxcover', 'board', 'ppt'];

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

  var categories = [], defaultCategory = 'all';
  function parseCategories(d) {
    categories = []; defaultCategory = 'all';
    if (!d || (!Object.prototype.hasOwnProperty.call(d, 'categories') && !Object.prototype.hasOwnProperty.call(d, 'default_category'))) return;
    if (!Array.isArray(d.categories) || !d.categories.length || d.categories.length > 8) throw new Error('categories');
    var seen = Object.create(null);
    d.categories.forEach(function (c) {
      if (!c || typeof c !== 'object' || typeof c.id !== 'string' || !/^[A-Za-z0-9_-]{1,32}$/.test(c.id) || c.id === 'all' || seen[c.id] ||
          !str(c.label, 1, 20) || !c.label.trim() || !str(c.description, 1, 240) || !c.description.trim()) throw new Error('category');
      seen[c.id] = 1;
      categories.push({ id: c.id, label: c.label, description: c.description });
    });
    if (!seen[d.default_category]) throw new Error('default category');
    defaultCategory = d.default_category;
  }

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
      if (o.placement != null && ['featured', 'brand', 'background'].indexOf(o.placement) === -1) return;
      if (o.category != null && !categories.some(function (c) { return c.id === o.category; })) return;
      if (categories.length && (!o.placement || o.placement === 'featured') && !o.category) return;
      if (o.source_label != null && !(str(o.source_label, 1, 24) && o.source_label.trim())) return;
      if (o.tags != null && (!Array.isArray(o.tags) || !o.tags.length || o.tags.length > 4 || o.tags.some(function (t, i) { return !str(t, 1, 16) || !t.trim() || o.tags.indexOf(t) !== i; }))) return;
      if (o.context != null && (typeof o.context !== 'object' || !str(o.context.title, 1, 100) || !str(o.context.excerpt, 1, 1200) || !str(o.context.purpose, 1, 300))) return;
      if (NO_MOTION_SCENES.indexOf(o.homepage_scene) !== -1) return;
      if (!dim(o.width) || !dim(o.height)) return;
      if (!(typeof o.duration === 'number' && o.duration > 0 && o.duration <= 60)) return;
      var poster = mediaPath(o.poster, 'poster.png');
      var video = mediaPath(o.video, 'share.mp4');
      if (!poster || !video) return;
      if (poster !== MEDIA_PREFIX + o.id + '/poster.png' || video !== MEDIA_PREFIX + o.id + '/share.mp4') return;
      var source = o.source == null ? null : mediaPath(o.source, 'source.png');
      if (o.source != null && source !== MEDIA_PREFIX + o.id + '/source.png') return;
      var gif = o.gif == null ? null : mediaPath(o.gif, 'share.gif');
      if (o.gif != null && (gif !== MEDIA_PREFIX + o.id + '/share.gif' || !/^[a-f0-9]{64}$/.test(o.gif_sha256 || ''))) return;
      if (o.gif == null && o.gif_sha256 != null) return;
      seen[o.id] = 1;
      out.push({
        id: o.id, title: o.title, styleCode: o.style_code || null,
        scene: o.scene, homeScene: o.homepage_scene || null, sceneLabel: o.scene_label,
        placement: o.placement || 'featured', duration: o.duration,
        width: o.width, height: o.height, poster: playbackPath(poster), video: playbackPath(video), source: source ? playbackPath(source) : null, gif: gif ? playbackPath(gif) : null,
        note: (typeof o.note === 'string' ? o.note : null), category: o.category || null,
        tags: o.tags || [], context: o.context || null, sourceLabel: o.source_label || '原始静图 · 完整画幅'
      });
    });
    return out;
  }

  var items = [], categoryCards = false;

  /* 作品墙最多同时播放两支可见作品，其余入口仍按单支交互播放。 */
  var players = [];
  var active = null;
  var globalPaused = false, modalOpen = false, modalVideo = null;

  function updateBtn(p) {
    p.area.classList.toggle('is-moving', p.state === 'playing');
    if (!p.btn) return;
    var busy = p.state === 'loading' || p.state === 'playing';
    if (p.gifBtn) p.gifBtn.setAttribute('aria-pressed', String(p.state === 'gif'));
    var label = p.state === 'gif' ? '暂停 GIF' : busy ? '暂停' : (p.state === 'failed' ? '重试播放' : '播放动态');
    p.btn.textContent = label;
    p.btn.setAttribute('aria-label', label + (p.item ? '：' + p.item.title : ''));
  }
  function setStatus(p, msg) { if (p.status) p.status.textContent = msg || ''; }

  function releaseSrc(p) {
    if (p.gifNode) { p.gifNode.hidden = true; p.gifNode.removeAttribute('src'); }
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
    if (mode === 'auto' && (document.hidden || globalPaused || modalOpen)) return;
    if (mode === 'auto' && p.viewportAuto && players.filter(function (other) { return other !== p && other.viewportAuto && (other.state === 'loading' || other.state === 'playing'); }).length >= 2) return;
    players.slice().forEach(function (other) {
      if (other !== p && other.state !== 'idle' && !(mode === 'auto' && p.viewportAuto && other.viewportAuto)) stop(other);
    });
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
    if (p.state === 'loading' || p.state === 'playing' || p.state === 'gif') { p.suppressed = true; stop(p); return; }
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
    players.forEach(viewportTry);
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
        h('h2', { class: 'sec-title', id: 'motion-title', text: '让插图动起来' }),
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

  function showcaseCard(it) {
    var source = h('img', { src: it.source || it.poster, alt: (it.source ? '原图：' : '静态图：') + it.title, loading: 'lazy', decoding: 'async' });
    var original = h('figure', { class: 'motion-source' }, [h('a', { href: it.source || it.poster, target: '_blank', rel: 'noopener' }, [source]), h('figcaption', { text: it.source ? it.sourceLabel : '静态备用图' })]);
    var video = h('video', { class: 'motion-video', poster: it.poster });
    var gif = h('img', { class: 'motion-gif', alt: '实际编码的 GIF：' + it.title, hidden: true });
    var frame = h('div', { class: 'motion-frame' }, [video, gif]);
    frame.style.aspectRatio = it.width + ' / ' + it.height;
    var btn = h('button', { class: 'motion-toggle', type: 'button' });
    var replay = h('button', { class: 'motion-replay', type: 'button', text: '重播', 'aria-label': '重播：' + it.title });
    var gifBtn = it.gif ? h('button', { class: 'motion-gif-toggle', type: 'button', text: '看实际 GIF', 'aria-pressed': 'false' }) : null;
    var status = h('p', { class: 'motion-status', role: 'status' });
    var card = h('article', { class: 'motion-card' }, [
      h('div', { class: 'motion-pair-legend' }, [h('span', { text: it.source ? it.sourceLabel : '静态备用图' }), h('span', { class: 'motion-legend-live', text: '横向动态 · ' + it.duration + ' 秒' })]),
      h('div', { class: 'motion-pair' }, [original, h('span', { class: 'motion-arrow', 'aria-hidden': 'true', text: '→' }), frame]),
      h('div', { class: 'motion-caption' }, [h('h3', { text: it.title }), h('p', { text: itemNote(it) })]),
      h('div', { class: 'motion-actions' }, [btn, replay,
        h('button', { class: 'motion-enlarge', type: 'button', text: it.context ? '放大与原文' : '放大观看', 'aria-label': '放大观看：' + it.title })]),
      detailsNode(it, gifBtn), status
    ]);
    card.setAttribute('data-motion-id', it.id);
    function sourceLoaded() { if (source.naturalWidth > source.naturalHeight) $('.motion-pair', card).classList.add('motion-pair-wide'); }
    source.addEventListener('load', sourceLoaded); if (source.complete) sourceLoaded();
    var player = makePlayer({ video: video, btn: btn, status: status, area: frame, viewportAuto: true });
    player.gifNode = gif; player.gifBtn = gifBtn;
    setItem(player, it);
    replay.addEventListener('click', function () { stop(player); player.suppressed = false; play(player, 'manual'); });
    if (gifBtn) gifBtn.addEventListener('click', function () {
      if (player.state === 'gif') { player.suppressed = true; stop(player); return; }
      stopAll(); player.suppressed = true; gif.hidden = false; gif.src = it.gif;
      player.state = 'gif'; active = player;
      setStatus(player, '正在播放实际 GIF；点「暂停 GIF」返回静帧。'); updateBtn(player);
    });
    gif.addEventListener('error', function () { if (gif.getAttribute('src')) fail(player, 'GIF 没有加载出来，可重试或下载查看。'); });
    $('.motion-enlarge', card).addEventListener('click', function () { openMotion(it); });
    return { node: card, player: player };
  }

  function detailContent(it) {
    var content = h('div', { class: 'motion-detail-content' });
    content.appendChild(h('p', { class: 'motion-meta', text: [it.styleCode, it.duration + ' 秒', it.tags.join(' · ')].filter(Boolean).join(' · ')}));
    if (it.context) {
      content.appendChild(h('p', { class: 'motion-context-title', text: it.context.title }));
      content.appendChild(h('blockquote', { text: it.context.excerpt }));
      content.appendChild(h('p', { text: it.context.purpose }));
    }
    var links = h('div', { class: 'motion-downloads' }, [
      it.gif ? h('a', { href: it.gif, download: it.id + '.gif', text: '下载 GIF' }) : null,
      h('a', { href: it.poster, download: it.id + '-still.png', text: '下载静态备用图' }),
      h('a', { href: it.video, download: it.id + '.mp4', text: '下载 MP4' })]);
    content.appendChild(links);
    return content;
  }
  function detailsNode(it, gifBtn) {
    return h('details', { class: 'motion-details' }, [
      h('summary', { text: it.context ? '看这段原文与下载' : '作品说明与下载' }),
      detailContent(it), gifBtn
    ]);
  }

  function buildShowcase() {
    var featured = items.filter(function (it) { return it.placement === 'featured'; });
    if (categories.length && !featured.some(function (it) { return it.category === defaultCategory; })) throw new Error('empty default category');
    var options = categories.filter(function (c) { return featured.some(function (it) { return it.category === c.id; }); });
    if (!categoryCards) options.push({id: 'all', label: '全部', description: '看看不同题材怎样因变化而更容易观看和理解。'});
    var tabs = h('div', { class: 'motion-choices', role: 'tablist', 'aria-label': '动态作品分类' });
    var pause = h('button', { type: 'button', class: 'motion-pause-all', text: '全部暂停', 'aria-pressed': 'false' });
    var description = h('p', { class: 'motion-category-description', 'aria-live': 'polite' });
    var stage = h('div', { class: 'motion-exhibit motion-gallery', id: 'motion-selected', role: 'tabpanel' });
    secEl = insertSection([
      h('p', { class: 'motion-intro', text: categoryCards ? '一张竖图，展开成横向动态。选一种题材，看看照片、知识和风景各自怎样动起来。' : '看看不同题材怎样动起来：沿着过程读懂知识，在光影与相册里留住一段情境。' }),
      h('div', { class: 'motion-filter-bar' }, [tabs, pause]), description, stage
    ]);
    if (!secEl) return;
    secEl.classList.toggle('motion-category-cards', categoryCards);
    var selected = defaultCategory, current = [], buttons = [];
    function select(id, focus) {
      current.forEach(destroyPlayer); current = []; stage.textContent = '';
      selected = id;
      featured.filter(function (it) { return id === 'all' || it.category === id; }).forEach(function (it) {
        var card = showcaseCard(it); current.push(card.player); stage.appendChild(card.node);
      });
      stage.classList.toggle('motion-exhibit-single', current.length === 1);
      var at = options.findIndex(function (o) { return o.id === id; });
      stage.setAttribute('aria-labelledby', 'motion-category-' + at);
      description.textContent = options[at].description;
      buttons.forEach(function (button, i) { button.setAttribute('aria-selected', String(i === at)); button.tabIndex = i === at ? 0 : -1; });
      if (focus) buttons[at].focus();
    }
    options.forEach(function (opt, i) {
      var count = featured.filter(function (it) { return opt.id === 'all' || it.category === opt.id; }).length;
      var button = h('button', { type: 'button', role: 'tab', id: 'motion-category-' + i, 'aria-controls': 'motion-selected', 'aria-selected': 'false', tabindex: '-1', text: opt.label + ' ' + count });
      if (categoryCards) {
        var example = featured.find(function (it) { return it.category === opt.id; });
        button.textContent = '';
        button.appendChild(h('img', { src: example.poster, alt: '', loading: 'lazy' }));
        button.appendChild(h('span', { class: 'motion-category-name', text: opt.label }));
        button.appendChild(h('span', { class: 'motion-category-count', text: count + ' 件作品' }));
      }
      button.setAttribute('data-category', opt.id);
      button.addEventListener('click', function () { select(opt.id, false); });
      button.addEventListener('keydown', function (e) {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(e.key) === -1) return;
        e.preventDefault();
        var at = options.findIndex(function (o) { return o.id === selected; });
        var next = e.key === 'Home' ? 0 : e.key === 'End' ? options.length - 1 : (at + (e.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
        select(options[next].id, true);
      });
      buttons.push(button); tabs.appendChild(button);
    });
    pause.addEventListener('click', function () {
      globalPaused = !globalPaused;
      pause.setAttribute('aria-pressed', String(globalPaused)); pause.textContent = globalPaused ? '继续自动播放' : '全部暂停';
      if (globalPaused) stopAll(); else players.forEach(viewportTry);
    });
    if (featured.length) select(defaultCategory, false);
    else { tabs.hidden = true; pause.hidden = true; stage.removeAttribute('role'); stage.appendChild(h('p', { class: 'motion-note', text: '横版动态示例正在更新，可以先浏览上方的静态画风与样图。' })); }
    var brands = items.filter(function (it) { return it.placement === 'brand'; });
    if (brands.length) {
      var brandBody = h('div', { class: 'motion-brand-gallery' });
      var brand = h('details', { class: 'motion-brand' }, [h('summary', { text: '品牌示例' }), brandBody]);
      var brandPlayers = [];
      brand.addEventListener('toggle', function () {
        brandPlayers.forEach(destroyPlayer); brandPlayers = []; brandBody.textContent = '';
        if (brand.open) brands.forEach(function (it) { var c = showcaseCard(it); brandBody.appendChild(c.node); brandPlayers.push(c.player); });
      });
      $('.motion-body', secEl).appendChild(brand);
    }
  }

  function openMotion(it) {
    modalOpen = true; stopAll();
    var video = h('video', { src: it.video, poster: it.poster, muted: true, playsinline: true, loop: true });
    modalVideo = video;
    video.muted = true; video.playsInline = true;
    var close = h('button', { class: 'btn', type: 'button', text: '关闭' });
    var toggle = h('button', { class: 'btn', type: 'button', text: '播放' });
    var replay = h('button', { class: 'btn', type: 'button', text: '从头播放' });
    var controls = h('div', { class: 'motion-modal-controls' }, [h('h3', { text: it.title }), toggle, replay, close]);
    var dlg = h('dialog', { class: 'motion-lightbox', 'aria-label': it.title }, [video, controls, detailContent(it)]);
    document.body.appendChild(dlg);
    function startVideo() { var r = video.play(); if (r && r.catch) r.catch(function () { toggle.textContent = '重试播放'; }); }
    video.addEventListener('play', function () { toggle.textContent = '暂停'; });
    video.addEventListener('pause', function () { toggle.textContent = '播放'; });
    video.addEventListener('error', function () { toggle.textContent = '重试播放'; });
    toggle.addEventListener('click', function () { if (video.paused) startVideo(); else video.pause(); });
    replay.addEventListener('click', function () { video.currentTime = 0; startVideo(); });
    function cleanup() { video.pause(); video.removeAttribute('src'); video.load(); dlg.remove(); modalOpen = false; modalVideo = null; players.forEach(viewportTry); }
    close.addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('close', cleanup, { once: true });
    dlg.showModal();
    if (!mqReduce.matches && !globalPaused) startVideo();
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
      if (it.placement !== 'featured') return;
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
    for (var i = 0; i < items.length; i++) if (items[i].placement === 'featured' && items[i].styleCode === m[1]) { it = items[i]; break; }
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
    if (document.hidden) { stopAll(); if (modalVideo) modalVideo.pause(); }
    else players.forEach(function (p) { mobileTry(p); viewportTry(p); });
  });
  onMq(mqReduce, function () { stopAll(); if (mqReduce.matches && modalVideo) modalVideo.pause(); if (!mqReduce.matches) players.forEach(viewportTry); });
  onMq(mqCoarse, stopAll);

  /* ---------- 启动 ---------- */
  function showItems(d) {
    categoryCards = d && d.showcase === 'portrait-to-landscape';
    if (d && Object.prototype.hasOwnProperty.call(d, 'showcase') && !categoryCards) throw new Error('showcase');
    parseCategories(d);
    items = parseItems(d);
    if (categoryCards && (!categories.length || categories.some(function (c) {
      var subset = items.filter(function (it) { return it.placement === 'featured' && it.category === c.id; });
      return !subset.length || subset.length > 2 || subset.some(function (it) { return !it.source || Math.abs(it.width / it.height / (16 / 9) - 1) > .01; });
    }))) throw new Error('portrait showcase');
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
