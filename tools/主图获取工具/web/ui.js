"use strict";

const get = id => document.getElementById(id);
const node = (tag, text, cls) => {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  if (cls) element.className = cls;
  return element;
};
const ui = {batch: "", offset: 0, mode: "excel", file: null, rowsKey: "", request: 0};
const pageSize = 24;
const statusList = ["排队中", "处理中", "已完成", "部分缺图", "缺图", "待复核", "待确认型号", "待确认颜色", "缺图库", "采集失败"];

function notice(text) {
  get("message").textContent = text;
  get("message").hidden = !text;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "操作失败");
  return payload;
}

function post(path, body = {}) {
  return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
}

async function action(command, body) {
  try {
    await post("/api/batch/" + ui.batch + "/" + command, body);
    ui.rowsKey = "";
    notice(command === "link" ? "已绑定 ZOL 链接，型号已加入队列。" :
      command === "pause" ? "当前型号处理结束后暂停。" : "");
    await refresh();
  } catch (error) {
    notice(error.message);
  }
}

function switchTab(mode) {
  ui.mode = mode;
  for (const type of ["excel", "paste"]) {
    get(type + "-tab").setAttribute("aria-pressed", String(type === mode));
    get(type + "-tab").classList.toggle("current", type === mode);
  }
  get("drop").hidden = mode !== "excel";
  get("paste").hidden = mode !== "paste";
}

const setFile = file => {
  ui.file = file;
  get("file-name").textContent = file ? file.name : "选择 Excel 文件";
};

get("excel-tab").onclick = () => switchTab("excel");
get("paste-tab").onclick = () => switchTab("paste");
get("excel").onchange = event => setFile(event.target.files[0]);
get("drop").ondragover = event => { event.preventDefault(); get("drop").classList.add("drag"); };
get("drop").ondragleave = () => get("drop").classList.remove("drag");
get("drop").ondrop = event => {
  event.preventDefault();
  get("drop").classList.remove("drag");
  setFile(event.dataTransfer.files[0]);
};

get("import-form").onsubmit = async event => {
  event.preventDefault();
  get("import-button").disabled = true;
  try {
    const query = new URLSearchParams({sample: get("sample").value});
    let imported;
    if (ui.mode === "excel") {
      if (!ui.file) throw new Error("请选择 Excel 文件。");
      query.set("name", ui.file.name.replace(/\.xlsx$/i, ""));
      imported = await api("/api/import?" + query, {method: "POST", body: ui.file,
        headers: {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}});
    } else {
      imported = await post("/api/import?" + query, {text: get("paste").value});
    }
    await loadBatches(imported.id);
    notice("清单已导入，点击「开始采集」运行此批次。");
  } catch (error) {
    notice(error.message);
  } finally {
    get("import-button").disabled = false;
  }
};

function badge(status) {
  const tone = ["已完成", "已选图"].includes(status) ? " good" :
    status.startsWith("待") || status.includes("缺") || status === "采集失败" ? " wait" : "";
  return node("span", status, "badge" + tone);
}

