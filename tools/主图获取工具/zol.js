// Loaded by the Ego Browser Node runtime. DOM callbacks run in its managed page.
globalThis.collectZol = async function (task, input) {
  const page = task.page("p1");
  const host = "https://detail.zol.com.cn";
  const fs = await import("node:fs/promises");
  const normalize = text => text.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}+]/gu, "");
  const headers = {timeout: 17000, waitUntil: "domcontentloaded"};
  const unavailable = error => String(error).includes("ZOL 页面暂时不可用");
  const load = async (url, domain = "detail.zol.com.cn") => {
    if (new URL(url).hostname !== domain) throw Error("仅支持 ZOL 搜索、产品和图库链接");
    await page.goto(url, headers);
    const current = new URL(await page.url());
    if (current.hostname !== domain) throw Error("ZOL 跳转到了其他页面");
    const title = await page.title();
    if (/502 Bad Gateway|503 Service Temporarily Unavailable/i.test(title)) throw Error("ZOL 页面暂时不可用");
    if (/验证|访问受限|403 Forbidden|429 Too Many Requests|Access Denied/i.test(title)) {
      throw Error("ZOL 暂时限制访问");
    }
  };
  const searchPath = /^\/(?:cell_phone\/index\d+\.shtml|series\/57\/\d+_\d+\.html)$/;
  const find = async (keyword, broad = false) => {
    if (broad) {
      await load("https://search.zol.com.cn/s/all.php?kword=" + encodeURIComponent(keyword), "search.zol.com.cn");
    } else {
      await load(host + "/index.php?c=SearchList&kword=" + encodeURIComponent(keyword));
    }
    return page.evaluate(() => {
      const products = new Map();
      for (const card of document.querySelectorAll(".list-box .series, .list-box .list-item")) {
        const title = card.querySelector("h4.title a, h3 a.title");
        const gallery = [...card.querySelectorAll("a[href]")].find(a => /\/pic(_\d+\.html|\.shtml)$/.test(new URL(a.href).pathname));
        const param = card.querySelector("a[href$='/param.shtml']")?.href;
        const radio = [...card.querySelectorAll(".param li")].find(li => /^使用场景[：:]/.test(li.textContent.trim()))?.textContent;
        if (title?.hostname === "detail.zol.com.cn") {
          products.set(title.href, {name: title.textContent.trim(), url: title.href, gallery: gallery?.href, param,
            radio: radio?.includes("5G手机") ? "5G" : radio?.includes("4G手机") ? "4G" : "",
            series: card.classList.contains("series")});
        }
      }
      for (const link of document.querySelectorAll("a[href*='/cell_phone/index'], a[href*='/series/57/']")) {
        const name = link.textContent.trim();
        if (!name || link.hostname !== "detail.zol.com.cn" || products.has(link.href) ||
            /^(?:报价|参数|图片|评测|点评|论坛|更多|综述|查看)/.test(name)) continue;
        products.set(link.href, {name, url:link.href, series:link.pathname.startsWith("/series/")});
      }
      return [...products.values()];
    }).then(rows => rows.filter(row => searchPath.test(new URL(row.url).pathname)));
  };

  let modelUrl = input.url;
  let verifyModelName;
  if (!modelUrl) {
    const brand = input.brand || (/^iphone/i.test(input.model) ? "Apple" :
      /^(?:mate|nova)\s*\d/i.test(input.model) ? "华为" : "");
    const mainBrand = brand.normalize("NFKC").split("(")[0].trim();
    const stripBrand = (name, brand) => {
      let key = normalize(name);
      const brands = {apple:["apple","苹果"], "苹果":["apple","苹果"], huawei:["huawei","华为"],
        "华为":["huawei","华为"], honor:["honor","荣耀"], "荣耀":["honor","荣耀"],
        samsung:["samsung","三星"], "三星":["samsung","三星"], xiaomi:["xiaomi","小米"],
        redmi:["redmi","红米"], "红米":["redmi","红米"],
        "小米": ["xiaomi","小米","redmi","红米"], "一加":["一加","oneplus"],
        "联想":["联想","lenovo"], "索尼":["索尼移动","索尼","sony"], "中兴":["中兴","zte"],
        "麦芒":["麦芒","maimang"], "黑鲨":["黑鲨","blackshark"],
        "摩托罗拉":["摩托罗拉","motorola","moto"], motorola:["摩托罗拉","motorola","moto"],
        "锤子":["锤子科技","锤子","smartisan"], "鼎桥":["鼎桥通信","鼎桥","tdtech"]};
      const names = brand.normalize("NFKC").split(/[()]/).map(normalize).filter(Boolean);
      const aliases = names.flatMap(word => brands[word] || [word]).sort((a,b) => b.length-a.length);
      for (const word of aliases) if (key.startsWith(word)) key = key.slice(word.length);
      return key;
    };
    const processorName = text => {
      const match = text.normalize("NFKC").match(/(骁龙|snapdragon|天玑|dimensity)\s*(\d{1,4})?\s*(\+|plus)?/i);
      if (!match) return "";
      const family = /骁龙|snapdragon/i.test(match[1]) ? "snapdragon" : "dimensity";
      return family + (match[2] || "") + (match[3] ? "+" : "");
    };
    const identity = name => {
      let network = "";
      let processor = "";
      let year = "";
      const readQualifiers = text => text
        .replace(/(?:^|\s)(?:(?:移动|联通|电信|全网通)\s*)?([45]G)(?:版|款)?(?=\s|$)/gi, (_, kind) => {
          network = kind.toUpperCase();
          return "";
        })
        .replace(/(?:^|\s)((?:19|20)\d{2})(?:\s*(?:款|版))?(?=\s|$)/g, (_, value) => {
          year = value;
          return "";
        })
        .replace(/(骁龙|snapdragon|天玑|dimensity)\s*(\d{1,4})?\s*(\+|plus)?\s*(?:版|款)/gi, value => {
          processor = processorName(value);
          return "";
        }).trim();
      const cleaned = name.normalize("NFKC").replace(/\b(Galaxy\s+Note)\s+(III|II|IV)\b/gi,
        (_, family, numeral) => family + " " + ({II:"2", III:"3", IV:"4"}[numeral.toUpperCase()]))
        .replace(/\(([^()]*)\)/g, (_, config) => {
          const versions = config.split("/")
            .filter(part => !/^(?:\d+(?:GB|TB)(?:\s*(?:RAM|ROM))?(?:\s*\+\s*\d+(?:GB|TB))*|全网通|双卡(?:版)?|单卡(?:版)?|行货|国行)$/i.test(part.trim()))
            .map(part => /^\s*(?:骁龙|snapdragon|天玑|dimensity)\s*\d{1,4}\+?\s*$/i.test(part) ? part.trim() + "版" : part);
          return " " + readQualifiers(versions.join(" "));
        });
      const baseName = readQualifiers(cleaned).replace(/系列\s*$/, "").replace(/(\bArt)版$/i, "$1")
        .replace(/^(小米|红米)手机\s*/i, "$1 ").replace(/\b(Pro)\s+Plus\b/gi, "$1+");
      const redmi = /^(?:(?:小米|xiaomi)\s*)?(?:红米|redmi)/i.test(baseName) || /^(?:红米|redmi)$/i.test(mainBrand);
      const core = stripBrand(baseName.replace(/^(?:小米|xiaomi)\s*(?=红米|redmi)/i, ""), brand);
      return {core: redmi ? "redmi" + core.replace(/^(?:redmi|红米)/i, "") : core, baseName, network, processor, year};
    };
    const wanted = identity(input.model);
    verifyModelName = name => {
      if (!name) return false;
      const actual = identity(name);
      return actual.core === wanted.core && (!wanted.network || !actual.network || actual.network === wanted.network)
        && (!wanted.year || !actual.year || actual.year === wanted.year);
    };
    const processorMatches = value => !wanted.processor ||
      (/\d/.test(wanted.processor) ? value === wanted.processor : value.startsWith(wanted.processor));
    const specifications = new Map();
    const readSpecifications = async row => {
      if (specifications.has(row.url)) return specifications.get(row.url);
      let url = row.param;
      if (!url) {
        await load(row.url);
        url = await page.evaluate(() => [...document.querySelectorAll("a[href]")].find(a =>
          a.textContent.trim() === "参数" && a.pathname.endsWith("/param.shtml"))?.href);
      }
      let spec = {};
      if (url) {
        await load(url);
        const params = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll("tr")]
          .map(tr => [tr.querySelector("th [id^='newPmName_']")?.textContent.trim(),
            tr.querySelector("td [id^='newPmVal_']")?.textContent.trim()])
          .filter(([name]) => ["CPU型号", "网络类型", "使用场景", "上市日期", "发布时间", "国内发布时间"].includes(name))));
        const mobile = (params["网络类型"] || "") + " " + (params["使用场景"] || "");
        spec = {network: /5G/i.test(mobile) ? "5G" : /4G/i.test(mobile) ? "4G" : "",
                processor: processorName(params["CPU型号"] || ""),
                year: (params["上市日期"] || params["国内发布时间"] || params["发布时间"] || "").match(/(?:19|20)\d{2}/)?.[0]};
      }
      specifications.set(row.url, spec);
      return spec;
    };
    const catalog = (input.catalog || []).filter(row => identity(row.name).core === wanted.core);
    const matches = new Map([...(input.hints || []), ...catalog].filter(row => {
      const url = new URL(row.url);
      return url.hostname === "detail.zol.com.cn" && searchPath.test(url.pathname);
    }).map(row => [row.url, row]));
    const raw = input.model.normalize("NFKC");
    const withoutNetwork = wanted.baseName;
    const prefix = [mainBrand, "苹果", "华为", "荣耀", "三星", "小米", "红米"].find(
      brand => brand && withoutNetwork.toLowerCase().startsWith(brand.toLowerCase()) && withoutNetwork.length > brand.length);
    const bare = prefix ? withoutNetwork.slice(prefix.length).trim() : withoutNetwork;
    const variants = [
      wanted.year && [withoutNetwork,wanted.year+"版"].join(" "),
      wanted.network && [withoutNetwork,wanted.network].join(" "),
      /^(?:红米|redmi)/i.test(withoutNetwork) && withoutNetwork.replace(/^(?:红米|redmi)/i,
        value => value === "红米" ? "Redmi " : "红米 "),
    ].filter(Boolean);
    const keywords = [raw, ...variants, withoutNetwork, [mainBrand, bare].filter(Boolean).join(" "),
      bare, bare.replace(/\s+/g,"")].filter(Boolean);
    const pick = async () => {
      const exact = [...matches.values()].map(row => {
        const id = identity(row.name);
        return {...row, ...id, declaredYear:id.year, year:id.year || row.releaseYear,
          declaredNetwork:id.network, network:id.network || row.radio};
      })
        .filter(row => row.core && row.core === wanted.core && (!wanted.year || !row.year || row.year === wanted.year));
      const compatible = exact.filter(row => (!wanted.network || (row.series ? row.declaredNetwork : row.network) === wanted.network)
        && processorMatches(row.processor) && (!wanted.year || row.year === wanted.year));
      const series = compatible.filter(row => row.series);
      const products = compatible.filter(row => !row.series);
      const networks = new Set(products.map(row => row.network).filter(Boolean));
      if (networks.size > 1 || new Set(products.map(row => row.year).filter(Boolean)).size > 1) return false;
      if (series.length === 1 && !/系列$/.test(series[0].name)) modelUrl = series[0].url;
      else if (products.length) modelUrl = products[0].url;
      else if (series.length === 1) modelUrl = series[0].url;
      if (!modelUrl && (wanted.network || wanted.processor || wanted.year)) {
        const candidates = exact.filter(row => !row.series
          && (!wanted.network || !row.declaredNetwork || row.declaredNetwork === wanted.network)
          && (!row.processor || processorMatches(row.processor)));
        for (const row of candidates.slice(0, 4)) {
          const spec = await readSpecifications(row).catch(error => {
            if (!unavailable(error)) throw error;
            return {};
          });
          if ((!wanted.network || spec.network === wanted.network || (!spec.network && row.network === wanted.network))
              && processorMatches(spec.processor || row.processor)
              && (!wanted.year || (row.declaredYear || spec.year || row.year) === wanted.year)) {
            modelUrl = row.url;
            break;
          }
        }
      }
      return Boolean(modelUrl);
    };
    let searchError;
    let completedSearches = 0;
    const search = async (keyword, broad) => {
      try {
        for (const row of await find(keyword, broad)) matches.set(row.url, row);
        completedSearches++;
        return pick();
      } catch (error) {
        if (!unavailable(error)) throw error;
        searchError = error;
        return true;
      }
    };
    if (!await pick() && !input.catalogOnly) {
      for (const keyword of [...new Set(keywords)].slice(0, input.maxSearchTerms ?? keywords.length)) {
        if (await search(keyword, false)) break;
      }
    }
    if (!modelUrl && input.broadSearch) {
      const broaderTerms = [...new Set([withoutNetwork, ...variants.slice(0,1)])];
      for (const keyword of broaderTerms.slice(0, input.maxBroadSearchTerms ?? broaderTerms.length)) {
        if (await search(keyword, true)) break;
      }
    }
    if (!modelUrl && input.references?.length) {
      const verified = [];
      for (const hint of input.references) {
        const url = new URL(hint.url);
        if (url.hostname !== "detail.zol.com.cn" || !searchPath.test(url.pathname)) continue;
        try {
          await load(url.href);
          const name = await page.evaluate(() => document.querySelector("h1")?.textContent.trim());
          if (!verifyModelName(name)) continue;
          verified.push({...hint, name, series:url.pathname.startsWith("/series/")});
        } catch (error) {
          if (/限制访问/.test(String(error))) throw error;
        }
      }
      const distinct = verified.filter(row => !row.series);
      let ambiguous = false;
      if (!wanted.network && !wanted.year && distinct.length > 1) {
        const specs = [];
        for (const row of distinct) specs.push(await readSpecifications(row).catch(error => {
          if (!unavailable(error)) throw error;
          return {};
        }));
        ambiguous = new Set(specs.map(spec => spec.year).filter(Boolean)).size > 1 ||
          new Set(specs.map(spec => spec.network).filter(Boolean)).size > 1;
      }
      for (const row of verified) matches.set(row.url, row);
      if (!ambiguous) await pick();
    }
    if (!modelUrl) {
      if (searchError && !completedSearches) throw searchError;
      const suggestions = [...matches.values()].sort((a,b) =>
        Number(identity(b.name).core === wanted.core) - Number(identity(a.name).core === wanted.core));
      return {status:"待确认型号", reason:"未找到可确认的完整型号或网络版本", choices:suggestions.slice(0,8)};
    }
  }
  await load(modelUrl);

  const model = await page.evaluate(() => ({
    name: document.querySelector("h1")?.textContent.trim(),
    gallery: [...document.querySelectorAll("a[href]")].find(a => /^图片(?:\s|[（(]|$)/.test(a.textContent.trim()) &&
      /\/pic(?:_[\d_]+)?\.(?:html|shtml)$/.test(a.pathname))?.href,
    colors: [...document.querySelectorAll(".versions [data-item-id], .versions-model-list [data-item-id]")].filter(a => {
      const block = a.closest(".versions-model-list");
      return block?.previousElementSibling?.textContent.includes("颜色");
    }).map(a => a.textContent.trim()).filter(Boolean)
  }));
  const current = await page.url();
  if (verifyModelName && (!searchPath.test(new URL(current).pathname) || !verifyModelName(model.name))) {
    return {status:"待确认型号", reason:"候选链接的实际产品标题未通过型号校验",
      choices:model.name ? [{name:model.name,url:current}] : []};
  }
  const isGallery = /\/pic(?:_[\d_]+)?\.(?:html|shtml)$/.test(new URL(current).pathname);
  const galleryUrl = isGallery ? current : model.gallery;
  if (!galleryUrl) return {status: "缺图库", reason: "型号页没有图片入口", modelUrl:current};
  if (!isGallery) {
    try {
      await load(galleryUrl);
    } catch (error) {
      if (!unavailable(error)) throw error;
      return {status:"缺图", reason:"已确认型号，图库暂时不可用", modelUrl:current, galleryUrl, name:model.name};
    }
  }

  const inspect = () => page.evaluate(() => {
    const label = a => [...a.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join("")
      .trim().replace(/\s*[（(][\d,]+[）)]$/, "");
    const wrap = [...document.querySelectorAll("a.imgwrap[href*='picture_index']")];
    return {
      title: document.querySelector("h1")?.textContent.trim(),
      overview: [...document.querySelectorAll("a[href]")].find(a => /综述介绍/.test(a.textContent))?.href,
      appearance: [...document.querySelectorAll(".pics-category-list a")].find(a => /^(?:整体)?外观图$/.test(label(a)))?.href,
      official: [...document.querySelectorAll(".pics-category-list a")].find(a => label(a) === "官方图")?.href,
      colors: [...document.querySelectorAll(".color-cate-list a")].map(a => [label(a), a.href]).filter(([name]) => name),
      next: [...document.querySelectorAll("a[href]")].find(a => a.textContent.trim() === "下一页")?.href,
      album: wrap.map(a => {
        const img = a.querySelector("img");
        const id = a.pathname.match(/\/(?:index|group)(\d+)/)?.[1];
        const src = img?.getAttribute(".src") || img?.getAttribute("data-src") || img?.getAttribute("src");
        return {id, url:a.href, thumb:src && new URL(src, location.href).href, alt:img?.alt};
      }).filter(image => image.id)
    };
  });
  const gallery = await inspect();
  if (isGallery && gallery.overview) {
    try {
      await load(gallery.overview);
      model.colors = await page.evaluate(() => [...document.querySelectorAll(".versions-model-list [data-item-id]")]
        .filter(a => a.closest(".versions-model-list")?.previousElementSibling?.textContent.includes("颜色"))
        .map(a => a.textContent.trim()).filter(Boolean));
    } catch (error) {
      if (!unavailable(error)) throw error;
    }
  }
  const walk = async url => {
    const found = new Map(), visited = new Set();
    while (url && !visited.has(url) && visited.size < 20) {
      visited.add(url);
      try {
        await load(url);
      } catch (error) {
        if (!unavailable(error)) throw error;
        break;
      }
      const data = await inspect();
      for (const image of data.album) found.set(image.id,image);
      url = data.next;
    }
    return found;
  };
  const galleryNames = gallery.colors.map(([name]) => name);
  const artGallery = /\bart\b/i.test(input.model + " " + (model.name || gallery.title)) &&
    galleryNames.length && galleryNames.every(name => /\bart\b/i.test(name));
  const names = [...new Set(artGallery ? galleryNames : [...model.colors, ...galleryNames])];
  const colors = [];
  const appearances = new Map(gallery.album.filter(image => /外观图|官方图/.test(image.alt))
    .map(image => [image.id, image]));
  for (const url of [gallery.appearance, gallery.official].filter(Boolean)) {
    for (const [id, image] of await walk(url)) {
      if (!appearances.has(id)) appearances.set(id, image);
    }
  }

  const colorAlbums = new Map();
  const labelCounts = new Map();
  for (const name of names) {
    const filter = gallery.colors.find(([color]) => color === name);
    if (!filter) continue;
    const tagged = await walk(filter[1]);
    const ids = [...tagged.keys()].filter(id => appearances.has(id));
    colorAlbums.set(name, ids);
    for (const id of ids) {
      labelCounts.set(id, (labelCounts.get(id) || 0) + 1);
    }
  }

  const download = async ids => {
    const images = [];
    for (const id of ids) {
      const image = appearances.get(id);
      const local = input.folder + "/" + id + ".jpg";
      try {
        const thumb = image.thumb && new URL(image.thumb);
        let src = thumb && thumb.pathname.match(/^\/product\/\d+_\d+x\d+\//)
          ? thumb.href.replace(/(\/product\/\d+)_\d+x\d+\//, "$1/")
          : null;
        if (!src) {
          await load(image.url);
          src = await page.evaluate(() => document.querySelector("#j_Image")?.src);
        }
        if (!src || !/(^|\.)zol-img\.com\.cn$/.test(new URL(src).hostname)) continue;
        const resp = await page.fetch(src.replace(/^http:/,"https:"), {saveAs:local, timeout:8000});
        if (resp?.ok === false || (await fs.stat(local)).size < 1000) continue;
        images.push({id, url:image.url, local});
      } catch (_) {
        continue;
      }
    }
    return images;
  };

  let needsUnlabeled = !names.length;
  for (const name of names) {
    const tagged = colorAlbums.get(name);
    const ids = (tagged || []).filter(id => labelCounts.get(id) === 1);
    if (!ids.length) {
      needsUnlabeled = true;
      const reason = !tagged ? "该颜色缺少可核实的图库标签" :
        tagged.length ? "候选图片标有多个颜色" : "该颜色没有可核实的外观图";
      colors.push({name, images:[], reason});
      continue;
    }
    const images = await download(ids);
    colors.push({name, images, reason: images.length ? "" : "大图下载失败"});
  }
  const unassigned = [...appearances.keys()].filter(id => labelCounts.get(id) !== 1);
  const unlabeled = needsUnlabeled || unassigned.some(id => labelCounts.has(id))
    ? await download(unassigned) : [];
  const reason = !colors.length && !unlabeled.length ? "图库没有可下载的外观大图" : "";
  return {modelUrl:current, galleryUrl, name:model.name || gallery.title, colors, unlabeled, reason, status:"已匹配"};
};
