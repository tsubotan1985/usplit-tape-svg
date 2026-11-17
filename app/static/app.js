'use strict';

const S = {
  job: null,
  model: null,
  faces: [],
  face: null,
  drop: [],
  cut: [],
  pts: {},
  sel: null,
  stats: null,
  svg: null,
  actual: false,
  busy: false,
  timer: null,
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------ theme */
(function initTheme() {
  const btn = $('theme');
  const set = (t) => {
    document.documentElement.dataset.theme = t;
    btn.textContent = t === 'dark' ? '☀' : '☾';
    try { localStorage.setItem('usplit-tape-svg.theme', t); } catch (e) {}
  };
  set(document.documentElement.dataset.theme || 'light');
  btn.addEventListener('click', () => set(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
})();

/* ------------------------------------------------------------ health */
fetch('/api/health').then((r) => r.json()).then((j) => {
  if (j.version) $('ver').textContent = 'v' + j.version;
}).catch(() => {});

/* ------------------------------------------------------------ upload */
const drop = $('drop');
const fileInput = $('file');
drop.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => fileInput.files[0] && upload(fileInput.files[0]));
['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => {
  ev.preventDefault(); drop.classList.add('drag');
}));
['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, (ev) => {
  ev.preventDefault(); drop.classList.remove('drag');
}));
drop.addEventListener('drop', (ev) => {
  const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
  if (f) upload(f);
});

async function upload(file) {
  msg('読み込み中…');
  const fd = new FormData();
  fd.append('file', file, file.name);
  try {
    const res = await fetch('/api/analyze', { method: 'POST', body: fd });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.statusText);
    S.job = j.job; S.model = j.model; S.faces = j.faces; S.face = null; S.stats = null;
    S.drop = []; S.cut = []; S.pts = {}; S.sel = null;
    fileInfo(j);
    renderFaces(j);
    if (j.faces.length) selectFace(j.faces[0].id);
    msg(j.n_faces > j.faces.length
      ? `平面 ${j.n_faces} 枚のうち、面積の大きい ${j.faces.length} 枚を表示しています。`
      : `平面 ${j.n_faces} 枚を検出しました。`, 'ok');
  } catch (e) {
    msg('読み込み失敗: ' + e.message);
  }
}

function fileInfo(j) {
  const b = j.bbox;
  const dx = (b[3] - b[0]).toFixed(1), dy = (b[4] - b[1]).toFixed(1), dz = (b[5] - b[2]).toFixed(1);
  const warn = (b[5] - b[2]) < 0.5 ? '' : '';
  $('fileinfo').innerHTML = `
    <dl>
      <dt>ファイル</dt><dd>${esc(j.model)}</dd>
      <dt>三角形</dt><dd>${j.tris.toLocaleString()} / ${j.size_mb} MB</dd>
      <dt>外形</dt><dd>${dx} × ${dy} × ${dz} mm</dd>
      <dt>解析</dt><dd>${j.sec} s</dd>
    </dl>${warn}`;
}

function renderFaces(j) {
  $('facecount').textContent = `全 ${j.n_faces} 面`;
  const box = $('faces');
  box.innerHTML = '';
  j.faces.forEach((f) => {
    const el = document.createElement('button');
    el.className = 'face';
    el.dataset.id = f.id;
    const holes = f.n_holes
      ? `<span class="tag hole">穴 ${f.n_holes}</span>` : '';
    const dia = f.hole_d.filter((d) => d > 0).map((d) => 'φ' + d.toFixed(1)).join(' ');
    el.innerHTML = `
      <span class="thumb">${f.thumb}</span>
      <span>
        <span class="t">${esc(f.axis)} 面 <span class="tag">${f.offset.toFixed(2)} mm</span>${holes}</span>
        <span class="m">${f.w.toFixed(1)} × ${f.h.toFixed(1)} mm ・ ${f.area.toFixed(0)} mm²</span>
        ${dia ? `<span class="m">${dia}</span>` : ''}
      </span>`;
    el.addEventListener('click', () => selectFace(f.id));
    box.appendChild(el);
  });
}

