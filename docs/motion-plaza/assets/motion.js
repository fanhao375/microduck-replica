// 详情页：按地址里的 ?id= 从 data/motions.js 取一个动作，把整页填出来。
// 有什么数据就出什么段落：播放器（都有）、八个瞬间（有 keys 才出）、关节曲线（data/curves.js 里有才出）、能不能装、包里有什么。
(function () {
  'use strict';
  var P = window.PLAZA, C = window.PLAZA_CURVES || {}, K = window.PLAZA_KEYS || {};
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  var id = new URLSearchParams(location.search).get('id');
  var m = P.motions.filter(function (x) { return x.id === id; })[0];

  if (!m) {
    $('#hero').innerHTML = '<div class="pad" style="padding-top: 60px; padding-bottom: 92px"><h1 style="margin: 0; font-weight: 900; font-size: clamp(48px, 8vw, 96px); letter-spacing: -0.03em">没找到这个动作</h1>' +
      '<p style="font-size: 19px; line-height: 1.75; margin: 20px 0 30px">地址里的动作名不在目录里。</p><a class="btn k" href="index.html#all">回全部动作</a></div>';
    $('#retrain').innerHTML = '<a class="btn o" href="index.html">回动作广场</a>';
    return;
  }
  document.title = m.name + ' · Microduck 动作广场';
  var dir = 'motions/' + m.id + '/', src = P.sources[m.source], curve = C[m.id];

  // ---- 首屏：名字、说明、按钮 + 播放器 ----
  var action = m.download ? '<a class="btn k" href="' + esc(m.download.href) + '" download>' + esc(m.download.label) + '</a>'
    : '<span class="state">' + esc(m.source === 'builtin' ? '镜像自带，不用装' : (m.downloadNote || '文件还没放上来')) + '</span>';
  if (m.files || m.checks) action += '<a class="btn o" href="#files">' + (m.files ? '看看包里有什么' : '怎么用') + '</a>';
  $('#hero').innerHTML =
    '<div class="pad two" style="display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 560px); gap: 64px; align-items: center; padding-top: 28px; padding-bottom: 92px">' +
    '<div><div class="mono" style="font-size: 13px; letter-spacing: 0.06em">动作广场 / ' + esc(src) + ' / ' + esc(m.name) + '</div>' +
    '<h1 style="margin: 18px 0 0; font-weight: 900; font-size: clamp(64px, ' + (m.name.length > 4 ? '9vw, 132px' : '13vw, 190px') + '); line-height: 0.95; letter-spacing: -0.04em">' + esc(m.name) + '</h1>' +
    '<div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 26px"><span class="mono" style="font-size: 14px; margin-right: 6px">' + esc(m.id) + '</span>' +
    '<span class="pill">' + esc(src) + '</span>' + (m.pills || []).map(function (p) { return '<span class="pill">' + esc(p) + '</span>'; }).join('') +
    (m.status ? '<span class="pill" style="' + (m.status.kind === 'solid' ? 'background: #fff' : 'border-style: dashed') + '">' + esc(m.status.text) + '</span>' : '') + '</div>' +
    '<p style="margin: 24px 0 0; max-width: 520px; font-size: 19px; line-height: 1.75">' + esc(m.long || m.desc) + '</p>' +
    '<div style="display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-top: 30px">' + action + '</div></div>' +
    '<div style="background: #fff; border: 2px solid #0E0E0C; border-radius: 24px; box-shadow: 10px 10px 0 #0E0E0C; padding: 14px">' +
    '<div style="position: relative; border-radius: 14px; overflow: hidden; aspect-ratio: 1 / 1; background: ' + esc(m.bg || '#FFC21F') + '">' +
    '<img src="' + dir + 'poster.webp" alt="' + esc(m.name) + '的仿真预览" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block">' +
    '<video id="v" muted loop playsinline preload="auto" src="' + dir + 'preview.mp4" aria-label="' + esc(m.name) + '的仿真回放" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block"></video>' +
    '<span style="position: absolute; left: 12px; top: 12px; font-size: 12.5px; font-weight: 500; padding: 5px 11px; border-radius: 999px; background: #0E0E0C; color: #fff">仿真回放</span></div>' +
    '<div style="display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 14px">' +
    '<button type="button" class="ctl" id="toggle" aria-label="暂停" style="width: 52px; height: 52px; flex: none; border-radius: 50%; background: #0E0E0C; display: flex; align-items: center; justify-content: center">' +
    '<svg id="ico-pause" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><rect x="3" y="2" width="4" height="14" rx="1" fill="#fff"/><rect x="11" y="2" width="4" height="14" rx="1" fill="#fff"/></svg>' +
    '<svg id="ico-play" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" style="display: none"><path d="M5 2.5v13l11-6.5z" fill="#fff"/></svg></button>' +
    '<button type="button" class="ctl" id="track" aria-label="进度条，点一下跳到那个位置" style="flex: 1 1 180px; min-width: 0; height: 44px; background: transparent; display: flex; align-items: center">' +
    '<span style="position: relative; display: block; width: 100%; height: 10px; border-radius: 999px; background: #E6E2D8; overflow: hidden"><span id="prog" style="position: absolute; left: 0; top: 0; bottom: 0; width: 0; background: #F0621A"></span></span></button>' +
    '<span class="mono" id="time" style="font-size: 13.5px; white-space: nowrap">0.0 / – s</span>' +
    '<button type="button" class="ctl mono" id="slow" style="font-size: 13.5px; background: transparent; color: #0E0E0C; border: 1.5px solid #0E0E0C; border-radius: 999px; min-height: 44px; padding: 0 16px">1×</button>' +
    '</div></div></div>';

  // ---- 中间几段 ----
  // 分解图：目录里自己写了 keys 的用专门渲染的（竖图）；没写的用从预览视频里截的（方图）
  var keyT = m.keys || K[m.id], keySq = !m.keys;
  var html = '';
  if (keyT) {
    html += '<section class="pad" style="padding-top: 100px; padding-bottom: 96px"><div class="head"><div><div class="mono eyebrow">动作分解</div><h2 class="h2">' + ['', '一', '两', '三', '四', '五', '六', '七', '八'][keyT.length] + '个瞬间</h2></div>' +
      '<p style="max-width: 400px; font-size: 16.5px; line-height: 1.8; color: #3A372F; margin: 0">点一格，上面的播放器就跳到那一刻。每一格都是仿真里录下来的。</p></div>' +
      '<div class="keys" style="display: grid; grid-template-columns: repeat(' + keyT.length + ', minmax(0, 1fr)); gap: 10px; margin-top: 44px">' +
      keyT.map(function (t, i) { return '<button type="button" class="key' + (keySq ? ' sq' : '') + '" data-t="' + t + '"><img src="' + dir + 'keys/' + i + '.webp" alt="' + t.toFixed(1) + ' 秒" loading="lazy"><span class="mono" style="font-size: 12.5px">' + t.toFixed(1) + ' s</span></button>'; }).join('') +
      '</div></section>';
  }
  if (curve) {
    var COL = ['#D99A00', '#F0621A', '#0E0E0C', '#9A6B3A', '#2A9D8F'], big = 1;
    curve.joints.forEach(function (j) { big = Math.max(big, Math.abs(j.min), Math.abs(j.max)); });
    var k = 118 / big, n = curve.joints[0].pts.length;
    var paths = curve.joints.map(function (j, i) {
      return '<path d="' + j.pts.map(function (d, x) { return (x ? 'L' : 'M') + (x / (n - 1) * 1000).toFixed(1) + ',' + (140 - d * k).toFixed(1); }).join(' ') +
        '" fill="none" stroke="' + COL[i] + '" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
    }).reverse().join('');
    function sign(v) { return (v < 0 ? '−' : '+') + Math.abs(v) + '°'; }
    var band = curve.band ? '<div aria-hidden="true" style="position: absolute; left: ' + (curve.band[0] * 100).toFixed(1) + '%; width: ' + ((curve.band[1] - curve.band[0]) * 100).toFixed(1) + '%; top: 0; bottom: 0; background: #FFF1C9"></div>' +
      '<span class="mono" style="position: absolute; left: ' + (curve.band[0] * 100).toFixed(1) + '%; top: 12px; padding-left: 12px; font-size: 12px; color: #4A4538">动作这一段 ' + ((curve.band[1] - curve.band[0]) * curve.dur).toFixed(1) + ' s</span>' +
      (curve.band[0] > 0.16 ? '<span class="mono" style="position: absolute; left: 14px; top: 12px; font-size: 12px; color: #6B6558">站着</span>' : '') : '';
    html += '<section class="pad" style="background: #FFF6DC; color: #0E0E0C; padding-top: 96px; padding-bottom: 96px"><div class="head"><div><div class="mono eyebrow">关节曲线</div><h2 class="h2">关节怎么动</h2></div>' +
      '<p style="max-width: 420px; font-size: 16.5px; line-height: 1.8; color: #4A4538; margin: 0">仿真里录下来、动得最多的' + ['', '一', '两', '三', '四', '五'][curve.joints.length] + '个关节，画的是相对站姿转了多少度。竖线跟着播放器走。</p></div>' +
      '<div style="position: relative; margin-top: 44px; background: #fff; border: 2px solid #0E0E0C; border-radius: 18px; overflow: hidden">' + band +
      '<svg viewBox="0 0 1000 280" preserveAspectRatio="none" role="img" aria-label="' + esc(m.name) + '时几个关节的角度曲线" style="position: relative; display: block; width: 100%; height: 300px">' +
      '<line x1="0" y1="140" x2="1000" y2="140" stroke="#D6D1C4" stroke-width="1" vector-effect="non-scaling-stroke"/>' + paths + '</svg>' +
      '<div id="cursor" aria-hidden="true" style="position: absolute; top: 0; bottom: 0; left: 0; width: 2px; background: #0E0E0C"></div></div>' +
      '<div style="display: flex; gap: 10px 28px; flex-wrap: wrap; margin-top: 22px; font-size: 14.5px">' +
      curve.joints.map(function (j, i) { return '<span style="display: flex; align-items: center; gap: 8px"><span style="width: 18px; height: 4px; border-radius: 2px; background: ' + COL[i] + '"></span>' + esc(j.label) + '<span class="mono" style="color: #6B6558">' + sign(j.min) + ' ~ ' + sign(j.max) + '</span></span>'; }).join('') +
      '</div></section>';
  }
  var ICON = {
    ok: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" style="flex: none; margin-top: 2px"><circle cx="11" cy="11" r="10" fill="#FFC21F" stroke="#0E0E0C" stroke-width="1.5"/><path d="M6.5 11.5l3 3 6-6.5" fill="none" stroke="#0E0E0C" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    half: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" style="flex: none; margin-top: 2px"><circle cx="11" cy="11" r="10" fill="#fff" stroke="#0E0E0C" stroke-width="1.5"/><path d="M11 1a10 10 0 0 1 0 20z" fill="#0E0E0C"/></svg>',
    todo: '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" style="flex: none; margin-top: 2px"><circle cx="11" cy="11" r="10" fill="#fff" stroke="#0E0E0C" stroke-width="1.5" stroke-dasharray="3 3"/></svg>'
  };
  var WORD = { ok: '没问题：', half: '要留意：', todo: '还没做：' };
  var left = m.checks ? '<div class="box"><h2 style="margin: 0 0 18px; font-size: 30px; font-weight: 900; letter-spacing: -0.01em">' + (m.source === 'builtin' ? '怎么用' : '能不能装到复刻版上') + '</h2>' +
    m.checks.map(function (c) { return '<div class="row">' + ICON[c.s] + '<span><span style="position: absolute; left: -9999px">' + WORD[c.s] + '</span>' + esc(c.t) + '</span></div>'; }).join('') + '</div>' : '';
  var right = '';
  if (m.files) {
    right = '<div class="box"><h2 style="margin: 0 0 18px; font-size: 30px; font-weight: 900; letter-spacing: -0.01em">包里有什么</h2>' +
      m.files.map(function (f) { return '<div class="frow"><span class="mono" style="font-size: 15px; font-weight: 500; flex: 1 1 150px">' + esc(f.name) + '</span><span style="font-size: 14px; color: #55524A; flex: 1 1 130px">' + esc(f.what) + '</span><span style="font-family: \'Space Grotesk\', sans-serif; font-weight: 700; white-space: nowrap">' + esc(f.size) + '</span></div>'; }).join('') +
      (m.filesTotal ? '<div class="frow" style="border-top: 2px solid #0E0E0C"><span style="font-size: 15px; font-weight: 700; flex: 1 1 150px">合起来</span><span style="font-family: \'Space Grotesk\', sans-serif; font-size: 20px; font-weight: 700; white-space: nowrap">' + esc(m.filesTotal) + '</span></div>' : '') +
      (m.credit ? '<div class="frow" style="font-size: 14px; line-height: 1.7; color: #3A372F"><span style="flex: 1 1 100%">作者 <b>' + esc(m.credit.author) + '</b>，' + esc(m.credit.license) + ' 许可。来源：<a href="' + esc(m.credit.url) + '" target="_blank" rel="noopener" style="text-underline-offset: 4px; overflow-wrap: anywhere">' + esc(m.credit.text) + '</a></span></div>' : '') +
      '</div>';
  }
  if (left || right) html += '<section class="pad two" id="files" style="display: grid; grid-template-columns: ' + (left && right ? 'minmax(0, 1.15fr) minmax(0, 1fr)' : 'minmax(0, 720px)') + '; gap: 24px; padding-top: 100px; padding-bottom: 100px; align-items: start">' + left + right + '</section>';
  $('#main').innerHTML = html;

  // ---- 底下那段：想自己练一个？ ----
  $('#retrain').innerHTML = '<h2 style="font-weight: 900; font-size: clamp(40px, 5vw, 68px); line-height: 1.08; letter-spacing: -0.02em; margin: 0">想自己练一个类似的？</h2>' +
    '<p style="max-width: 640px; font-size: 17.5px; line-height: 1.85; margin: 22px 0 0">' + esc(m.retrain || '用仓库里的训练流程新开一个任务；练好以后在仿真里跑一遍，就能生成和这页一样的预览。') + '</p>' +
    '<div style="display: flex; gap: 14px; flex-wrap: wrap; margin-top: 34px">' +
    (m.retrainLink ? '<a class="btn k" href="' + esc(m.retrainLink.href) + '" target="_blank" rel="noopener">' + esc(m.retrainLink.label) + '</a>' : '') +
    '<a class="btn ' + (m.retrainLink ? 'o' : 'k') + '" href="' + esc(P.trainUrl) + '" target="_blank" rel="noopener">看训练流程</a>' +
    '<a class="btn o" href="index.html#all">回动作广场</a></div>';

  // ---- 播放器 ----
  var v = $('#v'), dur = curve ? curve.dur : 0, slow = false;
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var keys = $$('.key'), cursor = $('#cursor');
  function setPlaying(on) {
    $('#ico-pause').style.display = on ? 'block' : 'none'; $('#ico-play').style.display = on ? 'none' : 'block';
    $('#toggle').setAttribute('aria-label', on ? '暂停' : '播放');
  }
  function update() {
    var t = v.currentTime || 0, pct = (dur ? Math.max(0, Math.min(100, t / dur * 100)) : 0).toFixed(1) + '%';
    $('#prog').style.width = pct; if (cursor) cursor.style.left = pct;
    $('#time').textContent = t.toFixed(1) + ' / ' + (dur ? dur.toFixed(1) : '–') + ' s';
    var cur = 0; keys.forEach(function (b, i) { if (t >= +b.getAttribute('data-t') - 0.3) cur = i; });
    keys.forEach(function (b, i) { b.classList.toggle('on', i === cur); });
  }
  function seek(t) { try { v.currentTime = Math.max(0, Math.min((dur || t) - 0.05, t)); } catch (e) {} update(); }
  v.muted = true; v.loop = true;
  v.addEventListener('loadedmetadata', function () { if (isFinite(v.duration)) dur = v.duration; update(); });
  $('#toggle').addEventListener('click', function () {
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function () {}); setPlaying(true); } else { v.pause(); setPlaying(false); }
  });
  $('#slow').addEventListener('click', function () { slow = !slow; v.playbackRate = slow ? 0.4 : 1; this.textContent = slow ? '0.4×' : '1×'; });
  $('#track').addEventListener('click', function (e) { var r = this.getBoundingClientRect(); if (dur) seek((e.clientX - r.left) / r.width * dur); });
  keys.forEach(function (b) { b.addEventListener('click', function () { seek(+b.getAttribute('data-t')); }); });
  if (still) setPlaying(false);
  else { var p = v.play(); if (p && p.catch) p.catch(function () { setPlaying(false); }); }
  (function tick() { if (!v.paused) update(); requestAnimationFrame(tick); })();
  update();
})();
