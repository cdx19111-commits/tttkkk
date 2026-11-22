const RULES = {
  A: `<b>A 池｜当前白底图库</b><p>当前业务可检索的白底图。应对应准确型号和 Gaia 标准颜色，图片清晰、主体完整、白底干净，没有明显促销文字或水印；同一精确型号 × 标准颜色只保留一张最佳图。历史 A 图仍可被业务复核，不代表未经复核的素材永远不会调整。</p>`,
  B: `<b>B 池｜存疑 / 待复核</b><p>尚不能确认可直接使用，也没有证据证明必须废弃。常见原因：型号或版本不清、颜色不确定、白底/完整度/水印需确认。必须记录具体原因；复核后可转 A 或 C。</p>`,
  C: `<b>C 池｜已证明当前不可用</b><p>已确认不符合当前直接使用要求，退出 A 池展示。保留图片和处理原因，等待加白边、去水印等处理；处理后的新结果回 B 复核。图片文件不会因为进入 C 池而被物理删除。</p><p class="muted">界面显示 C，数据库兼容沿用历史 X 标记。</p>`,
};
const $ = (id, root) => root.querySelector(`#${id}`);
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmt = (n) => Number(n || 0).toLocaleString('zh-CN');

export async function mount(root) {
  const platform = { pool: 'A', offset: 0, limit: 40, total: 0, rows: [], current: null, seq: 0, suggestSeq: 0, suggestItems: [], gallery: null, poolCounts: null, busy: false };
  const suggestions = $('apSuggestions', root);
  const input = $('apSearch', root);
  const hideLegacyEntries = () => {
    const decisionsNav = document.querySelector('.nav-item[data-view="decisions"]');
    const libraryNav = document.querySelector('.nav-item[data-view="library"]');
    const galleryNav = document.querySelector('.nav-item[data-view="white-gallery"]');
    if (decisionsNav) decisionsNav.style.display = 'none';
    if (libraryNav) libraryNav.style.display = 'none';
    if (galleryNav) galleryNav.style.display = 'none';
    document.querySelector('#view-decisions')?.setAttribute('hidden', '');
    const logo = document.querySelector('.logo-text strong');
    if (logo) logo.textContent = '图片资产管理平台';
    document.title = '图片资产管理平台';
  };
  hideLegacyEntries();

  const libraryRoot = $('apGalleryRoot', root);
  const manifestResponse = await fetch('/frontend-modules/manifest.json', {cache: 'no-store'});
  if (!manifestResponse.ok) throw new Error('图片资产库版本信息加载失败');
  const manifest = await manifestResponse.json();
  const galleryModule = manifest.modules.find((item) => item.id === 'white-gallery');
  if (!galleryModule) throw new Error('图片资产库模块尚未发布');
  let galleryStyle = document.querySelector('link[data-asset-platform-gallery-style]');
  if (!galleryStyle) {
    galleryStyle = document.createElement('link');
    galleryStyle.rel = 'stylesheet';
    galleryStyle.dataset.assetPlatformGalleryStyle = 'true';
    document.head.appendChild(galleryStyle);
  }
  galleryStyle.href = galleryModule.style_url;
  const oldHtml = await fetch(galleryModule.html_url, {cache: 'force-cache'});
  if (!oldHtml.ok) throw new Error(`图片资产库界面加载失败（${oldHtml.status}）`);
  libraryRoot.innerHTML = await oldHtml.text();
  const library = await import(galleryModule.script_url);
  platform.gallery = await library.mount(libraryRoot, {fetch: window.fetch.bind(window)});

  function toast(message, ok = true) {
    const el = $('apToast', root); el.textContent = message; el.hidden = false;
    el.style.background = ok ? '#182230' : '#9b3932';
    clearTimeout(platform.toastTimer); platform.toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
  }
  async function request(path, options = {}) {
    const response = await fetch('/api/v1/pool-management' + path, options);
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 || response.status === 403) throw new Error('登录状态已失效，请重新登录后继续');
    if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
    return data;
  }
  function showPool(pool) {
    platform.pool = pool; platform.offset = 0; platform.rows = [];
    root.querySelectorAll('[data-ap-pool]').forEach((button) => {
      const active = button.dataset.apPool === pool;
      button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active));
    });
    $('apRule', root).innerHTML = `<span class="ap-rule-version">图片用途规则 · 业务可讨论修订</span>${RULES[pool]}`;
    $('apViewA', root).hidden = pool !== 'A'; $('apViewOther', root).hidden = pool === 'A';
    $('apSearch', root).placeholder = pool === 'A' ? '输入品牌、型号或颜色，实时查看候选' : '搜索当前池的品牌、型号、颜色或原因';
    closeSuggestions();
    if (pool === 'A') {
      if (platform.poolCounts?.A !== undefined) $('apACount', root).textContent = `${fmt(platform.poolCounts.A)} 条图片—型号关系`;
      else request('/summary').then((summary) => {
        platform.poolCounts = summary.pools;
        $('apACount', root).textContent = `${fmt(summary.pools.A)} 条图片—型号关系`;
      }).catch(() => {});
      platform.gallery?.activate?.();
    }
    else loadPool(true);
  }
  async function loadPool(reset = true) {
    const seq = ++platform.seq;
    if (reset) { platform.offset = 0; platform.rows = []; }
    $('apError', root).hidden = true;
    const q = input.value.trim();
    const params = new URLSearchParams({limit: String(platform.limit), offset: String(platform.offset)});
    if (q) params.set('q', q);
    try {
      const [summary, page] = await Promise.all([
        request('/summary'), request(`/${platform.pool}/items?${params.toString()}`),
      ]);
      if (seq !== platform.seq) return;
      platform.poolCounts = summary.pools;
      for (const pool of ['A', 'B', 'C']) $(`apCount${pool}`, root).textContent = fmt(summary.pools[pool]);
      platform.total = page.total;
      platform.rows = reset ? page.items : platform.rows.concat(page.items);
      $('apACount', root).textContent = `${fmt(platform.total)} 条图片—型号关系`;
      $('apGrid', root).innerHTML = platform.rows.map((item) => `<button class="ap-card" type="button" data-ap-asset="${esc(item.asset_id)}">
        <img src="${esc(item.thumb_url)}" loading="lazy" alt="${esc(item.brand)} ${esc(item.model)}">
        <span class="ap-card-main"><b>${esc(item.brand)} · ${esc(item.model)}</b><small>${esc(item.color || '颜色未标注')} · ${esc(item.width || '?')} × ${esc(item.height || '?')}</small><small>原因：${esc(item.reason || '未记录')}</small><em>${platform.pool} 池${item.legacy ? ' · 历史候选' : ''}</em></span></button>`).join('');
      $('apEmpty', root).hidden = platform.rows.length !== 0;
      $('apMore', root).hidden = platform.rows.length >= page.total;
    } catch (error) {
      if (seq !== platform.seq) return;
      $('apError', root).textContent = error.message || '图片池加载失败'; $('apError', root).hidden = false;
    }
  }
  async function showAsset(id) {
    const item = platform.rows.find((row) => row.asset_id === id); if (!item) return;
    platform.current = item;
    $('apDetailTitle', root).textContent = `${item.brand} · ${item.model}`;
    $('apDetailSub', root).textContent = `${item.pool} 池 · ${item.legacy ? '历史候选关系' : '图片 ID ' + item.asset_id} · Gaia ${item.model_id || '未关联'}`;
    $('apDetailImage', root).src = item.file_url; $('apLightboxImage', root).src = item.file_url; $('apOriginal', root).href = item.file_url;
    $('apDetailBody', root).innerHTML = `<dl class="ap-detail-list">
      <dt>颜色</dt><dd>${esc(item.color || '未标注')}（来源于当前库字段，需结合图片核对）</dd>
      <dt>尺寸 / 格式</dt><dd>${esc(item.width || '?')} × ${esc(item.height || '?')} · ${esc(item.format || '?')}</dd>
      <dt>SHA-256</dt><dd>${esc(item.sha256 || '未记录')}</dd>
      <dt>型号状态</dt><dd>${esc(item.match_status || '未记录')}</dd>
      <dt>图片来源</dt><dd>${esc(item.source_channel || '未记录')}</dd>
      <dt>商品页</dt><dd>${item.product_page_url ? `<a href="${esc(item.product_page_url)}" target="_blank" rel="noopener">打开来源页面</a>` : '未记录'}</dd>
      <dt>当前归池原因</dt><dd>${esc(item.reason || '未记录')}</dd>
      ${item.legacy ? `<dt>历史 Gaia 颜色候选</dt><dd><select id="apLegacyColor"><option value="">选择准确标准色</option>${(item.allowed_colors || []).map((color) => `<option value="${esc(color)}">${esc(color)}</option>`).join('')}</select></dd>` : ''}</dl>`;
    const targets = item.pool === 'A' ? [['B', '移入 B 池'], ['C', '移入 C 池']] : item.pool === 'B' ? [['A', '复核后进入 A 池'], ['C', '确认不可用，移入 C 池']] : [['B', '处理后回 B 池复核']];
    $('apMoveActions', root).innerHTML = targets.map(([pool, text]) => `<button class="btn ${pool === 'C' ? 'danger' : pool === 'A' ? 'primary' : 'ghost'}" type="button" data-ap-target="${pool}">${text}</button>`).join('');
    $('apMoveReason', root).value = '';
    const history = await request(`/assets/${encodeURIComponent(id)}/history`).catch(() => ({events: []}));
    $('apHistory', root).innerHTML = `<h3>调整记录</h3>${history.events.length ? history.events.map((event) => `<p>${esc(event.created_at)} · ${esc(event.from_status)} → ${esc(event.to_status)} · ${esc(event.reviewer_note)}</p>`).join('') : '<p>暂无池子调整记录</p>'}`;
    $('apDrawer', root).hidden = false; $('apDrawerMask', root).hidden = false;
  }
  function closeAsset() { $('apDrawer', root).hidden = true; $('apDrawerMask', root).hidden = true; platform.current = null; }
  async function moveAsset(target, reason, color = '') {
    if (!platform.current || !reason.trim()) { toast('请填写调整原因', false); return; }
    if (target === 'C' && !window.confirm('移入 C 池后会退出 A 池展示，但原图不会删除。继续吗？')) return;
    const result = await request(`/assets/${encodeURIComponent(platform.current.asset_id)}/pool`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pool: target, reason, color})});
    toast(`${result.from_pool || '图片'} → ${target} 池，原因已记录`); closeAsset(); await loadPool(true);
  }
  function closeSuggestions() { suggestions.hidden = true; input.setAttribute('aria-expanded', 'false'); platform.suggestItems = []; }
  function renderSuggestions(items, query) {
    platform.suggestItems = items;
    if (!items.length) {
      suggestions.innerHTML = `<div class="ap-suggestion"><span>按“${esc(query)}”搜索全部匹配内容</span><small>回车搜索</small></div>`;
    } else {
      suggestions.innerHTML = items.map((item, index) => `<button class="ap-suggestion${index === 0 ? ' active' : ''}" type="button" role="option" aria-selected="${index === 0}" data-ap-suggest="${index}"><span><i class="ap-suggestion-kind">${item.kind}</i>${esc(item.value)}</span><small>${fmt(item.count)} 张</small></button>`).join('');
    }
    suggestions.hidden = false; input.setAttribute('aria-expanded', 'true');
  }
  async function suggest(query) {
    const seq = ++platform.suggestSeq;
    if (!query) { closeSuggestions(); return; }
    if (platform.pool !== 'A') {
      const values = [];
      for (const row of platform.rows) {
        for (const [kind, value, count] of [
          ['品牌', row.brand, 1], ['型号', row.model, 1], ['颜色', row.color, 1],
        ]) {
          if (value && String(value).toLocaleLowerCase().includes(query.toLocaleLowerCase())) values.push({kind, value, count});
        }
      }
      const seen = new Set();
      renderSuggestions(values.filter((item) => {
        const key = `${item.kind}\0${item.value}`; if (seen.has(key)) return false; seen.add(key); return true;
      }).slice(0, 10), query);
      return;
    }
    try {
      const [facetsResponse, treeResponse] = await Promise.all([
        fetch('/api/v1/images-facets?' + new URLSearchParams({q: query})),
        fetch('/api/v1/brand-tree?' + new URLSearchParams({q: query})),
      ]);
      if (!facetsResponse.ok || !treeResponse.ok) throw new Error('suggestions unavailable');
      const [facets, tree] = await Promise.all([facetsResponse.json(), treeResponse.json()]);
      if (seq !== platform.suggestSeq || query !== input.value.trim()) return;
      const items = [];
      for (const brand of (facets.brands || [])) items.push({kind: '品牌', value: brand.value, count: brand.count});
      for (const group of (tree.groups || [])) for (const brand of (group.brands || [])) if (brand.count) items.push({kind: '品牌', value: brand.value, count: brand.count});
      const models = facets.models || {};
      for (const item of [...(models.standard || []), ...(models.directory || [])]) items.push({kind: '型号', value: item.value, count: item.count});
      for (const color of (facets.colors || [])) items.push({kind: '颜色', value: color.value, count: color.count});
      const seen = new Set();
      renderSuggestions(items.filter((item) => {
        const key = `${item.kind}\0${item.value}`; if (seen.has(key)) return false; seen.add(key); return true;
      }).slice(0, 10), query);
    } catch (_) {
      if (seq === platform.suggestSeq) renderSuggestions([], query);
    }
  }
  function applySearch(value) {
    input.value = value; $('apSearchClear', root).hidden = !value; closeSuggestions();
    if (platform.pool === 'A') {
      const globalSearch = document.getElementById('q');
      if (globalSearch) { globalSearch.value = value; globalSearch.dispatchEvent(new Event('input', {bubbles: true})); }
    } else loadPool(true);
  }
  function syncSearchToGallery() {
    const globalSearch = document.getElementById('q');
    if (globalSearch && globalSearch.value !== input.value) { globalSearch.value = input.value; globalSearch.dispatchEvent(new Event('input', {bubbles: true})); }
  }

  root.querySelectorAll('[data-ap-pool]').forEach((button) => button.addEventListener('click', () => showPool(button.dataset.apPool)));
  $('apRefresh', root).addEventListener('click', () => {
    if (platform.pool === 'A') syncSearchToGallery(); else loadPool(true);
  });
  let poolSearchTimer, suggestTimer;
  input.addEventListener('input', () => {
    const value = input.value.trim(); $('apSearchClear', root).hidden = !value;
    clearTimeout(poolSearchTimer); clearTimeout(suggestTimer);
    if (platform.pool === 'A') syncSearchToGallery(); else poolSearchTimer = setTimeout(() => loadPool(true), 220);
    suggestTimer = setTimeout(() => suggest(value), 180);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSuggestions();
    if (event.key === 'Enter') { event.preventDefault(); const first = platform.suggestItems[0]; applySearch(first?.value || input.value.trim()); }
    if (event.key === 'ArrowDown' && !suggestions.hidden) { event.preventDefault(); suggestions.querySelector('.ap-suggestion')?.focus(); }
  });
  suggestions.addEventListener('click', (event) => {
    const button = event.target.closest('[data-ap-suggest]');
    if (button) applySearch(platform.suggestItems[Number(button.dataset.apSuggest)]?.value || input.value.trim());
  });
  document.addEventListener('click', (event) => { if (!event.target.closest('.ap-search-wrap')) closeSuggestions(); });
  $('apSearchClear', root).addEventListener('click', () => applySearch(''));
  $('apMore', root).addEventListener('click', () => { platform.offset += platform.limit; loadPool(false); });
  $('apGrid', root).addEventListener('click', (event) => { const card = event.target.closest('[data-ap-asset]'); if (card) showAsset(card.dataset.apAsset); });
  $('apEntrySubmit', root).addEventListener('click', async (event) => {
    const button = event.currentTarget, id = $('apEntryId', root).value.trim(), reason = $('apEntryReason', root).value.trim();
    if (!id || !reason) { toast('请填写图片 ID 和录入原因', false); return; }
    button.disabled = true;
    try {
      const response = await fetch('/api/v1/pool-management/assets/' + encodeURIComponent(id) + '/pool', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pool:'B', reason})});
      const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || `录入失败（${response.status}）`);
      $('apEntryId', root).value = ''; $('apEntryReason', root).value = ''; showPool('B'); toast('已录入 B 池并记录原因');
    } catch (error) { toast(error.message || '录入失败', false); } finally { button.disabled = false; }
  });
  $('apMoveActions', root).addEventListener('click', (event) => {
    const button = event.target.closest('[data-ap-target]'); if (!button || platform.busy) return;
    const color = $('apLegacyColor', root)?.value || '';
    if (button.dataset.apTarget === 'A' && platform.current?.legacy && !color) { toast('先选择与图片一致的 Gaia 标准色', false); return; }
    platform.busy = true; button.disabled = true;
    moveAsset(button.dataset.apTarget, $('apMoveReason', root).value, color).catch((error) => toast(error.message || '调整失败', false)).finally(() => { platform.busy = false; });
  });
  $('apClose', root).addEventListener('click', closeAsset); $('apDrawerMask', root).addEventListener('click', closeAsset);
  $('apDetailImage', root).addEventListener('click', () => { $('apLightbox', root).hidden = false; });
  $('apLightboxClose', root).addEventListener('click', () => { $('apLightbox', root).hidden = true; });
  $('apLightbox', root).addEventListener('click', (event) => { if (event.target.id === 'apLightbox') event.currentTarget.hidden = true; });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeSuggestions(); closeAsset(); $('apLightbox', root).hidden = true; } });

  // Keep old deep links working while presenting one unified platform entry.
  const normalizeOldRoute = () => {
    if (['', '#decisions', '#white-gallery', '#library'].includes(location.hash)) history.replaceState(null, '', location.pathname + location.search + '#asset-platform');
  };
  normalizeOldRoute(); window.addEventListener('hashchange', normalizeOldRoute);
  return {activate: async () => {
    hideLegacyEntries();
    const summary = await request('/summary').catch(() => null);
    if (summary) {
      platform.poolCounts = summary.pools;
      for (const pool of ['A', 'B', 'C']) $(`apCount${pool}`, root).textContent = fmt(summary.pools[pool]);
    }
    if (platform.pool === 'A') { await platform.gallery?.activate?.(); $('apACount', root).textContent = `${fmt(summary?.pools?.A)} 条图片—型号关系`; }
    else await loadPool(true);
  }};
}
