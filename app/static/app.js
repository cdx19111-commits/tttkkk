/* 商品图库 · 前端主逻辑（V0.3.3 统一品牌选择器） */

const state = {
  offset: 0, limit: 48, total: 0, loading: false, view: 'library',
  color: '',            // 颜色改由自定义下拉维护，不再依赖 <select>
  facets: null,
  tree: null,           // 统一品牌选择器的数据（组 + 子品牌 + 独立品牌）
  brand: { kind: 'all', id: '', value: '', label: '全部品牌' },
  brandActive: -1,      // 品牌面板内键盘高亮行
  collapsed: new Set(), // 已折叠的品牌组 id
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

/** 当前筛选条件；品牌由统一选择器维护，选中子品牌时不再携带品牌组。 */
function currentFilters() {
  const b = state.brand;
  return {
    q: ($('q')?.value || '').trim(),
    group: b.kind === 'group' ? b.id : '',
    brand: b.kind === 'brand' ? b.value : '',
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

/* ---------- 品牌：统一选择器（品牌组 + 子品牌 + 独立品牌） ---------- */

async function fetchTree() {
  const f = currentFilters();
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.status) p.set('status', f.status);
  try {
    const r = await fetch('/api/v1/brand-tree?' + p.toString());
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

// 明确且有限的别名表，只用于搜索命中，不做模糊推断
const BRAND_ALIAS = {
  '小米': ['xiaomi', 'mi'], 'xiaomi': ['小米', 'mi'], '小米系': ['xiaomi'],
  'redmi': ['红米'], '红米': ['redmi'],
  'vivo': [], 'vivo系': [],
  'iqoo': ['爱酷'],
  'oppo': [], 'oppo系': [],
  '一加': ['oneplus'], 'oneplus': ['一加'],
  '真我': ['realme'], 'realme': ['真我'],
  '华为': ['huawei'], 'huawei': ['华为'],
  '荣耀': ['honor'], 'honor': ['荣耀'],
  '三星': ['samsung'], 'samsung': ['三星'],
  'apple': ['苹果', 'iphone'], '苹果': ['apple', 'iphone'],
  '努比亚': ['nubia'], 'nubia': ['努比亚'],
};

const normText = (s) => String(s ?? '').normalize('NFKC').toLowerCase().replace(/\s+/g, '');

function matchesQuery(name, q) {
  if (!q) return true;
  if (normText(name).includes(q)) return true;
  const alias = BRAND_ALIAS[String(name || '').toLowerCase()] || [];
  return alias.some((a) => normText(a).includes(q));
}

/** 生成面板行：无搜索时按层级展示（组可折叠），搜索时跨组平铺。 */
function brandRows(q) {
  const t = state.tree;
  const rows = [];
  if (!t) return rows;
  if (!q) rows.push({ kind: 'all', label: '全部品牌', count: t.total, level: 0 });
  for (const g of t.groups) {
    const kids = g.brands.filter((b) => matchesQuery(b.value, q));
    const gMatch = matchesQuery(g.label, q);
    if (q) {
      // 搜索时只有组名本身命中才显示组行；否则只列出命中的子品牌，
      // 避免「搜 Redmi，回车却选中整个小米系」这种反直觉结果
      if ((!gMatch && !kids.length) || g.count === 0) continue;
      if (gMatch) {
        rows.push({ kind: 'group', id: g.id, label: `${g.label} · 全部`, count: g.count, level: 0 });
      }
      for (const b of (gMatch ? g.brands : kids)) {
        if (b.count > 0) {
          rows.push({ kind: 'brand', value: b.value, count: b.count, level: 1, parent: q ? g.label : '' });
        }
      }
    } else {
      if (g.count === 0) continue;
      const open = !state.collapsed.has(g.id);
      rows.push({ kind: 'group', id: g.id, label: `${g.label} · 全部`, count: g.count, level: 0, open });
      if (open) {
        for (const b of g.brands) {
          if (b.count === 0) continue;
          // 树形模式靠缩进表达层级，不再重复标注父组
          rows.push({ kind: 'brand', value: b.value, count: b.count, level: 1 });
        }
      }
    }
  }
  const stand = t.standalone.filter((b) => matchesQuery(b.value, q) && b.count > 0);
  if (stand.length) {
    rows.push({ kind: 'header', label: '独立品牌', level: 0 });
    for (const b of stand) rows.push({ kind: 'brand', value: b.value, count: b.count, level: 0 });
  }
  return rows;
}

function isSelected(row) {
  const b = state.brand;
  if (row.kind === 'all') return b.kind === 'all';
  if (row.kind === 'group') return b.kind === 'group' && b.id === row.id;
  if (row.kind === 'brand') return b.kind === 'brand' && b.value === row.value;
  return false;
}

function renderBrandList(query) {
  const ul = $('brandList');
  const empty = $('brandEmpty');
  if (!ul) return;
  const q = normText(query);
  const rows = brandRows(q);
  const items = [];
  ul.innerHTML = rows.map((r) => {
    if (r.kind === 'header') {
      return `<li class="combo-sec" role="presentation">${esc(r.label)}</li>`;
    }
    const idx = items.push(r) - 1;
    const on = isSelected(r) ? ' on' : '';
    const act = idx === state.brandActive ? ' active' : '';
    const toggle = (r.kind === 'group' && !q)
      ? `<span class="tw${r.open ? ' open' : ''}" data-toggle="${esc(r.id)}"></span>`
      : '<span class="tw"></span>';
    const parent = r.parent ? `<span class="ci-parent">${esc(r.parent)}</span>` : '';
    const name = r.kind === 'brand' ? r.value : r.label;
    const attr = r.kind === 'group' ? ` data-id="${esc(r.id)}"`
      : (r.kind === 'brand' ? ` data-value="${esc(r.value)}"` : '');
    return `<li class="combo-item lv${r.level}${on}${act}" role="option" data-kind="${r.kind}"${attr}
        aria-selected="${isSelected(r) ? 'true' : 'false'}">${toggle}
        <span class="ci-name">${esc(name)}</span>${parent}<span class="ci-num">${fmtNum(r.count)}</span></li>`;
  }).join('');

  state.brandItems = items;
  if (empty) empty.hidden = items.length > 0;
  const scope = $('brandScope');
  if (scope && state.tree) {
    const nBrands = state.tree.groups.reduce((s, g) => s + g.brands.length, 0)
      + state.tree.standalone.length;
    scope.textContent = `${state.tree.groups.length} 个品牌组 · ${nBrands} 个品牌 · 数字为图片张数`;
  }
}

function updateBrandLabel() {
  const el = $('brandLabel');
  const btn = $('brandBtn');
  if (!el) return;
  const label = state.brand.label || '全部品牌';
  el.textContent = label;
  el.title = label;
  if (btn) btn.classList.toggle('has-value', state.brand.kind !== 'all');
}

/** 面板定位：下方空间不足时向上展开，并按可用高度收缩列表，保证整块都在视口内。 */
function positionBrandPanel() {
  const btn = $('brandBtn');
  const panel = $('brandPanel');
  const list = $('brandList');
  if (!btn || !panel || panel.hidden) return;
  const r = btn.getBoundingClientRect();
  const below = window.innerHeight - r.bottom - 16;
  const above = r.top - 16;
  const up = below < 300 && above > below;
  panel.classList.toggle('up', up);
  if (list) list.style.maxHeight = Math.min(492, Math.max(200, (up ? above : below) - 96)) + 'px';
}

function openBrandPanel() {
  const panel = $('brandPanel');
  if (!panel) return;
  closeColorPanel();
  panel.hidden = false;
  $('brandBtn')?.setAttribute('aria-expanded', 'true');
  $('brandCombo')?.classList.add('open');
  const s = $('brandSearch');
  if (s) s.value = '';
  state.brandActive = -1;
  renderBrandList('');
  positionBrandPanel();
  s?.focus();
  const on = $('brandList')?.querySelector('.combo-item.on');
  if (on) on.scrollIntoView({ block: 'nearest' });
}

function closeBrandPanel() {
  const panel = $('brandPanel');
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $('brandBtn')?.setAttribute('aria-expanded', 'false');
  $('brandCombo')?.classList.remove('open');
  state.brandActive = -1;
}

function paintBrandActive() {
  const ul = $('brandList');
  if (!ul) return;
  const els = [...ul.querySelectorAll('.combo-item')];
  els.forEach((el, i) => el.classList.toggle('active', i === state.brandActive));
  if (state.brandActive >= 0 && els[state.brandActive]) {
    els[state.brandActive].scrollIntoView({ block: 'nearest' });
  }
}

/** 选定品牌：清空颜色（范围变了），关闭面板并按新条件刷新。 */
function selectBrand(row) {
  if (row.kind === 'all') state.brand = { kind: 'all', id: '', value: '', label: '全部品牌' };
  else if (row.kind === 'group') state.brand = { kind: 'group', id: row.id, value: '', label: row.label };
  else state.brand = { kind: 'brand', id: '', value: row.value, label: row.value };
  state.color = '';
  closeBrandPanel();
  refreshAll();
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

/** 统一的刷新入口：先按新条件重算聚合与品牌树，再按同一条件取列表，避免口径不一致。 */
async function refreshAll(reset = true) {
  const seq = ++state.seq;
  closeColorPanel();
  closeBrandPanel();
  const [d, t] = await Promise.all([fetchFacets(), fetchTree()]);
  if (seq !== state.seq) return;        // 期间又改了筛选，丢弃这批过期响应
  if (d) state.facets = d;
  if (t) state.tree = t;
  updateBrandLabel();
  renderColors(currentFilters());
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

// 品牌选择器在 selectBrand() 内统一处理：切品牌会清空颜色并重新取树
bind('status', 'change', () => refreshAll());

bind('reset', 'click', () => {
  ['q', 'status'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
  state.brand = { kind: 'all', id: '', value: '', label: '全部品牌' };
  state.color = '';
  state.collapsed.clear();
  const cs = $('colorSearch');
  if (cs) cs.value = '';
  const bs = $('brandSearch');
  if (bs) bs.value = '';
  refreshAll();
});

bind('more', 'click', () => load(false));
bind('d-close', 'click', closeDrawer);
bind('d-copy', 'click', copyShareLink);
bind('d-share', 'focus', (e) => e.target.select());   // 聚焦即全选，便于手动复制
bind('mask', 'click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeColorPanel(); closeBrandPanel(); closeDrawer(); }
});
document.querySelectorAll('.nav-item[data-view]').forEach((b) => {
  b.addEventListener('click', () => { if (!b.disabled) switchView(b.dataset.view); });
});
bind('menuBtn', 'click', () => $('sidebar')?.classList.toggle('open'));
window.addEventListener('resize', positionBrandPanel);

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
  e.stopPropagation();
  const li = e.target.closest('.combo-item');
  if (li) setColor(li.dataset.value || '');
});
bind('colorList', 'mousedown', (e) => e.preventDefault());   // 保持搜索框焦点
document.addEventListener('click', (e) => {
  if (!e.target.isConnected) return;   // 目标已被重渲染移除，视为面板内操作
  if (!e.target.closest('#colorCombo')) closeColorPanel();
});

/* 品牌选择器：点击展开、搜索、方向键/回车、组折叠、点外部关闭 */
bind('brandBtn', 'click', () => {
  const panel = $('brandPanel');
  if (panel?.hidden) openBrandPanel(); else closeBrandPanel();
});
bind('brandSearch', 'input', (e) => {
  state.brandActive = -1;
  renderBrandList(e.target.value);
});
bind('brandSearch', 'keydown', (e) => {
  const rows = state.brandItems || [];
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!rows.length) return;
    const step = e.key === 'ArrowDown' ? 1 : -1;
    state.brandActive = (state.brandActive + step + rows.length) % rows.length;
    paintBrandActive();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const row = rows[state.brandActive] || rows[0];
    if (row) selectBrand(row);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    closeBrandPanel();
    $('brandBtn')?.focus();
  }
});
bind('brandList', 'click', (e) => {
  e.stopPropagation();       // 列表内点击会重渲染，事件目标随即脱离 DOM，不能让它冒泡去关面板
  const tw = e.target.closest('.tw[data-toggle]');
  if (tw) {                                  // 折叠/展开品牌组，不改变筛选
    const id = tw.dataset.toggle;
    if (state.collapsed.has(id)) state.collapsed.delete(id); else state.collapsed.add(id);
    renderBrandList($('brandSearch')?.value || '');
    return;
  }
  const li = e.target.closest('.combo-item');
  if (!li) return;
  const idx = [...$('brandList').querySelectorAll('.combo-item')].indexOf(li);
  const row = (state.brandItems || [])[idx];
  if (row) selectBrand(row);
});
bind('brandList', 'mousedown', (e) => {
  if (!e.target.closest('.tw[data-toggle]')) e.preventDefault();   // 保持搜索框焦点
});
document.addEventListener('click', (e) => {
  if (!e.target.isConnected) return;
  if (!e.target.closest('#brandCombo')) closeBrandPanel();
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
