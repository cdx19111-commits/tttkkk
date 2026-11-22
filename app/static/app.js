/* 商品图库 · 共享登录与导航壳 */
const state = { role: 'business', view: '' };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmtNum = (n) => (n ?? 0).toLocaleString('zh-CN');
function bind(id, type, handler, options) { const el = $(id); if (el) el.addEventListener(type, handler, options); return el; }
fetch('/api/v1/me').then((r) => r.ok ? r.json() : null).then((d) => { if (d?.role) state.role = d.role; }).catch(() => {});
let toastTimer;
function toast(msg, ok = true) {
  const el = $('toast'); if (!el) return;
  el.textContent = msg; el.className = 'toast' + (ok ? '' : ' err'); el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}
async function loadInfo() {
  const r = await fetch('/api/v1/images-overview'); if (!r.ok) return;
  const d = await r.json();
  const rows = [['系统名称','商品图库 · 图片资产库'],['版本','V2.1（统一图库）'],['图片总量',`${fmtNum(d.total)} 张`],['品牌 / 型号 / 颜色',`${fmtNum(d.brands)} / ${fmtNum(d.models)} / ${fmtNum(d.colors)}`],['数据来源','飞书云盘「白底图库」+ 拍拍盖亚标准商品数据'],['访问控制','服务端 Session 登录']];
  $('infoList').innerHTML = rows.map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
}
/* ---------- 业务决策台：A/B/C 图片池 ---------- */
const poolDesk = { pool: 'A', offset: 0, limit: 40, total: 0, rows: [], current: null, busy: false, seq: 0 };
const poolRules = {
  A: `<b>A 池｜当前白底图库 · 本轮复核规则草案</b><p>准入目标：准确型号和 Gaia 标准颜色；图片可解码、最短边 ≥600px、完整单机、白底干净，没有明显促销文字或水印。每个精确型号 × 标准颜色只保留一张最佳图。图片来源和模型结论不能单独替代图片核验。</p><p>前批 ZOL 自动入库会检查双模型颜色一致、白底、完整单机、无促销文字和分辨率；但型号模型项只拦截明确错误，水印也不是硬拦截项。这是本轮要重点复查的风险，历史 A 图不代表已经按本草案重新审核。</p>`,
  B: `<b>B 池｜存疑 / 待复核</b><p>尚不能确认可直接使用，也没有证据证明必须废弃。常见原因：型号或版本不清、颜色不确定、白底/完整度/水印需确认。必须记录具体原因；复核后可转 A 或 C。</p>`,
  C: `<b>C 池｜当前不可直接使用</b><p>已确认不符合当前直接使用要求，退出正常白底图库展示。保留图片和处理原因，等待加白边、去水印等处理或后续清理；处理后的新结果回 B 复核。</p><p class="muted">界面显示 C，数据库暂沿用历史代码 X；移入 C 不会物理删除文件。</p>`,
};
const poolLabels = { A: '当前白底图库', B: '存疑 / 待复核', C: '当前不可直接使用' };

