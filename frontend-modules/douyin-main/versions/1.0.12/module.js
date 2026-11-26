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
const douyinState = { rootId: '', pathIds: [], folderId: '', offset: 0, limit: 48, total: 0, busy: false, seq: 0, summary: null, foldersByParent: new Map() };

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

function renderDouyinFolders() {
  const host = $('douyinFolderFilters'); host.replaceChildren();
  let parentId = douyinState.rootId; const labels = [];
  for (let level = 0; level <= douyinState.pathIds.length; level++) {
    const folders = douyinState.foldersByParent.get(parentId) || [];
    if (!folders.length) break;
    const field = document.createElement('label'); field.className = 'douyin-folder-field';
    const caption = document.createElement('span'); caption.className = 'douyin-folder-level';
    caption.textContent = `第 ${level + 1} 层目录`;
    const select = document.createElement('select'); select.className = 'douyin-folder-select';
    select.setAttribute('aria-label', caption.textContent);
    const all = document.createElement('option'); all.value = ''; all.textContent = level === 0 ? '全部一级目录' : `全部第 ${level + 1} 层目录`; select.appendChild(all);
    for (const folder of folders) {
      const option = document.createElement('option'); option.value = folder.folder_id;
      option.textContent = `${folder.name} · ${fmtNum(folder.image_count)} 张`;
      select.appendChild(option);
    }
    const selected = douyinState.pathIds[level] || ''; select.value = selected;
    select.addEventListener('change', () => {
      const next = douyinState.pathIds.slice(0, level); if (select.value) next.push(select.value);
      douyinState.pathIds = next; douyinState.folderId = next.length ? next[next.length - 1] : douyinState.rootId;
      renderDouyinFolders(); loadDouyinItems(true);
    });
    field.append(caption, select); host.appendChild(field);
    if (!selected) {
      if (level === 0 && douyinState.pathIds.length === 0) {
        const nextField = document.createElement('label'); nextField.className = 'douyin-folder-field';
        const nextCaption = document.createElement('span'); nextCaption.className = 'douyin-folder-level';
        nextCaption.textContent = '第 2 层目录';
        const nextSelect = document.createElement('select'); nextSelect.className = 'douyin-folder-select';
        nextSelect.setAttribute('aria-label', nextCaption.textContent); nextSelect.disabled = true;
        const hint = document.createElement('option'); hint.value = ''; hint.textContent = '先选择第 1 层目录';
        nextSelect.appendChild(hint); nextField.append(nextCaption, nextSelect); host.appendChild(nextField);
      }
      break;
    }
    const chosen = folders.find((x) => x.folder_id === selected);
    if (!chosen) { douyinState.pathIds = douyinState.pathIds.slice(0, level); break; }
    labels.push(chosen.name); parentId = chosen.folder_id;
  }
  douyinState.folderId = douyinState.pathIds.length ? douyinState.pathIds[douyinState.pathIds.length - 1] : douyinState.rootId;
  $('douyinBreadcrumb').textContent = ['抖音主图', ...labels].join(' / ');
}

function douyinCard(item) {
  const card = document.createElement('article'); card.className = 'card';
  const dim = item.width && item.height ? `${item.width}×${item.height}` : '图片';
  card.innerHTML = `<div class="thumb"><img loading="lazy" src="${esc(item.image_url)}" alt="${esc(item.name)}"></div><div class="cap"><div class="model" title="${esc(item.name)}">${esc(item.name)}</div><div class="sub"><span class="chip">抖音主图</span></div><div class="meta">${dim} · ${esc(item.extension.toUpperCase())} · ${fmtSize(item.size_bytes)}</div></div>`;
  card.setAttribute('role', 'button'); card.tabIndex = 0; card.setAttribute('aria-label', `预览图片：${item.name}`);
  const preview = () => openDouyinPreview(item);
  card.addEventListener('click', preview); card.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); preview(); } }); return card;
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
  if (douyinState.busy && !reset) return;
  if (reset) { douyinState.offset = 0; $('douyinGrid').replaceChildren(); }
  const seq = ++douyinState.seq; douyinState.busy = true; $('douyinMore').disabled = true;
  try {
    const p = new URLSearchParams({ folder_id: douyinState.folderId || douyinState.rootId, limit: douyinState.limit, offset: douyinState.offset });
    const q = $('douyinSearch').value.trim(); if (q) p.set('q', q);
    const data = await douyinRequest('/items?' + p.toString()); if (seq !== douyinState.seq) return;
    $('douyinCount').textContent = `共 ${fmtNum(data.total)} 张`;
    for (const item of data.items) $('douyinGrid').appendChild(douyinCard(item));
    douyinState.offset += data.items.length; $('douyinMore').hidden = douyinState.offset >= data.total; $('douyinEmpty').hidden = data.total > 0;
  } catch (e) { $('douyinEmpty').textContent = e.message; $('douyinEmpty').hidden = false; }
  finally { if (seq === douyinState.seq) { douyinState.busy = false; $('douyinMore').disabled = false; } }
}