function selectFace(id) {
  S.face = id;
  S.drop = [];
  S.cut = [];
  S.pts = {};
  S.sel = null;
  document.querySelectorAll('.face').forEach((el) => el.classList.toggle('on', el.dataset.id === id));
  const f = S.faces.find((x) => x.id === id);
  $('facesel').textContent = f ? `${esc(f.axis)} 面 / ${f.offset.toFixed(2)} mm / ${f.n_holes}穴` : '';
  update();
}

/* ------------------------------------------------------------ options */
const OPT_IDS = ['offset', 'margin', 'copies', 'gap', 'tabs', 'tablen', 'rotate', 'mirrorx', 'mirrory', 'arcfit', 'guides'];
OPT_IDS.forEach((id) => {
  $(id).addEventListener('input', schedule);
  $(id).addEventListener('change', schedule);
});

function opts() {
  return {
    offset_mm: num('offset', 0),
    margin_mm: num('margin', 4),
    copies: Math.max(1, Math.round(num('copies', 1))),
    copy_gap_mm: num('gap', 3),
    tabs: Math.max(0, Math.round(num('tabs', 0))),
    tab_len_mm: num('tablen', 1.2),
    rotate: parseInt($('rotate').value, 10) || 0,
    mirror_x: $('mirrorx').checked,
    mirror_y: $('mirrory').checked,
    arc_fit: $('arcfit').checked,
    guides: $('guides').checked,
    drop_pieces: S.drop.slice(),
    contours: S.cut.map((c) => ({
      id: c.id, include: c.include, order: c.order, start_index: c.start_index, dir: c.dir,
    })),
  };
}
function num(id, dflt) {
  const v = parseFloat($(id).value);
  return Number.isFinite(v) ? v : dflt;
}
function schedule() {
  clearTimeout(S.timer);
  S.timer = setTimeout(update, 220);
}

/* ------------------------------------------------------------ generate */
async function update() {
  if (!S.job || !S.face) return;
  $('msg').textContent = '計算中…';
  $('msg').className = 'spin';
  try {
    const res = await fetch('/api/svg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job: S.job, face: S.face, opts: opts() }),
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.statusText);
    S.svg = j.svg; S.stats = j.stats;
    syncCut(j.stats);
    renderPreview();
    renderStats();
    renderCutList();
    $('msg').textContent = '';
    $('msg').className = '';
    $('dl').disabled = false;
    $('dlpng').disabled = false;
  } catch (e) {
    $('msg').textContent = '生成できません: ' + e.message;
    $('msg').className = '';
    $('dl').disabled = true;
    $('dlpng').disabled = true;
  }
}

function renderPreview() {
  const host = $('preview');
  const txt = S.svg.replace(/^<\?xml[^>]*\?>\s*/, '');
  host.innerHTML = txt;
  const svg = host.querySelector('svg');
  if (!svg) return;
  // holes in accent, outline in ink (preview only; the file itself is all black)
  svg.querySelectorAll('g[data-kind]').forEach((g) => {
    g.classList.add(g.dataset.kind === 'hole' ? 'cut-hole' : 'cut-outer');
    g.querySelectorAll('path').forEach((p) => p.classList.add('cut'));
  });
  // sheet outline + cut order numbers (preview only)
  const W = S.stats.w, H = S.stats.h;
  const fs = Math.max(2.2, Math.max(W, H) / 45);
  const layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  layer.setAttribute('class', 'labels');
  const sheet = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  sheet.setAttribute('x', 0); sheet.setAttribute('y', 0);
  sheet.setAttribute('width', W); sheet.setAttribute('height', H);
  sheet.setAttribute('fill', 'none');
  sheet.setAttribute('stroke', 'var(--line)');
  sheet.setAttribute('stroke-dasharray', (W / 90).toFixed(2) + ' ' + (W / 150).toFixed(2));
  sheet.setAttribute('stroke-width', (W / 400).toFixed(2));
  layer.appendChild(sheet);
  (S.stats.order || []).forEach((o) => {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    // holes: centre of the hole; outline: its entry point, nudged outwards
    const hole = o.kind === 'hole';
    let ty = hole ? o.cy : o.sy - fs * 0.9;
    ty = Math.min(Math.max(ty, fs * 1.1), H - fs * 0.7);
    t.setAttribute('x', hole ? o.cx : o.sx);
    t.setAttribute('y', ty);
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('dominant-baseline', 'middle');
    t.setAttribute('font-size', fs.toFixed(2));
    t.setAttribute('class', 'lbl');
    if (!hole) t.style.fill = 'var(--ink-2)';
    t.textContent = o.n;
    layer.appendChild(t);
  });
  (S.stats.order || []).forEach((o) => {
    if (!o.id) return;
    const g = svg.querySelector(`g[data-cut-order="${o.n}"]`);
    if (g && S.sel === o.id) g.classList.add('sel');
    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', o.sx);
    dot.setAttribute('cy', o.sy);
    dot.setAttribute('r', Math.max(0.45, fs * 0.26).toFixed(2));
    dot.setAttribute('class', 'startdot' + (S.sel === o.id ? ' on' : ''));
    layer.appendChild(dot);
  });
  svg.appendChild(layer);
  applyZoom();
  $('stagenote').textContent = `全 ${S.stats.n_cuts} カット / ${S.stats.copies} 個`;
}

