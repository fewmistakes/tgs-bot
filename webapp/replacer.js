// JS-порт services/swex/replacer.py + services/swex/glyphs.py + services/svg_to_lottie.py.
// Заменяет swex-буквы внутри Lottie JSON на пользовательский текст или SVG-лого
// и оборачивает в gr с tr для per-emoji масштаба/позиции/поворота.
// Используется для живого превью в редакторе.

window.SwexReplacer = (() => {
  const LETTER_NAMES = new Set(["s", "w", "e", "x"]);

  // ─────────────────── Tree walk: containers ───────────────────
  function findSwexContainers(tree) {
    /** Возвращает [{parentList, idxs, bbox}] для каждой группы, в которой
     *  есть siblings с nm in {s,w,e,x}.  parentList — JS-массив (мутируем).  */
    const out = [];
    function visit(node) {
      if (!node || typeof node !== "object") return;
      for (const key of Object.keys(node)) {
        const v = node[key];
        if ((key === "layers" || key === "assets" || key === "shapes" || key === "it")
            && Array.isArray(v)) {
          const indicesByName = {};
          for (let i = 0; i < v.length; i++) {
            const child = v[i];
            if (child && typeof child === "object") {
              const nm = child.nm;
              if (LETTER_NAMES.has(nm) && !(nm in indicesByName)) {
                indicesByName[nm] = i;
              }
            }
          }
          if (Object.keys(indicesByName).length === 4) {
            const idxs = Object.values(indicesByName).sort((a, b) => a - b);
            const bboxes = idxs.map(i => shapeBBox(v[i])).filter(b => b);
            if (bboxes.length) {
              const xmin = Math.min(...bboxes.map(b => b[0]));
              const ymin = Math.min(...bboxes.map(b => b[1]));
              const xmax = Math.max(...bboxes.map(b => b[2]));
              const ymax = Math.max(...bboxes.map(b => b[3]));
              out.push({ parentList: v, idxs, bbox: [xmin, ymin, xmax, ymax] });
            }
          }
          for (const child of v) visit(child);
        }
      }
    }
    visit(tree);
    return out;
  }

  function shapeBBox(node) {
    /** Bbox по всем 'sh'-вершинам внутри узла. */
    const pts = [];
    function visit(n) {
      if (!n || typeof n !== "object") return;
      if (n.ty === "sh") {
        const ks = n.ks && n.ks.k;
        if (ks && Array.isArray(ks.v)) {
          for (const v of ks.v) pts.push([v[0], v[1]]);
        }
      }
      for (const key of Object.keys(n)) {
        if (key === "it" || key === "shapes") {
          const arr = n[key];
          if (Array.isArray(arr)) for (const c of arr) visit(c);
        }
      }
    }
    visit(node);
    if (!pts.length) return null;
    const xs = pts.map(p => p[0]);
    const ys = pts.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  // ─────────────────── Wrap-in-transform ───────────────────
  function hasTransform(t) {
    if (!t) return false;
    return Math.abs((t.scale || 1) - 1) > 1e-6
        || Math.abs(t.offset_x || 0) > 1e-6
        || Math.abs(t.offset_y || 0) > 1e-6
        || Math.abs(t.rotation || 0) > 1e-6;
  }
  function shapesBBox(shapes) {
    const pts = [];
    for (const sh of shapes || []) {
      const k = sh && sh.ks && sh.ks.k;
      if (!k || !Array.isArray(k.v)) continue;
      for (const v of k.v) pts.push([v[0], v[1]]);
    }
    if (!pts.length) return null;
    const xs = pts.map(p => p[0]);
    const ys = pts.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  function wrapInTransform(shapes, t, parentList) {
    const bbox = shapesBBox(shapes);
    const anchor = bbox
      ? [+( (bbox[0] + bbox[2]) / 2 ).toFixed(2), +( (bbox[1] + bbox[3]) / 2 ).toFixed(2)]
      : [0, 0];
    // Copy fill/stroke from parent list to inherit styling
    const fillStroke = [];
    if (parentList && Array.isArray(parentList)) {
      for (const item of parentList) {
        if (item && (item.ty === "fl" || item.ty === "st")) {
          fillStroke.push({ ...item });
        }
      }
    }
    const result = {
      ty: "gr",
      nm: "swex_override",
      it: [
        ...shapes,
        ...fillStroke,
        {
          ty: "tr",
          p: { a: 0, k: [+(t.offset_x || 0), +(t.offset_y || 0)] },
          a: { a: 0, k: anchor },
          s: { a: 0, k: [100 * (t.scale || 1), 100 * (t.scale || 1)] },
          r: { a: 0, k: +(t.rotation || 0) },
          o: { a: 0, k: 100 },
          sk: { a: 0, k: 0 },
          sa: { a: 0, k: 0 },
        },
      ],
    };
    return result;
  }

  // ─────────────────── Glyph rendering (opentype.js) ───────────────────
  function glyphToLottieSubpaths(glyph) {
    /** Строим Lottie-контуры напрямую из opentype commands.
     *  Не используем toPathData(): в WebView он может выдавать path-строки,
     *  которые плохо парсятся и превращают буквы в точки/черточки. */
    const path = glyph.path;
    if (!path || !Array.isArray(path.commands)) return [];
    const subpaths = [];
    let cur = null;
    let first = null;

    function ensure() {
      if (!cur) cur = { v: [], i: [], o: [], c: false };
      return cur;
    }
    function pushPoint(x, y, inTan = [0, 0]) {
      const sp = ensure();
      sp.v.push([x, -y]);
      sp.i.push([inTan[0], -inTan[1]]);
      sp.o.push([0, 0]);
    }
    function closeCurrent() {
      if (!cur) return;
      if (first && cur.v.length >= 2) {
        const last = cur.v[cur.v.length - 1];
        if (Math.abs(last[0] - first[0]) < 1e-6 && Math.abs(last[1] - first[1]) < 1e-6) {
          cur.i[0] = cur.i[cur.i.length - 1];
          cur.v.pop(); cur.i.pop(); cur.o.pop();
        }
      }
      cur.c = true;
      subpaths.push(cur);
      cur = null;
      first = null;
    }

    for (const c of path.commands) {
      if (c.type === "M") {
        if (cur) subpaths.push(cur);
        cur = { v: [], i: [], o: [], c: false };
        first = [c.x, -c.y];
        pushPoint(c.x, c.y);
      } else if (c.type === "L") {
        pushPoint(c.x, c.y);
      } else if (c.type === "C") {
        const sp = ensure();
        if (!sp.v.length) pushPoint(c.x, c.y);
        else {
          const prev = sp.v[sp.v.length - 1];
          sp.o[sp.o.length - 1] = [c.x1 - prev[0], -(c.y1 + prev[1])];
          pushPoint(c.x, c.y, [c.x2 - c.x, c.y2 - c.y]);
        }
      } else if (c.type === "Q") {
        const sp = ensure();
        if (!sp.v.length) pushPoint(c.x, c.y);
        else {
          const prev = sp.v[sp.v.length - 1];
          const prevY = -prev[1];
          const c1x = prev[0] + (2 / 3) * (c.x1 - prev[0]);
          const c1y = prevY + (2 / 3) * (c.y1 - prevY);
          const c2x = c.x + (2 / 3) * (c.x1 - c.x);
          const c2y = c.y + (2 / 3) * (c.y1 - c.y);
          sp.o[sp.o.length - 1] = [c1x - prev[0], -(c1y - prevY)];
          pushPoint(c.x, c.y, [c2x - c.x, c2y - c.y]);
        }
      } else if (c.type === "Z") {
        closeCurrent();
      }
    }
    if (cur) subpaths.push(cur);
    return subpaths;
  }

  function commandsToPathData(cmds) {
    /** Fallback на случай если нужно отладить opentype commands как SVG path. */
    const out = [];
    for (const c of cmds) {
      if (c.type === "M")      out.push(`M${c.x} ${c.y}`);
      else if (c.type === "L") out.push(`L${c.x} ${c.y}`);
      else if (c.type === "C") out.push(`C${c.x1} ${c.y1} ${c.x2} ${c.y2} ${c.x} ${c.y}`);
      else if (c.type === "Q") out.push(`Q${c.x1} ${c.y1} ${c.x} ${c.y}`);
      else if (c.type === "Z") out.push("Z");
    }
    return out.join(" ");
  }

  function layoutText(font, text) {
    /** Раскладывает текст в Lottie-координатах: {glyphs: [{subpaths,bbox,advance}], width, xHeight}. */
    let cursor = 0;
    let allYmin = 0, allYmax = 0;
    const out = [];
    for (const ch of text) {
      const g = font.charToGlyph(ch);
      if (!g || !g.path || !g.path.commands || !g.path.commands.length) {
        cursor += font.unitsPerEm / 4;
        continue;
      }
      const subs = glyphToLottieSubpaths(g);
      // translate by cursor in X (только v, не tangents)
      for (const sp of subs) {
        for (const v of sp.v) v[0] += cursor;
      }
      // bbox этого глифа уже в Y-flipped
      const xs = [], ys = [];
      for (const sp of subs) for (const v of sp.v) { xs.push(v[0]); ys.push(v[1]); }
      const bbox = xs.length
        ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
        : [0, 0, 0, 0];
      if (xs.length) {
        allYmin = Math.min(allYmin, bbox[1]);
        allYmax = Math.max(allYmax, bbox[3]);
      }
      out.push({ subpaths: subs, advance: g.advanceWidth || 0, bbox, char: ch });
      cursor += g.advanceWidth || 0;
    }
    // x_height — для масштаба
    const os2 = font.tables.os2 || {};
    const xHeight = os2.sxHeight || os2.sCapHeight || font.tables.head.unitsPerEm * 0.5;
    return { glyphs: out, width: cursor, xHeight, height: allYmax - allYmin, allYmin, allYmax };
  }

  function renderTextToShapes(font, text, targetBbox) {
    /** Текст → массив Lottie 'sh' shape items, вписанных в targetBbox. */
    if (!text || !text.length || !font) return [];
    const layout = layoutText(font, text);
    if (layout.width <= 0) return [];
    const [tx0, ty0, tx1, ty1] = targetBbox;
    const tw = tx1 - tx0, th = ty1 - ty0;
    const naturalScale = th / layout.xHeight;
    const naturalWidth = layout.width * naturalScale;
    const scale = naturalWidth > tw ? tw / layout.width : naturalScale;
    const finalW = layout.width * scale;
    const offX = tx0 + (tw - finalW) / 2;
    const textH = layout.height * scale;
    const offY = (ty0 + ty1) / 2 + textH / 2 - layout.allYmax * scale;
    const R = 2;
    const shapes = [];
    for (const g of layout.glyphs) {
      for (const sp of g.subpaths) {
        const v = sp.v.map(p => [+(p[0] * scale + offX).toFixed(R), +(p[1] * scale + offY).toFixed(R)]);
        const i = sp.i.map(p => [+(p[0] * scale).toFixed(R), +(p[1] * scale).toFixed(R)]);
        const o = sp.o.map(p => [+(p[0] * scale).toFixed(R), +(p[1] * scale).toFixed(R)]);
        shapes.push({
          ty: "sh",
          ks: { a: 0, k: { v, i, o, c: !!sp.c } },
        });
      }
    }
    return shapes;
  }

  // ─────────────────── SVG → Lottie shapes ───────────────────
  function parseSvgPathD(d) {
    /** Простой парсер SVG path d → массив команд {type, args[]} абсолютных координат.
     *  Поддерживает M/m, L/l, H/h, V/v, C/c, S/s, Q/q, T/t, A/a, Z/z. */
    const re = /([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)/g;
    const cmds = [];
    let m;
    let cx = 0, cy = 0;        // current point
    let startX = 0, startY = 0;
    let lastCtrl = null, lastQCtrl = null;
    let prevType = null;
    while ((m = re.exec(d)) !== null) {
      const type = m[1];
      const argsStr = m[2].trim();
      const args = argsStr.length
        ? (argsStr.match(/[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?/g) || [])
            .map(Number)
            .filter(n => !Number.isNaN(n))
        : [];
      const upper = type.toUpperCase();
      const rel = type !== upper;

      // Z
      if (upper === "Z") {
        cmds.push({ type: "Z" });
        cx = startX; cy = startY;
        prevType = "Z"; lastCtrl = null; lastQCtrl = null;
        continue;
      }

      let i = 0;
      // M — first pair is moveto, rest is implicit lineto
      if (upper === "M") {
        let first = true;
        while (i + 1 < args.length) {
          let x = args[i], y = args[i+1]; i += 2;
          if (rel) { x += cx; y += cy; }
          if (first) {
            cmds.push({ type: "M", x, y });
            startX = x; startY = y;
            first = false;
          } else {
            cmds.push({ type: "L", x, y });
          }
          cx = x; cy = y;
        }
      } else if (upper === "L") {
        while (i + 1 < args.length) {
          let x = args[i], y = args[i+1]; i += 2;
          if (rel) { x += cx; y += cy; }
          cmds.push({ type: "L", x, y });
          cx = x; cy = y;
        }
      } else if (upper === "H") {
        while (i < args.length) {
          let x = args[i++]; if (rel) x += cx;
          cmds.push({ type: "L", x, y: cy });
          cx = x;
        }
      } else if (upper === "V") {
        while (i < args.length) {
          let y = args[i++]; if (rel) y += cy;
          cmds.push({ type: "L", x: cx, y });
          cy = y;
        }
      } else if (upper === "C") {
        while (i + 5 < args.length) {
          let x1=args[i],y1=args[i+1],x2=args[i+2],y2=args[i+3],x=args[i+4],y=args[i+5]; i += 6;
          if (rel) { x1+=cx;y1+=cy;x2+=cx;y2+=cy;x+=cx;y+=cy; }
          cmds.push({ type: "C", x1, y1, x2, y2, x, y });
          lastCtrl = [x2, y2]; cx = x; cy = y; lastQCtrl = null;
        }
      } else if (upper === "S") {
        while (i + 3 < args.length) {
          let x2=args[i],y2=args[i+1],x=args[i+2],y=args[i+3]; i += 4;
          if (rel) { x2+=cx;y2+=cy;x+=cx;y+=cy; }
          let x1, y1;
          if (lastCtrl && (prevType === "C" || prevType === "S")) {
            x1 = 2*cx - lastCtrl[0]; y1 = 2*cy - lastCtrl[1];
          } else { x1 = cx; y1 = cy; }
          cmds.push({ type: "C", x1, y1, x2, y2, x, y });
          lastCtrl = [x2, y2]; cx = x; cy = y;
        }
      } else if (upper === "Q") {
        while (i + 3 < args.length) {
          let x1=args[i],y1=args[i+1],x=args[i+2],y=args[i+3]; i += 4;
          if (rel) { x1+=cx;y1+=cy;x+=cx;y+=cy; }
          cmds.push({ type: "Q", x1, y1, x, y });
          lastQCtrl = [x1, y1]; cx = x; cy = y; lastCtrl = null;
        }
      } else if (upper === "T") {
        while (i + 1 < args.length) {
          let x=args[i],y=args[i+1]; i += 2;
          if (rel) { x+=cx;y+=cy; }
          let x1, y1;
          if (lastQCtrl && (prevType === "Q" || prevType === "T")) {
            x1 = 2*cx - lastQCtrl[0]; y1 = 2*cy - lastQCtrl[1];
          } else { x1 = cx; y1 = cy; }
          cmds.push({ type: "Q", x1, y1, x, y });
          lastQCtrl = [x1, y1]; cx = x; cy = y;
        }
      } else if (upper === "A") {
        // Arc — конвертим в линию (упрощённо для превью).
        while (i + 6 < args.length) {
          i += 5;
          let x = args[i], y = args[i+1]; i += 2;
          if (rel) { x += cx; y += cy; }
          cmds.push({ type: "L", x, y });
          cx = x; cy = y;
        }
      }
      prevType = upper;
    }
    return cmds;
  }

  function svgPathToLottieSubpaths(d) {
    /** Парсим path d → конвертим в Lottie sh (без Y-flip — SVG и Lottie оба Y-down). */
    const cmds = parseSvgPathD(d);
    const subpaths = [];
    let cur = null;
    let firstPt = null;
    for (const c of cmds) {
      if (c.type === "M") {
        if (cur) subpaths.push(cur);
        cur = { v: [], i: [], o: [], c: false };
        firstPt = [c.x, c.y];
        cur.v.push([c.x, c.y]);
        cur.i.push([0, 0]); cur.o.push([0, 0]);
      } else if (c.type === "L") {
        if (!cur) continue;
        cur.v.push([c.x, c.y]);
        cur.i.push([0, 0]); cur.o.push([0, 0]);
      } else if (c.type === "C") {
        if (!cur) continue;
        const prev = cur.v[cur.v.length - 1];
        cur.o[cur.o.length - 1] = [c.x1 - prev[0], c.y1 - prev[1]];
        cur.v.push([c.x, c.y]);
        cur.i.push([c.x2 - c.x, c.y2 - c.y]);
        cur.o.push([0, 0]);
      } else if (c.type === "Q") {
        if (!cur) continue;
        const prev = cur.v[cur.v.length - 1];
        const c1 = [prev[0] + 2/3 * (c.x1 - prev[0]), prev[1] + 2/3 * (c.y1 - prev[1])];
        const c2 = [c.x + 2/3 * (c.x1 - c.x), c.y + 2/3 * (c.y1 - c.y)];
        cur.o[cur.o.length - 1] = [c1[0] - prev[0], c1[1] - prev[1]];
        cur.v.push([c.x, c.y]);
        cur.i.push([c2[0] - c.x, c2[1] - c.y]);
        cur.o.push([0, 0]);
      } else if (c.type === "Z") {
        if (!cur) continue;
        const last = cur.v[cur.v.length - 1];
        if (firstPt && cur.v.length >= 2 && last[0] === firstPt[0] && last[1] === firstPt[1]) {
          cur.i[0] = cur.i[cur.i.length - 1];
          cur.v.pop(); cur.i.pop(); cur.o.pop();
        }
        cur.c = true;
        subpaths.push(cur); cur = null;
      }
    }
    if (cur) { cur.c = false; subpaths.push(cur); }
    return subpaths;
  }

  // ── SVG helpers ──────────────────────────
  function parseSvgNumber(v, def) {
    const n = parseFloat(v);
    return Number.isNaN(n) ? def : n;
  }
  function rectToPath(x, y, w, h, rx, ry) {
    x = parseSvgNumber(x, 0); y = parseSvgNumber(y, 0);
    w = parseSvgNumber(w, 0); h = parseSvgNumber(h, 0);
    rx = parseSvgNumber(rx, 0); ry = parseSvgNumber(ry, rx || 0);
    if (!w || !h) return "";
    if (rx <= 0 && ry <= 0) {
      return `M${x} ${y}h${w}v${h}h${-w}Z`;
    }
    const r = Math.min(rx, w / 2);
    const r2 = Math.min(ry, h / 2);
    return `M${x + r} ${y}`
      + `h${w - 2 * r}a${r} ${r2} 0 0 1 ${r} ${r2}v${h - 2 * r2}`
      + `a${r} ${r2} 0 0 1 ${-r} ${r2}h${-(w - 2 * r)}`
      + `a${r} ${r2} 0 0 1 ${-r} ${-r2}v${-(h - 2 * r2)}`
      + `a${r} ${r2} 0 0 1 ${r} ${-r2}Z`;
  }
  function circleToPath(cx, cy, r) {
    cx = parseSvgNumber(cx, 0); cy = parseSvgNumber(cy, 0); r = parseSvgNumber(r, 0);
    if (!r) return "";
    return `M${cx} ${cy - r}a${r} ${r} 0 1 0 0 ${2 * r}a${r} ${r} 0 1 0 0 ${-2 * r}Z`;
  }
  function ellipseToPath(cx, cy, rx, ry) {
    cx = parseSvgNumber(cx, 0); cy = parseSvgNumber(cy, 0);
    rx = parseSvgNumber(rx, 0); ry = parseSvgNumber(ry, 0);
    if (!rx || !ry) return "";
    return `M${cx} ${cy - ry}a${rx} ${ry} 0 1 0 0 ${2 * ry}a${rx} ${ry} 0 1 0 0 ${-2 * ry}Z`;
  }
  function lineToPath(x1, y1, x2, y2) {
    x1 = parseSvgNumber(x1, 0); y1 = parseSvgNumber(y1, 0);
    x2 = parseSvgNumber(x2, 0); y2 = parseSvgNumber(y2, 0);
    return `M${x1} ${y1}L${x2} ${y2}`;
  }
  function polyToPath(points, close) {
    const pts = (points || "").trim().split(/[\s,]+/).map(parseFloat).filter(n => !Number.isNaN(n));
    if (pts.length < 4) return "";
    let d = `M${pts[0]} ${pts[1]}`;
    for (let i = 2; i < pts.length; i += 2) d += `L${pts[i]} ${pts[i + 1]}`;
    if (close) d += "Z";
    return d;
  }

  function identityMatrix() {
    return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
  }
  function parseTransformArgs(raw) {
    return String(raw || "")
      .trim()
      .split(/[\s,]+/)
      .map(parseFloat)
      .filter(n => !Number.isNaN(n));
  }
  function parseTransformMatrix(el) {
    /** Возвращает {a,b,c,d,e,f} матрицу трансформации (по умолчанию identity). */
    let m = identityMatrix();
    const t = (el.getAttribute && el.getAttribute("transform")) || "";
    if (!t) return m;
    const ops = String(t).match(/[a-zA-Z]+\([^)]*\)/g) || [];
    for (const op of ops) {
      const mm = op.match(/^([a-zA-Z]+)\(([^)]*)\)$/);
      if (!mm) continue;
      const kind = mm[1].toLowerCase();
      const args = parseTransformArgs(mm[2]);
      let cur = identityMatrix();
      if (kind === "matrix" && args.length >= 6) {
        cur = { a: args[0], b: args[1], c: args[2], d: args[3], e: args[4], f: args[5] };
      } else if (kind === "translate" && args.length >= 1) {
        cur.e = args[0];
        cur.f = args.length >= 2 ? args[1] : 0;
      } else if (kind === "scale" && args.length >= 1) {
        cur.a = args[0];
        cur.d = args.length >= 2 ? args[1] : args[0];
      } else if (kind === "rotate" && args.length >= 1) {
        const ang = args[0] * Math.PI / 180;
        const cos = Math.cos(ang), sin = Math.sin(ang);
        cur = { a: cos, b: sin, c: -sin, d: cos, e: 0, f: 0 };
        if (args.length >= 3) {
          const cx = args[1], cy = args[2];
          cur.e = cx - cx * cos + cy * sin;
          cur.f = cy - cx * sin - cy * cos;
        }
      } else if (kind === "skewx" && args.length >= 1) {
        cur.c = Math.tan(args[0] * Math.PI / 180);
      } else if (kind === "skewy" && args.length >= 1) {
        cur.b = Math.tan(args[0] * Math.PI / 180);
      } else {
        continue;
      }
      m = multiplyMatrix(m, cur);
    }
    return m;
  }
  function multiplyMatrix(a, b) {
    return {
      a: a.a * b.a + a.c * b.b,
      b: a.b * b.a + a.d * b.b,
      c: a.a * b.c + a.c * b.d,
      d: a.b * b.c + a.d * b.d,
      e: a.a * b.e + a.c * b.f + a.e,
      f: a.b * b.e + a.d * b.f + a.f,
    };
  }
  function applyMatrixToSubpaths(subpaths, m) {
    if (!m || (m.a === 1 && m.b === 0 && m.c === 0 && m.d === 1 && m.e === 0 && m.f === 0)) return;
    for (const sp of subpaths) {
      for (const pt of sp.v) {
        const x = pt[0], y = pt[1];
        pt[0] = +(m.a * x + m.c * y + m.e).toFixed(3);
        pt[1] = +(m.b * x + m.d * y + m.f).toFixed(3);
      }
      for (const arr of [sp.i, sp.o]) {
        for (const pt of arr) {
          const x = pt[0], y = pt[1];
          pt[0] = +(m.a * x + m.c * y).toFixed(3);
          pt[1] = +(m.b * x + m.d * y).toFixed(3);
        }
      }
    }
  }

  function svgToLottieShapes(svgText) {
    /** SVG-text → {shapes: [Lottie sh...], bbox: [xmin,ymin,xmax,ymax]}.
     *  Учитывает viewBox, width/height, transform и базовые shape-элементы. */
    const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
    const root = doc.documentElement;

    // ViewBox scaling
    const vb = (root.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(parseFloat).filter(n => !Number.isNaN(n));
    let scaleX = 1, scaleY = 1, offX = 0, offY = 0;
    if (vb.length === 4) {
      const [, , vbW, vbH] = vb;
      const w = parseSvgNumber(root.getAttribute("width"), vbW);
      const h = parseSvgNumber(root.getAttribute("height"), vbH);
      if (vbW > 0 && w > 0) scaleX = w / vbW;
      if (vbH > 0 && h > 0) scaleY = h / vbH;
      offX = vb[0];
      offY = vb[1];
    }
    const svgMatrix = { a: scaleX, b: 0, c: 0, d: scaleY, e: -offX * scaleX, f: -offY * scaleY };

    const subpaths = [];
    const allPts = [];

    function walk(el, parentMatrix) {
      if (!el || el.nodeType !== 1) return;
      const tag = (el.tagName || "").toLowerCase();
      let d = "";
      if (tag === "path") {
        d = el.getAttribute("d") || "";
      } else if (tag === "rect") {
        d = rectToPath(el.getAttribute("x"), el.getAttribute("y"),
                       el.getAttribute("width"), el.getAttribute("height"),
                       el.getAttribute("rx"), el.getAttribute("ry"));
      } else if (tag === "circle") {
        d = circleToPath(el.getAttribute("cx"), el.getAttribute("cy"), el.getAttribute("r"));
      } else if (tag === "ellipse") {
        d = ellipseToPath(el.getAttribute("cx"), el.getAttribute("cy"),
                          el.getAttribute("rx"), el.getAttribute("ry"));
      } else if (tag === "line") {
        d = lineToPath(el.getAttribute("x1"), el.getAttribute("y1"),
                       el.getAttribute("x2"), el.getAttribute("y2"));
      } else if (tag === "polyline") {
        d = polyToPath(el.getAttribute("points"), false);
      } else if (tag === "polygon") {
        d = polyToPath(el.getAttribute("points"), true);
      }

      if (d) {
        const sps = svgPathToLottieSubpaths(d);
        const localMatrix = multiplyMatrix(parentMatrix, parseTransformMatrix(el));
        applyMatrixToSubpaths(sps, localMatrix);
        for (const sp of sps) {
          subpaths.push(sp);
          for (const v of sp.v) allPts.push(v);
        }
      }

      for (const child of el.children || []) walk(child, multiplyMatrix(parentMatrix, parseTransformMatrix(el)));
    }
    walk(root, svgMatrix);

    let bbox;
    if (allPts.length) {
      const xs = allPts.map(p => p[0]);
      const ys = allPts.map(p => p[1]);
      bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    } else {
      bbox = [0, 0, 1, 1];
    }
    const shapes = subpaths.map(sp => ({
      ty: "sh",
      ks: { a: 0, k: { v: sp.v, i: sp.i, o: sp.o, c: !!sp.c } },
    }));
    return { shapes, bbox };
  }

  function transformShapes(shapes, t, bbox) {
    /** Применяет rotation, scale и offset напрямую к координатам vertices.
     *  Избегает проблем с anchor point в Lottie transform-группе. */
    const cx = (bbox[0] + bbox[2]) / 2;
    const cy = (bbox[1] + bbox[3]) / 2;
    const scale = t.scale || 1;
    const rot = (t.rotation || 0) * Math.PI / 180;
    const offsetX = t.offset_x || 0;
    const offsetY = t.offset_y || 0;
    const cos = Math.cos(rot);
    const sin = Math.sin(rot);
    const R = 2;
    return shapes.map(sh => {
      const k = sh.ks && sh.ks.k;
      if (!k || !Array.isArray(k.v)) return sh;
      const newV = k.v.map(v => {
        let x = (v[0] - cx) * scale;
        let y = (v[1] - cy) * scale;
        const xr = x * cos - y * sin;
        const yr = x * sin + y * cos;
        return [+(xr + cx + offsetX).toFixed(R), +(yr + cy + offsetY).toFixed(R)];
      });
      const tx = (pt) => {
        let x = pt[0] * scale;
        let y = pt[1] * scale;
        const xr = x * cos - y * sin;
        const yr = x * sin + y * cos;
        return [+xr.toFixed(R), +yr.toFixed(R)];
      };
      return {
        ty: "sh",
        ks: { a: 0, k: { v: newV, i: k.i.map(tx), o: k.o.map(tx), c: !!k.c } },
      };
    });
  }

  function placeLogoInBox(logoShapes, logoBbox, centerLocal, targetW, targetH, fitRatio) {
    /** Скейлит и центрирует SVG-лого в локальной системе координат группы. */
    const [lxmin, lymin, lxmax, lymax] = logoBbox;
    const lw = Math.max(1e-3, lxmax - lxmin);
    const lh = Math.max(1e-3, lymax - lymin);
    const sx = (targetW * fitRatio) / lw;
    const sy = (targetH * fitRatio) / lh;
    const s = Math.min(sx, sy);
    const finalW = lw * s, finalH = lh * s;
    const offX = centerLocal[0] - finalW / 2 - lxmin * s;
    const offY = centerLocal[1] - finalH / 2 - lymin * s;
    const R = 2;
    const out = [];
    for (const sh of logoShapes) {
      const k = sh.ks.k;
      out.push({
        ty: "sh",
        ks: { a: 0, k: {
          v: k.v.map(v => [+(v[0]*s + offX).toFixed(R), +(v[1]*s + offY).toFixed(R)]),
          i: k.i.map(t => [+(t[0]*s).toFixed(R), +(t[1]*s).toFixed(R)]),
          o: k.o.map(t => [+(t[0]*s).toFixed(R), +(t[1]*s).toFixed(R)]),
          c: !!k.c,
        }},
      });
    }
    return out;
  }

  function hexToLottieColor(hex) {
    const s = String(hex || "").trim();
    const m = s.match(/^#([0-9a-fA-F]{6})$/);
    if (!m) return { a: 0, k: [1, 1, 1, 1] };
    const raw = m[1];
    return {
      a: 0,
      k: [
        parseInt(raw.slice(0, 2), 16) / 255,
        parseInt(raw.slice(2, 4), 16) / 255,
        parseInt(raw.slice(4, 6), 16) / 255,
        1,
      ],
    };
  }

  function applyColorToNode(node, color) {
    /** Рекурсивно применяет цвет ко всем fill (ty=fl) и stroke (ty=st) в узле. */
    if (!node || typeof node !== "object") return;
    if (node.ty === "fl" && node.c) node.c = color;
    if (node.ty === "st" && node.c) node.c = color;
    if (Array.isArray(node.it)) for (const child of node.it) applyColorToNode(child, color);
    if (Array.isArray(node.shapes)) for (const child of node.shapes) applyColorToNode(child, color);
  }

  // ─────────────────── swex_text_* line overrides (passport) ───────────────────
  function findSwexTextGroups(tree) {
    /** Возвращает [{parent, idx, gr, nm}] для каждого gr с nm='swex_text_*'. */
    const out = [];
    function visit(node) {
      if (Array.isArray(node)) { node.forEach(visit); return; }
      if (!node || typeof node !== "object") return;
      for (const k of ["layers", "assets", "shapes", "it"]) {
        const v = node[k];
        if (Array.isArray(v)) {
          v.forEach((c, i) => {
            if (c && typeof c === "object" && c.ty === "gr"
                && typeof c.nm === "string"
                && c.nm.startsWith("swex_text_")) {
              out.push({ parent: v, idx: i, gr: c, nm: c.nm });
            }
          });
          v.forEach(visit);
        }
      }
    }
    visit(tree);
    return out;
  }

  function grTextBBox(gr) {
    const pts = [];
    function visit(node) {
      if (Array.isArray(node)) { node.forEach(visit); return; }
      if (!node || typeof node !== "object") return;
      if (node.ty === "sh") {
        const k = node.ks && node.ks.k;
        if (k && Array.isArray(k.v)) for (const v of k.v) pts.push([v[0], v[1]]);
      }
      for (const kk of ["it", "shapes"]) {
        if (Array.isArray(node[kk])) for (const c of node[kk]) visit(c);
      }
    }
    visit(gr);
    if (!pts.length) return null;
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  function shapesLeftmostX(shapes) {
    let out = null;
    for (const sh of shapes) {
      const k = sh.ks && sh.ks.k;
      if (!k || !Array.isArray(k.v)) continue;
      for (const v of k.v) {
        const x = +v[0];
        out = (out === null) ? x : Math.min(out, x);
      }
    }
    return out;
  }
  function shiftShapesX(shapes, dx) {
    if (Math.abs(dx) < 1e-6) return;
    for (const sh of shapes) {
      const k = sh.ks && sh.ks.k;
      if (!k || !Array.isArray(k.v)) continue;
      for (const v of k.v) v[0] += dx;
    }
  }

  function applyLinesOverride(tree, lines, font, contentColor) {
    if (!Array.isArray(lines) || !lines.length || !font) return;
    const lineMap = {};
    const styles = {};
    for (const l of lines) {
      if (!l || !l.name) continue;
      if (typeof l.text === "string" && l.text.trim() !== "") {
        lineMap[l.name] = l.text;
      }
      if (typeof l.scale === "number" || typeof l.color === "string"
          || typeof l.offset_x === "number" || typeof l.offset_y === "number") {
        styles[l.name] = {
          scale:    (typeof l.scale === "number") ? l.scale : 1,
          color:    (typeof l.color === "string") ? l.color : null,
          offset_x: (typeof l.offset_x === "number") ? l.offset_x : 0,
          offset_y: (typeof l.offset_y === "number") ? l.offset_y : 0,
        };
      }
    }
    if (!Object.keys(lineMap).length) return;
    const lineStyle = (nm) => styles[nm] || null;
    const groups = findSwexTextGroups(tree);
    if (!groups.length) return;
    const rows = [];
    for (const g of groups) {
      const bbox = grTextBBox(g.gr);
      if (bbox) rows.push({ ...g, bbox });
    }
    if (!rows.length) return;
    // Anchor each row to its OWN local bbox[0] — different rows have
    // different lefts that the designer tuned to align visually on
    // canvas given their layer transforms. Per-line offset_x slider is
    // the user's manual override.
    const xHeight = (font.tables && font.tables.os2 && font.tables.os2.sxHeight)
                    || (font.tables && font.tables.head && font.tables.head.unitsPerEm * 0.5)
                    || 500;
    for (const r of rows) {
      const newText = lineMap[r.nm];
      if (newText == null) continue;
      const bb = r.bbox;
      const cy = (bb[1] + bb[3]) / 2;
      const rowLeft = bb[0];
      const bakedText = r.nm.replace(/^swex_text_/, "");
      const lb = layoutText(font, bakedText);
      const bakedH = (lb && lb.height > 0) ? lb.height : xHeight;
      const bakedScale = (bb[3] - bb[1]) / Math.max(1, bakedH);
      const targetH = bakedScale * xHeight;
      const target = [-600, cy - targetH / 2, 600, cy + targetH / 2];
      const shapes = renderTextToShapes(font, newText, target);
      if (!shapes.length) continue;
      const curLeft = shapesLeftmostX(shapes);
      if (curLeft !== null) shiftShapesX(shapes, rowLeft - curLeft);
      // Per-line scale (anchored on rowLeft, cy of the row).
      const line = (typeof lineStyle === "function") ? lineStyle(r.nm) : null;
      const perScale = (line && typeof line.scale === "number") ? line.scale : 1;
      if (Math.abs(perScale - 1) > 1e-6) {
        for (const sh of shapes) {
          const k = sh.ks && sh.ks.k;
          if (!k) continue;
          if (Array.isArray(k.v)) {
            for (const v of k.v) {
              v[0] = rowLeft + (v[0] - rowLeft) * perScale;
              v[1] = cy + (v[1] - cy) * perScale;
            }
          }
          for (const arrName of ["i", "o"]) {
            if (Array.isArray(k[arrName])) for (const t of k[arrName]) {
              t[0] *= perScale; t[1] *= perScale;
            }
          }
        }
      }
      // Per-line position offset (translates every vertex; tangents are
      // deltas so they're unchanged).
      const perOx = (line && typeof line.offset_x === "number") ? line.offset_x : 0;
      const perOy = (line && typeof line.offset_y === "number") ? line.offset_y : 0;
      if (Math.abs(perOx) > 1e-6 || Math.abs(perOy) > 1e-6) {
        for (const sh of shapes) {
          const k = sh.ks && sh.ks.k;
          if (!k || !Array.isArray(k.v)) continue;
          for (const v of k.v) { v[0] += perOx; v[1] += perOy; }
        }
      }
      const lineColor = (line && typeof line.color === "string")
        ? hexToLottieColor(line.color)
        : contentColor;
      r.parent[r.idx] = {
        ty: "gr",
        nm: r.nm,
        it: [
          ...shapes,
          { ty: "fl", c: lineColor, o: { a: 0, k: 100 }, r: 1, bm: 0, nm: "Fill" },
          { ty: "tr", p: { a: 0, k: [0, 0] }, a: { a: 0, k: [0, 0] },
            s: { a: 0, k: [100, 100] }, r: { a: 0, k: 0 },
            o: { a: 0, k: 100 }, sk: { a: 0, k: 0 }, sa: { a: 0, k: 0 } },
        ],
      };
    }
  }

  function findQmarkGroups(tree) {
    const out = [];
    function visit(node) {
      if (Array.isArray(node)) { node.forEach(visit); return; }
      if (!node || typeof node !== "object") return;
      for (const k of ["layers", "assets", "shapes", "it"]) {
        const v = node[k];
        if (Array.isArray(v)) {
          v.forEach((c, i) => {
            if (c && c.ty === "gr" && c.nm === "swex_qmark") {
              out.push({ parent: v, idx: i, gr: c });
            }
          });
          v.forEach(visit);
        }
      }
    }
    visit(tree);
    return out;
  }

  function applyQmarkCleanup(tree, font, photoSvgB64, photoScale) {
    const groups = findQmarkGroups(tree);
    if (!groups.length) return;
    // SVG-photo path: parse once, fit inside every qmark group's bbox.
    let svgData = null;
    if (photoSvgB64) {
      try {
        const svgText = decodeURIComponent(escape(atob(photoSvgB64)));
        const parsed = svgToLottieShapes(svgText);
        if (parsed && parsed.shapes && parsed.shapes.length) svgData = parsed;
      } catch (_) { svgData = null; }
    }
    if (!font && !svgData) return;
    const scale = Math.max(0.3, Math.min(3.0, +photoScale || 1.0));
    for (const g of groups) {
      const bb = grTextBBox(g.gr);
      if (!bb) continue;
      const cx = (bb[0] + bb[2]) / 2;
      const cy = (bb[1] + bb[3]) / 2;
      if (svgData) {
        const size = Math.max(bb[2] - bb[0], bb[3] - bb[1]) * 1.05 * scale;
        const shapes = placeLogoInBox(svgData.shapes, svgData.bbox,
                                      [cx, cy], size, size, 0.9);
        const extras = [];
        for (const it of g.gr.it || []) {
          if (it && it.ty === "tr") extras.push(it);
        }
        if (!extras.length) {
          extras.push({ ty: "tr", p: { a: 0, k: [0, 0] }, a: { a: 0, k: [0, 0] },
                        s: { a: 0, k: [100, 100] }, r: { a: 0, k: 0 },
                        o: { a: 0, k: 100 }, sk: { a: 0, k: 0 }, sa: { a: 0, k: 0 } });
        }
        g.parent[g.idx] = { ty: "gr", nm: "swex_qmark", it: [
          ...shapes,
          { ty: "fl", c: { a: 0, k: [1, 1, 1, 1] }, o: { a: 0, k: 100 }, r: 1, bm: 0, nm: "Fill" },
          ...extras,
        ]};
        continue;
      }
      const size = Math.max(bb[2] - bb[0], bb[3] - bb[1]) * 1.35;
      const target = [cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2];
      const shapes = renderTextToShapes(font, "?", target);
      if (!shapes.length) continue;
      // Recentre — the font's '?' isn't symmetric.
      const pts = [];
      for (const sh of shapes) {
        const k = sh.ks && sh.ks.k;
        if (k && Array.isArray(k.v)) for (const v of k.v) pts.push(v);
      }
      if (pts.length) {
        const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
        const newCx = (Math.min(...xs) + Math.max(...xs)) / 2;
        const newCy = (Math.min(...ys) + Math.max(...ys)) / 2;
        const dx = cx - newCx, dy = cy - newCy;
        if (Math.abs(dx) > 1e-3 || Math.abs(dy) > 1e-3) {
          for (const sh of shapes) {
            const k = sh.ks && sh.ks.k;
            if (k && Array.isArray(k.v)) for (const v of k.v) { v[0] += dx; v[1] += dy; }
          }
        }
      }
      // Keep existing fl/st/tr items (white fill, identity tr).
      const extras = [];
      for (const it of g.gr.it || []) {
        if (it && (it.ty === "fl" || it.ty === "st" || it.ty === "tr")) extras.push(it);
      }
      if (!extras.some(e => e.ty === "fl")) {
        extras.push({ ty: "fl", c: { a: 0, k: [1, 1, 1, 1] },
                      o: { a: 0, k: 100 }, r: 1, bm: 0, nm: "Fill" });
      }
      if (!extras.some(e => e.ty === "tr")) {
        extras.push({ ty: "tr", p: { a: 0, k: [0, 0] }, a: { a: 0, k: [0, 0] },
                      s: { a: 0, k: [100, 100] }, r: { a: 0, k: 0 },
                      o: { a: 0, k: 100 }, sk: { a: 0, k: 0 }, sa: { a: 0, k: 0 } });
      }
      g.parent[g.idx] = { ty: "gr", nm: "swex_qmark", it: [...shapes, ...extras] };
    }
  }

  // ─────────────────── Apply override to Lottie tree ───────────────────
  function applyOverride(lottieJson, override, font) {
    /** Мутирует JSON: удаляет swex-буквы, вставляет текст или лого с per-emoji
     *  трансформом. Если override пустой/нет нужных данных — оставляет как есть.  */
    if (!override) return lottieJson;
    // Apply per-line text edits first (passport-style tiles); their groups
    // are independent of the swex letter containers so it's safe either way.
    if (Array.isArray(override.lines) && override.lines.length) {
      const c = hexToLottieColor(override.content_color || "#000000");
      // passport secondary lines are painted black on the ID card, so default
      // to a black-ish fill unless the user explicitly recolours via the
      // global content_color slider (then we honour it).
      const useBlack = !override.content_color || override.content_color.toLowerCase() === "#ffffff";
      applyLinesOverride(lottieJson, override.lines, font,
                         useBlack ? { a: 0, k: [0, 0, 0, 1] } : c);
    }
    // Always re-render and re-centre the passport '?' (it ships off-centre
    // out of the SeymourOne font glyph). If the user uploaded an SVG photo,
    // fit it into the placeholder instead of the glyph.
    applyQmarkCleanup(lottieJson, font, override.photo_svg_b64 || null, override.photo_scale);
    const containers = findSwexContainers(lottieJson);
    if (!containers.length) return lottieJson;

    let logoData = null;
    if (override.kind === "logo" && override.logo_svg_b64) {
      try {
        const svgText = decodeURIComponent(escape(atob(override.logo_svg_b64)));
        logoData = svgToLottieShapes(svgText);
        if (!logoData.shapes.length) logoData = null;
      } catch (e) { logoData = null; }
    }

    const useLogo = !!logoData;
    const useText = !useLogo && override.text && override.text.trim() !== "";
    const useEmpty = !useLogo && !useText; // нет текста/лого — работаем с оригинальными swex-буквами

    const t = {
      scale:    +override.scale || 1,
      offset_x: +override.offset_x || 0,
      offset_y: +override.offset_y || 0,
      rotation: +override.rotation || 0,
    };
    const contentColor = hexToLottieColor(override.content_color || "#ffffff");
    const isNonDefaultColor = override.content_color && override.content_color.toLowerCase() !== "#ffffff";

    for (const c of containers) {
      const { parentList, idxs, bbox } = c;

      // Режим "пустой" — удаляем оригинальные swex-буквы или перекрашиваем их
      if (useEmpty) {
        if (isNonDefaultColor) {
          // Перекрашиваем существующие fill/stroke внутри контейнера (применяем цвет к swex-буквам)
          for (const i of idxs) {
            const el = parentList[i];
            if (!el) continue;
            applyColorToNode(el, contentColor);
          }
          // Также перекрашиваем fill/stroke в parentList
          for (const item of parentList) {
            if (!item) continue;
            if (item.ty === "fl") item.c = contentColor;
            if (item.ty === "st") item.c = contentColor;
          }
        } else {
          // Дефолтный цвет и нет текста — удаляем swex-буквы (убирает VELORA)
          const sortedDesc = [...idxs].sort((a, b) => b - a);
          for (const i of sortedDesc) parentList.splice(i, 1);
        }
        continue;
      }

      let shapes;
      if (useLogo) {
        const cxLocal = (bbox[0] + bbox[2]) / 2;
        const cyLocal = (bbox[1] + bbox[3]) / 2;
        const letterExpand = +override.letter_expand || 1.0;
        const letterW = Math.max(1e-3, bbox[2] - bbox[0]) * letterExpand;
        const letterH = Math.max(1e-3, bbox[3] - bbox[1]) * letterExpand;
        shapes = placeLogoInBox(logoData.shapes, logoData.bbox,
                                [cxLocal, cyLocal], letterW, letterH, 1.0);
      } else {
        if (!font) { console.error("[replacer] font not loaded, skipping text"); continue; }
        shapes = renderTextToShapes(font, override.text, bbox);
      }
      if (!shapes.length) continue;

      // Применяем трансформацию напрямую к координатам (без wrap-группы)
      if (hasTransform(t)) {
        shapes = transformShapes(shapes, t, bbox);
      }

      // Удаляем swex-буквы (по убыванию индексов)
      const sortedDesc = [...idxs].sort((a, b) => b - a);
      for (const i of sortedDesc) parentList.splice(i, 1);
      const insertAt = idxs[0];

      // Копируем fill/stroke из родительской группы
      const fillStroke = [];
      let hasFill = false;
      let hasStroke = false;
      let strokeColor = contentColor;
      if (parentList && Array.isArray(parentList)) {
        for (const item of parentList) {
          if (!item) continue;
          if (item.ty === "fl") {
            hasFill = true;
            fillStroke.push({ ...item, c: contentColor });
          } else if (item.ty === "st") {
            hasStroke = true;
            const st = { ...item, c: contentColor };
            const curW = st.w && (st.w.k !== undefined ? st.w.k : st.w);
            if (curW === undefined || curW === null || Number(curW) < 0.5) {
              st.w = { a: 0, k: 2.5 };
            }
            if (!st.lc) st.lc = { a: 0, k: 2 };
            if (!st.lj) st.lj = { a: 0, k: 2 };
            fillStroke.push(st);
          }
        }
      }
      if (!hasFill) {
        fillStroke.push({
          ty: "fl",
          c: contentColor,
          o: { a: 0, k: 100 },
          r: 1,
          bm: 0,
          nm: "Fill",
        });
      }
      // Если stroke отсутствует — добавляем с ровными углами
      if (!hasStroke) {
        fillStroke.push({
          ty: "st",
          c: strokeColor,
          o: { a: 0, k: 100 },
          w: { a: 0, k: 2.5 },
          lc: { a: 0, k: 2 },
          lj: { a: 0, k: 2 },
          nm: "Stroke",
        });
      }
      // Вставляем shapes, затем fill/stroke
      for (let k = 0; k < shapes.length; k++) {
        parentList.splice(insertAt + k, 0, shapes[k]);
      }
      for (let k = 0; k < fillStroke.length; k++) {
        parentList.splice(insertAt + shapes.length + k, 0, fillStroke[k]);
      }
    }
    return lottieJson;
  }

  // ─────────────────── Public API ───────────────────
  let _font = null;
  let _fontPromise = null;

  function loadFont(url) {
    if (_font) return Promise.resolve(_font);
    if (_fontPromise) return _fontPromise;
    _fontPromise = new Promise((resolve, reject) => {
      opentype.load(url, (err, font) => {
        if (err) { reject(err); return; }
        _font = font;
        resolve(font);
      });
    });
    return _fontPromise;
  }

  function deepClone(o) {
    return JSON.parse(JSON.stringify(o));
  }

  function applyColorRemap(json, remap) {
    // Walk every static fl/st node; if its colour matches a key in
    // `remap`, repaint it with the mapped value. Animated colour
    // properties (`c.a == 1`) and gradient fills are left alone — the
    // editor doesn't expose them. Runs AFTER the text/logo insertion so
    // freshly added fills inherit the remap too if they happen to use a
    // remapped base colour.
    if (!remap || typeof remap !== "object") return json;
    // Build a normalized lookup table once.
    const table = {};
    let hasAny = false;
    for (const k in remap) {
      if (!Object.prototype.hasOwnProperty.call(remap, k)) continue;
      const from = normHex(k);
      const to = normHex(remap[k]);
      if (!from || !to || from === to) continue;
      table[from] = to;
      hasAny = true;
    }
    if (!hasAny) return json;

    function rgbaToHex(rgba) {
      if (!Array.isArray(rgba) || rgba.length < 3) return null;
      const r = Math.max(0, Math.min(255, Math.round((rgba[0] || 0) * 255)));
      const g = Math.max(0, Math.min(255, Math.round((rgba[1] || 0) * 255)));
      const b = Math.max(0, Math.min(255, Math.round((rgba[2] || 0) * 255)));
      return "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
    }
    function hexToRgba(hex, alphaFallback) {
      const a = (typeof alphaFallback === "number") ? alphaFallback : 1;
      return [
        parseInt(hex.slice(1, 3), 16) / 255,
        parseInt(hex.slice(3, 5), 16) / 255,
        parseInt(hex.slice(5, 7), 16) / 255,
        a,
      ];
    }

    function visit(n) {
      if (!n || typeof n !== "object") return;
      if (Array.isArray(n)) { for (const c of n) visit(c); return; }
      if (n.ty === "fl" || n.ty === "st") {
        const c = n.c;
        if (c && c.a === 0 && Array.isArray(c.k)) {
          const hex = rgbaToHex(c.k);
          if (hex && table[hex]) {
            const alpha = (c.k.length >= 4) ? c.k[3] : 1;
            c.k = hexToRgba(table[hex], alpha);
          }
        }
      }
      for (const k of ["layers", "assets", "shapes", "it"]) {
        if (Array.isArray(n[k])) visit(n[k]);
      }
    }
    visit(json);
    return json;
  }

  function normHex(v) {
    const s = String(v || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(s)) return s.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(s)) {
      return "#" + s.slice(1).split("").map(ch => ch + ch).join("").toLowerCase();
    }
    return null;
  }

  function applyToLottie(originalJson, override) {
    const json = deepClone(originalJson);
    const out = applyOverride(json, override, _font);
    // Recolour pass runs LAST so it can repaint fills that were just
    // inserted by the swex-letter replacement (e.g., the user's nickname).
    if (override && override.color_remap) {
      applyColorRemap(out, override.color_remap);
    }
    return out;
  }

  return {
    loadFont,
    applyToLottie,
    findSwexContainers,
    _internal: { renderTextToShapes, svgToLottieShapes, wrapInTransform },
  };
})();