async function loadDouyinGallery(reset = false) {
  try {
    if (!douyinState.summary || !douyinState.foldersByParent.size) {
      const [summary, tree] = await Promise.all([douyinRequest('/summary'), douyinRequest('/folder-tree')]);
      douyinState.summary = summary; douyinState.rootId = summary.root_folder_id;
      douyinState.foldersByParent = new Map();
      for (const folder of tree.items) {
        const siblings = douyinState.foldersByParent.get(folder.parent_id) || [];
        siblings.push(folder); douyinState.foldersByParent.set(folder.parent_id, siblings);
      }
      $('douyinTotal').textContent = fmtNum(summary.images);
      $('douyinFoldersTotal').textContent = fmtNum(Math.max(0, summary.folders - 1));
      $('douyinSize').textContent = douyinSize(summary.size_bytes);
    }
    if (reset) { douyinState.pathIds = []; douyinState.folderId = douyinState.rootId; renderDouyinFolders(); }
    await loadDouyinItems(reset);
  } catch (e) { $('douyinEmpty').textContent = e.message; $('douyinEmpty').hidden = false; }
}

let douyinSearchTimer;
bind('douyinSearch', 'input', () => { clearTimeout(douyinSearchTimer); douyinSearchTimer = setTimeout(() => loadDouyinItems(true), 250); });
bind('douyinMore', 'click', () => loadDouyinItems(false));
bind('douyinReload', 'click', () => { douyinState.summary = null; loadDouyinGallery(true); });

/* ---------- 主图生成 ---------- */
const PAGE_SIZE = 60;
const dyGen = { shop: '', items: [], templates: [], shops: [], facets: null, currentTemplate: null,
  busy: false, tab: 'library', total: 0, offset: 0, selected: new Set(), view: 'grid',
  runId: '', runBusy: false, runTimer: null, resumeChecked: false };
const dyFilters = {};

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

