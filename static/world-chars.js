/* EcomVital World: character assets (shoppers, their state markers, the party effect).
   Each look (shirt, skin, hair) is drawn once into a texture and reused, so hundreds of shoppers
   stay light. Separate from the environment (world-env.js). */
(function () {
  const SHIRTS = [0x3A4E7A, 0xE07A5F, 0x81B29A, 0xF2CC8F, 0xB56576, 0x6D9DC5, 0x9C89B8, 0x2A9D8F, 0xE9C46A, 0x5E6472];
  const SKIN = [0xF6D7B8, 0xE8B98F, 0xC98E63, 0x9C6644, 0x6F4A33];
  const HAIR = [0x2B2118, 0x5A3A22, 0x8C5A2B, 0xD9B26F, 0x1C1C1C, 0xA34E2E];
  const PANTS = [0x2E3440, 0x4C566A, 0x3B4A63, 0x5B4B3A];
  // state colours: costs stay neutral, only the purchase is green
  const STATE = { browsing: 0x3A4E7A, cart: 0xC98A2B, checkout: 0x6B4FA0, inactive: 0x9C988E, purchased: 0x1F6F5C };
  const NAMES = { browsing: "Browsing", cart: "Has a cart", checkout: "In checkout", inactive: "Inactive", purchased: "Bought!" };

  let renderer = null;
  const tex = {};

  function hashNum(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  function make(g, draw) { draw(g); const t = renderer.generateTexture({ target: g, resolution: 3 }); g.destroy(); return t; }

  function bodyTex(shirt, skin, hair, pants, longHair) {
    const k = [shirt, skin, hair, pants, longHair].join("-");
    if (tex[k]) return tex[k];
    tex[k] = make(new PIXI.Graphics(), (g) => {
      g.rect(-10, -44, 20, 46).fill({ color: 0xffffff, alpha: 0 });          // fixed box so every look lines up
      g.roundRect(-5, -15, 4, 15, 2).fill(pants); g.roundRect(1, -15, 4, 15, 2).fill(pants);
      g.roundRect(-6.5, -28, 13, 15, 5).fill(shirt);
      g.roundRect(-9, -26, 3.5, 11, 2).fill(shirt); g.roundRect(5.5, -26, 3.5, 11, 2).fill(shirt);
      g.circle(-7.2, -15, 2).fill(skin); g.circle(7.2, -15, 2).fill(skin);
      g.circle(0, -34, 6).fill(skin);
      g.arc(0, -35, 6.4, Math.PI, 0).fill(hair);
      if (longHair) { g.roundRect(-6.6, -36, 3, 9, 1.5).fill(hair); g.roundRect(3.6, -36, 3, 9, 1.5).fill(hair); }
    });
    return tex[k];
  }

  function init(app) {
    renderer = app.renderer;
    tex.shadow = make(new PIXI.Graphics(), (g) => g.ellipse(0, 0, 10, 4.5).fill({ color: 0x000000, alpha: 0.2 }));
    tex.ring = make(new PIXI.Graphics(), (g) => g.ellipse(0, 0, 12, 5.5).stroke({ width: 2.5, color: 0xffffff }));
    tex.dot = make(new PIXI.Graphics(), (g) => { g.circle(0, 0, 5).fill(0xffffff); g.circle(0, 0, 5).stroke({ width: 1.5, color: 0xffffff, alpha: 0.9 }); });
    tex.basket = make(new PIXI.Graphics(), (g) => {
      g.roundRect(-5, -4, 10, 7, 2).fill(STATE.cart);
      g.arc(0, -4, 4, Math.PI, 0).stroke({ width: 1.5, color: 0x8A5E1A });
    });
    tex.card = make(new PIXI.Graphics(), (g) => { g.roundRect(-5, -3.5, 10, 7, 1.5).fill(STATE.checkout); g.rect(-5, -1.5, 10, 1.6).fill(0x2D2240); });
    tex.zzz = make(new PIXI.Graphics(), (g) => {
      g.roundRect(-9, -7, 18, 14, 7).fill({ color: 0xffffff, alpha: 0.92 });
      g.moveTo(-4, -3).lineTo(1, -3).lineTo(-4, 3).lineTo(1, 3).stroke({ width: 1.4, color: 0x6B6862 });
      g.moveTo(3, -4).lineTo(6, -4).lineTo(3, 0).lineTo(6, 0).stroke({ width: 1.1, color: 0x6B6862 });
    });
    tex.confetti = make(new PIXI.Graphics(), (g) => g.rect(-2, -1, 4, 2).fill(0xffffff));
    tex.coin = make(new PIXI.Graphics(), (g) => { g.circle(0, 0, 6).fill(0xE9B949); g.circle(0, 0, 4).stroke({ width: 1.2, color: 0xB8891F }); });
  }

  /* One shopper. id = the server's hashed visitor id: it always gets the same look. */
  function shopper(id) {
    const h = hashNum(id);
    const c = new PIXI.Container();
    const shadow = new PIXI.Sprite(tex.shadow); shadow.anchor.set(0.5);
    const ring = new PIXI.Sprite(tex.ring); ring.anchor.set(0.5);
    const body = new PIXI.Sprite(bodyTex(SHIRTS[h % SHIRTS.length], SKIN[(h >> 4) % SKIN.length], HAIR[(h >> 8) % HAIR.length],
      PANTS[(h >> 12) % PANTS.length], (h >> 16) % 3 === 0));
    body.anchor.set(0.5, 44 / 46);
    const prop = new PIXI.Sprite(tex.basket); prop.anchor.set(0.5); prop.position.set(9, -14); prop.visible = false;
    const dot = new PIXI.Sprite(tex.dot); dot.anchor.set(0.5); dot.position.set(0, -50);
    const zzz = new PIXI.Sprite(tex.zzz); zzz.anchor.set(0.5); zzz.position.set(11, -50); zzz.visible = false;
    c.addChild(shadow, ring, body, prop, dot, zzz);
    c.parts = { ring, body, prop, dot, zzz };
    c.flip = (h >> 20) % 2 ? 1 : -1;
    return c;
  }

  function setState(c, state) {
    const col = STATE[state] || STATE.browsing, p = c.parts;
    p.ring.tint = col; p.dot.tint = col;
    p.prop.visible = state === "cart" || state === "checkout";
    p.prop.texture = state === "checkout" ? tex.card : tex.basket;
    p.zzz.visible = state === "inactive";
    p.body.tint = state === "inactive" ? 0xBDBAB2 : 0xFFFFFF;
    c.alpha = state === "inactive" ? 0.62 : 1;
  }

  /* A purchase: confetti + a coin and the order value rising. Returns the effect container
     (the caller adds it and runs effect.step(dtSeconds) until it returns false). */
  function party(label, sub) {
    const fx = new PIXI.Container();
    const bits = [];
    const cols = [0x1F6F5C, 0xE9B949, 0xE07A5F, 0x6D9DC5, 0x81B29A, 0xB56576];
    for (let i = 0; i < 34; i++) {
      const s = new PIXI.Sprite(tex.confetti); s.anchor.set(0.5); s.tint = cols[i % cols.length];
      const a = -Math.PI / 2 + (Math.random() - 0.5) * 2.2, v = 90 + Math.random() * 120;
      bits.push({ s, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: (Math.random() - 0.5) * 12 });
      fx.addChild(s);
    }
    const coin = new PIXI.Sprite(tex.coin); coin.anchor.set(0.5); coin.position.set(0, -60); fx.addChild(coin);
    const t = WorldEnv.text(label, 15, 0x1F6F5C, { weight: "800" }); t.position.set(0, -80);
    const pill = new PIXI.Graphics();
    pill.roundRect(-t.width / 2 - 10, -93, t.width + 20, 26, 13).fill({ color: 0xffffff, alpha: 0.95 });
    pill.roundRect(-t.width / 2 - 10, -93, t.width + 20, 26, 13).stroke({ width: 1.5, color: 0x1F6F5C, alpha: 0.5 });
    fx.addChild(pill, t);
    if (sub) { const s2 = WorldEnv.text(sub, 10, 0x5B6170, { weight: "600" }); s2.position.set(0, -60 + 22); fx.addChild(s2); }
    let age = 0;
    fx.step = (dt) => {
      age += dt;
      for (const b of bits) { b.vy += 260 * dt; b.s.x += b.vx * dt; b.s.y += b.vy * dt - 30 * dt; b.s.rotation += b.r * dt; b.s.alpha = Math.max(0, 1 - age / 2.2); }
      coin.y = -60 - age * 30; coin.scale.x = Math.cos(age * 8);
      const rise = Math.min(age, 1) * 20; pill.y = -rise; t.y = -80 - rise;
      fx.alpha = age < 2.4 ? 1 : Math.max(0, 1 - (age - 2.4) / 0.8);
      return age < 3.2;
    };
    return fx;
  }

  window.WorldChars = { init, shopper, setState, party, STATE, NAMES, hashNum };
})();
