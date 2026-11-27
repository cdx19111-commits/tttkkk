export async function mount(root) {
const $ = (id) => root.querySelector('#' + id);
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function fmtSize(b) { if (!b) return '—'; if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'; return Math.round(b / 1024) + ' KB'; }
function fmtNum(n) { return (n ?? 0).toLocaleString('zh-CN'); }
function fmtTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}).format(date);
}
function bind(id, type, handler, options) { const el = $(id); if (el) el.addEventListener(type, handler, options); return el; }
function shopDisplayName(name) {
  // 后端会把店铺显示成「店铺名（负责人）」；筛选器只展示店铺名。
  return String(name ?? '').replace(/[（(][^（）()]*[）)]\s*$/, '').trim() || String(name ?? '');
}
const DOUYIN_MAX_BATCH = 100;
const DOUYIN_MAX_BYTES = 300 * 1024 * 1024;
const douyinState = { rootId: '', shopFolderId: '', loadedShop: '', pathIds: [], folderId: '', offset: 0, limit: 48, total: 0, busy: false, seq: 0, summary: null, foldersByParent: new Map(), items: [], selected: new Set(), selectedItems: new Map(), downloading: false };

async function douyinRequest(path) {
  const r = await fetch('/api/v1/douyin-gallery' + path, { credentials: 'same-origin' });
  if (r.status === 401 || r.status === 403) throw new Error('请先登录图库');
  if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || '读取抖音主图失败'); }
  return r.json();
}

function douyinSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

const dyLibFilters = [];
const dyFilterClosers = [];

function dyFilterField(captionText, hostId) {
  const field = document.createElement('div'); field.className = 'douyin-folder-field';
  const caption = document.createElement('span'); caption.className = 'douyin-folder-level';
  caption.textContent = captionText;
  const filter = document.createElement('div'); filter.className = 'dy-filter'; filter.id = hostId;
  filter.innerHTML = '<button class="dy-filter-button" type="button" aria-haspopup="listbox" aria-expanded="false"><span class="dy-filter-value"></span><i>⌄</i></button><div class="dy-filter-menu" role="listbox" hidden></div>';
  field.append(caption, filter);
  return field;
}

function currentShopDisplayName() {
  const shop = dyGen.shops.find((x) => String(x.shop_id) === String(dyGen.shop));
  return shop ? shopDisplayName(shop.name) : '';
}

function updateLibraryStats() {
  const shops = douyinState.foldersByParent.get(douyinState.rootId) || [];
  const shop = shops.find((x) => String(x.path) === String(dyGen.shop));
  $('douyinTotal').textContent = fmtNum(shop ? shop.image_count : 0);
  const prefix = String(dyGen.shop) + '/';
  const nodes = [...douyinState.foldersByParent.values()].flat();
  $('douyinFoldersTotal').textContent = fmtNum(nodes.filter((node) =>
    String(node.path) === String(dyGen.shop) || String(node.path).startsWith(prefix)).length);
  $('douyinSize').textContent = douyinSize(douyinState.summary?.size_bytes || 0);
}

function renderDouyinFolders() {
  const host = $('douyinFolderFilters');
  while (dyLibFilters.length) dyLibFilters.pop().dispose();
  host.replaceChildren();
  const shopName = currentShopDisplayName();
  const labels = shopName ? [shopName] : [];
  if (!douyinState.shopFolderId) {
    $('douyinBreadcrumb').textContent = ['抖音主图', ...labels].join(' / ');
    return;
  }
  let parentId = douyinState.shopFolderId;
  for (let level = 0; level <= douyinState.pathIds.length; level++) {
    const folders = douyinState.foldersByParent.get(parentId) || [];
    if (!folders.length) break;
    const selected = douyinState.pathIds[level] || '';
    const captionText = level === 0 ? '品类' : level === 1 ? '品牌' : level === 2 ? '型号' : `第 ${level + 3} 层目录`;
    const field = dyFilterField(captionText, `dyLibFilter${level}`);
    host.appendChild(field);
    const allLabel = level === 0 ? '全部品类' : level === 1 ? '全部品牌' : level === 2 ? '全部型号' : `全部第 ${level + 3} 层目录`;
    const filter = createDyFilter(field.querySelector('.dy-filter'), allLabel, () => {
      const next = douyinState.pathIds.slice(0, level);
      if (filter.value) next.push(filter.value);
      douyinState.pathIds = next;
      douyinState.folderId = next.length ? next[next.length - 1] : douyinState.shopFolderId;
      renderDouyinFolders(); loadDouyinItems(true);
    });
    filter.setOptions(folders.map((folder) => ({
      value: folder.folder_id,
      label: folder.name,
      count: folder.image_count,
    })));
    filter.value = selected;
    dyLibFilters.push(filter);
    if (!selected) {
      const nextLevel = level + 1;
      const nextCaption = nextLevel === 1 ? '品牌' : nextLevel === 2 ? '型号' : `第 ${nextLevel + 3} 层目录`;
      const nextHint = nextLevel === 1 ? '先选择品类' : nextLevel === 2 ? '先选择品牌' : '先选择上一级';
      const nextField = dyFilterField(nextCaption, `dyLibFilterNext${nextLevel}`);
      host.appendChild(nextField);
      const nextFilter = createDyFilter(nextField.querySelector('.dy-filter'), nextHint);
      nextFilter.setDisabled(true);
      dyLibFilters.push(nextFilter);
      break;
    }
    const chosen = folders.find((x) => x.folder_id === selected);
    if (!chosen) { douyinState.pathIds = douyinState.pathIds.slice(0, level); break; }
    labels.push(chosen.name);
    parentId = chosen.folder_id;
  }
  douyinState.folderId = douyinState.pathIds.length ? douyinState.pathIds[douyinState.pathIds.length - 1] : douyinState.shopFolderId;
  $('douyinBreadcrumb').textContent = ['抖音主图', ...labels].join(' / ');
}

function douyinCard(item) {
  const card = document.createElement('article');
  const id = String(item.item_id);
  const selected = douyinState.selected.has(id);
  card.className = 'card' + (selected ? ' is-selected' : '');
  card.dataset.douyinCard = id;
  const dim = item.width && item.height ? `${item.width}×${item.height}` : '图片';
  card.innerHTML = `<button class="douyin-select-check" type="button" data-douyin-select="${esc(id)}" aria-pressed="${selected}" aria-label="选择 ${esc(item.name)}"><span>✓</span></button><div class="thumb"><img loading="lazy" src="${esc(item.image_url)}" alt="${esc(item.name)}"></div><div class="cap"><div class="model" title="${esc(item.name)}">${esc(item.name)}</div><div class="sub"><span class="chip">抖音主图</span></div><div class="meta">${dim} · ${esc(item.extension.toUpperCase())} · ${fmtSize(item.size_bytes)}</div></div>`;
  card.setAttribute('role', 'button'); card.tabIndex = 0; card.setAttribute('aria-label', `预览图片：${item.name}`);
  const preview = () => openDouyinPreview(item);
  card.addEventListener('click', (event) => {
    if (event.target.closest('[data-douyin-select]')) { event.stopPropagation(); toggleDouyinSelect(item); return; }
    preview();
  });
  card.addEventListener('keydown', (event) => {
    if (event.target.closest('[data-douyin-select]')) return;
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); preview(); }
  });
  return card;
}

function syncDouyinSelectionUI() {
  $('douyinGrid').querySelectorAll('[data-douyin-card]').forEach((card) => {
    const selected = douyinState.selected.has(card.dataset.douyinCard);
    card.classList.toggle('is-selected', selected);
    card.querySelector('[data-douyin-select]')?.setAttribute('aria-pressed', String(selected));
  });
}

function updateDouyinSelectionUI() {
  const count = douyinState.selected.size;
  const bytes = [...douyinState.selectedItems.values()]
    .reduce((sum, item) => sum + Number(item.size_bytes || 0), 0);
  $('douyinSelectionInfo').textContent = count
    ? `已选 ${count} 张 · 约 ${douyinSize(bytes)}（单次最多 ${DOUYIN_MAX_BATCH} 张）`
    : `勾选图片后可批量下载（单次最多 ${DOUYIN_MAX_BATCH} 张）`;
  $('douyinDownloadSelected').disabled = count === 0 || douyinState.downloading;
  $('douyinDownloadSelected').textContent = douyinState.downloading ? '打包中…' : '批量下载 ZIP';
  $('douyinClearSelection').disabled = count === 0 || douyinState.downloading;
  $('douyinSelectAll').disabled = douyinState.downloading;
}

