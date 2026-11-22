export async function mount(root) {
const state = {
  role: 'business', review: false,
  offset: 0, limit: 48, category: 'phone', kind: '',
  catalog: null, total: 0, loading: false, view: 'white-gallery',
  color: '', officialColor: '', model: { source: '', value: '', label: '全部型号' },
  modelActive: -1, facets: null, tree: null,
  brand: { kind: 'all', id: '', value: '', label: '全部品牌' },
  brandActive: -1, collapsed: new Set(), seq: 0,
};
const $ = (id) => root.querySelector('#' + id) || (id === 'q' ? document.getElementById('q') : null);
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
  let raw = (it.model_dir || '').trim() || (it.filename || '').trim();
  const b = (it.display_brand || '').trim();
  if (b && raw.toLowerCase().startsWith(b.toLowerCase()) && raw.length > b.length) {
    raw = raw.slice(b.length).replace(/^[\s\-_/·・]+/, '');
  }
  return { text: raw || '未识别型号', raw: true };
}

const modelOf = (i) => modelView(i).text;

function badgeClass(s) {
  if (s === '已匹配') return 'ok';
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
  const p = new URLSearchParams();
  if (state.category && state.category !== 'phone') p.set('category', state.category);
  const r = await fetch('/api/v1/images-overview?' + p.toString());
  if (!r.ok) return null;
  const d = await r.json();
  $('s-total').textContent = fmtNum(d.total);
  $('s-bytes').textContent = fmtSize(d.total_bytes);
  $('s-models').textContent = fmtNum(d.models);
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
    ['版本', 'V2.1（统一图库）'],
    ['图片总量', `${fmtNum(d.total)} 张 · ${fmtSize(d.total_bytes)}`],
    ['品牌 / 型号 / 颜色', `${fmtNum(d.brands)} / ${fmtNum(d.models)} / ${fmtNum(d.colors)}`],
    ['品牌组', groups],
    ['品类分布', Object.entries(d.by_category || {}).map(([k, v]) => `${k} ${fmtNum(v)}`).join(' · ') || '—'],
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
  const m = state.model;
  return {
    q: ($('q')?.value || '').trim(),
    group: b.kind === 'group' ? b.id : '',
    brand: b.kind === 'brand' ? b.value : '',
    model_exact: m.value || '',
    model_source: m.value ? m.source : '',
    color: state.color,
    official_color: state.officialColor,
    status: $('status')?.value || '',
    category: state.category || 'phone',
    asset_kind: state.kind || '',
  };
}

/* ---------- 品类 / 素材类型（V2.1 统一主线） ---------- */

async function loadCatalog() {
  try {
    const r = await fetch('/api/v1/catalog');
    if (!r.ok) return null;
    state.catalog = await r.json();
  } catch (e) { state.catalog = null; return null; }
  const sel = $('category');
  if (sel && state.catalog) {
    sel.innerHTML = state.catalog.categories
      .filter((c) => c.count > 0 || c.id === 'phone')
      .map((c) => `<option value="${esc(c.id)}">${esc(c.label)}（${fmtNum(c.count)}）</option>`)
      .join('');
    if ([...sel.options].some((o) => o.value === state.category)) sel.value = state.category;
  }
  return state.catalog;
}

function setCategory(v) {
  state.category = v || 'phone';
  state.brand = { kind: 'all', id: '', value: '', label: '全部品牌' };
  state.model = { source: '', value: '', label: '全部型号' };
  state.color = '';
  state.officialColor = '';
  closeColorPanel(); closeBrandPanel(); closeModelPanel();
  refreshAll().then(() => loadOverview());
}

function facetURL(f) {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.status) p.set('status', f.status);
  if (f.group) p.set('group', f.group);
  if (f.brand) p.set('brand', f.brand);
  if (f.model_exact) {
    p.set('model_exact', f.model_exact);
    p.set('model_source', f.model_source || 'standard');
  }
  if (f.category) p.set('category', f.category);
  if (f.asset_kind) p.set('asset_kind', f.asset_kind);
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
  if (f.category && f.category !== 'phone') p.set('category', f.category);
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
  state.model = { source: '', value: '', label: '全部型号' };
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
  if (scope) scope.textContent = colorModeDual()
    ? `${colorScopeText(f)} · 盖亚参考色 ${fmtNum(total)} 种 · 官方配色可搜索 · 数字为图片张数`
    : `${colorScopeText(f)} · 共 ${fmtNum(total)} 种颜色 · 数字为图片张数`;
  renderColorList('');
  updateColorLabel();
}