function external(text, href) {
  const link = node("a", text);
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function candidateCard(row, color, candidate) {
  const selected = color.selected === candidate.id;
  const card = node("div", "", "card" + (selected ? " selected" : ""));
  const img = node("img");
  img.src = "/image/" + ui.batch + "/" + candidate.file;
  img.alt = row.model + " " + (color.name || "图片 " + color.number);
  img.loading = "lazy";
  const full = external("", img.src);
  full.setAttribute("aria-label", "查看 800×800 成图");
  full.append(img);
  const footer = node("footer");
  footer.append(external("ZOL 来源", candidate.source));
  const choose = node("button", selected ? "已选" : "选用");
  choose.disabled = selected;
  choose.onclick = () => action("choose", {row: row.id, color: color.key ?? color.name, id: candidate.id});
  footer.append(choose);
  card.append(full, node("small", candidate.view), footer);
  if (candidate.note) card.append(node("small", candidate.note));
  return card;
}

function renderRow(row, isOpen) {
  const details = node("details", "", "model");
  details.dataset.row = row.id;
  details.open = isOpen;
  const summary = node("summary");
  summary.append(node("span", row.model, "model-name"), node("span", row.brand, "model-brand"), badge(row.status));
  details.append(summary);
  const body = node("div", "", "detail");
  details.append(body);
  if (row.galleryUrl) body.append(external("打开 ZOL 图库", row.galleryUrl));
  else if (row.modelUrl) body.append(external("打开 ZOL 产品页", row.modelUrl));
  if (row.reason) body.append(node("p", row.reason));
  if (!["已完成", "排队中", "处理中"].includes(row.status)) {
    const form = node("form", "", "link-row");
    const input = node("input");
    input.type = "url";
    input.required = true;
    input.placeholder = "粘贴正确的 ZOL 产品或图库链接";
    input.setAttribute("aria-label", "绑定 ZOL 链接");
    form.append(input, node("button", "绑定链接"));
    form.onsubmit = event => {
      event.preventDefault();
      action("link", {row: row.id, url: input.value});
    };
    if (row.choices?.length) {
      const choices = node("div", "", "choices");
      for (const choice of row.choices) {
        choices.append(external(choice.name, choice.url));
      }
      body.append(choices);
    }
    body.append(form);
  }
  for (const color of row.colors || []) {
    const section = node("section", "", "color");
    const title = node("h3", color.name || "未标色图片 " + String(color.number).padStart(2, "0"));
    title.append(badge(color.status));
    section.append(title);
    if (color.reason) section.append(node("p", color.reason));
    if (color.candidates.length) {
      const candidates = node("div", "", "candidates");
      candidates.append(...color.candidates.map(item => candidateCard(row, color, item)));
      section.append(candidates);
    }
    body.append(section);
  }
  return details;
}

function render(data) {
  get("empty").hidden = true;
  get("workspace").hidden = false;
  const done = data.count - (data.totals["排队中"] || 0) - (data.totals["处理中"] || 0);
  const parts = [data.count === data.originalTotal ? data.count + " 个型号" :
    "抽样 " + data.count + " / " + data.originalTotal + " 个型号", "已处理 " + done,
    "已完成 " + (data.totals["已完成"] || 0)];
  if (data.running) parts.push(data.pausing ? "正在暂停" : "采集中");
  get("summary").textContent = parts.join(" · ");
  get("progress").max = data.count;
  get("progress").value = done;
  get("start").hidden = data.running;
  get("start").disabled = done === data.count;
  get("start").textContent = done ? "继续采集" : "开始采集";
  get("pause").hidden = !data.running;
  get("pause").disabled = data.pausing;
  get("retry").hidden = data.running || !data.totals["采集失败"];
  get("export").href = "/api/batch/" + ui.batch + "/zip";
  get("pause-reason").hidden = !data.pauseReason;
  get("pause-reason").textContent = data.pauseReason;
  get("previous").disabled = ui.offset === 0;
  get("next").disabled = ui.offset + pageSize >= data.filtered;
  get("page-number").textContent = data.filtered ? `${Math.floor(ui.offset / pageSize) + 1} / ${Math.ceil(data.filtered / pageSize)}` : "0 项";
  const rowsKey = JSON.stringify([ui.batch, data.rows]);
  if (ui.rowsKey === rowsKey) return;
  ui.rowsKey = rowsKey;
  const oldRows = new Map([...get("rows").querySelectorAll("details.model")].map(row => [Number(row.dataset.row), row]));
  const rows = data.rows.map(row => {
    const previous = oldRows.get(row.id);
    const key = JSON.stringify([data.id, row]);
    if (previous?.dataset.key === key) return previous;
    const element = renderRow(row, previous?.open || false);
    element.dataset.key = key;
    return element;
  });
  get("rows").replaceChildren(...(rows.length ? rows : [node("p", "没有符合筛选条件的型号", "empty")]));
}

async function refresh() {
  if (!ui.batch) return;
  const request = ++ui.request;
  const query = new URLSearchParams({q: get("query").value, status: get("status").value, offset: ui.offset});
  const data = await api("/api/batch/" + ui.batch + "?" + query);
  if (request !== ui.request) return;
  if (ui.offset && !data.rows.length) {
    ui.offset = Math.max(0, Math.floor((data.filtered - 1) / pageSize) * pageSize);
    await refresh();
    return;
  }
  render(data);
}

function refreshSafe() {
  refresh().catch(error => notice(error.message));
}

async function loadBatches(selected) {
  const batches = await api("/api/list");
  get("batches").replaceChildren(...batches.map(batch => {
    const option = node("option", batch.title);
    option.value = batch.id;
    return option;
  }));
  get("batches").hidden = !batches.length;
  if (batches.length) {
    ui.batch = selected || batches[0].id;
    ui.offset = 0;
    get("batches").value = ui.batch;
    await refresh();
  }
}

for (const status of statusList) {
  get("status").append(node("option", status));
}
get("batches").onchange = event => {
  ui.batch = event.target.value;
  ui.offset = 0;
  get("status").value = "";
  get("query").value = "";
  refreshSafe();
};
get("start").onclick = () => action("start");
get("pause").onclick = () => action("pause");
get("retry").onclick = () => action("retry");
get("query").oninput = get("status").onchange = () => { ui.offset = 0; refreshSafe(); };
get("previous").onclick = () => { ui.offset = Math.max(0, ui.offset - pageSize); refreshSafe(); };
get("next").onclick = () => { ui.offset += pageSize; refreshSafe(); };
loadBatches().catch(error => notice(error.message));
setInterval(refreshSafe, 2500);
