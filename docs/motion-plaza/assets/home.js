// 首页：按 data/motions.js 把卡片、筛选、数字、跑马灯、「还在路上的」填出来；悬停播放预览；鸭子的头跟着鼠标转。
(function () {
  'use strict';
  var P = window.PLAZA, M = P.motions;
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- 数字 ----
  var count = { all: M.length, real: M.filter(function (m) { return m.real; }).length };
  Object.keys(P.sources).forEach(function (k) { count[k] = M.filter(function (m) { return m.source === k; }).length; });
  $$('[data-n]').forEach(function (el) { el.textContent = count[el.getAttribute('data-n')] || 0; });
  $$('[data-n2]').forEach(function (el) { var n = count[el.getAttribute('data-n2')] || 0; el.textContent = (n < 10 ? '0' : '') + n; });

  // ---- 跑马灯 ----
  var star = '<span style="color: #F0621A">✦</span>';
  var row = M.map(function (m) { return '<span>' + esc(m.short || m.name) + '</span>' + star; }).join('');
  $('#mq').innerHTML = row + row + row + row;

  // ---- 卡片 ----
  var NAME = 'font-weight: 900; font-size: 30px; letter-spacing: -0.01em; margin: 16px 6px 0; display: flex; align-items: baseline; justify-content: space-between; gap: 10px';
  var DESC = 'font-size: 14.5px; line-height: 1.65; color: #3A372F; margin: 8px 6px 0; min-height: 48px';
  var META = 'display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 14px 6px 0; font-size: 13px';
  function card(m, i) {
    var dir = 'motions/' + m.id + '/', no = (i < 9 ? '0' : '') + (i + 1);
    var st = m.status ? '<span class="pill" style="' + (m.status.kind === 'solid' ? 'background: #FFC21F' : 'border-style: dashed') + '">' + esc(m.status.text) + '</span>' : '';
    return '<a class="card" href="motion.html?id=' + encodeURIComponent(m.id) + '" data-card="1" data-tags="' + m.source + (m.real ? ' real' : '') + '">' +
      '<div class="stage"><img class="p" src="' + dir + 'poster.webp" alt="' + esc(m.name) + '的仿真预览">' +
      '<video muted loop playsinline preload="none" src="' + dir + 'preview.mp4"></video>' +
      '<span class="chip mono" style="left: 10px; background: #0E0E0C; color: #fff">' + no + '</span>' +
      '<span class="chip" style="right: 10px; background: #fff; color: #0E0E0C; border: 1.5px solid #0E0E0C">仿真</span>' +
      (m.real ? '<img class="shot" src="' + dir + 'real.jpg" alt="' + esc(m.name) + '的实拍">' : '') + '</div>' +
      '<div style="' + NAME + '"><span>' + esc(m.name) + '</span><span class="mono" style="font-size: 12px; font-weight: 400; color: #55524A">' + esc(m.id) + '</span></div>' +
      '<p style="' + DESC + '">' + esc(m.desc) + '</p>' +
      '<div style="' + META + '"><span class="pill">' + esc(P.sources[m.source]) + '</span>' + st + '<span class="mono" style="margin-left: auto">' + esc(m.meta || '') + '</span></div></a>';
  }
  var grid = $('#grid');
  grid.innerHTML = M.map(card).join('');

  // ---- 筛选 ----
  var tabs = [['all', '全部']].concat(Object.keys(P.sources).map(function (k) { return [k, P.sources[k]]; })).concat([['real', '有实拍']])
    .filter(function (t) { return count[t[0]] > 0; });
  var tabBox = $('#tabs'), filter = 'all';
  tabBox.innerHTML = tabs.map(function (t) { return '<button type="button" class="tab" data-f="' + t[0] + '">' + esc(t[1]) + ' ' + count[t[0]] + '</button>'; }).join('');
  function paint() {
    $$('.tab', tabBox).forEach(function (b) {
      var on = b.getAttribute('data-f') === filter;
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.style.background = on ? '#0E0E0C' : 'transparent'; b.style.color = on ? '#F4F1EA' : '#0E0E0C';
    });
    $$('.card', grid).forEach(function (c) {
      c.hidden = !(filter === 'all' || (' ' + c.getAttribute('data-tags') + ' ').indexOf(' ' + filter + ' ') >= 0);
    });
  }
  tabBox.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.tab'); if (!b) return;
    filter = b.getAttribute('data-f'); paint();
  });
  paint();

  // ---- 悬停 / 聚焦时播放预览 ----
  function play(c) { var v = c && c.querySelector('video'); if (!v) return; v.muted = true; var p = v.play(); if (p && p.catch) p.catch(function () {}); }
  function stop(c) { var v = c && c.querySelector('video'); if (!v) return; v.pause(); try { v.load(); } catch (e) {} }
  function cardOf(e) { return e.target.closest && e.target.closest('[data-card]'); }
  grid.addEventListener('pointerover', function (e) { var c = cardOf(e); if (c && !c.contains(e.relatedTarget)) play(c); });
  grid.addEventListener('pointerout', function (e) { var c = cardOf(e); if (c && !c.contains(e.relatedTarget)) stop(c); });
  grid.addEventListener('focusin', function (e) { play(cardOf(e)); });
  grid.addEventListener('focusout', function (e) { stop(cardOf(e)); });

  // ---- 还在路上的 ----
  var STAGES = [
    ['idea', '想好了，还没训', '动作长什么样、打算怎么训，都定了，还没开始跑。'],
    ['training', '训练中', '已经跑过，效果还不行，接着调。'],
    ['await', '等上真机', '仿真里过了，还没在真鸭子上试。']
  ];
  var FROM = { self: '自己训', open: '开源仓库', community: '群友' };
  function todo(p) {
    return '<div class="todo"><div style="display: flex; align-items: baseline; justify-content: space-between; gap: 10px"><span style="font-weight: 900; font-size: 22px">' + esc(p.name) + '</span>' +
      '<span class="mono" style="font-size: 12px; color: #55524A">' + esc(p.id || '') + '</span></div>' +
      '<p style="margin: 6px 0 0; font-size: 14.5px; line-height: 1.65; color: #3A372F">' + esc(p.note || '') + '</p>' +
      '<div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; font-size: 13px"><span class="pill">' + esc(FROM[p.from] || p.from || '') + '</span></div></div>';
  }
  var idea = '<a class="todo" href="#give" style="display: flex; align-items: center; justify-content: center; min-height: 56px; text-decoration: none; font-weight: 700; font-size: 15px; color: #0E0E0C; background: transparent">＋ 我也有个想法</a>';
  $('#lanes').innerHTML = STAGES.map(function (s, i) {
    var items = (P.plan || []).filter(function (p) { return p.stage === s[0]; }).map(todo).join('') + (s[0] === 'idea' ? idea : '');
    return '<div class="lane"><div style="display: flex; align-items: baseline; gap: 12px"><span style="font-family: \'Space Grotesk\', sans-serif; font-size: 40px; font-weight: 700; color: #E2570F; line-height: 1">' + (i + 1) + '</span>' +
      '<h3 style="margin: 0; font-size: 21px; font-weight: 700">' + s[1] + '</h3></div>' +
      '<p style="margin: 8px 0 18px; font-size: 14.5px; line-height: 1.7; color: #4A4538">' + s[2] + '</p>' + items + '</div>';
  }).join('');

  // ---- GitHub 入口：仓库地址填了才接上 ----
  if (P.repo) $$('[data-repo]').forEach(function (a) { a.href = P.repo.replace(/\/$/, '') + a.getAttribute('data-repo'); a.target = '_blank'; a.rel = 'noopener'; });

  // ---- 首屏的鸭子：鼠标在哪边就往哪边看，没人动就自己慢慢左右看 ----
  var hero = $('.hero'), duck = $('#duck'), look = -1, auto = true, t0 = performance.now();
  function setLook(v) {
    var f = Math.max(0, Math.min(16, Math.round(v))); if (f === look) return; look = f;
    duck.style.backgroundPosition = ((f % 6) / 5 * 100) + '% ' + (Math.floor(f / 6) / 2 * 100) + '%';
  }
  setLook(8);
  if (hero && duck) {
    hero.addEventListener('pointermove', function (e) {
      var box = hero.getBoundingClientRect(), cx = box.right - 375, half = Math.max(240, box.width * 0.42);
      auto = false; setLook((Math.max(-1, Math.min(1, (e.clientX - cx) / half)) + 1) * 8);
    });
    hero.addEventListener('pointerleave', function () { auto = true; t0 = performance.now(); });
    if (!still) (function tick(now) { if (auto) setLook(8 + Math.sin((now - t0) / 1400) * 5); requestAnimationFrame(tick); })(performance.now());
  }
})();
