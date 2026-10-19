// Pack-editor mini-app — vanilla JS.
// Связь с ботом: POST /api/build_pack с заголовком X-Telegram-Init-Data
// (HMAC по токену проверяет webapp_server). sendData больше не нужен —
// мини-аппа открывается одним тапом из inline-кнопки.

(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready?.();
  tg?.expand?.();

  // ── State ──────────────────────────────────
  // global: { kind, text, logo_svg_b64 }  — применяется ко всем 144
  // perEmoji[id] = { scale, offset_x, offset_y, rotation, content_color }  — настройки текущего тайла
  const state = {
    masterFiles: [],
    newFiles:    [],
    currentIdx:  null,
    global:      { kind: "text", text: "", logo_svg_b64: "", content_rotation: 0, content_color: "#ffffff" },
    perEmoji:    {},
    cache:       {},
    anim:        null,
    mode:        "text",
    format:      "emoji",
    selected:    new Set(),   // tiles that go into the built pack
    pricing:     { small_price_rub: 30, full_price_rub: 50, passport_price_rub: 25, small_threshold: 20, passport_ids: [] },
    // When selection contains *only* the passport, we hide the passport
    // editor by default — the user sees a regular editor view. A press
    // on ◁ / ▷ flips this to `true` and reveals the passport editor.
    passportRevealed: false,
  };
  const STICKER_LIMIT = 120;
  // Telegram custom-emoji set cap. We let users opt tiles in/out in emoji
  // mode too — same checkbox UX as sticker mode, just with a larger ceiling.
  const EMOJI_LIMIT = 200;
  function currentLimit() {
    // Effective cap is min(TG hard limit, tiles actually available). With
    // ~181 master+new tiles, showing "/200" in emoji mode is misleading —
    // the user can never reach 200. Clamp to the real available count.
    const hard = state.format === "sticker" ? STICKER_LIMIT : EMOJI_LIMIT;
    const total = (state.masterFiles.length + state.newFiles.length) || hard;
    return Math.min(hard, total);
  }

  // ── DOM ────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const previewBox = $("preview");
  const counter    = $("counter");
  const statusEl   = $("status");
  const grid       = $("grid");
  const gridNew    = $("grid-new");
  const previewStrip = $("preview-strip");
  const selectBar  = $("select-bar");
  const selectCountNum = $("select-count-num");
  const selectCountMax = $("select-count-max");

  // ── Helpers ────────────────────────────────
  function defaultTransform() {
    return {
      scale: 1.0, offset_x: 0, offset_y: 0, rotation: 0,
      content_color: "#ffffff", lines: null, photo_scale: 1.0,
      // Advanced colour editor: { "#oldhex": "#newhex", … }. Each fill/stroke
      // whose static colour matches an old key is repainted with the new
      // value on every preview render and during pack build.
      color_remap: null,
    };
  }
  function hasColorRemap(remap) {
    if (!remap || typeof remap !== "object") return false;
    for (const k in remap) {
      if (Object.prototype.hasOwnProperty.call(remap, k)
          && normalizeHexColor(remap[k]) !== normalizeHexColor(k)) {
        return true;
      }
    }
    return false;
  }
  function clampPhotoScale(v) {
    return Math.max(0.3, Math.min(3.0, +v || 1.0));
  }
  function clampScale(v) {
    return Math.max(0.5, Math.min(5.0, +v || 1.0));
  }
  function getTransform(id) {
    if (!state.perEmoji[id]) state.perEmoji[id] = defaultTransform();
    return state.perEmoji[id];
  }
  function isTransformChanged(t) {
    if (!t) return false;
    return Math.abs((t.scale || 1) - 1) > 1e-6
        || Math.abs(t.offset_x || 0) > 1e-6
        || Math.abs(t.offset_y || 0) > 1e-6
        || Math.abs(t.rotation || 0) > 1e-6
        || normalizeHexColor(t.content_color) !== "#ffffff"
        || hasLinesEdit(t.lines)
        || !!t.photo_svg_b64
        || Math.abs((t.photo_scale || 1) - 1) > 1e-3
        || hasColorRemap(t.color_remap);
  }
  function hasLinesEdit(lines) {
    if (!Array.isArray(lines)) return false;
    return lines.some(l => l && (
      (typeof l.text === "string" && l.text.trim() !== "")
      || (typeof l.scale === "number" && Math.abs(l.scale - 1) > 1e-3)
      || (typeof l.color === "string" && l.color.toLowerCase() !== "#000000")
      || (typeof l.offset_x === "number" && Math.abs(l.offset_x) > 1e-6)
      || (typeof l.offset_y === "number" && Math.abs(l.offset_y) > 1e-6)
    ));
  }
  function normalizeHexColor(v) {
    const s = String(v || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(s)) return s.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(s)) {
      return "#" + s.slice(1).split("").map(ch => ch + ch).join("").toLowerCase();
    }
    return "#ffffff";
  }
  function effectiveOverride(id) {
    /** Что реально подставить в превью: глобал + per-emoji трансформ.
     *  На паспорте глобальный ник/лого ИГНОРИРУЕМ: текст/цвет/масштаб
     *  s/w/e/x-букв берётся из строки `__header__` (Строка 1). */
    const g = state.global;
    const t = state.perEmoji[id] || defaultTransform();
    const f = fileById(id);
    const isPassport = !!(f && Array.isArray(f.passport_lines) && f.passport_lines.length);
    let kind = g.kind;
    let text = g.text;
    let logo_b64 = g.logo_svg_b64;
    let color = normalizeHexColor(t.content_color);
    let scale = t.scale;
    let ox = t.offset_x;
    let oy = t.offset_y;
    let rot = +t.rotation || 0;
    if (isPassport) {
      // Passport: глобальный ник/лого не применяется. Хэдер (s/w/e/x
      // буквы) всегда пустой → буквы удаляются в превью; пользователь
      // правит только три нижние строки.
      kind = "text";
      text = "";
      logo_b64 = "";
    }
    // New-tab tiles bake s/w/e/x letters at a smaller footprint than
    // master tiles. Pass an expand factor so the live-preview logo lands
    // at the same visual size as on master tiles.
    const isNewTile = typeof id === "string" && id.startsWith("new:");
    return {
      kind, text, logo_svg_b64: logo_b64,
      content_color: color,
      scale, offset_x: ox, offset_y: oy, rotation: rot,
      lines:         Array.isArray(t.lines) ? t.lines : null,
      photo_svg_b64: t.photo_svg_b64 || "",
      photo_scale:   clampPhotoScale(t.photo_scale),
      letter_expand: isNewTile ? 2.5 : 1.0,
      color_remap:   (t.color_remap && typeof t.color_remap === "object")
                       ? t.color_remap : null,
    };
  }
  function setStatus(msg) { statusEl.textContent = msg || ""; }
  function applyModeUI(mode) {
    state.mode = mode;
    state.global.kind = mode;
    document.querySelectorAll(".panel[data-panel='editor'] .seg-btn").forEach(b => {
      if (b.dataset.mode) b.classList.toggle("active", b.dataset.mode === mode);
    });
    document.body.classList.remove("mode-text", "mode-logo");
    document.body.classList.add("mode-" + mode);
  }

  // ── Master / New files load ────────────────
  async function loadMaster() {
    setStatus("Загружаю эмодзи…");
    const r = await fetch("/api/master/list");
    if (!r.ok) throw new Error("master list http " + r.status);
    state.masterFiles = await r.json();
    setStatus("");
  }
  async function loadNew() {
    try {
      const r = await fetch("/api/new/list");
      if (r.ok) state.newFiles = await r.json();
    } catch (_) { /* New tab optional */ }
  }
  function allFiles() { return state.masterFiles.concat(state.newFiles); }
  function editorFiles() {
    if (state.format !== "sticker" || state.selected.size === 0) return allFiles();
    return allFiles().filter(f => state.selected.has(f.id));
  }
  function fileById(id) { return allFiles().find(f => f.id === id) || null; }
  function isNewId(id) { return typeof id === "string" && id.startsWith("new:"); }

  async function ensureFont() {
    try {
      await SwexReplacer.loadFont("/static/fonts/SeymourOne-Regular.ttf");
    } catch (e) {
      console.error("[editor] font load failed:", e);
    }
  }

  async function loadLottie(id) {
    if (state.cache[id]) return JSON.parse(JSON.stringify(state.cache[id]));
    const f = fileById(id);
    if (!f) throw new Error("no file for id " + id);
    const base = isNewId(id) ? "/api/new/" : "/api/master/";
    // Cache-bust via `?v=<mtime>` returned by the listing endpoint. When a
    // .tgs file is regenerated on the server, mtime changes → URL changes →
    // browsers / Telegram WebView Service Worker / Cloudflare-style proxies
    // all fetch the new bytes instead of serving 24h-old cached copies.
    const ver = (f.v != null) ? ("?v=" + encodeURIComponent(f.v)) : "";
    const r = await fetch(base + encodeURIComponent(f.file) + ver);
    if (!r.ok) throw new Error("tgs http " + r.status);
    const buf = new Uint8Array(await r.arrayBuffer());
    const json = JSON.parse(pako.ungzip(buf, { to: "string" }));
    state.cache[id] = json;
    return JSON.parse(JSON.stringify(json));
  }

  // ── Preview render ─────────────────────────
  let _previewToken = 0;
  async function renderPreview(id) {
    const token = ++_previewToken;
    const list = editorFiles();
    const total = list.length;
    // Show 1-based position in the current navigation list, not the raw id
    // — master ids happen to be 1..144 so position == id there, but "new:N"
    // ids are 1..63 within their own range; without this remap the counter
    // shows "✨ 63 / 245" on the last tile instead of "245 / 245".
    const pos = list.findIndex(f => f.id === id) + 1;
    const sparkle = isNewId(id) ? "✨ " : "";
    counter.textContent = pos > 0
      ? `${sparkle}${pos} / ${total}`
      : `? / ${total}`;
    if (state.anim) { try { state.anim.destroy(); } catch (_) {} state.anim = null; }
    previewBox.innerHTML = "";
    try {
      const original = await loadLottie(id);
      if (token !== _previewToken) return;
      const eff = effectiveOverride(id);
      // Всегда применяем applyToLottie: убирает VELORA, применяет цвет/позицию/масштаб
      let data;
      try {
        data = SwexReplacer.applyToLottie(original, eff);
      } catch (e) {
        console.error("[editor] applyToLottie failed:", e);
        setStatus("Ошибка превью: " + (e?.message || e));
        data = JSON.parse(JSON.stringify(original));
      }
      state.anim = lottie.loadAnimation({
        container: previewBox,
        renderer: "svg",
        loop: true,
        autoplay: true,
        animationData: data,
      });
    } catch (e) {
      console.error("[editor] preview render failed:", e);
      previewBox.textContent = "✗ " + e.message;
    }
  }

  let _renderTimer = null;
  function rerenderPreviewSoon() {
    if (_renderTimer) clearTimeout(_renderTimer);
    _renderTimer = setTimeout(() => {
      _renderTimer = null;
      if (state.currentIdx != null) renderPreview(state.currentIdx);
    }, 90);
  }

  // ── Tabs ───────────────────────────────────
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.dataset.tab;
      document.querySelectorAll(".panel").forEach(p => {
        p.classList.toggle("hidden", p.dataset.panel !== target);
      });
      if (target === "grid") rebuildGrid();
      if (target === "new")  rebuildGridNew();
    });
  });

  // ── Mode (text/logo) — глобальный режим ────
  document.querySelectorAll(".panel[data-panel='editor'] .seg-btn").forEach(btn => {
    if (!btn.dataset.mode) return;
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      applyModeUI(mode);
      rerenderPreviewSoon();
    });
  });

  // ── Текст (глобальный) ─────────────────────
  const textInput = $("text-input");
  const contentColorInput = $("content-color");
  const contentColorVal = $("content-color-val");
  textInput.addEventListener("input", () => {
    const v = textInput.value.slice(0, 20);
    $("text-counter").textContent = `${v.length}/20`;
    state.global.text = v;
    rerenderPreviewSoon();
  });
  if (contentColorInput) {
    const onColor = () => {
      const color = normalizeHexColor(contentColorInput.value);
      const t = getTransform(state.currentIdx);
      t.content_color = color;
      contentColorInput.value = color;
      if (contentColorVal) contentColorVal.textContent = color.toUpperCase();
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
    };
    contentColorInput.addEventListener("input", onColor);
    contentColorInput.addEventListener("change", onColor);
  }

  // ── Advanced layer-colors modal ────────────────
  const lcmBackdrop = $("layer-colors-modal");
  const lcmList     = $("layer-colors-list");
  const lcmOpenBtn  = $("layer-colors-open");
  const lcmCloseBtn = $("layer-colors-close");
  const lcmResetBtn = $("layer-colors-reset");

  // Cache of unique colors per tile, computed on demand from the cached
  // lottie JSON. Keyed by tile id; value is an array of normalized hex
  // strings in the order they appear in the JSON (stable across opens).
  const _tileColorsCache = {};

  function rgbaToHex(rgba) {
    if (!Array.isArray(rgba)) return null;
    const r = Math.max(0, Math.min(255, Math.round((rgba[0] || 0) * 255)));
    const g = Math.max(0, Math.min(255, Math.round((rgba[1] || 0) * 255)));
    const b = Math.max(0, Math.min(255, Math.round((rgba[2] || 0) * 255)));
    return "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
  }

  function collectTileColors(json) {
    // Walk every static fl/st node and remember its colour. Animated colours
    // and gradient fills are skipped — they're rare in our pack and would
    // require keyframe-aware remapping to look right.
    const seen = new Set();
    const out = [];
    function visit(n) {
      if (n && typeof n === "object") {
        if (Array.isArray(n)) {
          for (const c of n) visit(c);
          return;
        }
        if (n.ty === "fl" || n.ty === "st") {
          const c = n.c;
          if (c && c.a === 0 && Array.isArray(c.k)) {
            const hex = rgbaToHex(c.k);
            if (hex && !seen.has(hex)) {
              seen.add(hex);
              out.push(hex);
            }
          }
        }
        for (const k of ["layers", "assets", "shapes", "it"]) {
          if (Array.isArray(n[k])) visit(n[k]);
        }
      }
    }
    visit(json);
    return out;
  }

  async function getTileColors(id) {
    if (_tileColorsCache[id]) return _tileColorsCache[id];
    let json;
    try { json = await loadLottie(id); }
    catch (e) { console.error("[lcm] load failed:", e); return []; }
    const colors = collectTileColors(json);
    _tileColorsCache[id] = colors;
    return colors;
  }

  function openLayerColorsModal() {
    if (state.currentIdx == null) return;
    lcmBackdrop.classList.remove("hidden");
    populateLayerColors();
  }
  function closeLayerColorsModal() {
    lcmBackdrop.classList.add("hidden");
  }
  if (lcmOpenBtn)  lcmOpenBtn.addEventListener("click", openLayerColorsModal);
  if (lcmCloseBtn) lcmCloseBtn.addEventListener("click", closeLayerColorsModal);
  if (lcmBackdrop) lcmBackdrop.addEventListener("click", (e) => {
    if (e.target === lcmBackdrop) closeLayerColorsModal();
  });

  async function populateLayerColors() {
    const id = state.currentIdx;
    if (!lcmList) return;
    lcmList.innerHTML = '<div class="lcm-empty">Загрузка…</div>';
    const colors = await getTileColors(id);
    if (!colors.length) {
      lcmList.innerHTML = '<div class="lcm-empty">В этом эмодзи нет статичных заливок для редактирования.</div>';
      return;
    }
    const t = getTransform(id);
    const remap = (t.color_remap && typeof t.color_remap === "object")
      ? t.color_remap : {};
    lcmList.innerHTML = "";
    for (const orig of colors) {
      const cur = normalizeHexColor(remap[orig] || orig);
      const row = document.createElement("div");
      row.className = "lcm-row";
      const sw = document.createElement("div");
      sw.className = "lcm-swatch";
      sw.style.background = cur;
      const hex = document.createElement("div");
      hex.className = "lcm-hex" + (cur !== orig ? " changed" : "");
      hex.textContent = cur.toUpperCase();
      const inp = document.createElement("input");
      inp.type = "color";
      inp.className = "lcm-input";
      inp.value = cur;
      inp.dataset.orig = orig;
      inp.addEventListener("input", () => {
        const v = normalizeHexColor(inp.value);
        sw.style.background = v;
        hex.textContent = v.toUpperCase();
        hex.classList.toggle("changed", v !== orig);
        const tile = getTransform(state.currentIdx);
        if (!tile.color_remap || typeof tile.color_remap !== "object") {
          tile.color_remap = {};
        }
        if (v === orig) {
          delete tile.color_remap[orig];
        } else {
          tile.color_remap[orig] = v;
        }
        // Drop empty maps so isTransformChanged stays accurate.
        if (Object.keys(tile.color_remap).length === 0) tile.color_remap = null;
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      });
      row.appendChild(sw);
      row.appendChild(hex);
      row.appendChild(inp);
      lcmList.appendChild(row);
    }
  }

  if (lcmResetBtn) lcmResetBtn.addEventListener("click", () => {
    const id = state.currentIdx;
    if (id == null) return;
    const t = getTransform(id);
    t.color_remap = null;
    rebuildGridCellState(id);
    rerenderPreviewSoon();
    populateLayerColors();
  });

  // ── Лого (глобальный) ──────────────────────
  $("logo-input").addEventListener("change", async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 256 * 1024) {
      setStatus("SVG > 256KB — слишком большой"); e.target.value = ""; return;
    }
    const txt = await f.text();
    if (!/<svg/i.test(txt)) {
      setStatus("Не похоже на SVG"); e.target.value = ""; return;
    }
    state.global.logo_svg_b64 = btoa(unescape(encodeURIComponent(txt)));
    applyModeUI("logo");
    $("logo-label").textContent = f.name + " ✓";
    setStatus("");
    rerenderPreviewSoon();
  });

  const contentRotationSlider = $("g-content-rotation");
  const contentRotationVal = $("g-content-rotation-val");
  if (!contentRotationSlider) {
    console.error("[editor] g-content-rotation not found");
  } else {
    const onRot = () => {
      const v = parseFloat(contentRotationSlider.value) || 0;
      const t = getTransform(state.currentIdx);
      t.rotation = v;
      contentRotationVal.textContent = v + "°";
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
    };
    contentRotationSlider.addEventListener("input", onRot);
    contentRotationSlider.addEventListener("change", onRot);
  }

  // ── Sliders (per-emoji transform) ──────────
  function bindSlider(id, valId, key, fmt) {
    const sl = $(id), val = $(valId);
    const handler = () => {
      const t  = getTransform(state.currentIdx);
      const v  = parseFloat(sl.value);
      if (key === "scale") t.scale = clampScale(v / 100);
      else t[key] = v;
      val.textContent = fmt(v);
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
    };
    sl.addEventListener("input", handler);
    sl.addEventListener("change", handler);
  }
  bindSlider("s-scale",     "v-scale",     "scale",    v => v + "%");
  bindSlider("s-offset-x",  "v-offset-x",  "offset_x", v => v + "");
  bindSlider("s-offset-y",  "v-offset-y",  "offset_y", v => v + "");

  // ── Format ─────────────────────────────────
  document.querySelectorAll(".fmt-seg .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const newFormat = btn.dataset.format === "sticker" ? "sticker" : "emoji";
      const changed = state.format !== newFormat;
      state.format = newFormat;
      document.querySelectorAll(".fmt-seg .seg-btn").forEach(b =>
        b.classList.toggle("active", b === btn));
      // На реальной смене формата всегда переинициализируем выбор
      // под дефолт нового формата. Иначе sticker→emoji оставляет
      // только 120 тайлов вместо полного эмодзи-набора (~180).
      if (changed) {
        state.selected = newFormat === "sticker"
          ? pickDefaultStickerSelection()
          : pickDefaultSelection(EMOJI_LIMIT);
      }
      applyStickerMode();
    });
  });

  // ── Sticker-mode selection ─────────────────
  function isPassportId(id) {
    if (!state.pricing.passport_ids || !state.pricing.passport_ids.length) return false;
    const s = String(id);
    return state.pricing.passport_ids.some(pid => String(pid) === s);
  }

  function pickDefaultStickerSelection() {
    /** Sticker mode default selection: every new tile first, then master,
     *  capped at STICKER_LIMIT. Passport NOT auto-included — user opts in. */
    const out = new Set();
    const masters = state.masterFiles, news = state.newFiles;
    for (const f of news) {
      if (out.size >= STICKER_LIMIT) break;
      if (isPassportId(f.id)) continue;
      out.add(f.id);
    }
    for (const f of masters) {
      if (out.size >= STICKER_LIMIT) break;
      if (isPassportId(f.id)) continue;
      out.add(f.id);
    }
    return out;
  }

  function pickDefaultSelection(maxN) {
    /** Default emoji-mode selection: every non-passport tile, capped at
     *  `maxN` (Telegram's per-set limit) so a fresh pack is always buildable.
     *  The passport tile is a paid add-on and is never preselected. */
    const cap = (typeof maxN === "number" && maxN > 0)
      ? maxN : Infinity;
    const out = new Set();
    // New tiles first — they're the most recently added so they tend to
    // be what the user wanted on opening.
    for (const f of state.newFiles) {
      if (out.size >= cap) return out;
      if (!isPassportId(f.id)) out.add(f.id);
    }
    for (const f of state.masterFiles) {
      if (out.size >= cap) return out;
      if (!isPassportId(f.id)) out.add(f.id);
    }
    return out;
  }

  function computePriceRub() {
    /** Mirror of services/billing.Billing.quote_pack_price_rub for the live
     *  CTA price tag. Server is the source of truth — this is for UX. */
    const p = state.pricing || {};
    const small = +p.small_price_rub || 30;
    const full = +p.full_price_rub || 50;
    const passport = +p.passport_price_rub || 25;
    const threshold = +p.small_threshold || 20;
    let count = 0, hasPassport = false;
    for (const id of state.selected) {
      if (isPassportId(id)) hasPassport = true;
      else count += 1;
    }
    if (count <= 0) return hasPassport ? passport : 0;
    const base = count <= threshold ? small : full;
    return base + (hasPassport ? passport : 0);
  }

  function applyStickerMode() {
    const on = state.format === "sticker";
    document.body.classList.toggle("sticker-mode", on);
    // Selection bar is now always visible (count + price). Sticker-mode
    // class still controls the per-cell click behaviour and the limit cap.
    selectBar.classList.remove("hidden");
    if (on) {
      // In sticker mode enforce the 120-cap default. If selection is empty
      // OR exceeds cap, re-seed with the limited default.
      if (state.selected.size === 0 || state.selected.size > STICKER_LIMIT) {
        state.selected = pickDefaultStickerSelection();
      }
      // Editor preview should only walk through selected stickers.
      const list = editorFiles();
      if (list.length && !list.some(f => f.id === state.currentIdx)) {
        state.currentIdx = list[0].id;
        renderPreview(state.currentIdx);
      }
    } else {
      // Emoji mode: empty OR over-the-cap selection seeds to the first
      // EMOJI_LIMIT non-passport tiles so the build is already valid.
      if (state.selected.size === 0 || state.selected.size > EMOJI_LIMIT) {
        state.selected = pickDefaultSelection(EMOJI_LIMIT);
      }
    }
    updateSelectionUI();
    rebuildGridSelectionState();
  }
  selectCountMax.textContent = STICKER_LIMIT;

  function updateSelectionUI() {
    const n = state.selected.size;
    const cap = currentLimit();
    selectCountNum.textContent = n;
    const over = n > cap;
    selectCountNum.classList.toggle("over", over);
    selectBar.classList.toggle("limit", over);
    // Show "N/cap" in both modes — the cap is just larger in emoji mode (200).
    const sep = selectBar.querySelector(".select-count-sep");
    const maxN = document.getElementById("select-count-max");
    if (sep) sep.classList.remove("hidden");
    if (maxN) {
      maxN.classList.remove("hidden");
      maxN.textContent = cap;
    }
    const rub = computePriceRub();
    const priceEl = document.getElementById("pack-price");
    if (priceEl) priceEl.textContent = rub > 0 ? rub + " ₽" : "—";
    const genBtn = document.getElementById("generate");
    if (genBtn) genBtn.textContent = rub > 0 ? `Создать пак · ${rub} ₽` : "Создать пак";
    // Passport toggle card state
    const passportToggle = document.getElementById("passport-toggle");
    const hasPassport = state.pricing.passport_ids && state.pricing.passport_ids.length &&
      state.pricing.passport_ids.some(pid => state.selected.has(pid));
    if (passportToggle) {
      passportToggle.classList.toggle("on", !!hasPassport);
    }
    // «×» в углу плашки — видна только когда паспорт уже в паке.
    const passportRemove = document.getElementById("passport-remove");
    if (passportRemove) passportRemove.classList.toggle("hidden", !hasPassport);
    // Solo-passport reveal state: сбрасываем, как только пользователь
    // выходит из этого состояния (добавил другой тайл, снял паспорт).
    if (!isOnlyPassportSelected()) {
      state.passportRevealed = false;
    }
    // Per-tile include toggle (sticker mode) — sync visibility + state.
    refreshPreviewToggle();
    // Sync strip cell .selected classes (sticker-mode dims non-selected).
    refreshStripSelectionState();
    // Refresh the live passport thumbnail (cheap, debounced internally).
    refreshPassportThumb();
    // Solo-passport может потребовать перерисовку секции паспорта
    // (показать/скрыть редактор), синхронно с изменением выбора.
    if (state.currentIdx != null) renderPassportLinesUI();
  }

  // ── Per-tile include toggle (visible in both formats) ───────
  function refreshPreviewToggle() {
    const pin = document.getElementById("preview-pin");
    const id = state.currentIdx;
    const inSet = id != null && state.selected.has(id);
    if (pin) {
      pin.classList.remove("hidden");
      pin.classList.toggle("on", inSet);
    }
  }

  function toggleCurrentTile() {
    const id = state.currentIdx;
    if (id == null) return;
    const prevId = state.currentIdx;
    const ok = toggleSelected(id);
    // Prevent sticker-mode auto-jump to next selected item.
    if (state.currentIdx !== prevId) {
      state.currentIdx = prevId;
      renderPreview(state.currentIdx);
    }
    refreshPreviewToggle();
    // Visual feedback.
    const pin = document.getElementById("preview-pin");
    if (pin) { pin.classList.remove("bump"); void pin.offsetWidth; pin.classList.add("bump"); }
    if (!ok) {
      // Sticker cap hit — flash the count.
      selectCountNum.classList.remove("over");
      void selectCountNum.offsetWidth;
      selectCountNum.classList.add("over");
      setTimeout(() => selectCountNum.classList.remove("over"), 350);
    }
  }

  document.getElementById("preview-pin")?.addEventListener("click", toggleCurrentTile);

  // ── Live passport thumbnail in the selection bar ─────────
  // Renders a mini Lottie of the actual passport tile (with the user's
  // current per-line edits + photo SVG applied) inside #passport-thumb.
  // The Lottie player keeps playing the looping animation at low cost.
  let _ppThumbAnim = null;
  let _ppThumbId = null;
  let _ppThumbToken = 0;
  let _ppThumbTimer = null;
  function refreshPassportThumb() {
    if (_ppThumbTimer) clearTimeout(_ppThumbTimer);
    _ppThumbTimer = setTimeout(_renderPassportThumb, 90);
  }
  async function _renderPassportThumb() {
    const host = document.getElementById("passport-thumb");
    if (!host) return;
    const ids = (state.pricing && state.pricing.passport_ids) || [];
    if (!ids.length) {
      host.classList.add("empty");
      return;
    }
    const pid = ids[0];
    const myToken = ++_ppThumbToken;
    try {
      const original = await loadLottie(pid);
      if (myToken !== _ppThumbToken) return;
      let data;
      try {
        // Use the same override the editor uses so user edits show through.
        data = SwexReplacer.applyToLottie(original, effectiveOverride(pid));
      } catch (_) {
        data = original;
      }
      // (Re)create the Lottie player. Loop the animation softly.
      if (_ppThumbAnim) { try { _ppThumbAnim.destroy(); } catch (_) {} _ppThumbAnim = null; }
      host.innerHTML = "";
      host.classList.remove("empty");
      _ppThumbAnim = lottie.loadAnimation({
        container: host,
        renderer: "svg",
        loop: true,
        autoplay: true,
        animationData: data,
        rendererSettings: { preserveAspectRatio: "xMidYMid meet" },
      });
      _ppThumbId = pid;
    } catch (e) {
      host.classList.add("empty");
    }
  }
  function bumpCount() {
    selectCountNum.classList.remove("bump");
    // restart animation
    void selectCountNum.offsetWidth;
    selectCountNum.classList.add("bump");
    setTimeout(() => selectCountNum.classList.remove("bump"), 260);
  }
  function rebuildGridSelectionState() {
    const cap = currentLimit();
    document.querySelectorAll(".grid .cell, .grid-new .cell").forEach(c => {
      const id = parseCellId(c.dataset.id);
      const isSel = state.selected.has(id);
      c.classList.toggle("selected", isSel);
      const overCap = !isSel && state.selected.size >= cap;
      c.classList.toggle("disabled", overCap);
    });
  }
  function parseCellId(raw) {
    if (raw && raw.startsWith("new:")) return raw;
    return +raw;
  }
  function toggleSelected(id) {
    if (state.selected.has(id)) {
      state.selected.delete(id);
    } else {
      // Enforce TG hard caps: 120 for sticker sets, 200 for custom-emoji.
      if (state.selected.size >= currentLimit()) return false;
      state.selected.add(id);
    }
    bumpCount();
    updateSelectionUI();
    rebuildGridSelectionState();
    // In sticker mode, keep the editor preview inside the selection.
    if (state.format === "sticker") {
      const list = editorFiles();
      if (list.length && !list.some(f => f.id === state.currentIdx)) {
        state.currentIdx = list[0].id;
        renderPreview(state.currentIdx);
      }
    }
    return true;
  }

  $("select-all").addEventListener("click", () => {
    runWave(grid.querySelectorAll(".cell"));
    runWave(gridNew.querySelectorAll(".cell"));
    state.selected = state.format === "sticker"
      ? pickDefaultStickerSelection()
      : pickDefaultSelection(EMOJI_LIMIT);
    bumpCount();
    updateSelectionUI();
    rebuildGridSelectionState();
  });
  $("select-none").addEventListener("click", () => {
    runWave(grid.querySelectorAll(".cell.selected"));
    runWave(gridNew.querySelectorAll(".cell.selected"));
    state.selected.clear();
    bumpCount();
    updateSelectionUI();
    rebuildGridSelectionState();
  });

  // Passport pill — теперь это «кнопка перехода» в редактор паспорта.
  // Один тап → паспорт автоматически попадает в пак (если ещё не там) +
  // превью переключается на него + раскрывается секция строк/фото. То
  // есть юзеру не надо искать паспорт-эмодзи в листании ◁ ▷ среди 180.
  // Удалить из пака — через маленькую «×» в правом-верхнем углу плашки.
  function jumpToPassport() {
    const ids = state.pricing.passport_ids || [];
    if (!ids.length) return;
    const pid = ids[0];
    if (!state.selected.has(pid)) {
      state.selected.add(pid);
      bumpCount();
    }
    state.passportRevealed = true;
    selectId(pid);  // обновит preview + renderPassportLinesUI() → редактор паспорта
    updateSelectionUI();
    rebuildGridSelectionState();
    // Дать рендеру кадр на отрисовку и доскроллить до редактора паспорта.
    requestAnimationFrame(() => {
      const sec = document.getElementById("passport-lines");
      if (sec && !sec.classList.contains("hidden")) {
        sec.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  const passportToggle = $("passport-toggle");
  if (passportToggle) {
    passportToggle.addEventListener("click", jumpToPassport);
    passportToggle.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        jumpToPassport();
      }
    });
  }
  const passportRemove = $("passport-remove");
  if (passportRemove) {
    passportRemove.addEventListener("click", (e) => {
      e.stopPropagation();
      const ids = state.pricing.passport_ids || [];
      if (!ids.length) return;
      const pid = ids[0];
      if (!state.selected.has(pid)) return;
      state.selected.delete(pid);
      bumpCount();
      updateSelectionUI();
      rebuildGridSelectionState();
      if (state.format === "sticker") {
        const list = editorFiles();
        if (list.length && !list.some(f => f.id === state.currentIdx)) {
          state.currentIdx = list[0].id;
          renderPreview(state.currentIdx);
        }
      }
    });
  }

  // ── Wave / pulse animation helpers ─────────
  function runWave(nodes) {
    // cascade highlight across cells, ~18ms per cell with 250ms tail
    const list = Array.from(nodes);
    list.forEach((c, i) => {
      setTimeout(() => {
        c.classList.remove("wave-hit");
        void c.offsetWidth;
        c.classList.add("wave-hit");
        setTimeout(() => c.classList.remove("wave-hit"), 460);
      }, i * 18);
    });
  }
  function pulseBtn(btn) {
    btn.classList.remove("pulsing");
    void btn.offsetWidth;
    btn.classList.add("pulsing");
    setTimeout(() => btn.classList.remove("pulsing"), 360);
  }

  // ── Nav ────────────────────────────────────
  $("prev").addEventListener("click", () => navTo(-1));
  $("next").addEventListener("click", () => navTo(+1));

  function isOnlyPassportSelected() {
    if (state.selected.size !== 1) return false;
    const ids = state.pricing && state.pricing.passport_ids;
    if (!Array.isArray(ids) || !ids.length) return false;
    const only = String([...state.selected][0]);
    return ids.includes(only);
  }

  function navTo(delta) {
    // Solo-passport: первый клик по стрелке не перелистывает (там
    // нечего), а открывает редактор паспорта.
    if (isOnlyPassportSelected() && !state.passportRevealed) {
      state.passportRevealed = true;
      const pid = state.pricing.passport_ids[0];
      if (state.currentIdx !== pid) {
        selectId(pid);
      } else {
        renderPassportLinesUI();
      }
      return;
    }
    const list = editorFiles();
    const n = list.length; if (!n) return;
    const cur = list.findIndex(f => f.id === state.currentIdx);
    const idx = (cur + delta + n) % n;
    selectId(list[idx].id);
  }

  // ── Apply-all / reset ──────────────────────
  function applyToAllCells(mutator) {
    allFiles().forEach(f => mutator(getTransform(f.id)));
    setStatus("");
    rebuildGrid();
    rebuildGridNew();
    rerenderPreviewSoon();
  }
  function applyAllWithAnimation(btnId, mutator) {
    const btn = $(btnId);
    pulseBtn(btn);
    applyToAllCells(mutator);
    // Wave through both grids — visible feedback that the change reached
    // every cell, not only the current one. Use a single rAF so the DOM
    // state from rebuild is already painted before the wave starts.
    requestAnimationFrame(() => {
      runWave(grid.querySelectorAll(".cell"));
      runWave(gridNew.querySelectorAll(".cell"));
    });
  }

  $("apply-all").addEventListener("click", () => {
    const src = state.perEmoji[state.currentIdx] || defaultTransform();
    const color = normalizeHexColor(src.content_color);
    applyAllWithAnimation("apply-all", (t) => {
      t.scale = clampScale(src.scale);
      t.offset_x = src.offset_x;
      t.offset_y = src.offset_y;
      t.content_color = color;
    });
  });
  $("apply-color-all").addEventListener("click", () => {
    const src = state.perEmoji[state.currentIdx] || defaultTransform();
    const color = normalizeHexColor(src.content_color);
    applyAllWithAnimation("apply-color-all", (t) => {
      t.content_color = color;
    });
  });
  $("apply-rotation-all").addEventListener("click", () => {
    const src = state.perEmoji[state.currentIdx] || defaultTransform();
    const rotation = +src.rotation || 0;
    applyAllWithAnimation("apply-rotation-all", (t) => {
      t.rotation = rotation;
    });
  });
  $("reset-one").addEventListener("click", () => {
    delete state.perEmoji[state.currentIdx];
    loadEditorFromState();
    rebuildGrid();
    setStatus("");
    rerenderPreviewSoon();
  });

  // ── Editor ↔ state ─────────────────────────
  function loadEditorFromState() {
    // Глобальные поля
    applyModeUI(state.global.kind);
    textInput.value = state.global.text || "";
    $("text-counter").textContent = (state.global.text || "").length + "/20";
    $("logo-label").textContent = state.global.logo_svg_b64 ? "Лого загружено ✓" : "Загрузить SVG";
    const t = state.perEmoji[state.currentIdx] || defaultTransform();
    if (contentColorInput) contentColorInput.value = normalizeHexColor(t.content_color);
    if (contentColorVal) contentColorVal.textContent = normalizeHexColor(t.content_color).toUpperCase();
    if (contentRotationSlider) contentRotationSlider.value = Math.round(t.rotation || 0);
    if (contentRotationVal) contentRotationVal.textContent = Math.round(t.rotation || 0) + "°";
    // Per-emoji трансформ
    t.scale = clampScale(t.scale);
    $("s-scale").value    = Math.round(t.scale * 100);
    $("v-scale").textContent = Math.round(t.scale * 100) + "%";
    $("s-offset-x").value = t.offset_x;
    $("v-offset-x").textContent = String(t.offset_x);
    $("s-offset-y").value = t.offset_y;
    $("v-offset-y").textContent = String(t.offset_y);
    renderPassportLinesUI();
  }

  function defaultLine(name) {
    return { name, text: "", scale: 1.0, color: "#000000", offset_x: 0, offset_y: 0 };
  }
  function isLineModified(l) {
    if (!l) return false;
    return (l.text && l.text.trim() !== "")
        || Math.abs((l.scale || 1) - 1) > 1e-3
        || (l.color && l.color.toLowerCase() !== "#000000")
        || Math.abs(l.offset_x || 0) > 1e-6
        || Math.abs(l.offset_y || 0) > 1e-6;
  }

  function renderPassportLinesUI() {
    const wrap = $("passport-lines");
    const rows = $("passport-lines-rows");
    if (!wrap || !rows) return;
    const f = fileById(state.currentIdx);
    let tplLines = f && Array.isArray(f.passport_lines) ? f.passport_lines : null;
    if (!tplLines || !tplLines.length) {
      wrap.classList.add("hidden");
      rows.innerHTML = "";
      document.body.classList.remove("passport-mode");
      return;
    }
    // Solo-passport: пока пользователь не «раскрыл» редактор клавишей
    // ◁/▷, показываем обычный редактор (без passport-mode и без секции
    // строк). После раскрытия — стандартный паспорт-режим.
    if (isOnlyPassportSelected() && !state.passportRevealed) {
      wrap.classList.add("hidden");
      rows.innerHTML = "";
      document.body.classList.remove("passport-mode");
      return;
    }
    document.body.classList.add("passport-mode");
    wrap.classList.remove("hidden");
    // Server sends: [__header__, secondary_a, secondary_b, secondary_c].
    // Swap the first two secondary rows (indices 1 and 2) so the @-handle
    // slot reads first under the header — natural passport order.
    if (tplLines.length >= 3) {
      tplLines = [tplLines[0], tplLines[2], tplLines[1], ...tplLines.slice(3)];
    }
    const t = getTransform(state.currentIdx);
    // Migrate using NAME-based lookup so user-typed text stays attached to
    // its swex_text_* slot even if we shuffle UI rows around.
    const prev = Array.isArray(t.lines) ? t.lines.slice() : [];
    t.lines = tplLines.map(tpl => {
      const existing = prev.find(l => l && l.name === tpl.name);
      if (existing) {
        return {
          name:     tpl.name,
          text:     typeof existing.text === "string" ? existing.text : "",
          scale:    typeof existing.scale === "number" ? existing.scale : 1.0,
          color:    typeof existing.color === "string" ? existing.color : "#000000",
          offset_x: typeof existing.offset_x === "number" ? existing.offset_x : 0,
          offset_y: typeof existing.offset_y === "number" ? existing.offset_y : 0,
        };
      }
      return defaultLine(tpl.name);
    });
    rows.innerHTML = "";
    // Hide the __header__ row from the UI — the passport's s/w/e/x letters
    // stay empty by default (which suppresses the global nick). Users only
    // see and edit the three secondary rows (Строка 2/3/4).
    tplLines.forEach((tpl, i) => {
      if (tpl.name === "__header__") return;
      const row = document.createElement("div");
      row.className = "pp-row";
      const line = t.lines[i];
      const cur = line.text || "";
      if (isLineModified(line)) row.classList.add("modified");
      if (cur) row.classList.add("has-value");
      // Labels start at 2 (the hidden header would be 1).
      const label = (i + 1).toString();
      const placeholder = tpl.text;
      row.innerHTML = `
        <div class="pp-row-line1">
          <span class="num">${label}</span>
          <input class="line-text" type="text" maxlength="30"
                 placeholder="${escapeHtml(placeholder)}"
                 value="${escapeHtml(cur)}"
                 autocomplete="off" autocorrect="off" spellcheck="false">
          <span class="ctr">${cur.length}/30</span>
          <button class="clear" type="button" title="Очистить">✕</button>
        </div>
        <div class="pp-row-line2">
          <div class="line-size">
            <span class="line-size-label">Размер</span>
            <div class="line-size-row">
              <input class="line-scale" type="range" min="50" max="200" step="5"
                     value="${Math.round((line.scale || 1) * 100)}">
              <span class="line-scale-val">${Math.round((line.scale || 1) * 100)}%</span>
            </div>
          </div>
          <input class="line-color" type="color" value="${escapeHtml(line.color || "#000000")}">
        </div>
        <div class="pp-row-line3">
          <div class="line-axis">
            <span class="line-axis-label">X</span>
            <input class="line-offset-x" type="range" min="-250" max="250" step="1"
                   value="${Math.round(line.offset_x || 0)}">
            <span class="line-axis-val line-offset-x-val">${Math.round(line.offset_x || 0)}</span>
          </div>
          <div class="line-axis">
            <span class="line-axis-label">Y</span>
            <input class="line-offset-y" type="range" min="-100" max="100" step="1"
                   value="${Math.round(line.offset_y || 0)}">
            <span class="line-axis-val line-offset-y-val">${Math.round(line.offset_y || 0)}</span>
          </div>
        </div>
      `;
      const textInputEl = row.querySelector(".line-text");
      const ctr = row.querySelector(".ctr");
      const clearBtn = row.querySelector(".clear");
      const scaleEl = row.querySelector(".line-scale");
      const scaleVal = row.querySelector(".line-scale-val");
      const colorEl = row.querySelector(".line-color");
      const offXEl = row.querySelector(".line-offset-x");
      const offXVal = row.querySelector(".line-offset-x-val");
      const offYEl = row.querySelector(".line-offset-y");
      const offYVal = row.querySelector(".line-offset-y-val");

      function updateModifiedClass() {
        row.classList.toggle("modified", isLineModified(line));
        row.classList.toggle("has-value", (line.text || "").length > 0);
      }
      function syncText() {
        const v = textInputEl.value.slice(0, 30);
        line.text = v;
        ctr.textContent = `${v.length}/30`;
        ctr.classList.toggle("warn", v.length >= 24 && v.length < 30);
        ctr.classList.toggle("full", v.length >= 30);
        updateModifiedClass();
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      }
      function syncScale() {
        line.scale = (parseFloat(scaleEl.value) || 100) / 100;
        scaleVal.textContent = Math.round(line.scale * 100) + "%";
        updateModifiedClass();
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      }
      function syncColor() {
        line.color = colorEl.value;
        updateModifiedClass();
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      }
      textInputEl.addEventListener("input", syncText);
      clearBtn.addEventListener("click", () => {
        textInputEl.value = "";
        syncText();
        textInputEl.focus();
      });
      scaleEl.addEventListener("input", syncScale);
      scaleEl.addEventListener("change", syncScale);
      colorEl.addEventListener("input", syncColor);
      colorEl.addEventListener("change", syncColor);

      function syncOffsetX() {
        line.offset_x = parseFloat(offXEl.value) || 0;
        offXVal.textContent = String(Math.round(line.offset_x));
        updateModifiedClass();
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      }
      function syncOffsetY() {
        line.offset_y = parseFloat(offYEl.value) || 0;
        offYVal.textContent = String(Math.round(line.offset_y));
        updateModifiedClass();
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      }
      offXEl.addEventListener("input", syncOffsetX);
      offXEl.addEventListener("change", syncOffsetX);
      offYEl.addEventListener("input", syncOffsetY);
      offYEl.addEventListener("change", syncOffsetY);

      ctr.classList.toggle("warn", cur.length >= 24 && cur.length < 30);
      ctr.classList.toggle("full", cur.length >= 30);
      rows.appendChild(row);
    });
    renderPassportPhotoUI();
  }

  function renderPassportPhotoUI() {
    const preview = $("passport-photo-preview");
    const label = $("passport-photo-label");
    const clearBtn = $("passport-photo-clear");
    if (!preview) return;
    const t = getTransform(state.currentIdx);
    const hasSvg = !!t.photo_svg_b64;
    if (hasSvg) {
      try {
        const svgText = decodeURIComponent(escape(atob(t.photo_svg_b64)));
        preview.innerHTML = svgText;
        // Force black-card visibility: anything light stays visible on the
        // black square via invert if needed.
      } catch (_) {
        preview.innerHTML = '<span class="pp-photo-q">?</span>';
      }
      if (label) label.textContent = "Заменить SVG";
      if (clearBtn) clearBtn.classList.remove("hidden");
    } else {
      preview.innerHTML = '<span class="pp-photo-q">?</span>';
      if (label) label.textContent = "Загрузить SVG";
      if (clearBtn) clearBtn.classList.add("hidden");
    }
    const sizeBox = document.getElementById("passport-photo-size");
    const sizeEl = document.getElementById("passport-photo-scale");
    const sizeVal = document.getElementById("passport-photo-scale-val");
    if (sizeBox && sizeEl && sizeVal) {
      // Slider is only meaningful when a logo SVG is present — '?' is
      // sized to a fixed font glyph (handled in the renderer) and the
      // scale knob doesn't apply to it.
      sizeBox.classList.toggle("hidden", !hasSvg);
      const pct = Math.round(clampPhotoScale(t.photo_scale) * 100);
      sizeEl.value = String(pct);
      sizeVal.textContent = pct + "%";
    }
  }

  // Passport-wide actions: reset all, photo SVG upload/clear.
  (function bindPassportActions() {
    const reset = document.getElementById("passport-reset");
    const svgInput = document.getElementById("passport-photo-svg");
    const clearBtn = document.getElementById("passport-photo-clear");
    if (reset) reset.addEventListener("click", () => {
      const t = state.perEmoji[state.currentIdx];
      if (!t) return;
      if (Array.isArray(t.lines)) t.lines.forEach(l => {
        l.text = ""; l.scale = 1.0; l.color = "#000000";
        l.offset_x = 0; l.offset_y = 0;
      });
      t.photo_svg_b64 = "";
      t.photo_scale = 1.0;
      renderPassportLinesUI();
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
    });
    if (svgInput) svgInput.addEventListener("change", async (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      if (f.size > 256 * 1024) { setStatus("SVG > 256KB"); e.target.value = ""; return; }
      const txt = await f.text();
      if (!/<svg/i.test(txt)) { setStatus("Не похоже на SVG"); e.target.value = ""; return; }
      const t = getTransform(state.currentIdx);
      t.photo_svg_b64 = btoa(unescape(encodeURIComponent(txt)));
      renderPassportPhotoUI();
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
      setStatus("");
    });
    if (clearBtn) clearBtn.addEventListener("click", () => {
      const t = state.perEmoji[state.currentIdx];
      if (!t) return;
      t.photo_svg_b64 = "";
      t.photo_scale = 1.0;
      const inp = document.getElementById("passport-photo-svg");
      if (inp) inp.value = "";
      renderPassportPhotoUI();
      rebuildGridCellState(state.currentIdx);
      rerenderPreviewSoon();
    });
    const sizeEl = document.getElementById("passport-photo-scale");
    const sizeVal = document.getElementById("passport-photo-scale-val");
    if (sizeEl) {
      const syncPhotoScale = () => {
        const t = getTransform(state.currentIdx);
        t.photo_scale = clampPhotoScale((parseFloat(sizeEl.value) || 100) / 100);
        if (sizeVal) sizeVal.textContent = Math.round(t.photo_scale * 100) + "%";
        rebuildGridCellState(state.currentIdx);
        rerenderPreviewSoon();
      };
      sizeEl.addEventListener("input", syncPhotoScale);
      sizeEl.addEventListener("change", syncPhotoScale);
    }
  })();

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── Grid ───────────────────────────────────
  let gridBuilt = false;
  let gridNewBuilt = false;

  // Lazy Lottie preview inside grid cells (original emoji, no override).
  const gridObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        const cell = entry.target;
        gridObserver.unobserve(cell);
        renderCellPreview(cell, parseCellId(cell.dataset.id));
      }
    }
  }, { rootMargin: "120px" });

  async function renderCellPreview(cell, id) {
    if (cell._previewLoaded) return;
    cell._previewLoaded = true;
    try {
      const data = await loadLottie(id);
      const container = cell.querySelector(".cell-anim");
      if (!container) return;
      const anim = lottie.loadAnimation({
        container,
        renderer: "canvas",
        loop: false,
        autoplay: false,
        animationData: JSON.parse(JSON.stringify(data)),
      });
      cell._gridAnim = anim;
      anim.addEventListener("DOMLoaded", () => {
        anim.goToAndStop(0, true);
        const fallback = cell.querySelector(".cell-fallback");
        if (fallback) fallback.style.display = "none";
      });
    } catch (e) {
      console.error("[editor] grid preview failed for", id, e);
    }
  }

  function makeCell(f) {
    const cell = document.createElement("div");
    cell.className = "cell";
    cell.dataset.id = String(f.id);
    cell.title = "#" + f.id;

    const animDiv = document.createElement("div");
    animDiv.className = "cell-anim";
    cell.appendChild(animDiv);

    const fallback = document.createElement("span");
    fallback.className = "cell-fallback";
    fallback.textContent = f.emoji || "·";
    cell.appendChild(fallback);

    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = isNewId(f.id) ? f.id.replace("new:", "✨") : f.id;
    cell.appendChild(idx);
    // Per-cell selection toggle (works in both modes, indep. of cell click).
    const chk = document.createElement("span");
    chk.className = "chk";
    chk.title = "В пак";
    chk.addEventListener("click", (e) => {
      e.stopPropagation();
      const ok = toggleSelected(f.id);
      if (!ok) {
        selectCountNum.classList.remove("over");
        void selectCountNum.offsetWidth;
        selectCountNum.classList.add("over");
        setTimeout(() => selectCountNum.classList.remove("over"), 350);
      }
    });
    cell.appendChild(chk);
    if (f.id === state.currentIdx) cell.classList.add("current");
    if (isTransformChanged(state.perEmoji[f.id])) cell.classList.add("modified");
    if (state.selected.has(f.id)) cell.classList.add("selected");
    if (isPassportId(f.id)) cell.classList.add("is-passport");
    gridObserver.observe(cell);
    cell.addEventListener("click", () => {
      if (state.format === "sticker") {
        const ok = toggleSelected(f.id);
        if (!ok) {
          selectCountNum.classList.remove("over");
          void selectCountNum.offsetWidth;
          selectCountNum.classList.add("over");
          setTimeout(() => selectCountNum.classList.remove("over"), 350);
        }
        return;
      }
      selectId(f.id);
      document.querySelector('.tab[data-tab="editor"]').click();
    });
    return cell;
  }

  function rebuildGrid() {
    if (gridBuilt) {
      grid.querySelectorAll(".cell").forEach(c => {
        const id = parseCellId(c.dataset.id);
        c.classList.toggle("current",  id === state.currentIdx);
        c.classList.toggle("modified", isTransformChanged(state.perEmoji[id]));
        c.classList.toggle("selected", state.selected.has(id));
      });
      rebuildGridSelectionState();
      return;
    }
    grid.innerHTML = "";
    state.masterFiles.forEach(f => grid.appendChild(makeCell(f)));
    gridBuilt = true;
    rebuildGridSelectionState();
  }
  function rebuildGridNew() {
    if (gridNewBuilt) {
      gridNew.querySelectorAll(".cell").forEach(c => {
        const id = parseCellId(c.dataset.id);
        c.classList.toggle("current",  id === state.currentIdx);
        c.classList.toggle("modified", isTransformChanged(state.perEmoji[id]));
        c.classList.toggle("selected", state.selected.has(id));
      });
      rebuildGridSelectionState();
      return;
    }
    gridNew.innerHTML = "";
    if (!state.newFiles.length) {
      gridNew.innerHTML = '<div class="row label" style="grid-column:1/-1;color:#6e6e73">пусто</div>';
      gridNewBuilt = true;
      return;
    }
    state.newFiles.forEach(f => gridNew.appendChild(makeCell(f)));
    gridNewBuilt = true;
    rebuildGridSelectionState();
  }
  function rebuildGridCellState(id) {
    const sel = `.cell[data-id="${String(id).replace(/"/g, '\\"')}"]`;
    document.querySelectorAll(sel).forEach(cell => {
      cell.classList.toggle("modified", isTransformChanged(state.perEmoji[id]));
    });
    refreshStripCellState(id);
  }

  // ── Preview strip (horizontal thumbnails of all emoji) ─────
  let stripBuilt = false;
  const stripObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        const cell = entry.target;
        stripObserver.unobserve(cell);
        renderStripCellPreview(cell, parseCellId(cell.dataset.id));
      }
    }
  }, { root: previewStrip, rootMargin: "120px" });

  async function renderStripCellPreview(cell, id) {
    if (cell._stripLoaded) return;
    cell._stripLoaded = true;
    try {
      const data = await loadLottie(id);
      const container = cell.querySelector(".strip-anim");
      if (!container) return;
      const anim = lottie.loadAnimation({
        container,
        renderer: "canvas",
        loop: false,
        autoplay: false,
        animationData: JSON.parse(JSON.stringify(data)),
      });
      cell._stripAnim = anim;
      anim.addEventListener("DOMLoaded", () => {
        anim.goToAndStop(0, true);
        const fb = cell.querySelector(".strip-fallback");
        if (fb) fb.style.display = "none";
      });
    } catch (e) {
      console.error("[editor] strip preview failed for", id, e);
    }
  }

  function makeStripCell(f) {
    const cell = document.createElement("div");
    cell.className = "strip-cell";
    cell.dataset.id = String(f.id);
    cell.setAttribute("role", "option");
    cell.title = "#" + f.id;

    const anim = document.createElement("div");
    anim.className = "strip-anim";
    cell.appendChild(anim);

    const fallback = document.createElement("span");
    fallback.className = "strip-fallback";
    fallback.textContent = f.emoji || "·";
    cell.appendChild(fallback);

    if (f.id === state.currentIdx) cell.classList.add("current");
    if (isTransformChanged(state.perEmoji[f.id])) cell.classList.add("modified");
    if (state.selected.has(f.id)) cell.classList.add("selected");
    if (isPassportId(f.id)) cell.classList.add("is-passport");

    cell.addEventListener("click", () => selectId(f.id));
    stripObserver.observe(cell);
    return cell;
  }

  function rebuildPreviewStrip() {
    if (!previewStrip) return;
    if (stripBuilt) {
      // Lightweight refresh of state classes only.
      previewStrip.querySelectorAll(".strip-cell").forEach(c => {
        const id = parseCellId(c.dataset.id);
        c.classList.toggle("current",  id === state.currentIdx);
        c.classList.toggle("modified", isTransformChanged(state.perEmoji[id]));
        c.classList.toggle("selected", state.selected.has(id));
      });
      return;
    }
    previewStrip.innerHTML = "";
    allFiles().forEach(f => previewStrip.appendChild(makeStripCell(f)));
    attachStripDesktopScroll();
    stripBuilt = true;
  }

  // Desktop scroll affordances. The strip is `overflow-x: auto` so phone
  // touch swipe works natively, but on desktop the scrollbar is hidden
  // (`display:none`) and the browser doesn't translate vertical wheel into
  // horizontal scroll automatically — so the strip looked frozen. Hook a
  // wheel listener that does the translation, plus mouse-drag (click-and-
  // pan) so users can scrub through the thumbnails like a film strip.
  function attachStripDesktopScroll() {
    if (!previewStrip || previewStrip._desktopScrollHooked) return;
    previewStrip._desktopScrollHooked = true;

    previewStrip.addEventListener("wheel", (e) => {
      // Only intercept when the user is actually scrolling primarily
      // vertically. Trackpads that already emit horizontal deltaX (e.g.
      // Apple Magic Trackpad two-finger swipe) keep their native behaviour.
      const dx = e.deltaX;
      const dy = e.deltaY;
      if (Math.abs(dy) > Math.abs(dx) && dy !== 0) {
        previewStrip.scrollLeft += dy;
        e.preventDefault();
      }
    }, { passive: false });

    let drag = null;
    previewStrip.addEventListener("mousedown", (e) => {
      // Ignore non-primary buttons; let clicks on cells through (the cell
      // click handler will run if the pointer didn't move).
      if (e.button !== 0) return;
      drag = {
        startX: e.clientX,
        startScroll: previewStrip.scrollLeft,
        moved: false,
      };
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.startX;
      if (!drag.moved && Math.abs(dx) > 4) {
        drag.moved = true;
        previewStrip.classList.add("dragging");
      }
      if (drag.moved) {
        previewStrip.scrollLeft = drag.startScroll - dx;
        e.preventDefault();
      }
    });
    window.addEventListener("mouseup", (e) => {
      if (!drag) return;
      const wasDrag = drag.moved;
      drag = null;
      previewStrip.classList.remove("dragging");
      if (wasDrag) {
        // Eat the trailing click so the cell under the mouse doesn't
        // accidentally select after the drag.
        const swallow = (ev) => {
          ev.stopPropagation();
          ev.preventDefault();
          window.removeEventListener("click", swallow, true);
        };
        window.addEventListener("click", swallow, true);
      }
    });
  }

  function refreshStripCellState(id) {
    if (!previewStrip) return;
    const sel = `.strip-cell[data-id="${String(id).replace(/"/g, '\\"')}"]`;
    const cell = previewStrip.querySelector(sel);
    if (!cell) return;
    cell.classList.toggle("modified", isTransformChanged(state.perEmoji[id]));
    cell.classList.toggle("selected", state.selected.has(id));
  }

  function refreshStripSelectionState() {
    if (!previewStrip || !stripBuilt) return;
    previewStrip.querySelectorAll(".strip-cell").forEach(c => {
      const id = parseCellId(c.dataset.id);
      c.classList.toggle("selected", state.selected.has(id));
    });
  }

  function syncStripCurrent(id) {
    if (!previewStrip) return;
    let target = null;
    previewStrip.querySelectorAll(".strip-cell").forEach(c => {
      const cid = parseCellId(c.dataset.id);
      const is = cid === id;
      c.classList.toggle("current", is);
      if (is) target = c;
    });
    if (target) {
      // Center the active thumb in the strip viewport.
      const cRect = previewStrip.getBoundingClientRect();
      const tRect = target.getBoundingClientRect();
      const delta = (tRect.left + tRect.width / 2) - (cRect.left + cRect.width / 2);
      previewStrip.scrollBy({ left: delta, behavior: "smooth" });
    }
  }

  // ── Selection ──────────────────────────────
  async function selectId(id) {
    state.currentIdx = id;
    loadEditorFromState();
    refreshPreviewToggle();
    syncStripCurrent(id);
    await renderPreview(id);
    rebuildGrid();
    rebuildGridNew();
    // If the layer-colors modal is open while the user navigates, refresh
    // its list against the newly-selected tile so the editor reflects the
    // current emoji's palette and stored remap.
    if (lcmBackdrop && !lcmBackdrop.classList.contains("hidden")) {
      populateLayerColors();
    }
  }

  // ── Generate (HTTP API вместо sendData) ────
  $("generate").addEventListener("click", async () => {
    const initData = tg?.initData || "";
    if (!initData) {
      setStatus("Открой через Telegram (нет initData)"); return;
    }
    // Когда в паке только паспорт(ы) — глобальный ник/лого не применяется
    // ни к одному тайлу. Валидацию пропускаем.
    const ids = (state.pricing && state.pricing.passport_ids) || [];
    const onlyPassports = state.selected.size > 0
      && [...state.selected].every(x => ids.includes(String(x)));
    const hasContent = (state.global.kind === "text" && state.global.text.trim())
                    || (state.global.kind === "logo" && state.global.logo_svg_b64);
    if (!onlyPassports && !hasContent) {
      setStatus("Сначала введи текст или загрузи лого."); return;
    }
    // Перед стартом сборки спрашиваем имя пака (то, что будет перед "by @bot").
    // Дефолт — текущий ник или прошлый pack_title из localStorage.
    const defaultName =
      (state.global.kind === "text" && state.global.text && state.global.text.trim())
      || (localStorage.getItem("pack_title") || "")
      || "";
    openNameModal(defaultName, (packName) => {
      try { localStorage.setItem("pack_title", packName); } catch (_) {}
      runBuild(packName);
    });
  });

  async function runBuild(packName) {
    const initData = tg?.initData || "";
    // Чистим transformы без изменений
    const transforms = {};
    Object.keys(state.perEmoji).forEach(k => {
      const t = state.perEmoji[k];
      if (isTransformChanged(t)) {
        const entry = {
          scale:    +(clampScale(t.scale).toFixed(3)),
          offset_x: +t.offset_x,
          offset_y: +t.offset_y,
          rotation: +((t.rotation || 0).toFixed(3)),
          content_color: normalizeHexColor(t.content_color),
        };
        if (hasLinesEdit(t.lines)) {
          entry.lines = t.lines
            .map(l => ({
              name:     String(l.name || ""),
              text:     String(l.text || "").slice(0, 30),
              scale:    +(((typeof l.scale === "number") ? l.scale : 1).toFixed(3)),
              color:    String(l.color || "#000000"),
              offset_x: +((typeof l.offset_x === "number") ? l.offset_x : 0),
              offset_y: +((typeof l.offset_y === "number") ? l.offset_y : 0),
            }))
            .filter(l => l.name && (
              l.text.trim() !== ""
              || Math.abs(l.scale - 1) > 1e-3
              || l.color.toLowerCase() !== "#000000"
              || Math.abs(l.offset_x) > 1e-6
              || Math.abs(l.offset_y) > 1e-6
            ));
        }
        if (t.photo_svg_b64) entry.photo_svg_b64 = String(t.photo_svg_b64);
        if (t.photo_svg_b64 && Math.abs((t.photo_scale || 1) - 1) > 1e-3) {
          entry.photo_scale = +clampPhotoScale(t.photo_scale).toFixed(3);
        }
        // Per-tile palette overrides — old hex → new hex. Normalised once
        // here so the server doesn't have to.
        if (hasColorRemap(t.color_remap)) {
          const clean = {};
          for (const ok in t.color_remap) {
            const from = normalizeHexColor(ok);
            const to = normalizeHexColor(t.color_remap[ok]);
            if (from !== to) clean[from] = to;
          }
          if (Object.keys(clean).length) entry.color_remap = clean;
        }
        transforms[k] = entry;
      }
    });
    // For sticker mode, validate selection count before sending.
    if (state.format === "sticker") {
      if (state.selected.size === 0) {
        setStatus("Выбери хотя бы один стикер");
        return;
      }
      if (state.selected.size > STICKER_LIMIT) {
        const cta = $("generate");
        cta.classList.add("limit-warn");
        setTimeout(() => cta.classList.remove("limit-warn"), 600);
        setStatus(`Слишком много (${state.selected.size}) — макс ${STICKER_LIMIT}`);
        return;
      }
    }
    // Always send the selection — pricing is based on count + passport.
    const selected = Array.from(state.selected).map(String);
    const payload = {
      format:     state.format,
      global:     {
        kind:         state.global.kind,
        text:         state.global.text || "",
        logo_svg_b64: state.global.logo_svg_b64 || "",
        content_color: "#ffffff",
        content_rotation: 0,
        logo_rotation: 0,
      },
      per_emoji:  transforms,
      selected,   // null = all (emoji mode); array of ids = sticker subset
      pack_title: String(packName || "").slice(0, 40),
    };
    setStatus("Запускаю сборку…");
    $("generate").disabled = true;
    try {
      const r = await fetch("/api/build_pack", {
        method: "POST",
        headers: {
          "Content-Type":          "application/json",
          "X-Telegram-Init-Data":  initData,
        },
        body: JSON.stringify(payload),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        setStatus("Ошибка: " + (data.error || r.status));
        $("generate").disabled = false;
        return;
      }
      setStatus("Сборка запущена! Закрываю…");
      setTimeout(() => tg?.close?.(), 800);
    } catch (e) {
      setStatus("Сеть: " + e.message);
      $("generate").disabled = false;
    }
  }

  // ── Pack-name modal ─────────────────────────
  function openNameModal(defaultName, onConfirm) {
    const m  = $("name-modal");
    const i  = $("name-modal-input");
    const c  = $("name-modal-counter");
    const p  = $("name-modal-preview-text");
    const ok = $("name-modal-ok");
    const cancel = $("name-modal-cancel");
    const botSpans = document.querySelectorAll("#name-modal-bot, .name-modal-bot2");
    const botUsername = (tg && tg.initDataUnsafe
        && tg.initDataUnsafe.bot && tg.initDataUnsafe.bot.username)
      || "swexmakerbot";
    botSpans.forEach(s => s.textContent = botUsername);
    i.value = String(defaultName || "").slice(0, 40);
    function refresh() {
      const v = i.value.slice(0, 40);
      const cleanV = v.trim();
      c.textContent = `${v.length}/40`;
      c.classList.toggle("warn", v.length >= 32 && v.length < 40);
      c.classList.toggle("full", v.length >= 40);
      p.textContent = cleanV || "имя пака";
      ok.disabled = cleanV.length === 0;
    }
    refresh();
    function close() {
      m.classList.add("hidden");
      m.classList.remove("show");
      cleanup();
    }
    function confirm() {
      const v = i.value.trim();
      if (!v) return;
      close();
      onConfirm(v.slice(0, 40));
    }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Enter") { e.preventDefault(); confirm(); }
    }
    function onBackdrop(e) {
      if (e.target.classList.contains("name-modal-backdrop")) close();
    }
    function cleanup() {
      i.removeEventListener("input", refresh);
      ok.removeEventListener("click", confirm);
      cancel.removeEventListener("click", close);
      m.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
    }
    i.addEventListener("input", refresh);
    ok.addEventListener("click", confirm);
    cancel.addEventListener("click", close);
    m.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
    m.classList.remove("hidden");
    requestAnimationFrame(() => {
      m.classList.add("show");
      i.focus();
      i.select();
    });
  }

  async function loadPricing() {
    try {
      const r = await fetch("/api/pricing");
      if (r.ok) state.pricing = await r.json();
    } catch (_) { /* keep defaults */ }
  }

  // ── My Packs view ────────────────────────────────────────────
  const packsState = {
    loaded:    false,
    loading:   false,
    items:     [],          // server response from /api/packs/list
    current:   null,        // currently-open pack detail object
    staged:    new Set(),   // tile ids queued for addition (string keys)
    overrides: new Map(),   // id -> per-tile override (from mini-editor)
    editing:   null,        // currently-edited tile id (or null)
  };

  function apiHeaders() {
    return {
      "X-Telegram-Init-Data": tg?.initData || "",
      "Content-Type": "application/json",
    };
  }

  function setView(name) {
    const editor = $("view-editor");
    const packs  = $("view-packs");
    document.querySelectorAll(".topbar-tab").forEach(b => {
      b.classList.toggle("active", b.dataset.view === name);
    });
    if (name === "packs") {
      editor.classList.add("hidden");
      packs.classList.remove("hidden");
      loadPacksIfNeeded();
    } else {
      packs.classList.add("hidden");
      editor.classList.remove("hidden");
    }
  }

  document.querySelectorAll(".topbar-tab").forEach(b => {
    b.addEventListener("click", () => setView(b.dataset.view));
  });

  async function loadPacksIfNeeded(force = false) {
    if (packsState.loading) return;
    if (packsState.loaded && !force) return;
    packsState.loading = true;
    const list = $("packs-list");
    list.innerHTML = '<div class="packs-skel">Загружаю…</div>';
    try {
      const r = await fetch("/api/packs/list", { headers: apiHeaders() });
      if (!r.ok) throw new Error("http " + r.status);
      packsState.items = await r.json();
      packsState.loaded = true;
      renderPacksList();
    } catch (e) {
      list.innerHTML = `<div class="packs-skel">Ошибка загрузки: ${e.message}</div>`;
    } finally {
      packsState.loading = false;
    }
  }

  function updatePacksCountBadge() {
    const badge = $("packs-count-badge");
    if (!badge) return;
    const n = packsState.items.length;
    badge.textContent = n;
    badge.classList.toggle("hidden", n === 0);
  }

  function renderPacksList() {
    const list = $("packs-list");
    const empty = $("packs-empty");
    const head  = $("packs-head-count");
    updatePacksCountBadge();
    if (!packsState.items.length) {
      list.innerHTML = "";
      list.classList.add("hidden");
      empty.classList.remove("hidden");
      head.textContent = "0";
      return;
    }
    list.classList.remove("hidden");
    empty.classList.add("hidden");
    head.textContent = packsState.items.length + (
      packsState.items.length === 1 ? " пак" : " пак(а/ов)"
    );
    list.innerHTML = "";
    packsState.items.forEach(p => list.appendChild(makePackCard(p)));
  }

  function makePackCard(pack) {
    const card = document.createElement("div");
    card.className = "pack-card";
    card.dataset.id = String(pack.id);

    const thumbs = document.createElement("div");
    thumbs.className = "pack-card-thumbs";
    const first4 = pack.included.slice(0, 4);
    for (let i = 0; i < 4; i++) {
      const t = document.createElement("div");
      t.className = "pack-card-thumb";
      const id = first4[i];
      if (id != null) {
        const anim = document.createElement("div");
        anim.className = "pct-anim";
        t.appendChild(anim);
        const fb = document.createElement("span");
        fb.className = "pct-fallback";
        const f = fileById(id);
        fb.textContent = f ? (f.emoji || "·") : "·";
        t.appendChild(fb);
        // Stash params on the thumb element; lazy observer renders on view.
        t._pctPack = pack;
        t._pctId = id;
        ensurePctObserver().observe(t);
      }
      thumbs.appendChild(t);
    }
    card.appendChild(thumbs);

    const info = document.createElement("div");
    info.className = "pack-card-info";
    const title = document.createElement("div");
    title.className = "pack-card-title";
    title.textContent = pack.title || "(без названия)";
    info.appendChild(title);
    const suffix = document.createElement("div");
    suffix.className = "pack-card-suffix";
    suffix.textContent = "by @" + (botUsernameGuess(pack) || "bot");
    info.appendChild(suffix);
    const meta = document.createElement("div");
    meta.className = "pack-card-meta";
    const badge = document.createElement("span");
    badge.className = "pack-card-badge format-" + (pack.format || "emoji");
    badge.textContent = pack.format === "sticker" ? "Стикер" : "Эмодзи";
    meta.appendChild(badge);
    const cnt = document.createElement("span");
    cnt.textContent = `${pack.count}/${pack.cap}`;
    meta.appendChild(cnt);
    info.appendChild(meta);
    card.appendChild(info);

    const chev = document.createElement("div");
    chev.className = "pack-card-chev";
    chev.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
      + '<path d="M9 5 L16 12 L9 19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
      + '</svg>';
    card.appendChild(chev);

    card.addEventListener("click", () => openPack(pack.id));
    return card;
  }

  function botUsernameGuess(pack) {
    // short_name ends with "_by_<bot_username>" by build_pack convention.
    const m = (pack && pack.short_name || "").match(/_by_([A-Za-z0-9_]+)$/);
    return m ? m[1] : "";
  }

  // Lazy-render the 4 mini-thumbs on each pack card. Tile is rendered with
  // pack.global + per_emoji baseline so the user sees how the pack actually
  // looks (nick / logo / colors / transforms) — not the bare master.
  let pctObserver = null;
  function ensurePctObserver() {
    if (pctObserver) return pctObserver;
    pctObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          const el = e.target;
          pctObserver.unobserve(el);
          renderPackCardThumb(el);
        }
      }
    }, { rootMargin: "200px" });
    return pctObserver;
  }

  async function renderPackCardThumb(thumb) {
    if (thumb._pctLoaded) return;
    thumb._pctLoaded = true;
    const pack = thumb._pctPack;
    const id   = thumb._pctId;
    if (!pack || id == null) return;
    try {
      // Make sure the font is ready — applyToLottie needs it for text mode,
      // otherwise it silently keeps swex letters and you see the original.
      try { await SwexReplacer.loadFont("/static/fonts/SeymourOne-Regular.ttf"); }
      catch (_) {}
      const data = await loadLottie(id);
      const container = thumb.querySelector(".pct-anim");
      if (!container) return;
      let animationData;
      if (window.SwexReplacer) {
        try {
          const eff = packTileEffectiveOverride(id, pack);
          animationData = SwexReplacer.applyToLottie(data, eff);
        } catch (e) {
          console.error("[packs] card applyToLottie failed:", e);
          animationData = JSON.parse(JSON.stringify(data));
        }
      } else {
        animationData = JSON.parse(JSON.stringify(data));
      }
      // SVG renderer keeps the nick/logo crisp at thumbnail sizes — canvas
      // rasterises and small glyphs turn into mush.
      const anim = lottie.loadAnimation({
        container,
        renderer: "svg",
        loop: false,
        autoplay: false,
        animationData,
      });
      anim.addEventListener("DOMLoaded", () => {
        anim.goToAndStop(0, true);
        const fb = thumb.querySelector(".pct-fallback");
        if (fb) fb.style.display = "none";
      });
    } catch (e) {
      console.error("[packs] pack card thumb failed for", id, e);
    }
  }

  async function openPack(id) {
    packsState.staged.clear();
    packsState.overrides.clear();
    packsState.editing = null;
    closeTileEdit();  // make sure editor isn't visible if user re-enters
    $("packs-list-view").classList.add("hidden");
    $("pack-detail-view").classList.remove("hidden");
    setPdStatus("Загружаю пак…");
    try {
      const r = await fetch(`/api/packs/${id}`, { headers: apiHeaders() });
      if (!r.ok) throw new Error("http " + r.status);
      const pack = await r.json();
      packsState.current = pack;
      renderPackDetail(pack);
      setPdStatus("");
    } catch (e) {
      setPdStatus("Не удалось загрузить пак: " + e.message);
    }
  }

  function backToPacksList() {
    $("pack-detail-view").classList.add("hidden");
    $("packs-list-view").classList.remove("hidden");
    packsState.current = null;
  }

  $("pd-back").addEventListener("click", backToPacksList);

  function setPdStatus(msg) {
    const el = $("pd-status");
    if (el) el.textContent = msg || "";
  }

  function renderPackDetail(pack) {
    $("pd-title").textContent = pack.title || "(без названия)";
    const suffix = "by @" + (botUsernameGuess(pack) || "bot");
    $("pd-suffix").textContent = suffix;
    $("pd-open").href = pack.link || "#";
    $("pd-format").textContent = pack.format === "sticker" ? "Стикер-пак" : "Эмодзи-пак";
    $("pd-have").textContent = pack.included.length;
    $("pd-cap").textContent  = pack.cap;
    updateStagedChip();
    renderPackGrid(pack);
  }

  function updateStagedChip() {
    const chip = $("pd-stage-chip");
    const cnt  = $("pd-stage-count");
    const cost = $("pd-stage-cost");
    const btn  = $("pd-add");
    const wrap = $("pd-add-wrap");
    const n = packsState.staged.size;
    cnt.textContent = n;
    const perEmoji = (state.pricing && state.pricing.addition_price_rub) || 2;
    const rub = n * perEmoji;
    cost.textContent = rub + "₽";
    chip.classList.toggle("hidden", n === 0);
    btn.disabled = n === 0;
    btn.textContent = n === 0
      ? "Добавить эмодзи"
      : `Добавить ${n} эмодзи · ${rub}₽`;
    // Sticky CTA: show only when there's something staged AND user isn't
    // inside the per-tile mini-editor (which has its own sticky CTA).
    if (wrap) {
      const editing = packsState.editing != null;
      wrap.classList.toggle("hidden", n === 0 || editing);
    }
  }

  // Separate Lottie observer scoped to the pack-detail grid.
  let pdObserver = null;
  function ensurePdObserver() {
    if (pdObserver) return pdObserver;
    pdObserver = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          const cell = e.target;
          pdObserver.unobserve(cell);
          renderPdCellPreview(cell, parseCellId(cell.dataset.id));
        }
      }
    }, { rootMargin: "160px" });
    return pdObserver;
  }

  async function renderPdCellPreview(cell, id) {
    if (cell._pdLoaded) return;
    cell._pdLoaded = true;
    try {
      const data = await loadLottie(id);
      const container = cell.querySelector(".pd-anim");
      if (!container) return;

      // For in-pack and staged tiles, render WITH the pack's customisation
      // (nick / logo / color / transforms) so the user sees what's actually
      // sitting in their Telegram pack, not the bare master tile. Missing
      // tiles render the raw master — CSS desaturates them.
      const pack = packsState.current;
      const includedSet = new Set((pack ? pack.included : []).map(String));
      const inPack = includedSet.has(String(id));
      const staged = packsState.staged.has(String(id));
      let animationData;
      if ((inPack || staged) && window.SwexReplacer) {
        try {
          const eff = packTileEffectiveOverride(id);
          animationData = SwexReplacer.applyToLottie(data, eff);
        } catch (e) {
          console.error("[packs] grid applyToLottie failed for", id, e);
          animationData = JSON.parse(JSON.stringify(data));
        }
      } else {
        animationData = JSON.parse(JSON.stringify(data));
      }

      const anim = lottie.loadAnimation({
        container,
        renderer: "canvas",
        loop: false,
        autoplay: false,
        animationData,
      });
      cell._pdAnim = anim;
      anim.addEventListener("DOMLoaded", () => {
        anim.goToAndStop(0, true);
        const fb = cell.querySelector(".pd-fallback");
        if (fb) fb.style.display = "none";
      });
    } catch (e) {
      console.error("[packs] pd preview failed for", id, e);
    }
  }

  function renderPackGrid(pack) {
    const grid = $("pd-grid");
    grid.innerHTML = "";
    const includedSet = new Set(pack.included.map(String));
    const cap = pack.cap;
    const slack = Math.max(0, cap - pack.included.length);

    // Sort: in-pack first (by original order), then missing.
    const all = allFiles();
    const ordered = [
      ...all.filter(f => includedSet.has(String(f.id))),
      ...all.filter(f => !includedSet.has(String(f.id))),
    ];

    if (slack === 0 && includedSet.size >= all.length) {
      const empty = document.createElement("div");
      empty.className = "pd-grid-empty";
      empty.innerHTML = "<b>Все эмодзи уже в паке.</b><br>Можно только переименовать.";
      grid.appendChild(empty);
      return;
    }

    const obs = ensurePdObserver();
    ordered.forEach(f => {
      const cell = document.createElement("div");
      cell.className = "pd-cell";
      cell.dataset.id = String(f.id);
      const inPack = includedSet.has(String(f.id));
      cell.classList.toggle("in-pack", inPack);
      cell.classList.toggle("missing", !inPack);

      const anim = document.createElement("div");
      anim.className = "pd-anim";
      cell.appendChild(anim);
      const fb = document.createElement("span");
      fb.className = "pd-fallback";
      fb.textContent = f.emoji || "·";
      cell.appendChild(fb);
      const idx = document.createElement("span");
      idx.className = "pd-idx";
      idx.textContent = typeof f.id === "string" ? f.id.replace("new:", "✨") : f.id;
      cell.appendChild(idx);

      if (!inPack) {
        cell.addEventListener("click", () => openTileEdit(f.id));
      }
      if (packsState.staged.has(String(f.id))) cell.classList.add("staged");

      grid.appendChild(cell);
      obs.observe(cell);
    });
  }

  // ── Per-tile mini-editor (inside pack detail) ──────────────
  let pdeAnim = null;
  let pdeRenderToken = 0;
  let pdeKind = "text";              // "text" | "logo"
  let pdeLogoB64 = "";               // base64 SVG for the currently-edited tile

  function setPdeStatus(msg) {
    const el = $("pde-status");
    if (el) el.textContent = msg || "";
  }

  function packTileEffectiveOverride(tileId, packArg) {
    const pack = packArg || packsState.current;
    if (!pack) return {};
    const g = pack.global || {};

    // Saved baseline from the pack's per_emoji (slim form from server).
    const isNewTile = typeof tileId === "string" && tileId.startsWith("new:");
    const baseline = isNewTile
      ? ((pack.new_per_emoji || {})[String(tileId).slice(4)] || {})
      : ((pack.per_emoji     || {})[String(tileId)]          || {});

    // Live override is only relevant for the currently-open pack while the
    // user is staging additions. For pack-card thumbs (other packs) skip it.
    const isCurrent = pack === packsState.current;
    const liveOv = isCurrent ? packsState.overrides.get(String(tileId)) : null;
    const ov = liveOv || baseline || {};

    const f = fileById(tileId);
    const isPassport = !!(f && Array.isArray(f.passport_lines) && f.passport_lines.length);

    // Per-tile kind: explicit ov.kind > implied by ov.logo_svg_b64 > pack global.
    let kind;
    if (ov.kind === "text" || ov.kind === "logo") {
      kind = ov.kind;
    } else if (ov.logo_svg_b64) {
      kind = "logo";
    } else {
      kind = g.kind || "text";
    }

    let text = (ov.text != null && String(ov.text).length > 0)
                  ? String(ov.text) : (g.text || "");
    // Per-tile logo wins; otherwise pack global SVG.
    let logo_b64 = ov.logo_svg_b64 || g.logo_svg_b64 || "";

    if (isPassport) { kind = "text"; text = ""; logo_b64 = ""; }
    return {
      kind, text, logo_svg_b64: logo_b64,
      content_color: normalizeHexColor(ov.content_color || g.content_color || "#ffffff"),
      scale:    ov.scale    != null ? +ov.scale    : 1,
      offset_x: ov.offset_x != null ? +ov.offset_x : 0,
      offset_y: ov.offset_y != null ? +ov.offset_y : 0,
      rotation: ov.rotation != null ? +ov.rotation : 0,
      lines: null,
      photo_svg_b64: "",
      photo_scale: 1.0,
      letter_expand: isNewTile ? 2.5 : 1.0,
    };
  }

  function applyPdeMode(mode) {
    pdeKind = (mode === "logo") ? "logo" : "text";
    document.querySelectorAll(".pde-seg .seg-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.pdeKind === pdeKind);
    });
    $("pde-text-row").classList.toggle("hidden", pdeKind !== "text");
    $("pde-logo-row").classList.toggle("hidden", pdeKind !== "logo");
  }

  async function renderPdePreview(id) {
    const token = ++pdeRenderToken;
    if (pdeAnim) { try { pdeAnim.destroy(); } catch (_) {} pdeAnim = null; }
    const container = $("pde-preview");
    container.innerHTML = "";
    try {
      const original = await loadLottie(id);
      if (token !== pdeRenderToken) return;
      const eff = packTileEffectiveOverride(id);
      let data;
      try { data = SwexReplacer.applyToLottie(original, eff); }
      catch (e) {
        console.error("[packs] applyToLottie failed:", e);
        data = JSON.parse(JSON.stringify(original));
      }
      pdeAnim = lottie.loadAnimation({
        container, renderer: "svg", loop: true, autoplay: true,
        animationData: data,
      });
    } catch (e) {
      container.textContent = "✗ " + e.message;
    }
  }

  let pdeRerenderTimer = null;
  function rerenderPdeSoon() {
    if (pdeRerenderTimer) clearTimeout(pdeRerenderTimer);
    pdeRerenderTimer = setTimeout(() => {
      pdeRerenderTimer = null;
      if (packsState.editing != null) renderPdePreview(packsState.editing);
    }, 90);
  }

  function loadEditorFromOverride(id) {
    const pack = packsState.current;
    const isNew = typeof id === "string" && id.startsWith("new:");
    // Pre-fill priority: live override (if any) → saved baseline → defaults.
    const baseline = isNew
      ? ((pack.new_per_emoji || {})[String(id).slice(4)] || {})
      : ((pack.per_emoji     || {})[String(id)]          || {});
    const liveOv = packsState.overrides.get(String(id));
    const ov = liveOv || baseline;

    const text = ov.text != null ? String(ov.text) : "";
    $("pde-text").value = text.slice(0, 20);
    $("pde-text-counter").textContent = $("pde-text").value.length + "/20";

    const color = normalizeHexColor(
      ov.content_color || (pack.global && pack.global.content_color) || "#ffffff"
    );
    $("pde-color").value = color;
    $("pde-color-val").textContent = color.toUpperCase();

    const rot = Math.round(+(ov.rotation || 0));
    $("pde-rotation").value = rot;
    $("pde-rotation-val").textContent = rot + "°";

    const scalePct = Math.round((+ov.scale || 1) * 100);
    $("pde-scale").value = scalePct;
    $("pde-scale-val").textContent = scalePct + "%";

    const ox = Math.round(+ov.offset_x || 0);
    $("pde-offset-x").value = ox;
    $("pde-offset-x-val").textContent = ox;

    const oy = Math.round(+ov.offset_y || 0);
    $("pde-offset-y").value = oy;
    $("pde-offset-y-val").textContent = oy;

    // Logo state: only live overrides carry the SVG bytes (server doesn't
    // round-trip them). For saved baselines we just have a `has_logo` flag,
    // which means "this tile had a logo before — but to change it you'd
    // upload again". We surface that via the label text.
    pdeLogoB64 = (liveOv && typeof liveOv.logo_svg_b64 === "string") ? liveOv.logo_svg_b64 : "";
    const hasOwnLogo = !!pdeLogoB64;
    const hasBaselineLogo = !liveOv && (baseline.has_logo === true);
    const lbl = $("pde-logo-label");
    if (lbl) {
      if (hasOwnLogo) {
        lbl.textContent = "Лого загружено ✓";
      } else if (hasBaselineLogo) {
        lbl.textContent = "Лого из пака · можно заменить";
      } else {
        lbl.textContent = "Загрузить SVG";
      }
    }
    $("pde-logo-clear").classList.toggle("hidden", !hasOwnLogo);

    // Mode resolution: explicit ov.kind > implied by ov.logo > baseline kind > pack global.
    let mode = ov.kind;
    if (mode !== "text" && mode !== "logo") {
      if (hasOwnLogo || hasBaselineLogo) mode = "logo";
      else mode = (pack.global && pack.global.kind) === "logo" ? "logo" : "text";
    }
    applyPdeMode(mode);
  }

  function readEditorOverride() {
    const ov = {
      scale:         (+$("pde-scale").value) / 100,
      offset_x:      +$("pde-offset-x").value,
      offset_y:      +$("pde-offset-y").value,
      rotation:      +$("pde-rotation").value,
      content_color: normalizeHexColor($("pde-color").value),
      text:          $("pde-text").value.slice(0, 20),
      kind:          pdeKind,
    };
    if (pdeLogoB64) ov.logo_svg_b64 = pdeLogoB64;
    return ov;
  }

  function openTileEdit(id) {
    const pack = packsState.current;
    if (!pack) return;
    packsState.editing = id;

    loadEditorFromOverride(id);

    const f = fileById(id);
    const label = f && f.emoji ? f.emoji + "  " : "";
    $("pde-title").textContent = label + "Эмодзи #" + id;
    const isStaged = packsState.staged.has(String(id));
    $("pde-sub").textContent = isStaged
      ? "Уже в очереди — отредактируй и сохрани"
      : "Настрой как тебе нравится";
    $("pde-remove").classList.toggle("hidden", !isStaged);
    $("pde-save").textContent = isStaged ? "Сохранить" : "Добавить в очередь";
    setPdeStatus("");

    $("pd-grid-wrap").classList.add("hidden");
    $("pd-edit").classList.remove("hidden");
    updateStagedChip();  // hides FAB while editor is open

    // Save user's *live* edits into overrides immediately so the preview
    // reflects them; final stage happens on Save button.
    packsState.overrides.set(String(id), readEditorOverride());
    renderPdePreview(id);

    // Bring the editor's top into view (mini-app scrolls inside Telegram).
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function closeTileEdit() {
    if (!$("pd-edit")) return;
    // If the tile being edited wasn't staged, drop its provisional override
    // so re-opening starts from baseline rather than partial state.
    if (packsState.editing != null
        && !packsState.staged.has(String(packsState.editing))) {
      packsState.overrides.delete(String(packsState.editing));
    }
    packsState.editing = null;
    if (pdeAnim) { try { pdeAnim.destroy(); } catch (_) {} pdeAnim = null; }
    const prev = $("pde-preview");
    if (prev) prev.innerHTML = "";
    $("pd-edit").classList.add("hidden");
    $("pd-grid-wrap").classList.remove("hidden");
    updateStagedChip();  // re-show FAB if there's still anything staged
  }

  function pulseStagedCell(id) {
    const sel = `.pd-cell[data-id="${String(id).replace(/"/g, '\\"')}"]`;
    const cell = $("pd-grid").querySelector(sel);
    if (!cell) return;
    cell.classList.remove("just-staged");
    void cell.offsetWidth;
    cell.classList.add("just-staged");
    setTimeout(() => cell.classList.remove("just-staged"), 500);
  }

  function saveTileEdit() {
    const id = packsState.editing;
    if (id == null) return;
    const pack = packsState.current;
    const slack = Math.max(0, pack.cap - pack.included.length);
    const wasStaged = packsState.staged.has(String(id));
    if (!wasStaged && packsState.staged.size >= slack) {
      setPdeStatus(`Лимит пака: максимум ${pack.cap} эмодзи.`);
      return;
    }
    packsState.staged.add(String(id));
    packsState.overrides.set(String(id), readEditorOverride());
    // Mark as "finalised" — don't let closeTileEdit drop the override.
    const justEdited = id;
    packsState.editing = null;
    closeTileEditCommitted();
    renderPackGrid(pack);
    pulseStagedCell(justEdited);
    updateStagedChip();
  }

  // Like closeTileEdit but preserves override (called after explicit save).
  function closeTileEditCommitted() {
    packsState.editing = null;
    if (pdeAnim) { try { pdeAnim.destroy(); } catch (_) {} pdeAnim = null; }
    const prev = $("pde-preview");
    if (prev) prev.innerHTML = "";
    $("pd-edit").classList.add("hidden");
    $("pd-grid-wrap").classList.remove("hidden");
    updateStagedChip();
  }

  function removeFromStage(id) {
    packsState.staged.delete(String(id));
    packsState.overrides.delete(String(id));
    packsState.editing = null;
    closeTileEditCommitted();
    renderPackGrid(packsState.current);
    updateStagedChip();
  }

  // ── Editor controls (sliders / inputs / buttons) ───────────
  function bindPdeSlider(id, valId, fmt) {
    const sl = $(id), val = $(valId);
    const handler = () => {
      val.textContent = fmt(sl.value);
      const eid = packsState.editing;
      if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
      rerenderPdeSoon();
    };
    sl.addEventListener("input", handler);
    sl.addEventListener("change", handler);
  }
  bindPdeSlider("pde-scale",    "pde-scale-val",    v => v + "%");
  bindPdeSlider("pde-offset-x", "pde-offset-x-val", v => v + "");
  bindPdeSlider("pde-offset-y", "pde-offset-y-val", v => v + "");
  bindPdeSlider("pde-rotation", "pde-rotation-val", v => v + "°");

  $("pde-text").addEventListener("input", () => {
    const v = $("pde-text").value.slice(0, 20);
    $("pde-text-counter").textContent = v.length + "/20";
    const eid = packsState.editing;
    if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
    rerenderPdeSoon();
  });
  $("pde-color").addEventListener("input", () => {
    const c = normalizeHexColor($("pde-color").value);
    $("pde-color").value = c;
    $("pde-color-val").textContent = c.toUpperCase();
    const eid = packsState.editing;
    if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
    rerenderPdeSoon();
  });

  $("pde-back").addEventListener("click", closeTileEdit);
  $("pde-save").addEventListener("click", saveTileEdit);
  $("pde-remove").addEventListener("click", () => {
    if (packsState.editing != null) removeFromStage(packsState.editing);
  });

  // Text/Logo mode toggle
  document.querySelectorAll(".pde-seg .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      applyPdeMode(btn.dataset.pdeKind);
      const eid = packsState.editing;
      if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
      rerenderPdeSoon();
    });
  });

  // SVG upload — read as text, base64-encode for transit + preview.
  $("pde-logo-input").addEventListener("change", async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (f.size > 256 * 1024) {
      setPdeStatus("SVG > 256KB — слишком большой");
      e.target.value = "";
      return;
    }
    let txt;
    try { txt = await f.text(); }
    catch (err) { setPdeStatus("Не получилось прочитать файл"); return; }
    if (!/<svg/i.test(txt)) {
      setPdeStatus("Не похоже на SVG");
      e.target.value = "";
      return;
    }
    pdeLogoB64 = btoa(unescape(encodeURIComponent(txt)));
    $("pde-logo-label").textContent = f.name + " ✓";
    $("pde-logo-clear").classList.remove("hidden");
    setPdeStatus("");
    const eid = packsState.editing;
    if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
    rerenderPdeSoon();
    e.target.value = "";
  });

  $("pde-logo-clear").addEventListener("click", () => {
    pdeLogoB64 = "";
    $("pde-logo-input").value = "";
    $("pde-logo-label").textContent = "Загрузить SVG";
    $("pde-logo-clear").classList.add("hidden");
    const eid = packsState.editing;
    if (eid != null) {
      // Explicit empty string tells the server to wipe a previously-saved
      // per-tile logo (and revert to pack global on logo mode).
      const ov = readEditorOverride();
      ov.logo_svg_b64 = "";
      packsState.overrides.set(String(eid), ov);
    }
    rerenderPdeSoon();
  });
  $("pde-reset").addEventListener("click", () => {
    const pack = packsState.current;
    const color = (pack && pack.global && pack.global.content_color) || "#ffffff";
    $("pde-text").value = "";
    $("pde-text-counter").textContent = "0/20";
    $("pde-color").value = normalizeHexColor(color);
    $("pde-color-val").textContent = $("pde-color").value.toUpperCase();
    $("pde-rotation").value = 0;
    $("pde-rotation-val").textContent = "0°";
    $("pde-scale").value = 100;
    $("pde-scale-val").textContent = "100%";
    $("pde-offset-x").value = 0;
    $("pde-offset-x-val").textContent = "0";
    $("pde-offset-y").value = 0;
    $("pde-offset-y-val").textContent = "0";
    pdeLogoB64 = "";
    $("pde-logo-input").value = "";
    $("pde-logo-label").textContent = "Загрузить SVG";
    $("pde-logo-clear").classList.add("hidden");
    // Fall back to pack global mode.
    const packMode = (pack && pack.global && pack.global.kind) === "logo" ? "logo" : "text";
    applyPdeMode(packMode);
    const eid = packsState.editing;
    if (eid != null) packsState.overrides.set(String(eid), readEditorOverride());
    rerenderPdeSoon();
  });

  // Inline rename
  $("pd-rename").addEventListener("click", () => {
    const titleEl = $("pd-title");
    const inputEl = $("pd-title-input");
    const pack = packsState.current;
    if (!pack) return;
    inputEl.value = pack.title || "";
    titleEl.classList.add("hidden");
    inputEl.classList.remove("hidden");
    inputEl.focus();
    inputEl.select();
  });

  $("pd-title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commitRename(); }
    if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
  });
  $("pd-title-input").addEventListener("blur", () => {
    // Brief delay: lets the click on a button finish first.
    setTimeout(commitRename, 60);
  });

  function cancelRename() {
    $("pd-title-input").classList.add("hidden");
    $("pd-title").classList.remove("hidden");
  }

  async function commitRename() {
    const inputEl = $("pd-title-input");
    if (inputEl.classList.contains("hidden")) return;
    const pack = packsState.current;
    if (!pack) return;
    const newTitle = inputEl.value.trim().slice(0, 40);
    inputEl.classList.add("hidden");
    $("pd-title").classList.remove("hidden");
    if (!newTitle || newTitle === pack.title) return;
    setPdStatus("Переименовываю…");
    try {
      const r = await fetch(`/api/packs/${pack.id}/rename`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ title: newTitle }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || ("http " + r.status));
      pack.title = data.title;
      $("pd-title").textContent = pack.title;
      // Update the list cache so going back shows the new name.
      const lc = packsState.items.find(p => p.id === pack.id);
      if (lc) lc.title = pack.title;
      setPdStatus("Готово ✓");
      setTimeout(() => setPdStatus(""), 1600);
    } catch (e) {
      setPdStatus("Ошибка: " + e.message);
    }
  }

  $("pd-add").addEventListener("click", async () => {
    const pack = packsState.current;
    if (!pack) return;
    if (!packsState.staged.size) return;
    const ids = Array.from(packsState.staged);
    const overrides = {};
    ids.forEach(s => {
      const ov = packsState.overrides.get(s);
      if (ov && Object.keys(ov).length) overrides[s] = ov;
    });
    const btn = $("pd-add");
    btn.disabled = true;
    setPdStatus("Дозаливаю в пак…");
    try {
      const r = await fetch(`/api/packs/${pack.id}/add_tiles`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ tile_ids: ids, overrides }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || ("http " + r.status));
      // Merge added ids into included.
      const addedSet = new Set((data.added || []).map(String));
      const orig = pack.included.map(String);
      ids.forEach(s => { if (addedSet.has(s) && !orig.includes(s)) orig.push(s); });
      pack.included = orig.map(s => /^\d+$/.test(s) ? +s : s);
      // Drop successfully-added from stage + overrides; keep failed ones so
      // user can retry without re-customising.
      [...packsState.staged].forEach(s => {
        if (addedSet.has(s)) {
          packsState.staged.delete(s);
          packsState.overrides.delete(s);
        }
      });
      // Update list cache count.
      const lc = packsState.items.find(p => p.id === pack.id);
      if (lc) { lc.count = pack.included.length; lc.included = pack.included.slice(); }
      // Re-render grid + chip.
      renderPackDetail(pack);
      const failed = (data.errors || []).length;
      if (failed > 0) {
        setPdStatus(`Добавлено ${data.added.length}, не получилось ${failed}.`);
      } else {
        setPdStatus(`Готово ✓ Добавил ${data.added.length}.`);
        setTimeout(() => setPdStatus(""), 2000);
      }
    } catch (e) {
      setPdStatus("Ошибка: " + e.message);
    } finally {
      $("pd-add").disabled = packsState.staged.size === 0;
    }
  });

  // ── Boot ───────────────────────────────────
  applyModeUI("text");
  Promise.all([loadMaster(), loadNew(), loadPricing(), ensureFont()])
    .then(() => {
      // Seed the default selection capped at the emoji-pack hard limit so
      // the user lands inside Telegram's 200-emoji cap on first open (was
      // 243-overflow, forcing 43 manual deselects before they could build).
      state.selected = pickDefaultSelection(EMOJI_LIMIT);
      // Selection bar visible from the start (count + price).
      selectBar.classList.remove("hidden");
      updateSelectionUI();
      rebuildPreviewStrip();
      if (state.masterFiles.length) selectId(state.masterFiles[0].id);
      const newTab = document.querySelector('.tab[data-tab="new"]');
      if (state.newFiles.length && newTab) {
        newTab.classList.add("has-content");
      }
      // Pre-fetch user's packs in background so the topbar badge populates
      // before the user clicks "Мои паки".
      loadPacksIfNeeded().catch(() => {});
    })
    .catch(e => setStatus("Не удалось загрузить эмодзи: " + e.message));
})();