/* ------------------------------------------------------- cut order editor */
function syncCut(s) {
  const rows = s.contours || [];
  S.pts = {};
  rows.forEach((t) => { if (t.pts) S.pts[t.id] = t.pts; });
  if (!S.cut.length) {
    const seq = [];
    (s.order || []).forEach((r) => {
      if (!r.id) return;
      const t = rows.find((x) => x.id === r.id) || {};
      seq.push({
        id: r.id, include: true, order: seq.length + 1,
        start_index: r.start_index, dir: r.dir,
        kind: r.kind, d: t.d, w: t.w, h: t.h, length: r.length,
      });
    });
    S.cut = seq;
    if (!S.sel && S.cut.length) S.sel = S.cut[0].id;
  } else {
    S.cut.forEach((c) => {
      const t = rows.find((x) => x.id === c.id);
      if (t) { c.kind = t.kind; c.d = t.d; c.w = t.w; c.h = t.h; }
    });
  }
  if (S.sel && !S.cut.find((c) => c.id === S.sel)) S.sel = null;
}

function renderCutList() {
  const box = $('cutlist');
  if (!box) return;
  if (!S.cut.length) {
    box.innerHTML = '<p class="muted">面を選ぶとカット線が並びます。</p>';
    return;
  }
  box.innerHTML = '';
  S.cut.forEach((c, i) => {
    const row = document.createElement('div');
    row.className = 'cup' + (c.id === S.sel ? ' on' : '') + (c.include ? '' : ' off');
    const size = (c.kind === 'hole' && c.d)
      ? 'φ' + c.d.toFixed(1)
      : `${(c.w || 0).toFixed(1)}×${(c.h || 0).toFixed(1)}`;
    row.innerHTML =
      `<span class="idx">${i + 1}</span>` +
      `<span class="ic">${c.kind === 'hole' ? '◯' : '▭'}</span>` +
      `<span class="nm">${c.kind === 'hole' ? '穴' : '輪郭'} <b>${size}</b></span>` +
      `<span class="len">${(c.length || 0).toFixed(1)} mm</span>` +
      `<span class="st">` +
      ['top|上', 'bottom|下', 'left|左', 'right|右'].map(
        (t) => { const [a, l] = t.split('|'); return `<button data-act="${a}" title="${l}端から切る">${l}</button>`; }
      ).join('') +
      `</span>` +
      `<button data-act="dir" class="dr" title="回転方向">${c.dir === 'cw' ? '↻' : '↺'}</button>` +
      `<span class="mv"><button data-act="up" title="上へ">↑</button><button data-act="down" title="下へ">↓</button></span>` +
      `<button data-act="toggle" class="tg" title="${c.include ? 'この線を切らない' : 'この線を切る'}">${c.include ? '×' : '＋'}</button>`;
    row.addEventListener('click', (ev) => {
      const act = ev.target.dataset ? ev.target.dataset.act : null;
      S.sel = c.id;
      if (act === 'up') moveCut(i, -1);
      else if (act === 'down') moveCut(i, 1);
      else if (act === 'dir') { c.dir = c.dir === 'cw' ? 'ccw' : 'cw'; schedule(); }
      else if (act === 'toggle') { c.include = !c.include; schedule(); }
      else if (['top', 'bottom', 'left', 'right'].includes(act)) { setStartExtreme(c, act); schedule(); }
      else { renderCutList(); renderPreview(); }
    });
    box.appendChild(row);
  });
  $('cuthint').textContent = S.sel
    ? `選択中: ${S.cut.findIndex((c) => c.id === S.sel) + 1} 番 — プレビューをクリックで開始位置`
    : '行を選んでプレビューをクリックすると開始位置';
}

