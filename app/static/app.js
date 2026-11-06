/* 商品图库 · 前端主逻辑 */

const state = { offset: 0, limit: 48, total: 0, loading: false, view: 'library' };

const $ = (id) => document.getElementById(id);
const grid = $('grid');
const emptyEl = $('empty');
const moreBtn = $('more');

/**
 * 安全绑定：元素不存在时只告警并跳过，不抛出。
 * 目的：某个可选按钮缺失时，其余功能照常初始化（不吞掉业务错误，只处理绑定本身）。
 */
function bind(id, type, handler, options) {
  const el = $(id);
  if (!el) {
    console.warn(`[图库] 未找到元素 #${id}，已跳过 ${type} 事件绑定`);
    return null;
  }
  el.addEventListener(type, handler, options);
  return el;
}

const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const modelOf = (i) => i.model_name || i.model_dir || i.display_filename || i.filename || '未命名型号';

function badgeClass(s) {
  if (s === '已匹配') return 'ok';
  if (s === '待处理') return 'warn';
  return 'err';
}

function fmtSize(b) {
  if (!b) return '—';
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
  return Math.round(b / 1024) + ' KB';
}

function fmtNum(n) {
  return (n ?? 0).toLocaleString('zh-CN');
}

/* ---------- 概览 ---------- */

async function loadOverview() {
  const r = await fetch('/api/v1/images-overview');
  if (!r.ok) return null;
  const d = await r.json();
  $('s-total').textContent = fmtNum(d.total);
  $('s-bytes').textContent = fmtSize(d.total_bytes);
  $('s-models').textContent = fmtNum(d.models);
  $('s-pending').textContent = fmtNum(d.pending);
  $('s-brands').textContent = fmtNum(d.brands);
  return d;
}

async function loadInfo() {
  const d = await loadOverview();
  if (!d) return;
  const rows = [
    ['系统名称', '商品图库 · 图片资产库'],
    ['版本', 'V0.3.1'],
    ['图片总量', `${fmtNum(d.total)} 张 · ${fmtSize(d.total_bytes)}`],
    ['品牌 / 型号 / 颜色', `${fmtNum(d.brands)} / ${fmtNum(d.models)} / ${fmtNum(d.colors)}`],
    ['匹配情况', `已匹配 ${fmtNum(d.matched)} · 待处理 ${fmtNum(d.pending)} · 未匹配 ${fmtNum(d.unmatched)}`],
    ['数据来源', '飞书云盘「白底图库」+ 拍拍盖亚标准商品数据'],
    ['图片存储', '服务器本地资产目录，按 file_token 命名'],
    ['访问控制', 'HTTP Basic Auth（全站）'],
    ['更新方式', '入库脚本增量同步'],
  ];
  $('infoList').innerHTML = rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
}

/* ---------- 筛选器 ---------- */

async function loadFacets() {
  const r = await fetch('/api/v1/images-facets');
  if (!r.ok) return;
  const d = await r.json();
  d.brands.forEach((b) => {
    const o = document.createElement('option');
    o.value = b.value;
    o.textContent = `${b.value} (${b.count})`;
    $('brand').appendChild(o);
  });
  d.colors.forEach((c) => {
    const o = document.createElement('option');
    o.value = c.value;
    o.textContent = `${c.value} (${c.count})`;
    $('color').appendChild(o);
  });
}

function queryParams() {
  const p = new URLSearchParams({ limit: state.limit, offset: state.offset });
  const q = $('q').value.trim();
  if (q) p.set('q', q);
  if ($('brand').value) p.set('brand', $('brand').value);
  if ($('color').value) p.set('color', $('color').value);
  if ($('status').value) p.set('status', $('status').value);
  return p;
}

/* ---------- 卡片 ---------- */

function cardHTML(it) {
  const color = it.display_color || '';
  const dim = (it.width && it.height) ? `${it.width}×${it.height}` : '—';
  return `
    <div class="thumb"><img loading="lazy" src="${it.thumb_url}" alt="${esc(modelOf(it))}"></div>
    <div class="cap">
      <div class="model" title="${esc(modelOf(it))}">${esc(modelOf(it))}</div>
      <div class="sub">
        <span class="chip">${esc(it.display_brand || '—')}</span>
        <span class="color">${esc(color || '颜色未知')}</span>
        <span class="badge ${badgeClass(it.match_status)}">${esc(it.match_status)}</span>
      </div>
      <div class="meta">${dim} · ${esc((it.format || '').toUpperCase() || '—')} · ${fmtSize(it.size_bytes)}</div>
    </div>`;
}

function makeCard(it) {
  const el = document.createElement('article');
  el.className = 'card';
  el.innerHTML = cardHTML(it);
  el.addEventListener('click', () => openDrawer(it));
  return el;
}

/* ---------- 列表 ---------- */

async function load(reset = false) {
  if (state.loading) return;
  state.loading = true;
  if (reset) { state.offset = 0; grid.innerHTML = ''; }
  try {
    const r = await fetch('/api/v1/images?' + queryParams());
    if (!r.ok) { $('count').textContent = `加载失败（${r.status}）`; return; }
    const d = await r.json();
    state.total = d.total;
    d.items.forEach((it) => grid.appendChild(makeCard(it)));
    state.offset += d.items.length;
    $('count').textContent = `共 ${fmtNum(d.total)} 张`
      + (d.total > state.offset ? `，已显示 ${fmtNum(state.offset)}` : '');
    if (emptyEl) emptyEl.hidden = d.total !== 0;
    if (moreBtn) moreBtn.hidden = state.offset >= d.total;
  } finally {
    state.loading = false;
  }
}