/** 非手机（双配色口径）才提供官方配色维度；手机保持单一颜色。 */
function colorModeDual() {
  return (state.category || 'phone') !== 'phone';
}

function renderColorList(keyword) {
  const ul = $('colorList');
  const empty = $('colorEmpty');
  if (!ul) return;
  const list = state.facets?.colors || [];
  const kw = (keyword || '').toLowerCase();
  const rows = kw ? list.filter((c) => c.value.toLowerCase().includes(kw)) : list;
  // V1.7：非手机额外给出官方配色（品牌正式命名），供查询与人工核实；
  // 官方色单独成组，不与盖亚色混为一列，避免两种口径互相顶替。
  const offs = colorModeDual() ? (state.facets?.official_colors || []) : [];
  const offRows = kw ? offs.filter((c) => c.value.toLowerCase().includes(kw)) : offs;
  const head = `<li class="combo-item${!state.color && !state.officialColor ? ' on' : ''}" role="option"
      aria-selected="${!state.color && !state.officialColor ? 'true' : 'false'}" data-value="" data-kind="all">
      <span class="ci-name">全部颜色</span><span class="ci-num">${fmtNum(state.facets?.color_base || 0)}</span></li>`;
  const gaiaGroup = colorModeDual() && rows.length
    ? '<li class="combo-sec" role="presentation">盖亚参考色（业务筛选口径）</li>' : '';
  const body = rows.map((c) => {
    const on = c.value === state.color && !state.officialColor;
    return `<li class="combo-item${on ? ' on' : ''}" role="option"
        aria-selected="${on}" data-value="${esc(c.value)}" data-kind="gaia">
        <span class="ci-name">${esc(c.value)}</span>
        <span class="ci-num">${fmtNum(c.count)}</span></li>`;
  }).join('');
  const offGroup = offRows.length
    ? '<li class="combo-sec" role="presentation">品牌官方配色（仅供查询/核实）</li>' : '';
  const offBody = offRows.map((c) => {
    const on = c.value === state.officialColor;
    return `<li class="combo-item combo-item-off${on ? ' on' : ''}" role="option"
        aria-selected="${on}" data-value="${esc(c.value)}" data-kind="official">
        <span class="ci-name">${esc(c.value)}</span>
        <span class="ci-num">${fmtNum(c.count)}</span></li>`;
  }).join('');
  ul.innerHTML = head + gaiaGroup + body + offGroup + offBody;
  if (empty) empty.hidden = rows.length > 0 || offRows.length > 0;
}

function updateColorLabel() {
  const label = $('colorLabel');
  const btn = $('colorBtn');
  if (!label) return;
  if (state.officialColor) {
    label.textContent = `官方配色：${state.officialColor}`;
    label.title = `品牌官方配色 ${state.officialColor}`;
    btn?.classList.add('has-value');
    return;
  }
  if (!state.color) {
    label.textContent = COMBO_LABEL_EMPTY;
    btn?.classList.remove('has-value');
    return;
  }
  const c = (state.facets?.colors || []).find((x) => x.value === state.color);
  label.textContent = colorModeDual() ? `盖亚：${state.color}` : state.color;
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
  state.officialColor = '';         // 盖亚色与官方色互斥，切换即清空另一侧
  updateColorLabel();
  if (refresh) refreshAll();
}

/** V1.7：按「品牌官方配色」筛选（仅非手机）；与盖亚色互斥。 */
function setOfficialColor(value, refresh = true) {
  state.officialColor = value || '';
  state.color = '';
  updateColorLabel();
  if (refresh) refreshAll();
}

/* ---------- 型号：可搜索下拉（标准型号 / 目录型号 分组） ---------- */

const MODEL_LABEL_EMPTY = '全部型号';
const MODEL_TEXT_EMPTY = '全部型号';

function modelGroups() {
  const m = state.facets?.models;
  return {
    standard: (m && m.standard) || [],
    directory: (m && m.directory) || [],
    base: (m && m.base) || 0,
  };
}

function modelScopeText(f) {
  const g = modelGroups();
  const where = f.brand ? `${f.brand} 品牌内`
    : f.group ? ((state.facets?.groups || []).find((x) => x.id === f.group)?.label || '当前品牌组内')
      : '全部品牌';
  return `${where} · ${fmtNum(g.standard.length)} 个标准型号 / ${fmtNum(g.directory.length)} 个目录型号 · 数字为图片张数`;
}