async function poolRequest(path, options = {}) {
  const response = await fetch('/api/v1/pool-management' + path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 || response.status === 403) {
    if (state.role !== 'review') location.href = '/login/review?next=' + encodeURIComponent('/#decisions');
  }
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

async function loadPoolDesk(reset = true) {
  const seq = ++poolDesk.seq;
  if (reset) poolDesk.offset = 0;
  $('poolError').hidden = true;
  $('poolRuleCard').innerHTML = `<span class="pool-rule-version">当前展示规则 v0.1 · 业务可讨论修订</span>${poolRules[poolDesk.pool]}`;
  document.querySelectorAll('.pool-tab').forEach(b => b.classList.toggle('active', b.dataset.pool === poolDesk.pool));
  const params = new URLSearchParams({limit: String(poolDesk.limit), offset: String(poolDesk.offset), q: $('poolSearch').value.trim()});
  try {
    const [summary, page] = await Promise.all([
      poolRequest('/summary'), poolRequest(`/${poolDesk.pool}/items?${params.toString()}`),
    ]);
    if (seq !== poolDesk.seq) return;
    ['A', 'B', 'C'].forEach(pool => { $(`poolCount${pool}`).textContent = fmtNum(summary.pools[pool] || 0); });
    poolDesk.total = page.total;
    poolDesk.rows = reset ? page.items : poolDesk.rows.concat(page.items);
    $('poolCount').textContent = `共 ${fmtNum(page.total)} 张`;
    $('poolGrid').innerHTML = poolDesk.rows.map(it => `
      <button class="decision-card" type="button" data-pool-asset="${esc(it.asset_id)}">
        <span class="decision-card-image-wrap"><img src="${esc(it.thumb_url)}" loading="lazy" alt="${esc(it.brand)} ${esc(it.model)}"></span>
        <span class="decision-card-main"><b>${esc(it.brand)} · ${esc(it.model)}</b>
          <small>${esc(it.color || '颜色未标注')} · ${esc(it.width || '?')} × ${esc(it.height || '?')}</small>
          <small>原因：${esc(it.reason || '未记录')}</small>
          <em class="decision-status">${poolDesk.pool} 池${it.legacy ? ' · 历史候选' : ''}</em></span></button>`).join('');
    $('poolEmpty').hidden = poolDesk.rows.length !== 0;
    $('poolMore').hidden = poolDesk.rows.length >= page.total;
  } catch (error) {
    if (seq !== poolDesk.seq) return;
    $('poolError').textContent = error.message || '图片池加载失败'; $('poolError').hidden = false;
  }
}

async function showPoolAsset(id) {
  const item = poolDesk.rows.find(x => x.asset_id === id);
  if (!item) return;
  poolDesk.current = item;
  $('poolDetailTitle').textContent = `${item.brand} · ${item.model}`;
  $('poolDetailSub').textContent = `${item.pool} 池 · ${item.legacy ? '历史候选关系' : '图片 ID ' + item.asset_id} · Gaia ${item.model_id || '未关联'}`;
  $('poolDetailImage').src = item.file_url;
  $('poolLightboxImage').src = item.file_url;
  $('poolOriginalLink').href = item.file_url;
  $('poolDetailBody').innerHTML = `<dl class="pool-detail-list">
    <dt>颜色</dt><dd>${esc(item.color || '未标注')}（来源于当前库字段，需结合图片核对）</dd>
    <dt>尺寸 / 格式</dt><dd>${esc(item.width || '?')} × ${esc(item.height || '?')} · ${esc(item.format || '?')}</dd>
    <dt>SHA-256</dt><dd class="pool-sha">${esc(item.sha256 || '未记录')}</dd>
    <dt>型号状态</dt><dd>${esc(item.match_status || '未记录')}</dd>
    <dt>图片来源</dt><dd>${esc(item.source_channel || '未记录')}</dd>
    <dt>商品页</dt><dd>${item.product_page_url ? `<a href="${esc(item.product_page_url)}" target="_blank" rel="noopener">打开来源页面</a>` : '未记录'}</dd>
    <dt>当前归池原因</dt><dd>${esc(item.reason || '未记录')}</dd>
    ${item.legacy ? `<dt>历史 Gaia 颜色候选</dt><dd><select id="poolLegacyColor"><option value="">选择准确标准色</option>${(item.allowed_colors || []).map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select></dd>` : ''}</dl>`;
  const targets = item.pool === 'A' ? [['B','移入 B 池'],['C','移入 C 池（退出正常图库）']] :
    item.pool === 'B' ? [['A','复核后进入 A 池'],['C','确认不可直接使用，移入 C 池']] : [['B','处理后回 B 池复核']];
  $('poolMoveActions').innerHTML = targets.map(([pool, label]) => `<button class="btn ${pool === 'C' ? 'danger' : (pool === 'A' ? 'primary' : 'ghost')}" data-pool-target="${pool}" type="button">${label}</button>`).join('');
  const history = await poolRequest(`/assets/${encodeURIComponent(id)}/history`).catch(() => ({events:[]}));
  $('poolHistory').innerHTML = `<h3>调整记录</h3>${history.events.length ? history.events.map(e => `<p>${esc(e.created_at)} · ${esc(e.from_status)} → ${esc(e.to_status)} · ${esc(e.reviewer_note)}</p>`).join('') : '<p class="muted">暂无池子调整记录</p>'}`;
  $('poolDrawer').hidden = false; $('poolDrawerMask').hidden = false;
}

function closePoolDetail() { $('poolDrawer').hidden = true; $('poolDrawerMask').hidden = true; poolDesk.current = null; }

async function movePoolAsset(id, targetPool, reason, color = '') {
  if (!reason.trim()) { toast('请填写调整原因', false); return; }
  if (targetPool === 'C' && !window.confirm('移入 C 池后会退出正常白底图库，但原图不会删除。继续吗？')) return;
  const result = await poolRequest(`/assets/${encodeURIComponent(id)}/pool`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pool:targetPool, reason, color}),
  });
  toast(`${result.from_pool || '图片'} → ${targetPool} 池，原因已记录`);
  closePoolDetail(); await loadPoolDesk(true);
}