/* ---------- 抽屉 ---------- */

// 分享链接固定使用正式域名根路径，避免带上 /assets 等历史入口
const SHARE_ORIGIN = 'https://picsystem.changziqi.com';
const shareURL = (id) => `${SHARE_ORIGIN}/?image=${encodeURIComponent(id)}`;

let toastTimer;
function toast(msg, ok = true) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast' + (ok ? '' : ' err');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

async function copyShareLink() {
  const url = $('d-share').value;
  if (!url) return false;
  let done = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(url);
      done = true;
    }
  } catch (e) {
    done = false;
  }
  if (!done) {                       // 回退：非安全上下文或权限被拒
    try {
      const ta = document.createElement('textarea');
      ta.value = url;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      done = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (e) {
      done = false;
    }
  }
  toast(done ? '链接已复制到剪贴板' : '复制失败，请手动选中上方链接复制', done);
  return done;
}

function fillKV(rows) {
  $('d-kv').innerHTML = rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
}

function openDrawer(it) {
  if (!$('drawer')) {
    console.warn('[图库] 详情抽屉元素缺失，已跳过打开');
    return;
  }
  $('d-model').textContent = modelOf(it);
  $('d-brand').textContent = [it.display_brand, it.display_color].filter(Boolean).join(' · ') || '—';
  const st = $('d-status');
  st.textContent = it.match_status || '—';
  st.className = 'badge ' + badgeClass(it.match_status);

  $('d-img').src = it.file_url;
  $('d-download').href = it.download_url;
  $('d-open').href = it.file_url;
  $('d-share').value = shareURL(it.id);
  $('d-share').title = $('d-share').value;   // 悬停可看完整链接
  $('toast').hidden = true;

  fillKV([
    ['标准品牌', it.display_brand || '—'],
    ['标准型号', modelOf(it)],
    ['标准颜色', it.display_color || '未标注'],
    ['图片尺寸', (it.width && it.height) ? `${it.width} × ${it.height} px` : '—'],
    ['格式与大小', `${(it.format || '—').toUpperCase()} · ${fmtSize(it.size_bytes)}`],
    ['原始文件名', it.filename || '—'],
    ['飞书原始目录', it.source_path || '—'],
    ['匹配状态', it.match_status || '—'],
    ['匹配方式', it.match_method || '—'],
  ]);

  syncURL('?image=' + encodeURIComponent(it.id));
  $('mask').hidden = false;
  $('drawer').hidden = false;
}

/** 让地址栏与当前查看状态一致，便于直接复制分享。 */
function syncURL(search) {
  const next = location.pathname + (search || '') + (location.hash || '');
  history.replaceState(null, '', next);
}

function closeDrawer() {
  $('drawer').hidden = true;
  $('mask').hidden = true;
  $('d-img').src = '';
  $('toast').hidden = true;
  if (new URLSearchParams(location.search).get('image')) syncURL('');
}

/* ---------- 视图切换 ---------- */

const VIEW_META = {
  library: ['图片资产库', '白底图 · 按品牌 / 型号 / 颜色检索'],
  info: ['系统基础信息', '运行状态、数据规模与接口说明'],
  models: ['标准型号管理', '规划中'],
};

function switchView(name) {
  if (name === state.view) return;
  state.view = name;
  ['library', 'info', 'models'].forEach((v) => {
    $('view-' + v).hidden = v !== name;
  });
  document.querySelectorAll('.nav-item[data-view]').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === name);
  });
  const [title, sub] = VIEW_META[name] || ['', ''];
  $('pageTitle').textContent = title;
  $('pageSub').textContent = sub;
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  $('q').style.display = name === 'library' ? '' : 'none';
  if (name === 'info') loadInfo();
  $('sidebar').classList.remove('open');
}

/* ---------- 事件 ---------- */

let debounce;
bind('q', 'input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => load(true), 280);
});
['brand', 'color', 'status'].forEach((id) => bind(id, 'change', () => load(true)));
bind('reset', 'click', () => {
  ['q', 'brand', 'color', 'status'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
  load(true);
});
bind('more', 'click', () => load(false));
bind('d-close', 'click', closeDrawer);
bind('d-copy', 'click', copyShareLink);
bind('d-share', 'focus', (e) => e.target.select());   // 聚焦即全选，便于手动复制
bind('mask', 'click', closeDrawer);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
document.querySelectorAll('.nav-item[data-view]').forEach((b) => {
  b.addEventListener('click', () => { if (!b.disabled) switchView(b.dataset.view); });
});
bind('menuBtn', 'click', () => $('sidebar')?.classList.toggle('open'));

/* ---------- 启动 ---------- */

loadFacets();
loadOverview();
load(true);

// 可分享的图片深链：?image=<id>
const imgParam = new URLSearchParams(location.search).get('image');
if (imgParam) {
  fetch('/api/v1/images/' + encodeURIComponent(imgParam))
    .then((r) => (r.ok ? r.json() : null))
    .then((it) => { if (it) openDrawer(it); })
    .catch(() => {});
}

const initial = (location.hash || '').replace('#', '');
if (VIEW_META[initial] && initial !== 'library') {
  state.view = 'library';           // 允许 switchView 生效
  switchView(initial);
}
window.addEventListener('hashchange', () => {
  const v = (location.hash || '').replace('#', '');
  if (VIEW_META[v]) switchView(v);
});