function toggleDouyinSelect(item) {
  const id = String(item.item_id);
  if (douyinState.selected.has(id)) {
    douyinState.selected.delete(id);
    douyinState.selectedItems.delete(id);
  } else {
    if (douyinState.selected.size >= DOUYIN_MAX_BATCH) {
      alert(`单次最多选择 ${DOUYIN_MAX_BATCH} 张`);
      return;
    }
    douyinState.selected.add(id);
    douyinState.selectedItems.set(id, item);
  }
  syncDouyinSelectionUI();
  updateDouyinSelectionUI();
}

function clearDouyinSelection() {
  douyinState.selected.clear();
  douyinState.selectedItems.clear();
  syncDouyinSelectionUI();
  updateDouyinSelectionUI();
}

function selectAllLoadedDouyin() {
  for (const item of douyinState.items) {
    if (douyinState.selected.size >= DOUYIN_MAX_BATCH) break;
    const id = String(item.item_id);
    if (!douyinState.selected.has(id)) {
      douyinState.selected.add(id);
      douyinState.selectedItems.set(id, item);
    }
  }
  syncDouyinSelectionUI();
  updateDouyinSelectionUI();
}

const DOUYIN_CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function douyinCrc32(bytes) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) crc = DOUYIN_CRC_TABLE[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

function douyinZipStore(files) {
  const encoder = new TextEncoder();
  const now = new Date();
  const dosTime = ((now.getHours() & 0x1f) << 11) | ((now.getMinutes() & 0x3f) << 5) | ((Math.floor(now.getSeconds() / 2)) & 0x1f);
  const dosDate = (((now.getFullYear() - 1980) & 0x7f) << 9) | (((now.getMonth() + 1) & 0x0f) << 5) | (now.getDate() & 0x1f);
  const chunks = [];
  const central = [];
  let offset = 0;
  for (const file of files) {
    const name = encoder.encode(file.name);
    const data = file.data;
    const crc = douyinCrc32(data);
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true);
    lv.setUint16(8, 0, true);
    lv.setUint16(10, dosTime, true);
    lv.setUint16(12, dosDate, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, data.length, true);
    lv.setUint32(22, data.length, true);
    lv.setUint16(26, name.length, true);
    lv.setUint16(28, 0, true);
    local.set(name, 30);
    chunks.push(local, data);
    const directory = new Uint8Array(46 + name.length);
    const cv = new DataView(directory.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, dosTime, true);
    cv.setUint16(14, dosDate, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, name.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);
    directory.set(name, 46);
    central.push(directory);
    offset += local.length + data.length;
  }
  const centralSize = central.reduce((sum, part) => sum + part.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);
  ev.setUint16(20, 0, true);
  return new Blob([...chunks, ...central, end], {type: 'application/zip'});
}