/* ---------- 视图切换 ---------- */





const VIEW_META = {
  info: ['系统基础信息', '运行状态、数据规模与接口说明'],
  'module-error': ['图库模块加载失败', '请刷新页面或联系管理员'],
  decisions: ['业务决策台', 'A / B / C 图片池与规则'],
};
const featureModules = new Map();
let moduleLoadError = '';

async function loadFeatureModules() {
  let payload;
  try {
    const response = await fetch('/frontend-modules/manifest.json', {cache: 'no-store'});
    if (!response.ok) throw new Error(`模块清单读取失败：HTTP ${response.status}`);
    payload = await response.json();
  } catch (error) {
    moduleLoadError = error.message || '模块清单读取失败';
    return;
  }
  for (const meta of (Array.isArray(payload.modules) ? payload.modules : [])) {
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(meta.id || '') || VIEW_META[meta.id]) continue;
    let nav, view, style;
    try {
      nav = document.createElement('button');
      nav.className = 'nav-item';
      nav.dataset.view = meta.id;
      const icon = document.createElement('span');
      icon.className = 'ico';
      icon.textContent = meta.icon || '◇';
      nav.append(icon, document.createTextNode(` ${meta.nav_label || meta.title}`));
      nav.addEventListener('click', () => switchView(meta.id));
      $('module-nav-items').append(nav);
      view = document.createElement('section');
      view.className = 'view';
      view.id = `view-${meta.id}`;
      view.hidden = true;
      const root = document.createElement('div');
      root.id = `module-root-${meta.id}`;
      view.append(root);
      $('module-view-items').append(view);
      VIEW_META[meta.id] = [meta.title, meta.subtitle];
      style = document.createElement('link');
      style.rel = 'stylesheet';
      style.href = meta.style_url;
      document.head.append(style);
      const htmlResponse = await fetch(meta.html_url);
      if (!htmlResponse.ok) throw new Error(`模块 ${meta.id} 页面加载失败：HTTP ${htmlResponse.status}`);
      root.innerHTML = await htmlResponse.text();
      const feature = await import(meta.script_url);
      if (typeof feature.mount !== 'function') throw new Error(`模块 ${meta.id} 缺少 mount(root) 导出`);
      featureModules.set(meta.id, (await feature.mount(root, {fetch: window.fetch.bind(window)})) || {});
    } catch (error) {
      nav?.remove(); view?.remove(); style?.remove(); delete VIEW_META[meta.id];
      console.error(`[图库] 模块 ${meta.id} 未加载`, error);
      if (meta.id === 'white-gallery') moduleLoadError = error.message || '白底图库模块未能加载';
    }
  }
  if (!featureModules.has('white-gallery')) moduleLoadError ||= '白底图库模块未能加载';
}

