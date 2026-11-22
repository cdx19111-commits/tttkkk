export async function mount(root) {
const $ = (id) => root.querySelector('#' + id);
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function fmtSize(b) { if (!b) return '—'; if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'; return Math.round(b / 1024) + ' KB'; }
function fmtNum(n) { return (n ?? 0).toLocaleString('zh-CN'); }
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

return { activate: () => loadDouyinGallery(true) };
}
