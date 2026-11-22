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
})();
