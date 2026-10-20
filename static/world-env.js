/* EcomVital World: environment assets (the district, the roofless store, its furniture).
   Drawn as vector shapes with PixiJS, no image files. Separate from the characters (world-chars.js),
   so either can be redrawn or swapped for artwork without touching the other.
   Coordinates are isometric tiles: x runs to the lower right, y to the lower left, z is height in px. */
(function () {
  const TW = 64, TH = 32;                    // one floor tile on screen
  const LOT = 16, ROAD = 4, COLS = 3, ROWS = 3;
  const LW = 14, LH = 11;                    // the live store's floor (tiles)

  const C = {
    asphalt: 0xB7B2A6, dash: 0xF4F1EA, walk: 0xE6E0D2, walkEdge: 0xD3CCBC, grass: 0xC6D6B2, grass2: 0xBBCCA6,
    plaza: 0xEFE8DA, floorA: 0xEBDCC0, floorB: 0xE3D1B1, wall: 0xF6F0E4, trim: 0x1F6F5C, ink: 0x14181F,
    shelf: 0xC9A27A, table: 0xD8B48C, counter: 0x2F4A44, till: 0x14181F, basket: 0xC98A2B,
    trunk: 0x8A6A4A, leaf: 0x7FA36B, leaf2: 0x93B67C,
    roofs: [0xD98E73, 0x8FA7C9, 0xC9B26B, 0x9DB79B, 0xB79BC4, 0xD7A15E, 0x7FB0AE, 0xC48D9E],
    goods: [0xE07A5F, 0x3D5A80, 0xF2CC8F, 0x81B29A, 0xB56576, 0x6D9DC5, 0xE9C46A, 0x9C89B8],
  };

  function iso(x, y, z = 0) { return { x: (x - y) * TW / 2, y: (x + y) * TH / 2 - z }; }
  function unIso(sx, sy) { const a = sx / (TW / 2), b = sy / (TH / 2); return { x: (a + b) / 2, y: (b - a) / 2 }; }
  function pts(list) { const o = []; for (const p of list) o.push(p.x, p.y); return o; }
  function shade(c, f) {
    const r = Math.min(255, Math.round(((c >> 16) & 255) * f)), g = Math.min(255, Math.round(((c >> 8) & 255) * f)),
      b = Math.min(255, Math.round((c & 255) * f));
    return (r << 16) | (g << 8) | b;
  }
  function quad(g, x, y, w, h, color, z = 0, alpha = 1) {
    g.poly(pts([iso(x, y, z), iso(x + w, y, z), iso(x + w, y + h, z), iso(x, y + h, z)])).fill({ color, alpha });
  }
  // a solid block: lower-left face lit, lower-right face in shadow, then the top
  function box(g, x, y, w, h, z0, ht, top, o = {}) {
    const left = o.left ?? shade(top, 0.86), right = o.right ?? shade(top, 0.7);
    g.poly(pts([iso(x + w, y, z0 + ht), iso(x + w, y + h, z0 + ht), iso(x + w, y + h, z0), iso(x + w, y, z0)])).fill(right);
    g.poly(pts([iso(x, y + h, z0 + ht), iso(x + w, y + h, z0 + ht), iso(x + w, y + h, z0), iso(x, y + h, z0)])).fill(left);
    quad(g, x, y, w, h, top, z0 + ht);
    if (o.edge) {
      g.poly(pts([iso(x, y, z0 + ht), iso(x + w, y, z0 + ht), iso(x + w, y + h, z0 + ht), iso(x, y + h, z0 + ht)]))
        .stroke({ width: 1, color: o.edge, alpha: 0.35 });
    }
  }
  function text(str, size, color, o = {}) {
    const t = new PIXI.Text({ text: str, style: { fontFamily: "Inter, system-ui, sans-serif", fontSize: size,
      fontWeight: o.weight || "650", fill: color, letterSpacing: o.spacing || 0, align: "center" } });
    t.resolution = o.res || 3;
    t.anchor.set(0.5, 0.5);
    return t;
  }

  // one furniture piece = one Graphics in the sorted layer, depth = its tile's centre
  function piece(layer, x, y, draw, depth) {
    const g = new PIXI.Graphics();
    draw(g);
    g.zIndex = depth ?? (x + y + 1);
    layer.addChild(g);
    return g;
  }

  function tree(layer, x, y, big) {
    piece(layer, x, y, (g) => {
      const p = iso(x + 0.5, y + 0.5);
      g.ellipse(p.x, p.y, 16, 7).fill({ color: 0x000000, alpha: 0.12 });
      g.rect(p.x - 3, p.y - 26, 6, 26).fill(C.trunk);
      const r = big ? 22 : 17;
      g.circle(p.x, p.y - 34 - r * 0.4, r).fill(C.leaf);
      g.circle(p.x - r * 0.45, p.y - 30 - r * 0.2, r * 0.7).fill(C.leaf2);
      g.circle(p.x + r * 0.5, p.y - 28 - r * 0.3, r * 0.6).fill(shade(C.leaf, 0.9));
    }, x + y + 1.2);
  }
  function lamp(layer, x, y) {
    piece(layer, x, y, (g) => {
      const p = iso(x + 0.5, y + 0.5);
      g.ellipse(p.x, p.y, 6, 3).fill({ color: 0x000000, alpha: 0.12 });
      g.rect(p.x - 1.5, p.y - 46, 3, 46).fill(0x4A4F57);
      g.roundRect(p.x - 6, p.y - 52, 12, 8, 3).fill(0x4A4F57);
      g.circle(p.x, p.y - 44, 4).fill({ color: 0xFFE7A8, alpha: 0.95 });
    });
  }
  function bench(layer, x, y) {
    piece(layer, x, y, (g) => { box(g, x + 0.1, y + 0.3, 0.8, 0.4, 0, 8, 0xA67C52); box(g, x + 0.1, y + 0.3, 0.8, 0.08, 8, 10, 0x8E6844); });
  }

  /* The whole district. stores = [{id, name, live}] with the live store first. */
  function build(app, stores) {
    const root = new PIXI.Container();
    const ground = new PIXI.Container(), back = new PIXI.Container(), objects = new PIXI.Container(),
      front = new PIXI.Container(), labels = new PIXI.Container();
    objects.sortableChildren = true;
    root.addChild(ground, back, objects, front, labels);

    const N = COLS * (LOT + ROAD) + ROAD;
    const gnd = new PIXI.Graphics();
    quad(gnd, -2, -2, N + 4, N + 4, C.grass2);
    quad(gnd, 0, 0, N, N, C.asphalt);
    // lane dashes down the middle of every road
    for (let i = 0; i <= COLS; i++) {
      const m = i * (LOT + ROAD) + ROAD / 2 - 0.06;
      for (let t = 0.5; t < N - 0.5; t += 1.6) { quad(gnd, m, t, 0.12, 0.7, C.dash, 0, 0.8); quad(gnd, t, m, 0.7, 0.12, C.dash, 0, 0.8); }
    }
    ground.addChild(gnd);

    const lots = [];
    const order = [[1, 1], [0, 0], [1, 0], [2, 0], [0, 1], [2, 1], [0, 2], [1, 2], [2, 2]];
    const others = stores.slice(1);
    order.forEach(([cx, cy], i) => {
      const ox = ROAD + cx * (LOT + ROAD), oy = ROAD + cy * (LOT + ROAD);
      const g = new PIXI.Graphics();
      box(g, ox, oy, LOT, LOT, 0, 3, C.walk, { left: C.walkEdge, right: shade(C.walkEdge, 0.9) });
      quad(g, ox + 0.6, oy + 0.6, LOT - 1.2, LOT - 1.2, i === 0 ? C.plaza : C.grass, 3);
      ground.addChild(g);
      if (i === 0) lots.push(mainStore(stores[0], ox, oy));
      else if (others[i - 1]) lots.push(otherStore(others[i - 1], ox, oy, i));
      else lots.push(park(ox, oy, i));
    });

    function mainStore(st, ox, oy) {
      const x0 = ox + 1, y0 = oy + 1;
      const fl = new PIXI.Graphics();
      box(fl, x0 - 0.2, y0 - 0.2, LW + 0.4, LH + 0.4, 3, 4, C.wall);
      for (let x = 0; x < LW; x++) for (let y = 0; y < LH; y++) quad(fl, x0 + x, y0 + y, 1, 1, (x + y) % 2 ? C.floorA : C.floorB, 7);
      // welcome mat + walkway to the door
      quad(fl, x0 + 6, y0 + LH - 0.9, 3, 0.8, C.trim, 7.5, 0.85);
      for (let t = 0; t < 3; t++) quad(fl, x0 + 6.3, y0 + LH + 0.4 + t * 0.9, 2.4, 0.6, 0xE1D7C3, 3.5);
      ground.addChild(fl);

      // back walls (tall, the store's "inside"), front walls low so the floor stays in view
      const bw = new PIXI.Graphics();
      box(bw, x0 - 0.25, y0 - 0.25, LW + 0.25, 0.25, 7, 78, C.wall, { left: 0xEFE6D4, edge: C.ink });
      box(bw, x0 - 0.25, y0, 0.25, LH, 7, 78, C.wall, { right: 0xE2D7C2, edge: C.ink });
      // green trim + a few framed pictures on the back walls
      box(bw, x0, y0 - 0.25, LW, 0.25, 7 + 78, 4, C.trim);
      box(bw, x0 - 0.25, y0, 0.25, LH, 7 + 78, 4, C.trim);
      for (let i = 0; i < 4; i++) {
        const fx = x0 + 1.2 + i * 3.2;
        bw.poly(pts([iso(fx, y0, 62), iso(fx + 1.6, y0, 62), iso(fx + 1.6, y0, 34), iso(fx, y0, 34)])).fill(C.goods[i * 2 % 8]);
      }
      back.addChild(bw);
      const fw = new PIXI.Graphics();
      box(fw, x0 - 0.25, y0 + LH, 6.25, 0.25, 7, 12, C.wall, { edge: C.ink });
      box(fw, x0 + 9, y0 + LH, LW - 8.75, 0.25, 7, 12, C.wall, { edge: C.ink });
      box(fw, x0 + LW, y0 - 0.25, 0.25, LH + 0.25, 7, 12, C.wall, { edge: C.ink });
      // door posts
      box(fw, x0 + 5.8, y0 + LH, 0.2, 0.25, 7, 40, C.trim);
      box(fw, x0 + 9, y0 + LH, 0.2, 0.25, 7, 40, C.trim);
      front.addChild(fw);

      const L = objects;
      // aisles: two long shelves (split into tiles so shoppers sort correctly around them)
      for (const sx of [1, 4]) for (let y = 1; y <= 6; y++) {
        piece(L, x0 + sx, y0 + y, (g) => {
          box(g, x0 + sx + 0.1, y0 + y, 0.8, 1, 7, 34, C.shelf, { edge: C.ink });
          for (let k = 0; k < 3; k++) box(g, x0 + sx + 0.2 + k * 0.22, y0 + y + 0.25, 0.18, 0.5, 41, 8 + ((y + k) % 3) * 3, C.goods[(sx + y + k) % 8]);
        });
      }
      // product tables
      for (const [tx, ty] of [[7, 2], [8, 2], [10, 2], [11, 2], [7, 5], [8, 5], [10, 5], [11, 5]]) {
        piece(L, x0 + tx, y0 + ty, (g) => {
          box(g, x0 + tx + 0.05, y0 + ty + 0.05, 0.9, 0.9, 7, 18, C.table, { edge: C.ink });
          box(g, x0 + tx + 0.25, y0 + ty + 0.25, 0.5, 0.5, 25, 6, C.goods[(tx * 3 + ty) % 8]);
        });
      }
      // basket station (cart)
      for (const bx of [12, 13]) {
        piece(L, x0 + bx, y0 + 7, (g) => {
          box(g, x0 + bx + 0.15, y0 + 7.15, 0.7, 0.7, 7, 10, 0x9A7B55);
          for (let k = 0; k < 3; k++) box(g, x0 + bx + 0.22, y0 + 7.22, 0.56, 0.56, 17 + k * 6, 5, C.basket);
        });
      }
      // checkout counter + till
      for (let cx = 1; cx <= 4; cx++) {
        piece(L, x0 + cx, y0 + 7, (g) => {
          box(g, x0 + cx, y0 + 7.1, 1, 0.8, 7, 22, C.counter, { edge: C.ink, left: 0x3B5A53, right: 0x2A3F3A });
          if (cx === 2 || cx === 4) {
            box(g, x0 + cx + 0.3, y0 + 7.3, 0.4, 0.35, 29, 6, C.till);
            box(g, x0 + cx + 0.34, y0 + 7.25, 0.32, 0.06, 35, 10, 0x2D3440);
          }
        });
      }
      // plants by the walls, benches + trees on the plaza
      for (const [px, py] of [[0, 0], [13, 0], [13, 4], [0, 10]]) {
        piece(L, x0 + px, y0 + py, (g) => {
          const p = iso(x0 + px + 0.5, y0 + py + 0.5, 7);
          g.roundRect(p.x - 7, p.y - 12, 14, 12, 3).fill(0xC86B4A);
          g.circle(p.x, p.y - 20, 10).fill(C.leaf); g.circle(p.x - 5, p.y - 16, 7).fill(C.leaf2);
        });
      }
      tree(L, ox + 1, oy + 13.5, true); tree(L, ox + 13.5, oy + 13.5, true);
      bench(L, ox + 3, oy + 13.6); bench(L, ox + 11, oy + 13.6);
      lamp(L, ox + 5, oy + 14.5); lamp(L, ox + 10, oy + 14.5);

      // sign above the back wall + floor labels
      const s = iso(x0 + LW / 2, y0 - 0.25, 112);
      const board = new PIXI.Graphics();
      const sign = text((st && st.name || "Store").toUpperCase(), 20, 0xFFFFFF, { weight: "750", spacing: 2 });
      board.roundRect(s.x - sign.width / 2 - 16, s.y - 17, sign.width + 32, 34, 8).fill(C.trim);
      board.roundRect(s.x - sign.width / 2 - 16, s.y - 17, sign.width + 32, 34, 8).stroke({ width: 2, color: 0xFFFFFF, alpha: 0.6 });
      sign.position.set(s.x, s.y);
      labels.addChild(board, sign);
      const floorLabel = (str, x, y) => { const p = iso(x0 + x, y0 + y, 8); const t = text(str, 11, C.ink, { weight: "700", spacing: 1 }); t.alpha = 0.42; t.position.set(p.x, p.y); ground.addChild(t); };
      floorLabel("AISLES", 3, 7.6); floorLabel("PRODUCTS", 9.5, 7.0); floorLabel("CART", 11.5, 10.5);
      floorLabel("CHECKOUT", 2.8, 10.3); floorLabel("ENTRANCE", 7.5, 9.9);

      const R = (a, b, c, d) => ({ x0: x0 + a, x1: x0 + b, y0: y0 + c, y1: y0 + d });
      return {
        id: st && st.id, name: st && st.name, live: true, main: true,
        bounds: { x0, y0, x1: x0 + LW, y1: y0 + LH + 3 },
        center: iso(x0 + LW / 2, y0 + LH / 2),
        door: { x: x0 + 7.5, y: y0 + LH + 2.6 },
        till: { x: x0 + 2.5, y: y0 + 7.5, z: 40 },
        zones: {
          entrance: [R(5.6, 9.4, 8.5, 10.6)],
          aisle: [R(2.15, 3.85, 1.2, 6.8), R(5.15, 5.85, 1.2, 6.8)],
          product: [R(6.4, 12.6, 3.15, 4.85), R(6.4, 12.6, 6.15, 6.9), R(9.1, 9.9, 1.2, 6.6), R(12.2, 13.6, 1.2, 6.6)],
          cart: [R(9.6, 13.6, 8.2, 10.5)],
          checkout: [R(0.8, 4.9, 8.2, 9.6)],
        },
      };
    }

    function otherStore(st, ox, oy, i) {
      const x0 = ox + 2, y0 = oy + 2, w = 12, h = 9, roof = C.roofs[i % C.roofs.length];
      piece(objects, x0 + w / 2, y0 + h / 2, (g) => {
        g.poly(pts([iso(x0 + w, y0), iso(x0 + w + 1.5, y0 + 1), iso(x0 + w + 1.5, y0 + h + 1.2), iso(x0 + 1, y0 + h + 1.2), iso(x0, y0 + h)]))
          .fill({ color: 0x000000, alpha: 0.08 });
        box(g, x0, y0, w, h, 3, 72, C.wall, { edge: C.ink, left: 0xEDE4D2, right: 0xDCD0BA });
        // windows on both visible sides
        for (let k = 0; k < 4; k++) {
          const wx = x0 + 1 + k * 2.7;
          g.poly(pts([iso(wx, y0 + h, 52), iso(wx + 1.6, y0 + h, 52), iso(wx + 1.6, y0 + h, 26), iso(wx, y0 + h, 26)])).fill(0xA9C1D6);
        }
        for (let k = 0; k < 3; k++) {
          const wy = y0 + 1 + k * 2.6;
          g.poly(pts([iso(x0 + w, wy, 52), iso(x0 + w, wy + 1.5, 52), iso(x0 + w, wy + 1.5, 26), iso(x0 + w, wy, 26)])).fill(0x93ADC3);
        }
        // door + awning
        g.poly(pts([iso(x0 + 5.3, y0 + h, 34), iso(x0 + 6.7, y0 + h, 34), iso(x0 + 6.7, y0 + h, 3), iso(x0 + 5.3, y0 + h, 3)])).fill(0x4A3B30);
        box(g, x0 + 0.5, y0 + h, w - 1, 0.7, 56, 4, roof);
        // roof: flat top with a raised rim
        box(g, x0, y0, w, h, 75, 3, shade(roof, 0.95), { edge: C.ink });
        quad(g, x0 + 0.5, y0 + 0.5, w - 1, h - 1, roof, 78);
        box(g, x0 + 2, y0 + 2, 2, 1.5, 78, 10, 0xCFCBC2); box(g, x0 + 7, y0 + 3, 1.5, 1.5, 78, 14, 0xBDB8AE);
      }, x0 + y0 + (w + h) / 2);
      tree(objects, ox + 0.5, oy + 13.8); tree(objects, ox + 14, oy + 0.5); lamp(objects, ox + 8, oy + 14.6);
      const p = iso(x0 + w / 2, y0 + h / 2, 120);
      const name = text(st.name || "Store", 15, C.ink, { weight: "700" });
      const note = text(st.pixel ? "Joins World in phase 2" : "No EcomVital pixel yet", 10, 0x5B6170, { weight: "600" });
      const tag = new PIXI.Graphics();
      const wdt = Math.max(name.width, note.width) + 24;
      tag.roundRect(p.x - wdt / 2, p.y - 22, wdt, 44, 9).fill({ color: 0xFFFFFF, alpha: 0.92 });
      tag.roundRect(p.x - wdt / 2, p.y - 22, wdt, 44, 9).stroke({ width: 1, color: C.ink, alpha: 0.15 });
      name.position.set(p.x, p.y - 7); note.position.set(p.x, p.y + 10);
      labels.addChild(tag, name, note);
      return { id: st.id, name: st.name, live: false, bounds: { x0, y0, x1: x0 + w, y1: y0 + h }, center: iso(x0 + w / 2, y0 + h / 2) };
    }

    function park(ox, oy) {
      for (const [a, b, big] of [[3, 3, 1], [8, 4, 0], [12, 3, 1], [4, 10, 0], [10, 11, 1], [13, 8, 0], [6, 7, 1]]) tree(objects, ox + a, oy + b, big);
      bench(objects, ox + 7, oy + 12); lamp(objects, ox + 2, oy + 14.5);
      return { id: null, live: false, park: true, bounds: { x0: ox, y0: oy, x1: ox + LOT, y1: oy + LOT }, center: iso(ox + LOT / 2, oy + LOT / 2) };
    }

    return { root, objects, labels, lots, size: N, mainLot: lots[0] };
  }

  window.WorldEnv = { TW, TH, iso, unIso, shade, build, text, box, quad };
})();
