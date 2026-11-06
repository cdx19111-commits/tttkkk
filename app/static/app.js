/* 商品图库 · 前端主逻辑（V0.3.2 筛选器） */

const state = {
  offset: 0, limit: 48, total: 0, loading: false, view: 'library',
  color: '',            // 颜色改由自定义下拉维护，不再依赖 <select>
  facets: null,
  seq: 0,               // 每次筛选变更递增，用于丢弃过期的响应
};

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

/** 型号展示：只有 standard 才是已确认的标准型号；其余一律按目录原始名展示。 */
function modelView(it) {
  if (it.model_source === 'standard' && it.model_name) {
    return { text: it.model_name, raw: false };
  }
  const raw = (it.model_dir || '').trim() || (it.filename || '').trim();
  return { text: raw || '未识别型号', raw: true };
}

const modelOf = (i) => modelView(i).text;

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
  const f = state.facets;
  const groups = f ? f.groups.map((g) => `${g.label} ${fmtNum(g.count)}`).join(' · ') : '—';
  const rows = [
    ['系统名称', '商品图库 · 图片资产库'],
    ['版本', 'V0.3.2'],
    ['图片总量', `${fmtNum(d.total)} 张 · ${fmtSize(d.total_bytes)}`],
    ['品牌 / 型号 / 颜色', `${fmtNum(d.brands)} / ${fmtNum(d.models)} / ${fmtNum(d.colors)}`],
    ['品牌组', groups],
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

/** 当前筛选条件；品牌组与品牌是两级，颜色单独存在 state。 */
function currentFilters() {
  return {
    q: ($('q')?.value || '').trim(),
    group: $('group')?.value || '',
    brand: $('brand')?.value || '',
    color: state.color,
    status: $('status')?.value || '',
  };
}

function facetURL(f) {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.status) p.set('status', f.status);
  if (f.group) p.set('group', f.group);
  if (f.brand) p.set('brand', f.brand);
  return '/api/v1/images-facets?' + p.toString();
}