function douyinZipName(item, used) {
  let base = String(item.name || item.item_id || 'image').replace(/[\\/:*?"<>|]/g, '_').trim() || 'image';
  const extension = String(item.extension || '').toLowerCase();
  if (extension && !base.toLowerCase().endsWith('.' + extension)) base += '.' + extension;
  let name = base;
  let index = 2;
  while (used.has(name.toLowerCase())) {
    const dot = base.lastIndexOf('.');
    name = dot > 0 ? `${base.slice(0, dot)} (${index})${base.slice(dot)}` : `${base} (${index})`;
    index += 1;
  }
  used.add(name.toLowerCase());
  return name;
}

async function downloadSelectedDouyin() {
  if (douyinState.downloading) return;
  const items = [...douyinState.selected]
    .map((id) => douyinState.selectedItems.get(id))
    .filter(Boolean);
  if (!items.length) return;
  const totalBytes = items.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0);
  if (totalBytes > DOUYIN_MAX_BYTES) {
    alert(`所选图片约 ${douyinSize(totalBytes)}，超过单次 ${douyinSize(DOUYIN_MAX_BYTES)} 限制，请减少选择`);
    return;
  }
  douyinState.downloading = true;
  updateDouyinSelectionUI();
  const files = [];
  const failed = [];
  const used = new Set();
  try {
    for (let index = 0; index < items.length; index++) {
      const item = items[index];
      $('douyinDownloadSelected').textContent = `打包中 ${index + 1}/${items.length}`;
      try {
        const response = await fetch(item.image_url, {credentials: 'same-origin'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        files.push({name: douyinZipName(item, used), data: new Uint8Array(await response.arrayBuffer())});
      } catch (error) {
        failed.push(item.name || item.item_id);
      }
    }
    if (!files.length) {
      alert('所选图片都没有读取成功，请重试');
      return;
    }
    const url = URL.createObjectURL(douyinZipStore(files));
    const link = document.createElement('a');
    link.href = url;
    link.download = `抖音主图_${currentShopDisplayName() || '批量下载'}_${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    if (failed.length) alert(`已打包 ${files.length} 张，${failed.length} 张读取失败：\n${failed.slice(0, 10).join('\n')}${failed.length > 10 ? '\n…' : ''}`);
  } finally {
    douyinState.downloading = false;
    updateDouyinSelectionUI();
  }
}

function openDouyinPreview(item) {
  const overlay = $('douyinPreview');
  $('douyinPreviewImage').src = item.image_url;
  $('douyinPreviewImage').alt = item.name;
  $('douyinPreviewTitle').textContent = item.name;
  $('douyinPreviewPath').textContent = item.source_path || '';
  $('douyinPreviewMeta').textContent = `${item.width && item.height ? `${item.width}×${item.height}` : '图片'} · ${item.extension.toUpperCase()} · ${fmtSize(item.size_bytes)}`;
  $('douyinPreviewDownload').href = item.image_url;
  overlay.hidden = false;
  document.body.classList.add('douyin-preview-open');
  $('douyinPreviewClose').focus();
}

function closeDouyinPreview() {
  const overlay = $('douyinPreview');
  if (overlay.hidden) return;
  overlay.hidden = true;
  $('douyinPreviewImage').removeAttribute('src');
  document.body.classList.remove('douyin-preview-open');
}

bind('douyinPreviewClose', 'click', closeDouyinPreview);
bind('douyinPreview', 'click', (event) => { if (event.target === $('douyinPreview')) closeDouyinPreview(); });
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeDouyinPreview();
});

async function loadDouyinItems(reset = false) {
  if (!douyinState.folderId) return;
  if (douyinState.busy && !reset) return;
  if (reset) { douyinState.offset = 0; douyinState.items = []; $('douyinGrid').replaceChildren(); }
  const seq = ++douyinState.seq; douyinState.busy = true; $('douyinMore').disabled = true;
  try {
    const p = new URLSearchParams({ folder_id: douyinState.folderId || douyinState.rootId, limit: douyinState.limit, offset: douyinState.offset });
    const q = $('douyinSearch').value.trim(); if (q) p.set('q', q);
    const data = await douyinRequest('/items?' + p.toString()); if (seq !== douyinState.seq) return;
    $('douyinCount').textContent = `共 ${fmtNum(data.total)} 张`;
    for (const item of data.items) {
      douyinState.items.push(item);
      $('douyinGrid').appendChild(douyinCard(item));
    }
    syncDouyinSelectionUI();
    updateDouyinSelectionUI();
    douyinState.offset += data.items.length; $('douyinMore').hidden = douyinState.offset >= data.total; $('douyinEmpty').hidden = data.total > 0;
  } catch (e) { $('douyinEmpty').textContent = e.message; $('douyinEmpty').hidden = false; }
  finally { if (seq === douyinState.seq) { douyinState.busy = false; $('douyinMore').disabled = false; } }
}

async function loadDouyinTree() {
  const [summary, tree] = await Promise.all([douyinRequest('/summary'), douyinRequest('/folder-tree')]);
  douyinState.summary = summary; douyinState.rootId = summary.root_folder_id;
  douyinState.foldersByParent = new Map();
  for (const folder of tree.items) {
    const siblings = douyinState.foldersByParent.get(folder.parent_id) || [];
    siblings.push(folder); douyinState.foldersByParent.set(folder.parent_id, siblings);
  }
  updateLibraryStats();
}

async function syncLibraryToShop() {
  if (!douyinState.summary || !douyinState.foldersByParent.size) await loadDouyinTree();
  const shops = douyinState.foldersByParent.get(douyinState.rootId) || [];
  const shop = shops.find((x) => String(x.path) === String(dyGen.shop));
  douyinState.shopFolderId = shop ? shop.folder_id : '';
  douyinState.pathIds = [];
  douyinState.folderId = douyinState.shopFolderId;
  updateLibraryStats();
  renderDouyinFolders();
  if (douyinState.shopFolderId) {
    await loadDouyinItems(true);
    douyinState.loadedShop = dyGen.shop;
  } else {
    douyinState.seq++;
    douyinState.busy = false;
    $('douyinGrid').replaceChildren();
    $('douyinCount').textContent = '';
    $('douyinMore').hidden = true;
    $('douyinEmpty').textContent = '该店铺在主图库中暂无目录';
    $('douyinEmpty').hidden = false;
  }
}

async function loadDouyinGallery(reset = false) {
  try {
    if (reset) {
      await syncLibraryToShop();
    } else {
      if (!douyinState.summary || !douyinState.foldersByParent.size) await loadDouyinTree();
      await loadDouyinItems(false);
    }
  } catch (e) { $('douyinEmpty').textContent = e.message; $('douyinEmpty').hidden = false; }
}

let douyinSearchTimer;
bind('douyinSearch', 'input', () => { clearTimeout(douyinSearchTimer); douyinSearchTimer = setTimeout(() => loadDouyinItems(true), 250); });
bind('douyinMore', 'click', () => loadDouyinItems(false));
bind('douyinReload', 'click', () => { douyinState.summary = null; loadDouyinGallery(true); });
bind('douyinSelectAll', 'click', selectAllLoadedDouyin);
bind('douyinClearSelection', 'click', clearDouyinSelection);
bind('douyinDownloadSelected', 'click', () => downloadSelectedDouyin().catch((error) => alert(error.message || '批量下载失败')));

/* ---------- 主图生成 ---------- */
const PAGE_SIZE = 60;
const dyGen = { shop: '', items: [], templates: [], shops: [], facets: null, currentTemplate: null,
  busy: false, tab: 'library', total: 0, offset: 0, selected: new Set(), view: 'grid',
  runId: '', runBusy: false, runTimer: null, resumeChecked: false, selectAllActive: false };
const dyFilters = {};
let dyShopFilter = null;
let dyTemplateScopeFilter = null;
let dyTemplateCategoryFilter = null;
let shopsPromise = null;
const dyLayout = { template: null, config: null, base: null, models: [], modelKey: '', drag: null };

async function dyGenRequest(path, options = {}) {
  const response = await fetch('/api/v1/main-images' + path, {credentials: 'same-origin', ...options});
  if (response.status === 401 || response.status === 403) throw new Error('登录状态已失效，请重新登录');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

const DY_STATUS = {
  ok: ['已生成', 'ok'], missing: ['缺失', 'missing'], stale: ['待更新', 'stale'],
  failed: ['失败', 'failed'], no_template: ['无模板', 'missing'], missing_file: ['文件缺失', 'missing'],
};

async function setDyTab(tab) {
  dyGen.tab = tab;
  root.querySelectorAll('[data-dy-tab]').forEach((button) => {
    const active = button.dataset.dyTab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $('douyinLibraryPane').hidden = tab !== 'library';
  $('douyinGeneratePane').hidden = tab !== 'generate';
  try { await ensureShops(); } catch (error) { /* 店铺加载失败时保留页面提示 */ }
  if (tab === 'generate') await loadDyGen();
  else if (douyinState.loadedShop !== dyGen.shop || !douyinState.shopFolderId) await loadDouyinGallery(true);
}

function createDyFilter(hostRef, allLabel, onChange, options = {}) {
  const host = typeof hostRef === 'string' ? $(hostRef) : hostRef;
  const includeAll = options.includeAll !== false;
  const button = host.querySelector('.dy-filter-button');
  const valueNode = host.querySelector('.dy-filter-value');
  const menu = host.querySelector('.dy-filter-menu');
  let items = [];
  let value = '';
  let disabled = false;
  const labelOf = (item) => item.count === undefined ? item.label : `${item.label}（${fmtNum(item.count)}）`;
  const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  const render = () => {
    const rows = includeAll ? [{value: '', label: allLabel}].concat(items) : items;
    menu.innerHTML = rows.map((item) =>
      `<button type="button" role="option" data-value="${esc(item.value)}" aria-selected="${item.value === value}">${esc(labelOf(item))}</button>`).join('');
  };
  const setValue = (next) => {
    value = disabled ? '' : (next || (includeAll ? '' : (items[0]?.value || '')));
    const found = items.find((item) => item.value === value);
    valueNode.textContent = disabled ? allLabel : (found ? labelOf(found) : allLabel);
    button.title = valueNode.textContent;
  };
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    if (disabled) return;
    for (const closer of dyFilterClosers) {
      if (closer !== close) closer();
    }
    render();
    menu.hidden = !menu.hidden;
    button.setAttribute('aria-expanded', String(!menu.hidden));
  });
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('[data-value]');
    if (!option) return;
    const previous = value;
    setValue(option.dataset.value);
    close();
    if (previous !== value && onChange) onChange();
  });
  dyFilterClosers.push(close);
  const api = {
    get value() { return value; },
    set value(next) { setValue(next); },
    setOptions(next) {
      const current = value;
      items = next;
      if (!includeAll) {
        if (!items.some((item) => item.value === current)) value = items[0]?.value || '';
      } else if (current && !items.some((item) => item.value === current)) {
        value = '';
      }
      render();
      setValue(value);
    },
    setDisabled(next) {
      disabled = !!next;
      button.disabled = disabled;
      button.classList.toggle('is-disabled', disabled);
      if (disabled) close();
      setValue('');
    },
    close,
    dispose() {
      close();
      const index = dyFilterClosers.indexOf(close);
      if (index >= 0) dyFilterClosers.splice(index, 1);
    },
  };
  setValue('');
  return api;
}

// 点击筛选器以外的任意位置（含字段标题、页面空白）立即收起；pointerdown 先于 click，
// 且用捕获阶段，避免被字段外层元素的默认点击行为重新打开。
document.addEventListener('pointerdown', (event) => {
  if (event.target instanceof Element && event.target.closest('.dy-filter')) return;
  for (const close of dyFilterClosers.slice()) close();
}, true);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') for (const close of dyFilterClosers.slice()) close();
});

function refreshDyModelOptions() {
  const category = dyFilters.category.value;
  const brand = dyFilters.brand.value;
  const models = (dyGen.facets?.models || []).filter((item) =>
    (!category || item.category === category) && (!brand || item.brand === brand));
  dyFilters.model.setOptions(models.map((x) => ({value: x.value, label: x.value, count: x.count})));
}

function dyGenQuery(offset = 0, limit = PAGE_SIZE) {
  const params = new URLSearchParams({shop_id: dyGen.shop, offset: String(offset), limit: String(limit)});
  for (const key of ['category', 'brand', 'model', 'status']) {
    const value = dyFilters[key]?.value;
    if (value) params.set(key, value);
  }
  const q = $('dyGenSearch').value.trim();
  if (q) params.set('q', q);
  return params;
}

function updateDyPreviewOptions() {
  const select = $('dyPreviewModel');
  const previous = select.value;
  select.innerHTML = dyGen.items.map((item) => `<option value="${esc(item.model_key)}">${esc(item.model_name)}</option>`).join('');
  if (previous && dyGen.items.some((x) => x.model_key === previous)) select.value = previous;
}

function updateTemplateScopeOptions() {
  if (!dyTemplateScopeFilter) return;
  const shop = dyGen.shops.find((x) => String(x.shop_id) === String(dyShopFilter?.value || dyGen.shop));
  const name = shop ? shopDisplayName(shop.name) : (dyGen.shop || '当前店铺');
  const current = dyTemplateScopeFilter.value || 'shop';
  dyTemplateScopeFilter.setOptions([
    {value: 'shop', label: `当前店铺：${name}`},
    {value: 'global', label: '通用模板'},
  ]);
  dyTemplateScopeFilter.value = current;
}

async function ensureShops() {
  if (dyGen.shops.length) return dyGen.shops;
  if (!shopsPromise) {
    shopsPromise = dyGenRequest('/shops').then((data) => {
      dyGen.shops = data.items || [];
      const previous = dyShopFilter?.value || '';
      dyShopFilter?.setOptions(dyGen.shops.map((x) => ({value: x.shop_id, label: shopDisplayName(x.name)})));
      const preferred = dyGen.shops.find((x) => x.shop_id === '129675602') || dyGen.shops[0];
      if (dyShopFilter) dyShopFilter.value = previous || preferred?.shop_id || '';
      dyGen.shop = dyShopFilter?.value || preferred?.shop_id || '';
      updateTemplateScopeOptions();
      return dyGen.shops;
    }).catch((error) => {
      shopsPromise = null;
      throw error;
    });
  }
  return shopsPromise;
}

async function onShopChange() {
  dyGen.shop = dyShopFilter?.value || '';
  clearDouyinSelection();
  dyGen.facets = null;
  dyGen.selected.clear();
  dyGen.selectAllActive = false;
  dyGen.resumeChecked = false;
  dyGen.items = [];
  dyGen.offset = 0;
  if (dyFilters.category) dyFilters.category.value = '';
  if (dyFilters.brand) dyFilters.brand.value = '';
  if (dyFilters.model) dyFilters.model.value = '';
  if (dyFilters.status) dyFilters.status.value = '';
  $('dyGenSearch').value = '';
  updateSelectionUI();
  updateTemplateScopeOptions();
  if (!$('dyTemplatePanel').hidden) await loadTemplateEditor();
  await loadDyGen(true);
  await syncLibraryToShop();
}

async function loadDyGen(reset = true) {
  $('dyGenEmpty').hidden = true;
  try {
    await ensureShops();
    dyGen.shop = dyShopFilter?.value || dyGen.shops[0]?.shop_id || '';
    const shopId = dyGen.shop;
    if (reset) {
      const [overview, facets] = await Promise.all([
        dyGenRequest(`/overview?shop_id=${encodeURIComponent(shopId)}`),
        dyGenRequest(`/facets?shop_id=${encodeURIComponent(shopId)}`),
      ]);
      dyGen.facets = facets;
      dyFilters.category.setOptions((facets.categories || []).map((x) => ({value: x.value, label: x.label, count: x.count})));
      dyFilters.brand.setOptions((facets.brands || []).map((x) => ({value: x.value, label: x.value, count: x.count})));
      refreshDyModelOptions();
      const o = overview;
      $('dyGenItems').textContent = fmtNum(o.items);
      $('dyGenGenerated').textContent = fmtNum(o.generated);
      $('dyGenMissing').textContent = fmtNum(o.missing);
      $('dyGenStale').textContent = fmtNum(o.stale);
      $('dyGenFailed').textContent = fmtNum(o.failed);
      dyGenRequest('/schedule').then((schedule) => {
        $('dyGenSchedule').textContent = `自动任务：${schedule.enabled ? '已启用' : '未启用'} · 每天 ${schedule.time} · 下次 ${schedule.next_run || '—'}${schedule.running ? ' · 正在执行' : ''}`;
      }).catch(() => {});
      dyGen.offset = 0;
      dyGen.items = [];
    }
    const data = await dyGenRequest('/items?' + dyGenQuery(dyGen.offset, PAGE_SIZE).toString());
    const incoming = data.items || [];
    if (reset) {
      dyGen.items = incoming;
    } else {
      const known = new Set(dyGen.items.map((x) => x.model_key));
      dyGen.items = dyGen.items.concat(incoming.filter((x) => !known.has(x.model_key)));
    }
    dyGen.total = data.total;
    dyGen.offset = dyGen.items.length;
    updateDyPreviewOptions();
    renderDyGenItems();
    if (reset && !dyGen.resumeChecked) {
      dyGen.resumeChecked = true;
      resumeGeneration();
    }
  } catch (error) {
    $('dyGenEmpty').textContent = error.message || '主图生成数据加载失败';
    $('dyGenEmpty').hidden = false;
  }
}

function dyGenCard(item) {
  const [label, cls] = DY_STATUS[item.status] || [item.status, ''];
  const selected = dyGen.selected.has(item.model_key);
  const time = item.generated_at ? `生成 ${fmtTime(item.generated_at)}` : '尚未生成';
  const image = item.thumb_url
    ? `<img loading="lazy" src="${esc(item.thumb_url)}&v=${encodeURIComponent(item.generated_at || '')}" alt="${esc(item.model_name)}">`
    : `<div class="dy-gen-card-placeholder ${cls}"><span>${esc(label)}</span></div>`;
  return `<article class="dy-gen-card ${selected ? 'is-selected' : ''} ${cls}" data-dy-model="${esc(item.model_key)}">
    <div class="dy-gen-card-image" data-dy-open="${esc(item.model_key)}">
      ${image}
      <span class="dy-gen-card-status ${cls}">${esc(label)}</span>
      <button class="dy-gen-check" type="button" data-dy-check="${esc(item.model_key)}" aria-pressed="${selected}" aria-label="选择 ${esc(item.model_name)}"><span>✓</span></button>
      <div class="dy-gen-card-caption">
        <b>${esc(item.model_name)}</b>
        <small>${esc(time)}</small>
      </div>
    </div>
  </article>`;
}

function dyGenRow(item) {
  const [label, cls] = DY_STATUS[item.status] || [item.status, ''];
  const selected = dyGen.selected.has(item.model_key);
  const thumb = item.thumb_url
    ? `<img class="dy-gen-thumb" loading="lazy" src="${esc(item.thumb_url)}&v=${encodeURIComponent(item.generated_at || '')}" alt="${esc(item.model_name)}" data-dy-open="${esc(item.model_key)}">`
    : `<span class="dy-gen-thumb" data-dy-open="${esc(item.model_key)}"></span>`;
  return `<article class="dy-gen-row ${selected ? 'is-selected' : ''}" data-dy-model="${esc(item.model_key)}">
    <button class="dy-gen-check dy-gen-check-row" type="button" data-dy-check="${esc(item.model_key)}" aria-pressed="${selected}" aria-label="选择 ${esc(item.model_name)}"><span>✓</span></button>
    ${thumb}
    <div class="dy-gen-row-main" data-dy-open="${esc(item.model_key)}">
      <b>${esc(item.model_name)}</b>
      <small>Gaia 型号键 ${esc(item.model_key)} · ${esc(item.template_key)} · 源图 ${esc(item.source_color || '未选')}</small>
    </div>
    <div class="dy-gen-row-time">${item.generated_at ? '生成 ' + esc(fmtTime(item.generated_at)) : '尚未生成'}</div>
    <span class="dy-gen-badge ${cls}">${esc(label)}</span>
    <div class="dy-gen-row-actions">
      <button class="btn ghost" type="button" data-dy-view="${esc(item.model_key)}">预览</button>
    </div>
    ${item.error ? `<div class="dy-gen-row-error">${esc(item.error)}</div>` : ''}
  </article>`;
}

function renderDyGenItems() {
  const list = $('dyGenList');
  list.className = dyGen.view === 'grid' ? 'dy-gen-grid' : 'dy-gen-list';
  list.innerHTML = dyGen.items.map((item) => dyGen.view === 'grid' ? dyGenCard(item) : dyGenRow(item)).join('');
  $('dyGenEmpty').hidden = dyGen.items.length !== 0;
  $('dyGenMore').hidden = dyGen.offset >= dyGen.total;
  $('dyGenLoaded').textContent = dyGen.total ? `已加载 ${fmtNum(dyGen.offset)} / ${fmtNum(dyGen.total)} 个型号` : '';
  updateSelectionUI();
}

function updateSelectionUI() {
  const count = dyGen.selected.size;
  $('dyGenRegenSelected').textContent = `重新生成选中（${count}）`;
  $('dyGenRegenSelected').disabled = count === 0 || dyGen.runBusy;
  $('dyGenClearSelection').disabled = count === 0 || dyGen.runBusy;
  $('dyGenSelectAll').disabled = dyGen.runBusy;
  $('dyGenSelectAll').textContent = dyGen.selectAllActive ? '取消全选' : '全选';
  $('dyGenSelectAll').title = dyGen.selectAllActive ? '取消当前筛选下的全选' : '全选当前筛选下的全部型号';
  $('dyGenAll').disabled = dyGen.runBusy;
  $('dyGenSelectionInfo').textContent = dyGen.runBusy
    ? `生成任务进行中，已选 ${count} 个；完成后可继续操作`
    : `点图片预览，点右下角勾选框多选；已选 ${count} 个`;
}

function toggleSelected(modelKey) {
  if (dyGen.selected.has(modelKey)) dyGen.selected.delete(modelKey);
  else dyGen.selected.add(modelKey);
  dyGen.selectAllActive = false;
  renderDyGenItems();
}

async function selectAllFiltered() {
  if (dyGen.runBusy) return;
  try {
    const data = await dyGenRequest('/items?' + dyGenQuery(0, 1000).toString());
    const keys = (data.items || []).map((item) => item.model_key);
    const allSelected = keys.length > 0 && keys.every((key) => dyGen.selected.has(key));
    if (dyGen.selectAllActive || allSelected) {
      for (const key of keys) dyGen.selected.delete(key);
      dyGen.selectAllActive = false;
    } else {
      for (const key of keys) dyGen.selected.add(key);
      dyGen.selectAllActive = true;
    }
    renderDyGenItems();
  } catch (error) {
    alert(error.message || '全选失败');
  }
}

function setDyGenView(view) {
  dyGen.view = view;
  $('dyGenViewGrid').classList.toggle('active', view === 'grid');
  $('dyGenViewList').classList.toggle('active', view === 'list');
  renderDyGenItems();
}

function updateRunProgress(run) {
  const total = run.total || 0;
  const done = run.done || 0;
  const percent = total ? Math.min(100, Math.round(done / total * 100)) : 0;
  $('dyGenProgress').hidden = false;
  $('dyGenProgressBar').style.width = `${percent}%`;
  $('dyGenProgressText').textContent = run.status === 'running'
    ? `生成中 ${done}/${total} · 成功 ${run.ok || 0} · 失败 ${run.failed || 0} · 无模板 ${run.no_template || 0} · 跳过 ${run.skipped || 0}`
    : `完成：成功 ${run.ok || 0} · 失败 ${run.failed || 0} · 无模板 ${run.no_template || 0} · 跳过 ${run.skipped || 0}${run.errors?.length ? ` · 首个失败：${run.errors[0].model_name} ${run.errors[0].error}` : ''}`;
}

async function pollRun(runId) {
  clearTimeout(dyGen.runTimer);
  try {
    const run = await dyGenRequest(`/runs/${encodeURIComponent(runId)}`);
    updateRunProgress(run);
    if (run.status === 'running') {
      dyGen.runTimer = setTimeout(() => pollRun(runId), 1500);
      return;
    }
    dyGen.runBusy = false;
    dyGen.selected.clear();
    dyGen.selectAllActive = false;
    updateSelectionUI();
    await loadDyGen(true);
    setTimeout(() => { $('dyGenProgress').hidden = true; }, 8000);
  } catch (error) {
    dyGen.runBusy = false;
    updateSelectionUI();
    $('dyGenProgressText').textContent = error.message || '生成任务状态读取失败';
  }
}

async function resumeGeneration() {
  try {
    const data = await dyGenRequest(`/runs/current?shop_id=${encodeURIComponent(dyGen.shop)}`);
    const run = data.run;
    if (run?.status === 'running') {
      dyGen.runId = run.run_id;
      dyGen.runBusy = true;
      updateSelectionUI();
      updateRunProgress(run);
      pollRun(run.run_id);
    } else if (run && run.status === 'done' && run.finished_at) {
      const age = Date.now() - new Date(run.finished_at).getTime();
      if (Number.isFinite(age) && age < 10 * 60 * 1000) {
        updateRunProgress(run);
        setTimeout(() => { if (!dyGen.runBusy) $('dyGenProgress').hidden = true; }, 8000);
      }
    }
  } catch (error) {
    // no active run is fine
  }
}

async function startAllMissing() {
  if (dyGen.runBusy) return;
  try {
    const data = await dyGenRequest('/items?' + dyGenQuery(0, 1000).toString());
    const keys = (data.items || [])
      .filter((item) => item.status === 'missing' || item.status === 'stale')
      .map((item) => item.model_key);
    if (!keys.length) { alert('当前筛选下没有需要生成的型号'); return; }
    await startRun(keys, false);
  } catch (error) {
    alert(error.message || '读取待生成型号失败');
  }
}

async function startRun(keys = [], force = false) {
  if (dyGen.runBusy) return;
  const count = keys.length || dyGen.total;
  if (count > 30 && !window.confirm(`将生成 ${count} 个型号，任务会在后台执行，可能耗时较长，继续吗？`)) return;
  try {
    const data = await dyGenRequest('/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({shop_id: dyGen.shop, model_keys: keys, force}),
    });
    if (!data.run_id) { await loadDyGen(true); return; }
    dyGen.runId = data.run_id;
    dyGen.runBusy = true;
    updateSelectionUI();
    updateRunProgress({...data, status: 'running', done: 0, ok: 0, failed: 0, skipped: 0});
    pollRun(data.run_id);
  } catch (error) {
    alert(error.message || '生成任务启动失败');
  }
}

function openMainPreview(modelKey) {
  const item = dyGen.items.find((x) => x.model_key === modelKey);
  if (!item) return;
  const [label] = DY_STATUS[item.status] || [item.status, ''];
  $('dyMainPreviewTitle').textContent = item.model_name;
  const parts = [item.shop_name || '', item.category || '', label];
  if (item.generated_at) parts.push(`生成于 ${fmtTime(item.generated_at)}`);
  $('dyMainPreviewMeta').textContent = parts.filter(Boolean).join(' · ');
  const image = $('dyMainPreviewImage');
  const link = $('dyMainPreviewDownload');
  if (item.output_url) {
    image.src = `${item.output_url}&t=${encodeURIComponent(item.generated_at || Date.now())}`;
    image.style.display = '';
    link.href = item.output_url;
    link.style.display = '';
    $('dyMainPreviewStatus').textContent = item.error || '';
  } else {
    image.removeAttribute('src');
    image.style.display = 'none';
    link.removeAttribute('href');
    link.style.display = 'none';
    $('dyMainPreviewStatus').textContent = item.error || '尚未生成，可点击“重新生成”尝试。';
  }
  $('dyMainPreviewRegen').dataset.key = item.model_key;
  $('dyMainPreviewRegen').disabled = dyGen.runBusy;
  $('dyMainPreview').hidden = false;
  document.body.classList.add('dy-main-preview-open');
}

function closeMainPreview() {
  if ($('dyMainPreview').hidden) return;
  $('dyMainPreview').hidden = true;
  $('dyMainPreviewImage').removeAttribute('src');
  document.body.classList.remove('dy-main-preview-open');
}

const DY_TEMPLATE_LABELS = {
  ios_phone: '苹果手机', android_phone: '安卓手机', ipad: 'iPad',
  android_tablet: '安卓平板', watch: '手表', other_digital: '其他数码',
};

function templateCard(template) {
  const active = template.active;
  const state = active ? '当前使用' : '未启用';
  return `<article class="dy-template-card ${active ? 'active' : ''}" data-dy-template-card="${template.id}">
    <img src="/api/v1/main-images/templates/${template.id}/preview-image?shop_id=${encodeURIComponent(dyGen.shop)}&t=${Date.now()}" alt="${esc(template.name)}" onerror="this.onerror=null;this.src='${esc(template.image_url)}?t=${Date.now()}'">
    <div class="dy-template-card-body">
      <b>${esc(template.name)}</b>
      <small>${template.scope === 'shop' ? '店铺模板' : '通用模板'} · 版本 ${template.version} · ${state}</small>
      <div class="dy-template-card-actions">
        <button class="btn ${active ? 'ghost' : 'primary'}" type="button" data-dy-template-activate="${template.id}" ${active ? 'disabled' : ''}>${active ? '当前使用' : '设为当前'}</button>
        <button class="btn ghost" type="button" data-dy-template-preview="${template.id}">预览</button>
        <button class="btn ghost" type="button" data-dy-template-edit="${template.id}">调整版式</button>
        <button class="btn danger" type="button" data-dy-template-delete="${template.id}" data-dy-template-name="${esc(template.name)}">删除</button>
      </div>
    </div>
  </article>`;
}

function setTemplateStatus(text) {
  $('dyTemplateStatus').textContent = text;
}

function dyLayoutModelName() {
  const select = $('dyLayoutModel');
  return select?.selectedOptions?.[0]?.textContent || dyLayout.modelKey || '型号';
}

function dyLayoutMetrics() {
  const stage = $('dyLayoutStage');
  const rect = stage.getBoundingClientRect();
  return {stage, rect, unit: 1080 / Math.max(1, rect.width)};
}

function hideLayoutExact() {
  const exact = $('dyLayoutExact');
  if (!exact) return;
  if (exact.src) URL.revokeObjectURL(exact.src);
  exact.removeAttribute('src');
  exact.hidden = true;
  $('dyLayoutExactBtn').hidden = false;
  $('dyLayoutBackEdit').hidden = true;
}

async function showLayoutExact() {
  if (!dyLayout.config || !dyLayout.template) return;
  $('dyLayoutStatus').textContent = '正在生成最终效果…';
  try {
    const response = await fetch('/api/v1/main-images/preview-config', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({shop_id: dyGen.shop, model_key: $('dyLayoutModel').value || dyLayout.modelKey,
                            template_id: dyLayout.template.id, config: dyLayout.config}),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `生成预览失败（${response.status}）`);
    }
    const exact = $('dyLayoutExact');
    if (exact.src) URL.revokeObjectURL(exact.src);
    exact.src = URL.createObjectURL(await response.blob());
    exact.hidden = false;
    $('dyLayoutExactBtn').hidden = true;
    $('dyLayoutBackEdit').hidden = false;
    $('dyLayoutStatus').textContent = '这是服务端生成的最终效果，与正式生成使用同一套配置';
  } catch (error) {
    $('dyLayoutStatus').textContent = error.message || '生成最终效果失败';
  }
}

function dyLayoutRender() {
  const cfg = dyLayout.config;
  if (!cfg) return;
  hideLayoutExact();
  const {rect} = dyLayoutMetrics();
  const product = cfg.product_image || {};
  const text = cfg.product_name || {};
  const wrap = $('dyLayoutProductWrap');
  const productX = Math.max(0, Math.min(1080, Number(product.center_x) || 540));
  const productY = Math.max(0, Math.min(1080, Number(product.center_y) || 540));
  const maxWidth = Math.max(5, Math.min(100, (Number(product.max_width) || 520) / 10.8));
  const maxHeight = Math.max(5, Math.min(100, (Number(product.max_height) || 650) / 10.8));
  wrap.style.left = `${productX / 10.8}%`;
  wrap.style.top = `${productY / 10.8}%`;
  wrap.style.width = `${maxWidth}%`;
  wrap.style.height = `${maxHeight}%`;
  const image = $('dyLayoutProduct');
  if (image?.naturalWidth && image.naturalHeight) {
    const boxW = rect.width * maxWidth / 100;
    const boxH = rect.height * maxHeight / 100;
    const scale = Math.min(boxW / image.naturalWidth, boxH / image.naturalHeight);
    image.style.width = `${image.naturalWidth * scale}px`;
    image.style.height = `${image.naturalHeight * scale}px`;
  }

  const textX = Math.max(0, Math.min(1080, Number(text.center_x) || 540));
  const textY = Math.max(0, Math.min(1080, Number(text.center_y) || 150));
  const node = $('dyLayoutText');
  node.style.left = `${textX / 10.8}%`;
  node.style.top = `${textY / 10.8}%`;
  node.style.fontSize = `${Math.max(10, (Number(text.font_size) || 52) / 1080 * rect.width)}px`;
  node.style.color = text.color || '#1a1a1a';
  node.style.fontWeight = text.bold === false ? 600 : 700;
  node.style.maxWidth = `${Math.max(10, Math.min(100, (Number(text.max_width) || 520) / 10.8))}%`;
  node.textContent = String(text.text || '').trim() || dyLayoutModelName();
}

async function layoutLoadProduct() {
  const key = $('dyLayoutModel').value || dyLayout.modelKey;
  if (!key) { $('dyLayoutStatus').textContent = '没有可预览的型号'; return; }
  dyLayout.modelKey = key;
  $('dyLayoutStatus').textContent = '正在读取商品图…';
  const image = $('dyLayoutProduct');
  image.onload = () => { $('dyLayoutStatus').textContent = ''; dyLayoutRender(); };
  image.onerror = () => { $('dyLayoutStatus').textContent = '该型号没有可用的 A 级商品图'; };
  image.src = `/api/v1/main-images/product-cutout?shop_id=${encodeURIComponent(dyGen.shop)}&model_key=${encodeURIComponent(key)}&t=${Date.now()}`;
}

async function layoutLoadModels(category) {
  let items = dyGen.items || [];
  if (!items.length) {
    const data = await dyGenRequest(`/items?shop_id=${encodeURIComponent(dyGen.shop)}&limit=1000`);
    items = data.items || [];
  }
  let candidates = items.filter((item) => item.template_key === category);
  if (!candidates.length) candidates = items;
  dyLayout.models = candidates;
  const select = $('dyLayoutModel');
  const previous = dyLayout.modelKey || $('dyPreviewModel').value;
  select.innerHTML = candidates.map((item) => `<option value="${esc(item.model_key)}">${esc(item.model_name)}</option>`).join('');
  if (candidates.some((item) => item.model_key === previous)) select.value = previous;
  dyLayout.modelKey = select.value;
  await layoutLoadProduct();
}

function dyLayoutBindDrag() {
  for (const [id, kind] of [['dyLayoutProductWrap', 'product'], ['dyLayoutText', 'text']]) {
    const node = $(id);
    if (!node || node.dataset.dyDragBound === '1') continue;
    node.dataset.dyDragBound = '1';
    node.style.touchAction = 'none';
    node.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const cfg = dyLayout.config?.[kind === 'product' ? 'product_image' : 'product_name'];
      if (!cfg) return;
      const {rect} = dyLayoutMetrics();
      const startClientX = event.clientX;
      const startClientY = event.clientY;
      const startCenterX = Number(cfg.center_x) || 540;
      const startCenterY = Number(cfg.center_y) || 540;
      let frame = 0;
      const move = (moveEvent) => {
        const dx = (moveEvent.clientX - startClientX) * 1080 / Math.max(1, rect.width);
        const dy = (moveEvent.clientY - startClientY) * 1080 / Math.max(1, rect.height);
        cfg.center_x = Math.max(0, Math.min(1080, Math.round(startCenterX + dx)));
        cfg.center_y = Math.max(0, Math.min(1080, Math.round(startCenterY + dy)));
        if (!frame) {
          frame = requestAnimationFrame(() => { frame = 0; dyLayoutRender(); });
        }
      };
      const finish = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', finish);
        if (frame) cancelAnimationFrame(frame);
        frame = 0;
        dyLayoutRender();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
    });
  }
}

async function openLayoutEditor(templateId = 0) {
  try {
    const id = Number(templateId) || dyGen.currentTemplate?.id || 0;
    if (!id) { setTemplateStatus('请先选择一个模板'); return; }
    const data = await dyGenRequest(`/templates?shop_id=${encodeURIComponent(dyGen.shop)}&category=${encodeURIComponent(dyTemplateCategoryFilter.value)}&all=1`);
    const template = (data.items || []).find((item) => item.id === id);
    if (!template) { setTemplateStatus('模板不存在，请刷新列表'); return; }
    dyTemplateCategoryFilter.value = template.category;
    const cfg = JSON.parse(JSON.stringify(template.config || {}));
    cfg.product_image = {center_x: 540, center_y: 540, max_width: 520, max_height: 650,
                         fit_mode: 'contain', auto_crop_square: false, ...(cfg.product_image || {})};
    cfg.product_name = {center_x: 540, center_y: 150, font_size: 52, color: '#1a1a1a',
                        max_width: 520, align: 'center', auto_shrink: true, bold: true, text: '',
                        ...(cfg.product_name || {})};
    dyLayout.template = template;
    dyLayout.config = cfg;
    dyLayout.base = JSON.parse(JSON.stringify(cfg));
    dyLayout.modelKey = $('dyPreviewModel').value || '';
    $('dyLayoutTitle').textContent = `调整版式 · ${template.name} · 版本 ${template.version}`;
    $('dyLayoutCategory').value = template.category;
    $('dyLayoutTemplate').src = `${template.image_url}?t=${Date.now()}`;
    $('dyLayoutTextInput').value = cfg.product_name.text || '';
    $('dyLayoutScale').value = '100';
    $('dyLayoutScaleValue').textContent = '100%';
    $('dyLayoutFont').value = String(cfg.product_name.font_size || 52);
    $('dyLayoutFontValue').textContent = String(cfg.product_name.font_size || 52);
    $('dyLayoutStatus').textContent = '';
    hideLayoutExact();
    $('dyLayoutModal').hidden = false;
    document.body.classList.add('dy-layout-open');
    await layoutLoadModels(template.category);
    dyLayoutRender();
  } catch (error) {
    setTemplateStatus(error.message || '调整版式加载失败');
  }
}

function closeLayoutEditor() {
  hideLayoutExact();
  $('dyLayoutModal').hidden = true;
  $('dyLayoutProduct').removeAttribute('src');
  $('dyLayoutTemplate').removeAttribute('src');
  document.body.classList.remove('dy-layout-open');
}

async function saveLayoutEditor() {
  const template = dyLayout.template;
  const cfg = dyLayout.config;
  if (!template || !cfg) return;
  $('dyLayoutSave').disabled = true;
  $('dyLayoutStatus').textContent = '正在保存新版本…';
  try {
    const category = $('dyLayoutCategory').value;
    if (category !== template.category) {
      await dyGenRequest(`/templates/${template.id}/category`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({category}),
      });
      template.category = category;
      dyTemplateCategoryFilter.value = category;
    }
    const data = await dyGenRequest(`/templates/${template.id}/config`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({product_image: cfg.product_image, product_name: cfg.product_name}),
    });
    closeLayoutEditor();
    await loadTemplateEditor();
    await loadDyGen(true);
    setTemplateStatus(`版式已保存，新版本 ${data.version}`);
    await previewTemplate(data.id);
  } catch (error) {
    $('dyLayoutStatus').textContent = error.message || '保存失败';
  } finally {
    $('dyLayoutSave').disabled = false;
  }
}

async function loadTemplateEditor() {
  const category = dyTemplateCategoryFilter.value;
  const scope = dyTemplateScopeFilter.value;
  try {
    const data = await dyGenRequest(`/templates?shop_id=${encodeURIComponent(dyGen.shop)}&category=${encodeURIComponent(category)}&all=1`);
    const rows = (data.items || []).filter((x) => x.scope === scope)
      .sort((a, b) => (Number(b.active) - Number(a.active)) || (b.version - a.version));
    $('dyTemplateList').innerHTML = rows.length ? rows.map(templateCard).join('') : '<p class="muted">当前范围还没有模板</p>';
    const active = rows.find((x) => x.active) || rows[0] || null;
    dyGen.currentTemplate = active;
    setTemplateStatus(active
      ? `${active.scope === 'shop' ? '店铺模板' : '通用模板'} · ${active.name} · 版本 ${active.version}`
      : '当前品类没有可用模板，请上传或切换到通用模板');
    const productBox = active?.config?.product_image || {};
    $('dyTemplateSpec').textContent = productBox.max_width
      ? `商品安全区 ${productBox.max_width}×${productBox.max_height}，商品会等比缩放放入该区域；模板装饰不要压进这块区域。`
      : '';
    $('dyTemplateModal').hidden = true;
    $('dyTemplateDownload').href = `/api/v1/main-images/template-sample?category=${encodeURIComponent(category)}&shop_id=${encodeURIComponent(dyGen.shop)}`;
  } catch (error) {
    setTemplateStatus(error.message || '模板加载失败');
  }
}

async function deleteTemplate(templateId, name) {
  if (!window.confirm(`删除模板「${name}」及其所有版本？已生成图片不会删除。`)) return;
  try {
    await dyGenRequest(`/templates/${templateId}/delete`, {method: 'POST'});
    await loadTemplateEditor();
    await loadDyGen(true);
    setTemplateStatus('模板已删除');
  } catch (error) {
    setTemplateStatus(error.message || '删除失败');
  }
}

async function openTemplatePanel() {
  $('dyTemplatePanel').hidden = false;
  $('dyTemplatePanel').scrollIntoView({behavior: 'smooth', block: 'start'});
  await loadTemplateEditor();
}

async function activateTemplate(templateId) {
  try {
    await dyGenRequest(`/templates/${templateId}/activate`, {method: 'POST'});
    await loadTemplateEditor();
    await loadDyGen();
    setTemplateStatus('模板已切换为当前使用');
  } catch (error) {
    setTemplateStatus(error.message || '切换模板失败');
  }
}

async function uploadTemplateFile() {
  const file = $('dyTemplateFile').files?.[0];
  if (!file) { setTemplateStatus('请先选择 1080×1080 PNG 模板'); return; }
  const form = new FormData();
  form.append('shop_id', dyTemplateScopeFilter.value === 'shop' ? dyGen.shop : '');
  form.append('category', dyTemplateCategoryFilter.value);
  form.append('name', DY_TEMPLATE_LABELS[dyTemplateCategoryFilter.value] || dyTemplateCategoryFilter.value);
  form.append('file', file);
  try {
    const response = await fetch('/api/v1/main-images/templates/upload', {
      method: 'POST', credentials: 'same-origin', body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `上传失败（${response.status}）`);
    $('dyTemplateFile').value = '';
    $('dyTemplateFileName').textContent = '未选择文件';
    if (data.scope) dyTemplateScopeFilter.value = data.scope;
    setTemplateStatus(`模板已上传，请调整版式后保存启用，版本 ${data.version}`);
    await loadTemplateEditor();
    await loadDyGen();
    await openLayoutEditor(data.id);
  } catch (error) {
    setTemplateStatus(error.message || '上传失败');
  }
}

async function previewTemplate(templateId = 0) {
  if (templateId) {
    const data = await dyGenRequest(`/templates?shop_id=${encodeURIComponent(dyGen.shop)}&category=${encodeURIComponent(dyTemplateCategoryFilter.value)}&all=1`).catch(() => ({items: []}));
    dyGen.currentTemplate = (data.items || []).find((x) => x.id === templateId) || dyGen.currentTemplate;
  }
  if (!dyGen.currentTemplate) { setTemplateStatus('当前没有模板可预览'); return; }
  const modelKey = $('dyPreviewModel').value || dyGen.items[0]?.model_key;
  if (!modelKey) { setTemplateStatus('没有可预览的型号'); return; }
  const url = `/api/v1/main-images/preview?shop_id=${encodeURIComponent(dyGen.shop)}&model_key=${encodeURIComponent(modelKey)}&template_id=${dyGen.currentTemplate.id}&t=${Date.now()}`;
  $('dyTemplatePreviewImg').src = url;
  $('dyTemplatePreviewInfo').textContent = `${$('dyPreviewModel').selectedOptions[0]?.textContent || modelKey} · ${dyGen.currentTemplate.name}`;
  $('dyTemplateModal').hidden = false;
  setTemplateStatus(`正在预览：${$('dyPreviewModel').selectedOptions[0]?.textContent || modelKey}`);
}

function closeTemplatePreview() {
  $('dyTemplateModal').hidden = true;
  $('dyTemplatePreviewImg').removeAttribute('src');
}

bind('dyGenRefresh', 'click', () => loadDyGen(true));
bind('dyGenAll', 'click', () => startAllMissing());
bind('dyGenRegenSelected', 'click', () => startRun([...dyGen.selected], true));
bind('dyGenClearSelection', 'click', () => { dyGen.selected.clear(); dyGen.selectAllActive = false; renderDyGenItems(); });
bind('dyGenSelectAll', 'click', () => selectAllFiltered());
bind('dyGenMore', 'click', () => loadDyGen(false));
bind('dyGenViewGrid', 'click', () => setDyGenView('grid'));
bind('dyGenViewList', 'click', () => setDyGenView('list'));
bind('dyGenSearch', 'input', () => {
  clearTimeout(dyGen.searchTimer);
  dyGen.selectAllActive = false;
  dyGen.searchTimer = setTimeout(() => loadDyGen(true), 250);
});
dyShopFilter = createDyFilter('dyGlobalShopFilter', '加载店铺…', () => {
  onShopChange().catch((error) => {
    $('dyGenEmpty').textContent = error.message || '店铺切换失败';
    $('dyGenEmpty').hidden = false;
  });
}, {includeAll: false});
dyFilters.category = createDyFilter('dyGenCategoryFilter', '全部品类', () => { dyGen.selectAllActive = false; refreshDyModelOptions(); loadDyGen(true); });
dyFilters.brand = createDyFilter('dyGenBrandFilter', '全部品牌', () => { dyGen.selectAllActive = false; refreshDyModelOptions(); loadDyGen(true); });
dyFilters.model = createDyFilter('dyGenModelFilter', '全部型号', () => { dyGen.selectAllActive = false; loadDyGen(true); });
dyFilters.status = createDyFilter('dyGenStatusFilter', '全部状态', () => { dyGen.selectAllActive = false; loadDyGen(true); });
dyFilters.status.setOptions([{value:'missing',label:'缺失'},{value:'stale',label:'待更新'},{value:'ok',label:'已生成'},{value:'failed',label:'失败'},{value:'no_template',label:'无模板'}]);
dyTemplateScopeFilter = createDyFilter('dyTemplateScopeFilter', '当前店铺', () => { loadTemplateEditor(); }, {includeAll: false});
dyTemplateScopeFilter.setOptions([{value:'shop', label:'当前店铺'}, {value:'global', label:'通用模板'}]);
dyTemplateScopeFilter.value = 'shop';
dyTemplateCategoryFilter = createDyFilter('dyTemplateCategoryFilter', '苹果手机', () => { loadTemplateEditor(); }, {includeAll: false});
dyTemplateCategoryFilter.setOptions([
  {value:'ios_phone', label:'苹果手机'}, {value:'android_phone', label:'安卓手机'},
  {value:'ipad', label:'iPad'}, {value:'android_tablet', label:'安卓平板'},
  {value:'watch', label:'手表'}, {value:'other_digital', label:'其他数码'},
]);
dyTemplateCategoryFilter.value = 'ios_phone';
bind('dyGenList', 'click', (event) => {
  const check = event.target.closest('[data-dy-check]');
  if (check) { toggleSelected(check.dataset.dyCheck); return; }
  const view = event.target.closest('[data-dy-view]');
  if (view) { openMainPreview(view.dataset.dyView); return; }
  const open = event.target.closest('[data-dy-open]');
  if (open) openMainPreview(open.dataset.dyOpen);
});
bind('dyMainPreviewClose', 'click', closeMainPreview);
bind('dyMainPreview', 'click', (event) => { if (event.target.id === 'dyMainPreview') closeMainPreview(); });
bind('dyMainPreviewRegen', 'click', () => {
  const key = $('dyMainPreviewRegen').dataset.key;
  closeMainPreview();
  if (key) startRun([key], true);
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMainPreview(); });

async function loadManual() {
  const shop = dyGen.shop;
  $('dyManualStatus').textContent = '';
  try {
    const mappings = await dyGenRequest(`/mappings?shop_id=${encodeURIComponent(shop)}`);
    const rows = mappings.items || [];
    $('dyManualMappedCount').textContent = rows.length ? `${rows.length} 个` : '';
    $('dyManualList').innerHTML = rows.length ? rows.map((row) => `<div class="dy-manual-row">
      <div class="dy-manual-row-main">
        <b>${esc(row.model_name)}</b>
        <small>${esc(row.brand || '未知品牌')} · ${esc(row.category || '未知品类')}</small>
      </div>
      <button class="btn ghost" type="button" data-dy-unmap="${esc(row.model_key)}">移除</button>
    </div>`).join('') : '<p class="muted">当前店铺还没有手动映射</p>';
    await loadManualOptions();
  } catch (error) {
    $('dyManualStatus').textContent = error.message || '手动映射加载失败';
  }
}

async function loadManualOptions() {
  const q = $('dyManualSearch').value.trim();
  const params = new URLSearchParams({shop_id: dyGen.shop, limit: '200'});
  if (q) params.set('q', q);
  $('dyManualOptionsCount').textContent = '加载中…';
  try {
    const data = await dyGenRequest('/available-models?' + params.toString());
    const items = data.items || [];
    $('dyManualOptionsCount').textContent = items.length ? `${fmtNum(items.length)} 个候选` : '';
    $('dyManualOptions').innerHTML = items.length ? items.map((item) => {
      const colors = (item.colors || []).slice(0, 4).join(' / ');
      return `<div class="dy-manual-option">
        <div class="dy-manual-option-main">
          <b>${esc(item.model_name)}</b>
          <small>${esc(item.brand || '未知品牌')} · ${esc(item.category || '')}${colors ? ` · ${esc(colors)}` : ''}</small>
        </div>
        <button class="btn primary" type="button" data-dy-manual-add="${esc(item.model_key)}" data-brand="${esc(item.brand || '')}">添加</button>
      </div>`;
    }).join('') : '<p class="muted">没有匹配的型号，换个关键词试试</p>';
  } catch (error) {
    $('dyManualOptionsCount').textContent = '';
    $('dyManualOptions').innerHTML = `<p class="muted">${esc(error.message || '候选型号加载失败')}</p>`;
  }
}

async function addManualMapping(modelKey, brand, button) {
  if (!modelKey) return;
  if (button) { button.disabled = true; button.textContent = '添加中…'; }
  try {
    await dyGenRequest('/mappings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({shop_id: dyGen.shop, model_key: modelKey, brand: brand || ''}),
    });
    $('dyManualStatus').textContent = '已添加手动映射';
    await loadManual();
    await loadDyGen();
  } catch (error) {
    $('dyManualStatus').textContent = error.message || '添加失败';
    if (button) { button.disabled = false; button.textContent = '添加'; }
  }
}

async function removeManualMapping(modelKey) {
  try {
    await dyGenRequest(`/mappings/remove?shop_id=${encodeURIComponent(dyGen.shop)}&model_key=${encodeURIComponent(modelKey)}`, {method: 'POST'});
    $('dyManualStatus').textContent = '已移除手动映射';
    await loadManual();
    await loadDyGen();
  } catch (error) {
    $('dyManualStatus').textContent = error.message || '移除失败';
  }
}

bind('dyGenMap', 'click', () => { $('dyManualPanel').hidden = false; $('dyManualPanel').scrollIntoView({behavior: 'smooth', block: 'start'}); loadManual(); });
bind('dyManualClose', 'click', () => { $('dyManualPanel').hidden = true; });
bind('dyManualSearch', 'input', () => { clearTimeout(dyGen.manualTimer); dyGen.manualTimer = setTimeout(loadManualOptions, 250); });
bind('dyManualRefresh', 'click', () => loadManualOptions());
bind('dyManualOptions', 'click', (event) => {
  const button = event.target.closest('[data-dy-manual-add]');
  if (button) addManualMapping(button.dataset.dyManualAdd, button.dataset.brand, button);
});
bind('dyManualList', 'click', (event) => {
  const button = event.target.closest('[data-dy-unmap]');
  if (button) removeManualMapping(button.dataset.dyUnmap);
});
bind('dyGenTemplates', 'click', () => openTemplatePanel());
bind('dyTemplateClose', 'click', () => { $('dyTemplatePanel').hidden = true; });
bind('dyTemplateLoad', 'click', () => loadTemplateEditor());
bind('dyTemplateUpload', 'click', () => uploadTemplateFile());
bind('dyTemplateEdit', 'click', () => openLayoutEditor(dyGen.currentTemplate?.id || 0));
bind('dyLayoutClose', 'click', closeLayoutEditor);
bind('dyLayoutCancel', 'click', closeLayoutEditor);
bind('dyLayoutModal', 'click', (event) => { if (event.target.id === 'dyLayoutModal') closeLayoutEditor(); });
bind('dyLayoutSave', 'click', () => saveLayoutEditor());
bind('dyLayoutExactBtn', 'click', () => showLayoutExact());
bind('dyLayoutBackEdit', 'click', () => { hideLayoutExact(); dyLayoutRender(); });
bind('dyLayoutReload', 'click', () => layoutLoadProduct());
bind('dyLayoutModel', 'change', () => { dyLayout.modelKey = $('dyLayoutModel').value; layoutLoadProduct(); });
bind('dyLayoutScale', 'input', () => {
  const base = dyLayout.base?.product_image || dyLayout.config?.product_image;
  if (!base || !dyLayout.config) return;
  const factor = Number($('dyLayoutScale').value) / 100;
  $('dyLayoutScaleValue').textContent = `${$('dyLayoutScale').value}%`;
  dyLayout.config.product_image.max_width = Math.round(base.max_width * factor);
  dyLayout.config.product_image.max_height = Math.round(base.max_height * factor);
  dyLayoutRender();
});
bind('dyLayoutFont', 'input', () => {
  if (!dyLayout.config) return;
  dyLayout.config.product_name.font_size = Number($('dyLayoutFont').value);
  $('dyLayoutFontValue').textContent = $('dyLayoutFont').value;
  dyLayoutRender();
});
bind('dyLayoutTextInput', 'input', () => {
  if (!dyLayout.config) return;
  dyLayout.config.product_name.text = $('dyLayoutTextInput').value;
  dyLayoutRender();
});
bind('dyLayoutModal', 'click', (event) => {
  const align = event.target.closest('[data-dy-align]');
  if (!align || !dyLayout.config) return;
  if (align.dataset.dyAlign === 'center') {
    dyLayout.config.product_image.center_x = 540;
    dyLayout.config.product_name.center_x = 540;
  } else if (align.dataset.dyAlign === 'top') {
    dyLayout.config.product_name.center_y = 150;
  } else if (align.dataset.dyAlign === 'reset') {
    dyLayout.config = JSON.parse(JSON.stringify(dyLayout.base));
    $('dyLayoutScale').value = '100';
    $('dyLayoutScaleValue').textContent = '100%';
    $('dyLayoutFont').value = String(dyLayout.config.product_name.font_size || 52);
    $('dyLayoutFontValue').textContent = String(dyLayout.config.product_name.font_size || 52);
    $('dyLayoutTextInput').value = dyLayout.config.product_name.text || '';
  }
  dyLayoutRender();
});
if ($('dyLayoutProductWrap')) dyLayoutBindDrag();
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeLayoutEditor(); });
bind('dyTemplateFile', 'change', () => {
  const file = $('dyTemplateFile').files?.[0];
  $('dyTemplateFileName').textContent = file ? file.name : '未选择文件';
});
bind('dyTemplatePreview', 'click', () => previewTemplate());
bind('dyTemplateModalClose', 'click', closeTemplatePreview);
bind('dyTemplateModal', 'click', (event) => { if (event.target.id === 'dyTemplateModal') closeTemplatePreview(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeTemplatePreview(); });
bind('dyTemplateList', 'click', (event) => {
  const activate = event.target.closest('[data-dy-template-activate]');
  if (activate) { activateTemplate(Number(activate.dataset.dyTemplateActivate)); return; }
  const edit = event.target.closest('[data-dy-template-edit]');
  if (edit) { openLayoutEditor(Number(edit.dataset.dyTemplateEdit)); return; }
  const remove = event.target.closest('[data-dy-template-delete]');
  if (remove) { deleteTemplate(Number(remove.dataset.dyTemplateDelete), remove.dataset.dyTemplateName || '模板'); return; }
  const preview = event.target.closest('[data-dy-template-preview]');
  if (preview) previewTemplate(Number(preview.dataset.dyTemplatePreview));
});
root.querySelectorAll('[data-dy-tab]').forEach((button) => button.addEventListener('click', () => setDyTab(button.dataset.dyTab)));

if (new URLSearchParams(location.search).get('preview_dy') === 'template') {
  setTimeout(() => openTemplatePanel(), 1200);
}

updateDouyinSelectionUI();

return { activate: async () => {
  try { await ensureShops(); } catch (error) { /* 页面内已有错误提示 */ }
  if (dyGen.tab === 'generate') await loadDyGen();
  else await loadDouyinGallery(true);
} };
}
