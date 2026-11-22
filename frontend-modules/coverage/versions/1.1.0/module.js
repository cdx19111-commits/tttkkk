let root;
let coverageOffset = 0;
let coverageTotal = 0;
let coverageSequence = 0;
let coverageScope = 'post2021';
let coverageView = 'combinations';
let coverageSearchTimer;
let uploadTarget = null;
let uploadContext = null;
const $ = (id) => root.querySelector(`#${id}`);
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmtNum = (n) => (n ?? 0).toLocaleString('zh-CN');

async function loadCoverage(reset = true) {
  const sequence = ++coverageSequence;
  if (reset) coverageOffset = 0;
  const scope = coverageScope;
  const view = coverageView;
  const q = ($('coverageSearch')?.value || '').trim();
  const params = new URLSearchParams({scope, view, offset: String(coverageOffset), limit: '100'});
  if (q) params.set('q', q);
  try {
    const response = await fetch('/api/v1/coverage-monitor?' + params.toString());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (sequence !== coverageSequence) return;
    coverageTotal = data.items_total;
    const s = data.summary;
    const modelRate = s.models_total ? (s.models_with_any_A / s.models_total * 100) : 0;
    const colorRate = s.combinations_total ? (s.combinations_covered / s.combinations_total * 100) : 0;
    const pct = (value) => `${Math.max(0, Math.min(100, Number(value) || 0)).toFixed(1)}%`;
    const modelCoveredWidth = s.models_total ? (s.models_with_any_A / s.models_total * 100) : 0;
    const modelMissingWidth = s.models_total ? (s.models_no_A / s.models_total * 100) : 0;
    const colorCoveredWidth = s.combinations_total ? (s.combinations_covered / s.combinations_total * 100) : 0;
    const colorMissingWidth = s.combinations_total ? (s.combinations_missing / s.combinations_total * 100) : 0;

    $('coverageTitle').textContent = scope === 'post2021' ? '2021 年及以后型号缺口' : '盖亚全量型号缺口';
    root.querySelectorAll('[data-coverage-scope]').forEach((button) => {
      const active = button.dataset.coverageScope === scope;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    root.querySelectorAll('[data-coverage-view]').forEach((button) => {
      const active = button.dataset.coverageView === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });

    $('coverageCards').innerHTML = `
      <article class="coverage-metric-card coverage-model-metric">
        <div class="coverage-metric-top"><span class="coverage-metric-label">型号覆盖</span><span class="coverage-rate">${pct(modelRate)} 已有 A 级图</span></div>
        <div class="coverage-metric-main"><strong>${fmtNum(s.models_no_A)}</strong><span>个型号完全缺少 A 级白底图</span></div>
        <div class="coverage-meter" role="img" aria-label="${fmtNum(s.models_with_any_A)} 个型号已有 A 级图，${fmtNum(s.models_no_A)} 个型号缺少 A 级图">
          <span class="coverage-meter-covered" style="width:${modelCoveredWidth}%"></span><span class="coverage-meter-missing" style="width:${modelMissingWidth}%"></span>
        </div>
        <div class="coverage-meter-legend"><span><i class="legend-dot covered"></i>已有 A 级图 <b>${fmtNum(s.models_with_any_A)}</b></span><span><i class="legend-dot missing"></i>完全缺图 <b>${fmtNum(s.models_no_A)}</b></span></div>
        <p class="coverage-metric-foot">目标 ${fmtNum(s.models_total)} 个型号 · 型号覆盖按至少一张 A 级白底图计算</p>
      </article>
      <article class="coverage-metric-card coverage-color-metric">
        <div class="coverage-metric-top"><span class="coverage-metric-label">型号 × 颜色覆盖</span><span class="coverage-rate">${pct(colorRate)} 已覆盖</span></div>
        <div class="coverage-metric-main"><strong>${fmtNum(s.combinations_missing)}</strong><span>个颜色组合仍缺图</span></div>
        <div class="coverage-meter" role="img" aria-label="${fmtNum(s.combinations_covered)} 个组合已覆盖，${fmtNum(s.combinations_missing)} 个组合仍缺图">
          <span class="coverage-meter-covered" style="width:${colorCoveredWidth}%"></span><span class="coverage-meter-missing" style="width:${colorMissingWidth}%"></span>
        </div>
        <div class="coverage-meter-legend"><span><i class="legend-dot covered"></i>已覆盖 <b>${fmtNum(s.combinations_covered)}</b></span><span><i class="legend-dot missing"></i>仍缺图 <b>${fmtNum(s.combinations_missing)}</b></span></div>
        <p class="coverage-metric-foot">目标 ${fmtNum(s.combinations_total)} 个型号 × 颜色组合 · 重复图不重复计数</p>
      </article>`;

    const topBrands = (s.by_brand || []).filter(x => x.missing_combinations || x.models_no_A).slice(0, 8);
    const maxBrandGap = Math.max(1, ...topBrands.map(x => Number(x.missing_combinations) || 0));
    $('coverageBrandGaps').innerHTML = topBrands.length ? topBrands.map((item) => {
      const width = Math.max(0, Math.min(100, (Number(item.missing_combinations) || 0) / maxBrandGap * 100));
      return `<div class="coverage-brand-row"><div class="coverage-brand-name" title="${esc(item.brand || '未标品牌')}">${esc(item.brand || '未标品牌')}</div><div class="coverage-brand-track" role="img" aria-label="${esc(item.brand || '未标品牌')} 缺 ${fmtNum(item.missing_combinations)} 个颜色组合"><span style="width:${width}%"></span></div><div class="coverage-brand-value"><b>${fmtNum(item.missing_combinations)}</b><small>组合 · ${fmtNum(item.models_no_A)} 个全缺型号</small></div></div>`;
    }).join('') : '<p class="muted">当前范围没有品牌缺口</p>';

    const poolsResponse = await fetch('/api/v1/asset-pools');
    if (poolsResponse.ok && sequence === coverageSequence) {
      const pools = await poolsResponse.json();
      const labels = {
        white_assets: ['白底图片总数', '明确标记为白底用途的手机图片，包含不同质量等级'],
        white_A_visible: ['可用 A 级白底图', '已关联标准型号和颜色、可用于覆盖统计'],
        feishu_source: ['飞书来源', '按来源统计，可与白底图片池重叠'],
        douyin_marked_in_phone_base: ['手机底表中被隔离的主图标记', '不参与白底覆盖'],
        soft_hidden: ['软隐藏 / 已停用', '保留记录，但不进入正常业务覆盖'],
        review_queue: ['待复核', 'B 级或型号、颜色关联未确认'],
        purpose_quarantine: ['用途待确认', '不能确定为白底或抖音用途的记录'],
        douyin_main_separate: ['抖音主图（独立图库）', '独立存放和统计，不参与白底图库覆盖'],
      };
      $('coveragePools').innerHTML = pools.pools.map((pool) => {
        const [label, description] = labels[pool.id] || [pool.id, pool.description || ''];
        return `<div class="pool-chip"><b>${esc(label)} <span>${fmtNum(pool.asset_count)} 张</span></b><small>${esc(description)}</small></div>`;
      }).join('');
    }
    $('coverageSnapshot').textContent = `Gaia 池更新时间：${data.gaia_snapshot_at || '未知'} · 图库数据实时读取 · ${fmtNum(s.launch_year_unknown_all)} 个型号上市年份未知`;

    const modelView = view === 'models';
    $('coverageHead').innerHTML = `<tr>${(modelView ? ['Gaia ID','品牌','型号','上市年','A 级图数','型号状态','缺少颜色'] : ['Gaia ID','品牌','型号','上市年','缺失颜色','现有 A 级图数','缺口类型','操作']).map(x => `<th>${x}</th>`).join('')}</tr>`;
    $('coverageRows').innerHTML = data.items.map((item) => modelView
      ? `<tr><td>${esc(item.model_id)}</td><td>${esc(item.brand)}</td><td>${esc(item.model)}</td><td>${esc(item.launch_year || '未知')}</td><td>${fmtNum(item.image_count)}</td><td>${esc(item.model_status)}</td><td>${esc((item.missing_colors || []).join('、') || '—')}</td></tr>`
      : `<tr><td>${esc(item.model_id)}</td><td>${esc(item.brand)}</td><td>${esc(item.model)}</td><td>${esc(item.launch_year || '未知')}</td><td>${esc(item.color || '—')}</td><td>${fmtNum(item.image_count)}</td><td>${esc(item.gap_type)}</td><td>${item.color ? `<button type="button" class="coverage-upload-trigger" data-model-id="${esc(item.model_id)}" data-brand="${esc(item.brand)}" data-model="${esc(item.model)}" data-color="${esc(item.color)}">上传图片</button>` : '—'}</td></tr>`).join('');
    $('coverageEmpty').hidden = data.items.length !== 0;
    $('coverageError').hidden = true;
    const pages = Math.max(1, Math.ceil(coverageTotal / 100));
    $('coveragePageInfo').textContent = `共 ${fmtNum(coverageTotal)} 条 · 第 ${coverageTotal ? Math.floor(coverageOffset / 100) + 1 : 1} / ${pages} 页`;
    $('coveragePrev').disabled = coverageOffset <= 0;
    $('coverageNext').disabled = coverageOffset + 100 >= coverageTotal;
  } catch (error) {
    if (sequence !== coverageSequence) return;
    $('coverageRows').innerHTML = '';
    $('coverageError').textContent = '覆盖数据加载失败：请确认 Gaia 快照可用，或检查图库数据库配置。';
    $('coverageError').hidden = false;
    $('coverageEmpty').hidden = true;
  }
}

async function openUploadDialog(button) {
  uploadTarget = {model_id: button.dataset.modelId, brand: button.dataset.brand,
    model: button.dataset.model, color: button.dataset.color};
  uploadContext = null;
  $('coverageUploadFile').value = '';
  $('coverageUploadFile').disabled = false;
  $('coverageUploadPreview').hidden = true;
  $('coverageUploadStatus').textContent = '';
  $('coverageUploadSubmit').disabled = true;
  $('coverageUploadContext').textContent = '正在读取 Gaia 型号、颜色和参数…';
  $('coverageUploadDialog').showModal();
  try {
    const params = new URLSearchParams({model_id: uploadTarget.model_id, color: uploadTarget.color});
    const response = await fetch('/api/v1/coverage-upload-context?' + params.toString());
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    uploadContext = data;
    const paramRows = (data.model_params || []).map(x => `<li>${esc(x.name)}：${esc(x.value)}</li>`).join('');
    $('coverageUploadContext').innerHTML = `<p><b>${esc(data.brand)} ${esc(data.model)}</b> · Gaia ID ${esc(data.model_id)} · ${esc(data.launch_year || '上市年未知')} · ${esc(data.color)}</p><p>型号参数（${(data.model_params || []).length} 项）</p>${paramRows ? `<ul>${paramRows}</ul>` : '<p class="coverage-upload-warning">当前没有可用型号参数，暂不能提交。</p>'}<p class="coverage-upload-warning">上传后进入业务决策台待审核；确认型号、颜色和白底质量后才会计入覆盖。</p>`;
    $('coverageUploadSubmit').disabled = !data.can_upload;
  } catch (error) {
    $('coverageUploadContext').textContent = `无法确认缺口数据：${error.message}`;
    $('coverageUploadStatus').textContent = '未接收图片。请刷新页面或稍后重试。';
  }
}

function resetUploadDialog() {
  if ($('coverageUploadDialog').open) $('coverageUploadDialog').close();
  uploadTarget = null; uploadContext = null;
}

export function mount(rootElement) {
  root = rootElement;
  root.addEventListener('click', (event) => {
    const button = event.target.closest('.coverage-upload-trigger');
    if (button) openUploadDialog(button);
  });
  $('coverageUploadCancel').addEventListener('click', resetUploadDialog);
  $('coverageUploadFile').addEventListener('change', () => {
    const file = $('coverageUploadFile').files?.[0];
    const preview = $('coverageUploadPreview');
    if (!file) { preview.hidden = true; $('coverageUploadSubmit').disabled = !uploadContext?.can_upload; return; }
    const allowed = ['image/jpeg', 'image/png', 'image/webp'].includes(file.type);
    const validSize = file.size > 0 && file.size <= 20 * 1024 * 1024;
    $('coverageUploadStatus').textContent = !allowed ? '格式不支持，请选择 JPEG、PNG 或 WebP。' : !validSize ? '文件为空或超过 20 MB。' : '';
    if (allowed && validSize) { preview.src = URL.createObjectURL(file); preview.hidden = false; }
    else preview.hidden = true;
    $('coverageUploadSubmit').disabled = !(allowed && validSize && uploadContext?.can_upload);
  });
  $('coverageUploadSubmit').addEventListener('click', async () => {
    const file = $('coverageUploadFile').files?.[0];
    if (!file || !uploadTarget || !uploadContext?.can_upload) return;
    const button = $('coverageUploadSubmit'); button.disabled = true;
    $('coverageUploadStatus').textContent = '正在上传并校验图片…';
    try {
      const params = new URLSearchParams({model_id: uploadTarget.model_id, color: uploadTarget.color, filename: file.name});
      const response = await fetch('/api/v1/business-review/uploads?' + params.toString(), {
        method: 'POST', headers: {'Content-Type': file.type}, body: file
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      $('coverageUploadStatus').textContent = `${data.message}（审核单 ${data.item_id}）`;
      $('coverageUploadFile').disabled = true;
      loadCoverage(true);
    } catch (error) {
      $('coverageUploadStatus').textContent = `上传失败：${error.message}`;
      button.disabled = false;
    }
  });
  root.querySelectorAll('[data-coverage-scope]').forEach((button) => button.addEventListener('click', () => {
    if (coverageScope === button.dataset.coverageScope) return;
    coverageScope = button.dataset.coverageScope;
    loadCoverage(true);
  }));
  root.querySelectorAll('[data-coverage-view]').forEach((button) => button.addEventListener('click', () => {
    if (coverageView === button.dataset.coverageView) return;
    coverageView = button.dataset.coverageView;
    loadCoverage(true);
  }));
  $('coverageSearch').addEventListener('input', () => {
    clearTimeout(coverageSearchTimer);
    coverageSearchTimer = setTimeout(() => loadCoverage(true), 220);
  });
  $('coveragePrev').addEventListener('click', () => {
    coverageOffset = Math.max(0, coverageOffset - 100);
    loadCoverage(false);
  });
  $('coverageNext').addEventListener('click', () => {
    if (coverageOffset + 100 < coverageTotal) coverageOffset += 100;
    loadCoverage(false);
  });
  return {activate: () => loadCoverage(true)};
}