function setDyTab(tab) {
  dyGen.tab = tab;
  root.querySelectorAll('[data-dy-tab]').forEach((button) => {
    const active = button.dataset.dyTab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $('douyinLibraryPane').hidden = tab !== 'library';
  $('douyinGeneratePane').hidden = tab !== 'generate';
  if (tab === 'generate') loadDyGen();
}

function createDyFilter(hostId, allLabel, onChange) {
  const host = $(hostId);
  const button = host.querySelector('.dy-filter-button');
  const valueNode = host.querySelector('.dy-filter-value');
  const menu = host.querySelector('.dy-filter-menu');
  let items = [];
  let value = '';
  const labelOf = (item) => item.count === undefined ? item.label : `${item.label}（${fmtNum(item.count)}）`;
  const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
  const render = () => {
    menu.innerHTML = [{value: '', label: allLabel}].concat(items).map((item) =>
      `<button type="button" role="option" data-value="${esc(item.value)}" aria-selected="${item.value === value}">${esc(labelOf(item))}</button>`).join('');
  };
  const setValue = (next) => {
    value = next || '';
    const found = items.find((item) => item.value === value);
    valueNode.textContent = found ? labelOf(found) : allLabel;
    button.title = valueNode.textContent;
  };
  button.addEventListener('click', (event) => {
    event.stopPropagation();
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
  document.addEventListener('click', (event) => { if (!host.contains(event.target)) close(); });
  const api = {
    get value() { return value; },
    set value(next) { setValue(next); },
    setOptions(next) {
      const current = value;
      items = next;
      if (current && !items.some((item) => item.value === current)) value = '';
      render();
      setValue(value);
    },
    close,
  };
  setValue('');
  return api;
}

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

async function loadDyGen(reset = true) {
  $('dyGenEmpty').hidden = true;
  try {
    if (!dyGen.shops.length) {
      const shops = await dyGenRequest('/shops');
      dyGen.shops = shops.items || [];
      dyFilters.shop.setOptions(dyGen.shops.map((x) => ({value: x.shop_id, label: x.name, count: x.images})));
      const preferred = dyGen.shops.find((x) => x.shop_id === '129675602') || dyGen.shops[0];
      if (!dyFilters.shop.value) dyFilters.shop.value = preferred?.shop_id || '';
    }
    const shopId = dyFilters.shop.value || dyGen.shops[0]?.shop_id || '';
    dyGen.shop = shopId;
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
  $('dyGenAll').disabled = dyGen.runBusy;
  $('dyGenSelectionInfo').textContent = dyGen.runBusy
    ? `生成任务进行中，已选 ${count} 个；完成后可继续操作`
    : `点图片预览，点右下角勾选框多选；已选 ${count} 个`;
}

function toggleSelected(modelKey) {
  if (dyGen.selected.has(modelKey)) dyGen.selected.delete(modelKey);
  else dyGen.selected.add(modelKey);
  renderDyGenItems();
}

async function selectAllFiltered() {
  if (dyGen.runBusy) return;
  try {
    const data = await dyGenRequest('/items?' + dyGenQuery(0, 1000).toString());
    for (const item of data.items || []) dyGen.selected.add(item.model_key);
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
  return `<article class="dy-template-card ${active ? 'active' : ''}" data-dy-template-card="${template.id}">
    <img src="${esc(template.image_url)}?t=${Date.now()}" alt="${esc(template.name)}">
    <div class="dy-template-card-body">
      <b>${esc(template.name)}</b>
      <small>${template.scope === 'shop' ? '店铺模板' : '通用模板'} · 版本 ${template.version}${active ? ' · 当前使用' : ''}</small>
      <div class="dy-template-card-actions">
        <button class="btn ${active ? 'ghost' : 'primary'}" type="button" data-dy-template-activate="${template.id}" ${active ? 'disabled' : ''}>${active ? '当前使用' : '设为当前'}</button>
        <button class="btn ghost" type="button" data-dy-template-preview="${template.id}">预览</button>
      </div>
    </div>
  </article>`;
}

function setTemplateStatus(text) {
  $('dyTemplateStatus').textContent = text;
}

async function loadTemplateEditor() {
  const category = $('dyTemplateCategory').value;
  const scope = $('dyTemplateScope').value;
  try {
    const data = await dyGenRequest(`/templates?shop_id=${encodeURIComponent(dyGen.shop)}&category=${encodeURIComponent(category)}&all=1`);
    const rows = (data.items || []).filter((x) => x.scope === scope);
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
  form.append('shop_id', $('dyTemplateScope').value === 'shop' ? dyGen.shop : '');
  form.append('category', $('dyTemplateCategory').value);
  form.append('name', DY_TEMPLATE_LABELS[$('dyTemplateCategory').value] || $('dyTemplateCategory').value);
  form.append('file', file);
  try {
    const response = await fetch('/api/v1/main-images/templates/upload', {
      method: 'POST', credentials: 'same-origin', body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `上传失败（${response.status}）`);
    $('dyTemplateFile').value = '';
    $('dyTemplateFileName').textContent = '未选择文件';
    setTemplateStatus(`模板已上传并设为当前，版本 ${data.version}`);
    await loadTemplateEditor();
    await loadDyGen();
    await previewTemplate(data.id);
  } catch (error) {
    setTemplateStatus(error.message || '上传失败');
  }
}

async function previewTemplate(templateId = 0) {
  if (templateId) {
    const data = await dyGenRequest(`/templates?shop_id=${encodeURIComponent(dyGen.shop)}&category=${encodeURIComponent($('dyTemplateCategory').value)}&all=1`).catch(() => ({items: []}));
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
bind('dyGenClearSelection', 'click', () => { dyGen.selected.clear(); renderDyGenItems(); });
bind('dyGenSelectAll', 'click', () => selectAllFiltered());
bind('dyGenMore', 'click', () => loadDyGen(false));
bind('dyGenViewGrid', 'click', () => setDyGenView('grid'));
bind('dyGenViewList', 'click', () => setDyGenView('list'));
bind('dyGenSearch', 'input', () => {
  clearTimeout(dyGen.searchTimer);
  dyGen.searchTimer = setTimeout(() => loadDyGen(true), 250);
});
dyFilters.shop = createDyFilter('dyGenShopFilter', '选择店铺', () => {
  dyGen.facets = null;
  dyGen.selected.clear();
  dyGen.resumeChecked = false;
  dyFilters.category.value = ''; dyFilters.brand.value = ''; dyFilters.model.value = '';
  updateSelectionUI();
  loadDyGen(true);
});
dyFilters.category = createDyFilter('dyGenCategoryFilter', '全部品类', () => { refreshDyModelOptions(); loadDyGen(true); });
dyFilters.brand = createDyFilter('dyGenBrandFilter', '全部品牌', () => { refreshDyModelOptions(); loadDyGen(true); });
dyFilters.model = createDyFilter('dyGenModelFilter', '全部型号', () => loadDyGen(true));
dyFilters.status = createDyFilter('dyGenStatusFilter', '全部状态', () => loadDyGen(true));
dyFilters.status.setOptions([{value:'missing',label:'缺失'},{value:'stale',label:'待更新'},{value:'ok',label:'已生成'},{value:'failed',label:'失败'},{value:'no_template',label:'无模板'}]);
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
bind('dyTemplateScope', 'change', () => loadTemplateEditor());
bind('dyTemplateCategory', 'change', () => loadTemplateEditor());
bind('dyTemplateUpload', 'click', () => uploadTemplateFile());
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
  const preview = event.target.closest('[data-dy-template-preview]');
  if (preview) previewTemplate(Number(preview.dataset.dyTemplatePreview));
});
root.querySelectorAll('[data-dy-tab]').forEach((button) => button.addEventListener('click', () => setDyTab(button.dataset.dyTab)));

if (new URLSearchParams(location.search).get('preview_dy') === 'template') {
  setTimeout(() => openTemplatePanel(), 1200);
}

return { activate: () => dyGen.tab === 'generate' ? loadDyGen() : loadDouyinGallery(true) };
}
