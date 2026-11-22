/* 商品图库 · 接入指南交互
   只做三件事：代码一键复制、左侧导航跟随滚动高亮、手机端抽屉。
   不引入任何第三方库。 */
(function () {
  "use strict";

  // ---------- 代码一键复制 ----------
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // 非安全上下文（http）或旧浏览器的兜底
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        var ok = document.execCommand("copy");
        document.body.removeChild(ta);
        ok ? resolve() : reject(new Error("execCommand failed"));
      } catch (e) {
        document.body.removeChild(ta);
        reject(e);
      }
    });
  }

  document.querySelectorAll(".code").forEach(function (box) {
    var btn = box.querySelector(".copy-btn");
    var pre = box.querySelector("pre");
    if (!btn || !pre) return;

    btn.addEventListener("click", function () {
      var text = pre.innerText.replace(/\s+$/, "");
      copyText(text).then(function () {
        btn.textContent = "已复制";
        btn.classList.add("done");
        setTimeout(function () {
          btn.textContent = "复制";
          btn.classList.remove("done");
        }, 1600);
      }).catch(function () {
        // 复制失败时不假装成功：选中代码让用户手动复制
        var range = document.createRange();
        range.selectNodeContents(pre);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        btn.textContent = "请按 ⌘C";
        setTimeout(function () { btn.textContent = "复制"; }, 2200);
      });
    });
  });

  // ---------- 左侧导航：滚动高亮 ----------
  var links = Array.prototype.slice.call(
    document.querySelectorAll("#sideNav a[href^='#']")
  );
  var targets = links.map(function (a) {
    return document.getElementById(a.getAttribute("href").slice(1));
  }).filter(Boolean);

  function setActive(id) {
    links.forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("href") === "#" + id);
    });
  }

  if ("IntersectionObserver" in window && targets.length) {
    var visible = new Map();
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) visible.set(en.target.id, en.intersectionRatio);
        else visible.delete(en.target.id);
      });
      // 取当前可见且位置最靠上的标题
      var best = null;
      var bestTop = Infinity;
      visible.forEach(function (_r, id) {
        var el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top < bestTop) {
          bestTop = el.getBoundingClientRect().top;
          best = id;
        }
      });
      if (best) setActive(best);
    }, { rootMargin: "-72px 0px -60% 0px", threshold: [0, 0.1, 0.5] });

    targets.forEach(function (t) { io.observe(t); });
  } else {
    // 兜底：按滚动位置计算
    var onScroll = function () {
      var y = window.scrollY + 100;
      var cur = targets[0];
      targets.forEach(function (t) { if (t.offsetTop <= y) cur = t; });
      if (cur) setActive(cur.id);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ---------- 手机端抽屉 ----------
  var side = document.getElementById("side");
  var mask = document.getElementById("mask");
  var menuBtn = document.getElementById("menuBtn");

  function closeDrawer() {
    if (side) side.classList.remove("open");
    if (mask) mask.classList.remove("open");
  }
  function toggleDrawer() {
    if (!side) return;
    var open = side.classList.toggle("open");
    if (mask) mask.classList.toggle("open", open);
  }

  if (menuBtn) menuBtn.addEventListener("click", toggleDrawer);
  if (mask) mask.addEventListener("click", closeDrawer);
  // 点导航后自动收起（手机上不然会挡住正文）
  links.forEach(function (a) { a.addEventListener("click", closeDrawer); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });

  // 在线接口清单以当前服务实际暴露的 OpenAPI 为源，避免手写路由表过期。
  var apiNames = {
    "/api/v1/images": "按品牌、型号、颜色和品类查询图片资产",
    "/api/v1/images-facets": "按当前筛选条件统计可选品牌、型号和颜色",
    "/api/v1/brand-tree": "读取品牌组、子品牌和独立品牌树",
    "/api/v1/images-overview": "读取图库图片、型号、品牌和容量概览",
    "/api/v1/images/{image_id}": "读取单张图片详情",
    "/api/v1/images/{image_id}/file": "在线预览原图或指定宽度缩略图",
    "/api/v1/images/{image_id}/download": "下载图片文件",
    "/api/v1/pool-management/summary": "读取 A、B、C 三池关系数量",
    "/api/v1/pool-management/{pool}/items": "按池子、关键词和分页查询图片—型号关系",
    "/api/v1/pool-management/assets/{asset_id}/history": "读取单条图片—型号关系的调整历史",
    "/api/v1/pool-management/assets/{asset_id}/image": "预览池内图片原图",
    "/api/v1/pool-management/assets/{asset_id}/pool": "调整图片—型号关系所属池子并记录原因",
    "/api/v1/business-review/uploads": "提交缺失型号×颜色组合候选图片",
    "/api/v1/business-review/summary": "旧审核队列统计（兼容保留）",
    "/api/v1/business-review/items": "旧审核队列查询（兼容保留）",
    "/api/v1/business-review/items/{item_id}": "读取旧审核记录详情（兼容保留）",
    "/api/v1/business-review/items/{item_id}/image": "读取旧审核记录图片（兼容保留）",
    "/api/v1/business-review/items/{item_id}/decision": "提交旧审核决定（兼容保留）",
    "/api/v1/coverage-monitor": "读取 Gaia 型号与标准颜色覆盖缺口",
    "/api/v1/coverage-upload-context": "读取缺口组合的 Gaia 型号、颜色和参数上下文",
    "/api/v1/coverage/{model_id}": "读取单个 Gaia 型号的标准颜色覆盖情况",
    "/api/v1/asset-pools": "读取图库分类与用途池的统计信息",
    "/api/v1/douyin-gallery/summary": "读取抖音主图素材总览",
    "/api/v1/douyin-gallery/folders": "读取抖音素材目录",
    "/api/v1/douyin-gallery/folder-tree": "读取抖音素材目录树",
    "/api/v1/douyin-gallery/items": "按目录和关键词检索抖音素材",
    "/api/v1/douyin-gallery/items/{item_id}/image": "预览抖音素材图片",
    "/api/v1/me": "读取当前登录角色及权限",
    "/api/v1/catalog": "读取图片品类和素材类型字典"
  };
  var tagNames = {
    "images": "图片资产查询",
    "image pools": "A / B / C 图片池管理",
    "business review": "历史候选审核接口（兼容）",
    "coverage-monitor": "型号与颜色覆盖",
    "douyin-gallery": "抖音主图素材",
    "default": "系统与其他接口"
  };
  var inventory = document.getElementById("apiInventory");
  var inventoryMeta = document.getElementById("apiInventoryMeta");
  var inventoryError = document.getElementById("apiInventoryError");
  if (inventory) {
    fetch("/openapi.json", { credentials: "same-origin", cache: "no-store" })
      .then(function (response) { if (!response.ok) throw new Error("HTTP " + response.status); return response.json(); })
      .then(function (schema) {
        var groups = new Map();
        Object.keys(schema.paths || {}).sort().forEach(function (path) {
          if (!path.startsWith("/api/")) return;
          Object.keys(schema.paths[path] || {}).sort().forEach(function (method) {
            var operation = schema.paths[path][method] || {};
            var tag = (operation.tags && operation.tags[0]) || "default";
            if (!groups.has(tag)) groups.set(tag, []);
            var params = (operation.parameters || []).map(function (item) { return item.name; });
            if (operation.requestBody) params.push("请求体");
            groups.get(tag).push({
              method: method.toUpperCase(), path: path,
              description: apiNames[path] || operation.summary || "接口能力由 OpenAPI 定义",
              params: params
            });
          });
        });
        var operationCount = Array.from(groups.values()).reduce(function (n, rows) { return n + rows.length; }, 0);
        var pathCount = Object.keys(schema.paths || {}).filter(function (path) { return path.startsWith("/api/"); }).length;
        inventoryMeta.textContent = pathCount + " 个 API 路径 · " + operationCount + " 个操作 · 更新于 " + new Date().toLocaleString("zh-CN");
        inventory.innerHTML = "";
        groups.forEach(function (rows, tag) {
          var section = document.createElement("section");
          section.className = "api-inventory-group";
          var heading = document.createElement("h3");
          heading.textContent = tagNames[tag] || tag;
          section.appendChild(heading);
          var table = document.createElement("table");
          table.className = "api-inventory-table";
          table.innerHTML = "<thead><tr><th>方法</th><th>接口</th><th>功能</th><th>参数</th></tr></thead>";
          var body = document.createElement("tbody");
          rows.forEach(function (row) {
            var tr = document.createElement("tr");
            var method = document.createElement("td"); method.textContent = row.method;
            var path = document.createElement("td"); var code = document.createElement("code"); code.textContent = row.path; path.appendChild(code);
            var desc = document.createElement("td"); desc.textContent = row.description;
            var params = document.createElement("td"); params.textContent = row.params.length ? row.params.join(" · ") : "—";
            tr.append(method, path, desc, params); body.appendChild(tr);
          });
          table.appendChild(body); section.appendChild(table); inventory.appendChild(section);
        });
      })
      .catch(function () { if (inventoryError) inventoryError.hidden = false; if (inventoryMeta) inventoryMeta.textContent = "未能连接当前 OpenAPI"; });
  }
})();