/** 匹配：不做同义推断，只做「包含」匹配，避免把不同型号混为一谈。 */
function modelMatches(name, q) {
  if (!q) return true;
  return normText(name).includes(normText(q));
}

function updateModelLabel() {
  const el = $('modelLabel');
  const btn = $('modelBtn');
  if (!el) return;
  const m = state.model;
  el.textContent = m.value || MODEL_LABEL_EMPTY;
  el.title = m.value ? `${m.value}${m.source === 'directory' ? '（目录型号，未标准化）' : ''}` : MODEL_LABEL_EMPTY;
  if (btn) {
    btn.classList.toggle('has-value', !!m.value);
    btn.classList.toggle('is-raw', m.source === 'directory');
  }
}

function renderModelList(keyword) {
  const ul = $('modelList');
  const empty = $('modelEmpty');
  if (!ul) return;
  const g = modelGroups();
  const kw = (keyword || '').trim();
  const std = g.standard.filter((x) => modelMatches(x.value, kw));
  const dir = g.directory.filter((x) => modelMatches(x.value, kw));

  const all = `<li class="combo-item${state.model.value ? '' : ' on'}" role="option"
      aria-selected="${state.model.value ? 'false' : 'true'}" data-kind="all">
      <span class="ci-name">全部型号</span><span class="ci-num">${fmtNum(g.base)}</span></li>`;

  const stdHtml = std.length ? `
      <li class="combo-group" role="presentation">标准型号<span class="cg-num">${fmtNum(std.length)}</span></li>
      ${std.map((x) => {
        const on = state.model.source === 'standard' && state.model.value === x.value;
        return `<li class="combo-item${on ? ' on' : ''}" role="option"
          aria-selected="${on}" data-kind="standard" data-value="${esc(x.value)}">
          <span class="ci-name" title="${esc(x.value)}">${esc(x.value)}</span><span class="ci-num">${fmtNum(x.count)}</span></li>`;
      }).join('')}` : '';

  const dirHtml = dir.length ? `
      <li class="combo-group" role="presentation">目录型号 · 未标准化<span class="cg-num">${fmtNum(dir.length)}</span></li>
      ${dir.map((x) => {
        const on = state.model.source === 'directory' && state.model.value === x.value;
        return `<li class="combo-item${on ? ' on' : ''}" role="option"
          aria-selected="${on}" data-kind="directory" data-value="${esc(x.value)}">
          <span class="ci-name" title="${esc(x.value)}（目录型号，未标准化）">${esc(x.value)}<span class="src-tag">未标准化</span></span>
          <span class="ci-num">${fmtNum(x.count)}</span></li>`;
      }).join('')}` : '';

  ul.innerHTML = all + stdHtml + dirHtml;
  state.modelItems = [...ul.querySelectorAll('.combo-item')];
  if (empty) empty.hidden = (std.length + dir.length) > 0;
  const scope = $('modelScope');
  if (scope) scope.textContent = modelScopeText(currentFilters());
}

function positionModelPanel() {
  const btn = $('modelBtn');
  const panel = $('modelPanel');
  const list = $('modelList');
  if (!btn || !panel || panel.hidden) return;
  const r = btn.getBoundingClientRect();
  const below = window.innerHeight - r.bottom - 16;
  const above = r.top - 16;
  const up = below < 300 && above > below;
  panel.classList.toggle('up', up);
  if (list) list.style.maxHeight = Math.min(492, Math.max(200, (up ? above : below) - 96)) + 'px';
}

function openModelPanel() {
  const panel = $('modelPanel');
  if (!panel) return;
  closeColorPanel();
  closeBrandPanel();
  panel.hidden = false;
  $('modelBtn')?.setAttribute('aria-expanded', 'true');
  $('modelCombo')?.classList.add('open');
  const s = $('modelSearch');
  if (s) s.value = '';
  state.modelActive = -1;
  renderModelList('');
  positionModelPanel();
  s?.focus();
  const on = $('modelList')?.querySelector('.combo-item.on');
  if (on) on.scrollIntoView({ block: 'nearest' });
}

function closeModelPanel() {
  const panel = $('modelPanel');
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $('modelBtn')?.setAttribute('aria-expanded', 'false');
  $('modelCombo')?.classList.remove('open');
  state.modelActive = -1;
}