function moveCut(i, d) {
  const j = i + d;
  if (j < 0 || j >= S.cut.length) return;
  const t = S.cut[i];
  S.cut[i] = S.cut[j];
  S.cut[j] = t;
  S.cut.forEach((c, k) => { c.order = k + 1; });
  schedule();
}

function setStartExtreme(c, mode) {
  const pts = S.pts[c.id];
  if (!pts || !pts.length) return;
  let best = 0;
  let bv = null;
  pts.forEach((p, i) => {
    const v = mode === 'top' ? p[1] : mode === 'bottom' ? -p[1] : mode === 'left' ? p[0] : -p[0];
    if (bv === null || v < bv) { bv = v; best = i; }
  });
  c.start_index = best;
}

$('preview').addEventListener('click', (ev) => {
  if (!S.sel || !S.pts[S.sel]) return;
  const svg = $('preview').querySelector('svg');
  if (!svg || !svg.getScreenCTM()) return;
  const pt = svg.createSVGPoint();
  pt.x = ev.clientX;
  pt.y = ev.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  const pts = S.pts[S.sel];
  let best = 0;
  let bd = Infinity;
  pts.forEach((q, i) => {
    const d = (q[0] - p.x) ** 2 + (q[1] - p.y) ** 2;
    if (d < bd) { bd = d; best = i; }
  });
  const c = S.cut.find((x) => x.id === S.sel);
  if (!c) return;
  c.start_index = best;
  schedule();
});

function applyZoom() {
  const svg = $('preview').querySelector('svg');
  if (!svg || !S.stats) return;
  if (S.actual) {
    svg.style.width = S.stats.w + 'mm';
    svg.style.height = S.stats.h + 'mm';
    svg.style.maxWidth = 'none';
  } else {
    const stage = $('stage');
    const availW = Math.max(200, stage.clientWidth - 40);
    const availH = Math.max(220, Math.min(window.innerHeight * 0.46, 470));
    const k = Math.min(availW / S.stats.w, availH / S.stats.h, 12);
    svg.style.width = (S.stats.w * k).toFixed(1) + 'px';
    svg.style.height = (S.stats.h * k).toFixed(1) + 'px';
    svg.style.maxWidth = 'none';
  }
}

$('actual').addEventListener('click', () => {
  S.actual = !S.actual;
  $('actual').classList.toggle('on', S.actual);
  $('actual').textContent = S.actual ? '画面に合わせる' : '実寸表示';
  applyZoom();
});

/* ------------------------------------------------------------ play */
$('play').addEventListener('click', async () => {
  const svg = $('preview').querySelector('svg');
  if (!svg || !S.stats) return;
  const groups = [...svg.querySelectorAll('g[data-kind]')];
  groups.forEach((g) => {
    g.querySelectorAll('path').forEach((p) => {
      const L = p.getTotalLength();
      p.style.strokeDasharray = L;
      p.style.strokeDashoffset = L;
    });
    g.classList.add('playing');
    g.classList.remove('active');
  });
  let total = 0;
  const plan = groups.map((g) => {
    const L = [...g.querySelectorAll('path')].reduce((a, p) => a + p.getTotalLength(), 0);
    total += L;
    return { g, L };
  });
  const SPEED = 220; // mm per second
  for (const step of plan) {
    step.g.classList.add('active');
    const dur = Math.max(120, (step.L / SPEED) * 1000);
    await animate(step.g, dur);
    step.g.classList.remove('active');
  }
  groups.forEach((g) => {
    g.querySelectorAll('path').forEach((p) => {
      p.style.strokeDasharray = '';
      p.style.strokeDashoffset = '';
    });
    g.classList.remove('playing');
  });
});