function switchView(name) {
  if (name === state.view) return;
  state.view = name;
  if (!VIEW_META[name]) return;
  document.querySelectorAll('.view').forEach((view) => { view.hidden = view.id !== `view-${name}`; });
  document.querySelectorAll('.nav-item[data-view]').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === name);
  });
  if (name === 'module-error') $('moduleLoadError').textContent = moduleLoadError || '图库模块暂时不可用，请刷新页面或联系管理员。';
  const [title, sub] = VIEW_META[name] || ['', ''];
  $('pageTitle').textContent = title;
  $('pageSub').textContent = sub;
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  $('q').style.display = name === 'white-gallery' ? '' : 'none';
  if (name === 'info') loadInfo();
  if (name === 'decisions') loadPoolDesk(true);
  featureModules.get(name)?.activate?.();
  $('sidebar').classList.remove('open');
}


bind('menuBtn', 'click', () => $('sidebar')?.classList.toggle('open'));
document.querySelectorAll('.nav-item[data-view]').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));
/* 业务决策台：池切换、录入复核、归池与原因留痕 */
bind('poolReload', 'click', () => loadPoolDesk(true));
document.querySelectorAll('.pool-tab').forEach(button => button.addEventListener('click', () => {
  poolDesk.pool = button.dataset.pool; loadPoolDesk(true);
}));
let poolSearchTimer;
bind('poolSearch', 'input', () => { clearTimeout(poolSearchTimer); poolSearchTimer = setTimeout(() => loadPoolDesk(true), 220); });
bind('poolMore', 'click', () => { poolDesk.offset += poolDesk.limit; loadPoolDesk(false); });
bind('poolGrid', 'click', e => { const card = e.target.closest('[data-pool-asset]'); if (card) showPoolAsset(card.dataset.poolAsset); });
bind('poolMoveActions', 'click', e => {
  const button = e.target.closest('[data-pool-target]'); if (!button || !poolDesk.current || poolDesk.busy) return;
  const color = $('poolLegacyColor')?.value || '';
  if (button.dataset.poolTarget === 'A' && poolDesk.current.legacy && !color) { toast('先选择与图片一致的 Gaia 标准色', false); return; }
  movePoolAsset(poolDesk.current.asset_id, button.dataset.poolTarget, $('poolMoveReason').value, color).catch(err => toast(err.message || '调整失败', false));
});
bind('poolEntrySubmit', 'click', async () => {
  const id = $('poolEntryId').value.trim(), reason = $('poolEntryReason').value.trim();
  if (!id || !reason) { toast('请填写图片 ID 和录入原因', false); return; }
  const button = $('poolEntrySubmit'); button.disabled = true;
  try {
    await movePoolAsset(id, 'B', reason); $('poolEntryId').value = ''; $('poolEntryReason').value = '';
    poolDesk.pool = 'B'; await loadPoolDesk(true);
  } catch (err) { toast(err.message || '录入失败', false); }
  finally { button.disabled = false; }
});
bind('poolClose', 'click', closePoolDetail);
bind('poolDrawerMask', 'click', closePoolDetail);
bind('poolDetailImage', 'click', () => { $('poolLightbox').hidden = false; });
bind('poolLightboxClose', 'click', () => { $('poolLightbox').hidden = true; });
bind('poolLightbox', 'click', e => { if (e.target.id === 'poolLightbox') $('poolLightbox').hidden = true; });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { $('poolLightbox').hidden = true; closePoolDetail(); }
});

loadFeatureModules().then(() => {
  const aliases = { library: 'white-gallery', douyin: 'douyin-main' };
  const requested = (location.hash || '').replace('#', '');
  const initial = aliases[requested] || requested;
  switchView(VIEW_META[initial] ? initial : (VIEW_META['white-gallery'] ? 'white-gallery' : 'module-error'));
  window.addEventListener('hashchange', () => {
    const raw = (location.hash || '').replace('#', '');
    const view = aliases[raw] || raw;
    if (VIEW_META[view]) switchView(view);
  });
});