/** 键盘导航只覆盖真实型号，「全部型号」用 Esc / 重置或点击处理。 */
function modelNavItems() {
  return (state.modelItems || []).filter((el) => el.dataset.kind !== 'all');
}

function paintModelActive() {
  const ul = $('modelList');
  if (!ul) return;
  (state.modelItems || []).forEach((el) => el.classList.remove('active'));
  const els = modelNavItems();
  const el = els[state.modelActive];
  if (el) {
    el.classList.add('active');
    el.scrollIntoView({ block: 'nearest' });
  }
}

/** 选型号：写入 state，清空颜色（颜色要收敛到该型号），再按新条件刷新。 */
function selectModel(row) {
  const kind = row?.dataset?.kind || '';
  if (!row || kind === 'all' || !row.dataset.value) {
    state.model = { source: '', value: '', label: '全部型号' };
  } else {
    state.model = {
      // 目录型号必须保留 directory 来源，否则会被当成标准型号而查不到
      source: kind === 'directory' ? 'directory' : 'standard',
      value: row.dataset.value,
      label: row.dataset.value,
    };
  }
  state.color = '';                 // 型号变化 → 颜色自动重置
  state.officialColor = '';
  closeModelPanel();
  refreshAll();
}

/* ---------- 列表 ---------- */

function queryParams(f) {
  const p = new URLSearchParams({ limit: state.limit, offset: state.offset });
  if (f.q) p.set('q', f.q);
  if (f.group) p.set('group', f.group);
  if (f.brand) p.set('brand', f.brand);
  if (f.model_exact) {
    p.set('model_exact', f.model_exact);
    p.set('model_source', f.model_source || 'standard');
  }
  if (f.color) p.set('color', f.color);
  if (f.official_color && f.category !== 'phone') p.set('official_color', f.official_color);
  if (f.status) p.set('status', f.status);
  if (f.category && f.category !== 'phone') p.set('category', f.category);
  if (f.asset_kind && f.category !== 'phone') p.set('asset_kind', f.asset_kind);
  if (state.review) p.set('view', 'review');
  return p;
}

/* ---------- 卡片 ---------- */