function animate(g, dur) {
  return new Promise((done) => {
    const paths = [...g.querySelectorAll('path')].map((p) => ({ p, L: p.getTotalLength() }));
    const t0 = performance.now();
    function frame(t) {
      const k = Math.min(1, (t - t0) / dur);
      paths.forEach(({ p, L }) => { p.style.strokeDashoffset = L * (1 - k); });
      if (k < 1) requestAnimationFrame(frame); else done();
    }
    requestAnimationFrame(frame);
  });
}

/* ------------------------------------------------------------ stats */
function renderStats() {
  const s = S.stats;
  $('k-sheet').textContent = `${s.w.toFixed(1)} × ${s.h.toFixed(1)} mm`;
  $('k-piece').textContent = `${s.piece_w.toFixed(1)} × ${s.piece_h.toFixed(1)} mm`;
  $('k-ncuts').textContent = `${s.n_cuts}（穴 ${s.order.filter((o) => o.kind === 'hole').length} / 輪郭 ${s.order.filter((o) => o.kind !== 'hole').length}）`;
  $('k-len').textContent = `${s.cut_length.toFixed(1)} mm`;
  $('legenddim').textContent = `シート ${s.w.toFixed(1)} × ${s.h.toFixed(1)} mm`;
  renderPieces(s);
}

function renderPieces(s) {
  const box = $('pieces');
  const list = s.pieces || [];
  if (list.length < 2) { box.innerHTML = ''; return; }
  box.innerHTML = '<div class="ph">ピース（外すもののチェックを外す）</div>';
  list.forEach((p) => {
    const lab = document.createElement('label');
    const on = !S.drop.includes(p.id);
    lab.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}>` +
      `<span>ピース${esc(p.id)}</span>` +
      `<span class="sz">${p.w.toFixed(1)}×${p.h.toFixed(1)}</span>` +
      `<span class="hl">${p.holes ? '穴 ' + p.holes : '穴なし'}</span>`;
    lab.querySelector('input').addEventListener('change', (e) => {
      if (e.target.checked) S.drop = S.drop.filter((x) => x !== p.id);
      else if (!S.drop.includes(p.id)) S.drop.push(p.id);
      S.cut = [];  // ピース構成が変わったらカット一覧は作り直す
      S.sel = null;
      schedule();
    });
    box.appendChild(lab);
  });
}

/* ------------------------------------------------------------ download */
function baseName() {
  const base = (S.model || 'usplit').replace(/\.[^.]+$/, '').replace(/[^\w\-]+/g, '_');
  const f = S.faces.find((x) => x.id === S.face) || {};
  const d = new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
  return `${base}_${(f.axis || 'face').replace(/[^\w\-]/g, '')}${(f.offset ?? 0).toFixed(2)}_${stamp}`;
}

function saveBlob(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  msg('書き出しました: ' + name, 'ok');
}

$('dl').addEventListener('click', () => {
  if (!S.svg) return;
  saveBlob(new Blob([S.svg], { type: 'image/svg+xml' }), baseName() + '.svg');
});

$('dlpng').addEventListener('click', () => {
  if (!S.svg || !S.stats) return;
  const dpi = parseInt($('dpi').value, 10) || 300;
  const k = dpi / 25.4;
  const w = Math.round(S.stats.w * k);
  const h = Math.round(S.stats.h * k);
  const src = S.svg
    .replace(/^<\?xml[^>]*\?>\s*/, '')
    .replace(/width="[\d.]+mm" height="[\d.]+mm"/, `width="${w}" height="${h}"`);
  const img = new Image();
  img.onload = () => {
    const cv = document.createElement('canvas');
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    cv.toBlob((b) => { if (b) saveBlob(b, `${baseName()}_${dpi}dpi.png`); else msg('PNGの生成に失敗しました'); }, 'image/png');
  };
  img.onerror = () => msg('PNGに変換できませんでした');
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(src);
});

/* ------------------------------------------------------------ misc */
function msg(t, cls) {
  $('msg').textContent = t;
  $('msg').className = cls || '';
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