async function fetchFacets() {
  try {
    const r = await fetch(facetURL(currentFilters()));
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

/** 品牌组下拉：计数来自服务端全量统计，切换品牌组时清空品牌与颜色。 */
function renderGroups(d, f) {
  const el = $('group');
  if (!el) return;
  el.innerHTML = '<option value="">全部品牌组</option>'
    + d.groups.map((g) => `<option value="${esc(g.id)}"${g.count === 0 && g.id !== f.group ? ' disabled' : ''}>`
      + `${esc(g.label)}（${fmtNum(g.count)}）</option>`).join('');
  el.value = f.group;
}

/** 品牌下拉：只列当前品牌组范围内的子品牌；华为、荣耀等独立品牌在"全部品牌组"下平铺。 */
function renderBrands(d, f) {
  const el = $('brand');
  if (!el) return;
  const exists = d.brands.some((b) => b.value === f.brand);
  if (f.brand && !exists) {
    // 数据里已不存在该品牌（例如切到别的品牌组），一并清掉颜色，避免残留
    state.color = '';
  }
  el.innerHTML = '<option value="">全部品牌</option>'
    + d.brands.map((b) => `<option value="${esc(b.value)}">${esc(b.value)}（${fmtNum(b.count)}）</option>`).join('');
  el.value = exists ? f.brand : '';
}

/* ---------- 颜色：可搜索下拉 ---------- */

const COMBO_LABEL_EMPTY = '全部颜色';

function colorScopeText(f) {
  if (f.brand) return `${f.brand} 品牌内`;
  if (f.group) {
    const g = state.facets?.groups.find((x) => x.id === f.group);
    return g ? `${g.label}范围内` : '当前品牌组内';
  }
  return '全部品牌';
}

function renderColors(f) {
  const list = state.facets?.colors || [];
  if (state.color && !list.some((c) => c.value === state.color)) {
    setColor('', false);      // 品牌切换后原颜色不在范围内，直接清空
  }
  const total = list.length;
  const scope = $('colorScope');
  if (scope) scope.textContent = `${colorScopeText(f)} · 共 ${fmtNum(total)} 种颜色 · 数字为图片张数`;
  renderColorList('');
  updateColorLabel();
}

function renderColorList(keyword) {
  const ul = $('colorList');
  const empty = $('colorEmpty');
  if (!ul) return;
  const list = state.facets?.colors || [];
  const kw = (keyword || '').toLowerCase();
  const rows = kw ? list.filter((c) => c.value.toLowerCase().includes(kw)) : list;
  const head = `<li class="combo-item${state.color ? '' : ' on'}" role="option"
      aria-selected="${state.color ? 'false' : 'true'}" data-value="">
      <span class="ci-name">全部颜色</span><span class="ci-num">${fmtNum(state.facets?.color_base || 0)}</span></li>`;
  ul.innerHTML = head + rows.map((c) => {
    const on = c.value === state.color;
    const raw = c.standard === 0 ? '<span class="ci-flag">未标准化</span>' : '';
    return `<li class="combo-item${on ? ' on' : ''}" role="option"
        aria-selected="${on}" data-value="${esc(c.value)}">
        <span class="ci-name">${esc(c.value)}</span>${raw}
        <span class="ci-num">${fmtNum(c.count)}</span></li>`;
  }).join('');
  if (empty) empty.hidden = rows.length > 0;
}

function updateColorLabel() {
  const label = $('colorLabel');
  const btn = $('colorBtn');
  if (!label) return;
  if (!state.color) {
    label.textContent = COMBO_LABEL_EMPTY;
    btn?.classList.remove('has-value');
    return;
  }
  const c = (state.facets?.colors || []).find((x) => x.value === state.color);
  label.textContent = state.color;
  label.title = c ? `${state.color}（${fmtNum(c.count)} 张）` : state.color;
  btn?.classList.add('has-value');
}

function openColorPanel() {
  const panel = $('colorPanel');
  if (!panel) return;
  panel.hidden = false;
  $('colorBtn')?.setAttribute('aria-expanded', 'true');
  $('colorCombo')?.classList.add('open');
  renderColorList('');
  const s = $('colorSearch');
  if (s) { s.value = ''; s.focus(); }
  const on = $('colorList')?.querySelector('.combo-item.on');
  if (on) on.scrollIntoView({ block: 'nearest' });
}

function closeColorPanel() {
  const panel = $('colorPanel');
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $('colorBtn')?.setAttribute('aria-expanded', 'false');
  $('colorCombo')?.classList.remove('open');
}

/** 选颜色：写入 state 后重新拉取聚合与列表（颜色统计始终来自服务端全量集合）。 */
function setColor(value, refresh = true) {
  state.color = value || '';
  updateColorLabel();
  if (refresh) refreshAll();
}

/* ---------- 列表 ---------- */

function queryParams(f) {
  const p = new URLSearchParams({ limit: state.limit, offset: state.offset });
  if (f.q) p.set('q', f.q);
  if (f.group) p.set('group', f.group);
  if (f.brand) p.set('brand', f.brand);
  if (f.color) p.set('color', f.color);
  if (f.status) p.set('status', f.status);
  return p;
}

/* ---------- 卡片 ---------- */

function cardHTML(it) {
  const mv = modelView(it);
  const color = it.display_color || '';
  const dim = (it.width && it.height) ? `${it.width}×${it.height}` : '—';
  const rawTag = mv.raw ? '<span class="tag-origin" title="型号未完成标准化匹配，显示目录原始名">原始</span>' : '';
  const modelTitle = mv.raw ? `${mv.text}（未标准化）` : mv.text;
  return `
    <div class="thumb"><img loading="lazy" src="${it.thumb_url}" alt="${esc(mv.text)}"></div>
    <div class="cap">
      <div class="model${mv.raw ? ' raw' : ''}" title="${esc(modelTitle)}">${rawTag}${esc(mv.text)}</div>
      <div class="sub">
        <span class="chip" title="图片原始品牌">${esc(it.display_brand || '—')}</span>
        <span class="color${color ? '' : ' muted'}">${esc(color || '颜色未标注')}</span>
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

/* ---------- 加载 ---------- */

async function load(reset = false, seq = state.seq) {
  if (state.loading && seq === state.seq) return;
  state.loading = true;
  if (reset) { state.offset = 0; grid.innerHTML = ''; }
  try {
    const r = await fetch('/api/v1/images?' + queryParams(currentFilters()));
    if (seq !== state.seq) return;                 // 期间筛选已变化，丢弃过期结果
    if (!r.ok) { $('count').textContent = `加载失败（${r.status}）`; return; }
    const d = await r.json();
    if (seq !== state.seq) return;
    state.total = d.total;
    d.items.forEach((it) => grid.appendChild(makeCard(it)));
    state.offset = (reset ? 0 : state.offset) + d.items.length;
    $('count').textContent = `共 ${fmtNum(d.total)} 张`
      + (d.total > state.offset ? `，已显示 ${fmtNum(state.offset)}` : '');
    if (emptyEl) emptyEl.hidden = d.total !== 0;
    if (moreBtn) moreBtn.hidden = state.offset >= d.total;
  } finally {
    state.loading = false;
  }
}

/** 统一的刷新入口：先按新条件重算聚合，再按同一条件取列表，避免两者口径不一致。 */
async function refreshAll(reset = true) {
  const seq = ++state.seq;
  closeColorPanel();
  const d = await fetchFacets();
  if (seq !== state.seq) return;
  if (d) {
    state.facets = d;
    const f = currentFilters();
    renderGroups(d, f);
    renderBrands(d, f);
    renderColors(f);
  }
  await load(reset, seq);
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
  const mv = modelView(it);
  // 未走标准匹配的字段不写成"标准 X"，改为标明来源，避免把未识别值当成已确认
  const brandLabel = it.brand_source === 'standard' ? '标准品牌' : '目录品牌';
  const modelLabel = it.model_source === 'standard' ? '标准型号' : '目录型号';
  const colorLabel = it.color_source === 'standard' ? '标准颜色'
    : (it.color_source === 'raw' ? '原始颜色' : '标准颜色');
  const unconfirmed = [];
  if (it.model_source !== 'standard') unconfirmed.push('型号');
  if (it.color_source === 'raw') unconfirmed.push('颜色');
  if (it.brand_source !== 'standard') unconfirmed.push('品牌');

  $('d-model').textContent = mv.text;
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
    [brandLabel, it.display_brand || '未识别'],
    [modelLabel, mv.raw ? (mv.text || '未识别') : mv.text],
    [colorLabel, it.display_color || '未标注'],
    ['图片尺寸', (it.width && it.height) ? `${it.width} × ${it.height} px` : '—'],
    ['格式与大小', `${(it.format || '—').toUpperCase()} · ${fmtSize(it.size_bytes)}`],
    ['原始文件名', it.filename || '—'],
    ['飞书原始目录', it.source_path || '—'],
    ['匹配状态', it.match_status || '—'],
    ['匹配方式', it.match_method || '—'],
  ]);

  const note = $('d-note');
  if (note) {
    note.hidden = unconfirmed.length === 0;
    note.textContent = unconfirmed.length
      ? `${unconfirmed.join(' / ')}未完成标准化匹配，以上标注为「目录」「原始」的值为目录原始信息，仅作参考。`
      : '';
  }

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
  library: ['图片资产库', '白底图 · 按品牌组 / 品牌 / 颜色检索'],
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
  debounce = setTimeout(() => refreshAll(), 280);
});

// 切换品牌组：品牌与颜色都属于该组范围内的结果，一并清空，避免跨组残留
bind('group', 'change', () => {
  const b = $('brand');
  if (b) b.value = '';
  state.color = '';
  refreshAll();
});

// 切换品牌：重置已选颜色（颜色只在当前品牌范围内有意义）
bind('brand', 'change', () => {
  state.color = '';
  refreshAll();
});

bind('status', 'change', () => refreshAll());

bind('reset', 'click', () => {
  ['q', 'group', 'brand', 'status'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
  state.color = '';
  const s = $('colorSearch');
  if (s) s.value = '';
  refreshAll();
});

bind('more', 'click', () => load(false));
bind('d-close', 'click', closeDrawer);
bind('d-copy', 'click', copyShareLink);
bind('d-share', 'focus', (e) => e.target.select());   // 聚焦即全选，便于手动复制
bind('mask', 'click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeColorPanel(); closeDrawer(); }
});
document.querySelectorAll('.nav-item[data-view]').forEach((b) => {
  b.addEventListener('click', () => { if (!b.disabled) switchView(b.dataset.view); });
});
bind('menuBtn', 'click', () => $('sidebar')?.classList.toggle('open'));

/* 颜色下拉交互：搜索、点击选择、键盘上下与回车、点外部关闭 */
bind('colorBtn', 'click', () => {
  const panel = $('colorPanel');
  if (panel?.hidden) openColorPanel(); else closeColorPanel();
});
bind('colorSearch', 'input', (e) => renderColorList(e.target.value));
bind('colorSearch', 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const first = $('colorList')?.querySelector('.combo-item');
    if (first) setColor(first.dataset.value || '');
  }
});
bind('colorList', 'click', (e) => {
  const li = e.target.closest('.combo-item');
  if (li) setColor(li.dataset.value || '');
});
bind('colorList', 'mousedown', (e) => e.preventDefault());   // 保持搜索框焦点
document.addEventListener('click', (e) => {
  if (!e.target.closest('#colorCombo')) closeColorPanel();
});

/* ---------- 启动 ---------- */

refreshAll().then(() => loadOverview());

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