function cardHTML(it) {
  const mv = modelView(it);
  const color = it.display_color || '';
  const official = it.official_color || '';
  const dim = (it.width && it.height) ? `${it.width}×${it.height}` : '—';
  const qBadge = state.review && (it.quality_level && it.quality_level !== 'A')
    ? `<span class="badge q-${esc(it.quality_level)}">${it.quality_level === 'X' ? '已停用' : '待确认'}</span>`
    : '';
  // V1.7 双配色：非手机同时给出「盖亚参考色」与「品牌官方配色」，缺失一侧不显示，
  // 也不拿另一侧顶替；手机保持单一颜色（业务口径未变）。
  const dual = (it.category || 'phone') !== 'phone';
  // 非手机：有盖亚色时以盖亚为主色、官方色为副色；只有官方色时只显示官方色（不伪造盖亚色）
  let colorHTML;
  if (dual) {
    const parts = [];
    if (color) parts.push(`<span class="color" title="盖亚参考色">盖亚 ${esc(color)}</span>`);
    if (official && official !== color) {
      parts.push(`<span class="color color-off" title="品牌官方配色${color ? '' : '（盖亚参考色缺失）'}">官方 ${esc(official)}</span>`);
    }
    colorHTML = parts.length
      ? `<span class="colors">${parts.join('')}</span>`
      : '<span class="colors"><span class="color muted">颜色未标注</span></span>';
  } else {
    colorHTML = `<span class="colors"><span class="color${color ? '' : ' muted'}">${esc(color || '颜色未标注')}</span></span>`;
  }
  // V1.2.2 业务卡片只保留：图片 / 品牌 / 型号（全文悬停）/ 颜色 / 尺寸。
  // 型号标准化状态、发布状态、品类等治理与内部信息只在详情与后台体现。
  return `
    <div class="thumb"><img loading="lazy" src="${it.thumb_url}" alt="${esc(mv.text)}"></div>
    <div class="cap">
      <div class="model" title="${esc(mv.text)}">${esc(mv.text)}</div>
      <div class="sub">
        <span class="chip">${esc(it.display_brand || '—')}</span>
        ${colorHTML}${qBadge}
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

async function initRole() {
  try {
    const r = await fetch('/api/v1/me');
    if (!r.ok) return;
    const d = await r.json();
    state.role = d.role;
    state.review = d.can_review && new URLSearchParams(location.search).get('view') === 'review';
    if (d.can_review) mountReviewToggle();
  } catch (e) { /* 角色不可用时保持业务视图 */ }
}

function mountReviewToggle() {
  if (root.querySelector('#reviewToggle')) return;
  const box = document.createElement('label');
  box.id = 'reviewToggle';
  box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:60;background:#1c232c;' +
    'color:#e8eef6;border:1px solid #33414f;border-radius:10px;padding:8px 12px;font-size:12px;' +
    'display:flex;gap:8px;align-items:center;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.35)';
  box.innerHTML = '<input type="checkbox" id="reviewCb" ' + (state.review ? 'checked' : '') +
    '><span>复核视图（含待确认/已停用）</span>';
  root.appendChild(box);
  box.querySelector('#reviewCb').addEventListener('change', (e) => {
    state.review = e.target.checked;
    const u = new URL(location.href);
    if (state.review) u.searchParams.set('view', 'review');
    else u.searchParams.delete('view');
    history.replaceState(null, '', u);
    refreshAll(true);
  });
}
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
    if (emptyEl) {
      emptyEl.hidden = d.total !== 0;
      if (d.total === 0 && d.empty_hint) emptyEl.textContent = d.empty_hint;
      else emptyEl.textContent = '没有匹配的图片';
    }
    if (d.suppressed > 0) {
      $('count').textContent += `（已隐藏 ${fmtNum(d.suppressed)} 张待确认/已停用）`;
    }
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
  closeModelPanel();
  const [d, t] = await Promise.all([fetchFacets(), fetchTree()]);
  if (seq !== state.seq) return;        // 期间又改了筛选，丢弃这批过期响应
  if (d) state.facets = d;
  if (t) state.tree = t;
  // 已选型号在新范围内不存在（数据变动/跨品类切换兜底）→ 回落到「全部型号」再取一次
  if (state.model.value) {
    const g = modelGroups();
    const pool = state.model.source === 'directory' ? g.directory : g.standard;
    if (!pool.some((x) => x.value === state.model.value)) {
      state.model = { source: '', value: '', label: '全部型号' };
      closeModelPanel();
      refreshAll(reset);
      return;
    }
  }
  updateBrandLabel();
  updateModelLabel();
  renderModelList('');
  renderColors(currentFilters());
  await load(reset, seq);
}

/* ---------- 抽屉 ---------- */

// 分享链接固定使用正式域名根路径，避免带上 /assets 等历史入口
const SHARE_ORIGIN = 'https://picsystem.changziqi.com';
const shareURL = (id) => `${SHARE_ORIGIN}/?image=${encodeURIComponent(id)}`;

let toastTimer;
function toast(msg, ok = true) {
  const el = $('galleryToast');
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

/* ---- 型号参数卡片（仅手机、按线上 model_id 关联 EJ 参数表） ----
 * 参数数据为静态 JSON，首次打开详情时懒加载一次并缓存；
 * 只展示「参数名：参数值」成对内容，不显示任何表头，不做独立页面。
 */
const specState = { data: null, loading: null, expanded: false, seq: 0, currentId: '' };

function loadSpecData() {
  if (specState.data) return Promise.resolve(specState.data);
  if (specState.loading) return specState.loading;
  specState.loading = fetch('/static/model_params.json?pv=20261119-fullparams', { cache: 'no-store' })
    .then((r) => { if (!r.ok) throw new Error('参数数据加载失败'); return r.json(); })
    .then((d) => { specState.data = d; return d; })
    .catch((e) => { console.warn('[图库] 型号参数数据不可用：', e.message); specState.data = {}; return specState.data; })
    .finally(() => { specState.loading = null; });
  return specState.loading;
}

function renderSpec(it) {
  const card = $('d-spec');
  if (!card) return;
  // 单调递增令牌：快速切换详情时，旧请求回调必须作废，否则会把上一个型号的
  // 参数写进当前详情（异步串型号）。closeDrawer 也会递增，关闭后回调同样作废。
  const token = ++specState.seq;
  const mid = (it.category || 'phone') === 'phone' && it.model_id ? String(it.model_id) : '';
  specState.currentId = mid;
  if (!mid) { card.hidden = true; return; }

  loadSpecData().then((data) => {
    // 关键校验：令牌过期 / 抽屉已关 / 当前型号已变，一律不写 DOM
    if (token !== specState.seq || mid !== specState.currentId) return;
    if (!card.isConnected || $('drawer').hidden) return;
    const listEl = $('d-spec-list');
    const entry = data && data.models && data.models[mid];
    if (!entry || !Array.isArray(entry.params) || entry.params.length === 0) {
      card.hidden = true;
      return;
    }
    const pairs = entry.params.filter(([k, v]) => k && v);
    if (!pairs.length) { card.hidden = true; return; }
    $('d-spec-title').textContent = `型号参数（${pairs.length}项）`;
    listEl.innerHTML = pairs.map(([k, v]) =>
      `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
    card.hidden = false;
    listEl.hidden = !specState.expanded;
    $('d-spec-toggle').textContent = specState.expanded ? '收起' : '展开';
    $('d-spec-head').setAttribute('aria-expanded', String(specState.expanded));
  });
}
/** 关闭详情时作废进行中的参数渲染，避免回调写进已关闭的抽屉。 */
function invalidateSpec() {
  specState.seq += 1;
  specState.currentId = '';
}

