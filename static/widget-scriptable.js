// ProfitDesk widget for iPhone (runs in the free Scriptable app).
// Widget: __WIDGET_NAME__
// Change what it shows in ProfitDesk → Settings → iPhone widget; no need to touch this script.
// The link below is private and read-only. Turning the widget off in ProfitDesk kills it.

const URL_ = args.widgetParameter && args.widgetParameter.startsWith("http") ? args.widgetParameter : "__WIDGET_URL__";
const APP = "__APP_URL__";

const C = {
  bg: Color.dynamic(new Color("#F2F1ED"), new Color("#14181F")),
  card: Color.dynamic(new Color("#FFFFFF"), new Color("#1D232C")),
  ink: Color.dynamic(new Color("#14181F"), new Color("#E6EDF3")),
  muted: Color.dynamic(new Color("#6B7280"), new Color("#9BA5B4")),
  profit: Color.dynamic(new Color("#1F6F5C"), new Color("#34D399")),
  loss: Color.dynamic(new Color("#B3402A"), new Color("#F87171")),
};

function money(v, cur, compact) {
  if (v === null || v === undefined) return "–";
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency: cur, maximumFractionDigits: compact || Math.abs(v) >= 1000 ? 0 : 2,
      notation: compact && Math.abs(v) >= 10000 ? "compact" : "standard" }).format(v);
  } catch (e) { return cur + " " + Math.round(v).toLocaleString(); }
}
function fmt(it, cur, compact) {
  const v = it.value;
  if (v === null || v === undefined) return "–";
  if (it.format === "money") return money(v, cur, compact);
  if (it.format === "pct") return (v * 100).toFixed(1) + "%";
  if (it.format === "ratio") return v.toFixed(2) + "x";
  return Math.round(v).toLocaleString();
}
function deltaText(it) {
  if (it.delta === null || it.delta === undefined) return null;
  const d = it.delta;
  return (d >= 0 ? "▲ " : "▼ ") + Math.abs(d).toFixed(0) + "%";
}
function deltaColor(it) {
  if (!it.good || it.delta === null || it.delta === undefined) return C.muted;   // costs stay neutral
  return it.delta >= 0 ? C.profit : C.loss;
}
function valueColor(it) {
  if ((it.key === "net_profit" || it.key === "net_margin") && it.value < 0) return C.loss;
  if (it.key === "net_profit") return C.profit;
  return C.ink;
}

async function load() {
  const r = new Request(URL_);
  r.timeoutInterval = 40;
  const d = await r.loadJSON();
  if (r.response && r.response.statusCode >= 400) throw new Error(d.error || "ProfitDesk didn't answer.");
  return d;
}

function header(w, d, small) {
  const h = w.addStack();
  h.centerAlignContent();
  const t = h.addText(small ? d.period : d.title + " · " + d.period);
  t.font = Font.semiboldSystemFont(small ? 11 : 12);
  t.textColor = C.muted;
  t.lineLimit = 1;
  h.addSpacer();
  const pd = h.addText("PD");
  pd.font = Font.heavySystemFont(10);
  pd.textColor = C.muted;
}

function cell(stack, it, cur, size) {
  const s = stack.addStack();
  s.layoutVertically();
  const l = s.addText(it.label.toUpperCase());
  l.font = Font.mediumSystemFont(size === "large" ? 10 : 9);
  l.textColor = C.muted;
  l.lineLimit = 1;
  const v = s.addText(fmt(it, cur, size !== "large"));
  v.font = Font.boldMonospacedSystemFont(size === "small" ? 17 : size === "large" ? 20 : 16);
  v.textColor = valueColor(it);
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.5;
  const dt = deltaText(it);
  if (dt) {
    const x = s.addText(dt);
    x.font = Font.mediumMonospacedSystemFont(9);
    x.textColor = deltaColor(it);
  }
  return s;
}

function build(d) {
  const fam = config.widgetFamily || "medium";
  const w = new ListWidget();
  w.url = d.open_url || APP;
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  const items = d.items || [];

  if (fam === "accessoryInline") {
    const it = items[0];
    w.addText(it ? it.label + " " + fmt(it, d.currency, true) : "ProfitDesk");
    return w;
  }
  if (fam === "accessoryRectangular" || fam === "accessoryCircular") {
    for (const it of items.slice(0, fam === "accessoryCircular" ? 1 : 3)) {
      const t = w.addText((fam === "accessoryCircular" ? "" : it.label + " ") + fmt(it, d.currency, true));
      t.font = Font.semiboldMonospacedSystemFont(fam === "accessoryCircular" ? 11 : 12);
      t.minimumScaleFactor = 0.6;
      t.lineLimit = 1;
    }
    return w;
  }

  w.backgroundColor = C.bg;
  const pad = fam === "small" ? 12 : 14;
  w.setPadding(pad, pad, pad, pad);
  header(w, d, fam === "small");
  w.addSpacer(fam === "small" ? 6 : 8);

  const cols = fam === "small" ? 1 : fam === "medium" ? 3 : 2;
  const max = fam === "small" ? 3 : fam === "medium" ? 6 : 10;
  const shown = items.slice(0, max);
  for (let i = 0; i < shown.length; i += cols) {
    const row = w.addStack();
    row.layoutHorizontally();
    for (let j = 0; j < cols; j++) {
      const it = shown[i + j];
      if (it) cell(row, it, d.currency, fam);
      else row.addStack();
      if (j < cols - 1) row.addSpacer();
    }
    if (i + cols < shown.length) w.addSpacer(fam === "large" ? 10 : 6);
  }

  w.addSpacer();
  const foot = w.addText(fam === "small" ? timeNow() : d.compare + " · " + timeNow());
  foot.font = Font.regularSystemFont(9);
  foot.textColor = C.muted;
  foot.lineLimit = 1;
  return w;
}
function timeNow() {
  const f = new DateFormatter();
  f.useNoDateStyle();
  f.useShortTimeStyle();
  return "Updated " + f.string(new Date());
}

function errorWidget(msg) {
  const w = new ListWidget();
  w.backgroundColor = C.bg;
  w.url = APP;
  const t = w.addText("ProfitDesk");
  t.font = Font.boldSystemFont(13);
  t.textColor = C.ink;
  w.addSpacer(6);
  const m = w.addText(msg);
  m.font = Font.regularSystemFont(11);
  m.textColor = C.muted;
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  return w;
}

let widget;
try { widget = build(await load()); }
catch (e) { widget = errorWidget(String(e.message || e)); }
if (config.runsInWidget || config.runsInAccessoryWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