function bindSpecCard() {
  const headEl = $('d-spec-head');
  if (!headEl) return;
  const toggle = () => {
    specState.expanded = !specState.expanded;
    const listEl = $('d-spec-list');
    if (listEl) listEl.hidden = !specState.expanded;
    $('d-spec-toggle').textContent = specState.expanded ? '收起' : '展开';
    headEl.setAttribute('aria-expanded', String(specState.expanded));
  };
  headEl.addEventListener('click', toggle);
  headEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
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
  const dual = (it.category || 'phone') !== 'phone';
  const colorLabel = it.color_source === 'standard' ? '标准颜色'
    : (it.color_source === 'raw' ? '原始颜色' : '标准颜色');
  const unconfirmed = [];
  if (it.model_source !== 'standard') unconfirmed.push('型号');
  if (it.color_source === 'raw' || (dual && it.color_source === 'none')) unconfirmed.push('颜色');
  if (it.brand_source !== 'standard') unconfirmed.push('品牌');

  $('d-model').textContent = mv.text;
  $('d-brand').textContent = [it.display_brand, it.display_color].filter(Boolean).join(' · ') || '—';
  const st = $('d-status');
  if (state.review && st) {           // 仅复核角色可见内部匹配状态
    st.hidden = false;
    st.textContent = it.match_status || '—';
    st.className = 'badge ' + badgeClass(it.match_status);
  } else if (st) {
    st.hidden = true;
  }

  $('d-img').src = it.thumb_url || it.file_url;
  const dl = $('d-download'), op = $('d-open');
  if (dl && it.can_download === false) {
    dl.hidden = true;
  } else if (dl) {
    dl.hidden = false; dl.href = it.download_url;
  }
  if (op && it.can_view_original === false) {
    op.hidden = true;
  } else if (op) {
    op.hidden = false; op.href = it.file_url;
  }
  $('d-share').value = shareURL(it.id);
  $('d-share').title = $('d-share').value;   // 悬停可看完整链接
  $('galleryToast').hidden = true;

  fillKV([
    [brandLabel, it.display_brand || '未识别'],
    [modelLabel, mv.raw ? (mv.text || '未识别') : mv.text],
    // V1.7 双配色：非手机把两种口径分行列出，任一缺失显示"未标注"，绝不用另一侧填充
    ...(dual ? [['盖亚参考色', it.gaia_color || '未标注'],
               ['品牌官方配色', it.official_color || '未标注']]
             : [[colorLabel, it.display_color || '未标注']]),
    ['图片尺寸', (it.width && it.height) ? `${it.width} × ${it.height} px` : '—'],
    ['格式与大小', `${(it.format || '—').toUpperCase()} · ${fmtSize(it.size_bytes)}`],
    ['原始文件名', it.filename || '—'],
    ['飞书原始目录', it.source_path || '—'],
    ['匹配状态', it.match_status || '—'],
    ['匹配方式', it.match_method || '—'],
    ['商品品类', it.category_label || '—'],
    ['素材类型', it.asset_kind_label || '—'],
    ['发布状态', it.publish_label || '—'],
    ['身份精度', it.category !== 'phone' && it.model_source !== 'standard'
      ? '型号外观参考（具体版本未确认）' : '标准型号（已确认）'],
  ]);

  renderSpec(it);

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
  invalidateSpec();
  $('drawer').hidden = true;
  $('mask').hidden = true;
  $('d-img').src = '';
  $('galleryToast').hidden = true;
  if (new URLSearchParams(location.search).get('image')) syncURL('');
}
let debounce;
bind('q', 'input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => refreshAll(), 280);
});

// 品牌选择器在 selectBrand() 内统一处理：切品牌会清空颜色并重新取树
bind('status', 'change', () => refreshAll());

bind('reset', 'click', () => {
  ['q', 'status'].forEach((id) => { const el = $(id); if (el) el.value = ''; 
  state.category = 'phone';
  state.kind = '';
  const cat0 = $('category'); if (cat0) cat0.value = 'phone';
});
  state.brand = { kind: 'all', id: '', value: '', label: '全部品牌' };
  state.model = { source: '', value: '', label: '全部型号' };
  state.color = '';
  state.collapsed.clear();
  const cs = $('colorSearch');
  if (cs) cs.value = '';
  const bs = $('brandSearch');
  if (bs) bs.value = '';
  const ms = $('modelSearch');
  if (ms) ms.value = '';
  refreshAll();
});

bind('more', 'click', () => load(false));
bind('d-close', 'click', closeDrawer);
bindSpecCard();
bind('d-copy', 'click', copyShareLink);
bind('d-share', 'focus', (e) => e.target.select());   // 聚焦即全选，便于手动复制
bind('mask', 'click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeColorPanel(); closeBrandPanel(); closeModelPanel(); closeDrawer(); }
});
window.addEventListener('resize', () => { positionBrandPanel(); positionModelPanel(); });

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
    if (first) {
      const v = first.dataset.value || '';
      if (first.dataset.kind === 'official') setOfficialColor(v); else setColor(v);
    }
  }
});
bind('colorList', 'click', (e) => {
  e.stopPropagation();
  const li = e.target.closest('.combo-item');
  if (!li) return;
  const value = li.dataset.value || '';
  if (li.dataset.kind === 'official') setOfficialColor(value);
  else setColor(value);
});
bind('colorList', 'mousedown', (e) => e.preventDefault());   // 保持搜索框焦点
document.addEventListener('click', (e) => {
  if (!e.target.isConnected) return;   // 目标已被重渲染移除，视为面板内操作
  if (!e.target.closest('#colorCombo')) closeColorPanel();
  if (!e.target.closest('#modelCombo')) closeModelPanel();
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
/* 型号选择器：点击展开、搜索、方向键/回车、点外部关闭 */
bind('modelBtn', 'click', () => {
  const panel = $('modelPanel');
  if (panel?.hidden) openModelPanel(); else closeModelPanel();
});
bind('modelSearch', 'input', (e) => {
  state.modelActive = -1;
  renderModelList(e.target.value);
});
bind('modelSearch', 'keydown', (e) => {
  const rows = modelNavItems();
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!rows.length) return;
    const step = e.key === 'ArrowDown' ? 1 : -1;
    state.modelActive = (state.modelActive + step + rows.length) % rows.length;
    paintModelActive();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const row = rows[state.modelActive] || rows[0];
    if (row) selectModel(row);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    closeModelPanel();
    $('modelBtn')?.focus();
  }
});
bind('modelList', 'click', (e) => {
  e.stopPropagation();          // 列表重渲染会让事件目标脱离 DOM，不能冒泡去关面板
  const li = e.target.closest('.combo-item');
  if (li) selectModel(li);
});
bind('modelList', 'mousedown', (e) => e.preventDefault());   // 保持搜索框焦点

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

/* ---------- 品类事件（V2.1） ---------- */

bind('category', 'change', (e) => setCategory(e.target.value));
$('kind')?.addEventListener('change', () => refreshAll());

let activated = false;
async function activate() {
  if (activated) return;
  activated = true;
  await initRole();
  await loadCatalog();
  await refreshAll();
  await loadOverview();
  const imgParam = new URLSearchParams(location.search).get('image');
  if (imgParam) {
    fetch('/api/v1/images/' + encodeURIComponent(imgParam))
      .then((r) => (r.ok ? r.json() : { __blocked: r.status }))
      .then((it) => {
        if (it && it.id) openDrawer(it);
        else if (it && it.__blocked === 404) toast('该图片未通过质量校验，业务视图不可见', false);
      }).catch(() => {});
  }
}
return { activate };
}
