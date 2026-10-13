// Minimal dependency-free SVG charts (line + bars) with hover tooltips.
// Colours come from CSS custom properties so light/dark themes stay in one place.

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Axis maximum whose quarter is a "nice" step (1, 2, 2.5 or 5 x 10^n).
function niceMax(v) {
  if (v <= 0) return 4;
  const step = v / 4;
  const exp = Math.pow(10, Math.floor(Math.log10(step)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * exp >= step) return Math.max(4, m * exp * 4);
  return 40 * exp;
}

export const fmt = {
  int: (v) => (v == null ? "–" : Math.round(v).toLocaleString()),
  pct: (v) => (v == null ? "–" : (v * 100).toFixed(1) + "%"),
  sec: (v) => (v == null ? "–" : v < 60 ? v.toFixed(1) + " s" : (v / 60).toFixed(1) + " min"),
  compact: (v) =>
    v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e4 ? Math.round(v / 1e3) + "k" : v >= 1e3 ? (v / 1e3).toFixed(1) + "k" : String(Math.round(v)),
};

let tip;
function tooltip() {
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
  }
  return tip;
}
function showTip(evt, html) {
  const t = tooltip();
  t.innerHTML = html;
  t.style.display = "block";
  const pad = 14;
  const { innerWidth: w } = window;
  const rect = t.getBoundingClientRect();
  let x = evt.clientX + pad;
  if (x + rect.width > w - 8) x = evt.clientX - rect.width - pad;
  t.style.left = Math.max(8, x) + "px";
  t.style.top = Math.max(8, evt.clientY - rect.height - pad) + "px";
}
export function hideTip() {
  if (tip) tip.style.display = "none";
}

function width(container) {
  return Math.max(280, container.clientWidth || 600);
}

/**
 * Line chart sharing one y axis.
 * opts: {points:[{t,...}], series:[{key,label,color}], xLabel:(t)=>str, height}
 */
export function lineChart(container, opts) {
  container.innerHTML = "";
  const W = width(container);
  const H = opts.height || 260;
  const m = { top: 12, right: 88, bottom: 28, left: 44 };
  const iw = W - m.left - m.right;
  const ih = H - m.top - m.bottom;
  const pts = opts.points;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart", role: "img",
    "aria-label": opts.ariaLabel || "Line chart" });
  container.appendChild(svg);
  if (!pts.length) return;

  const maxV = niceMax(Math.max(1, ...pts.flatMap((p) => opts.series.map((s) => p[s.key] || 0))));
  const x = (i) => m.left + (pts.length === 1 ? iw / 2 : (i / (pts.length - 1)) * iw);
  const y = (v) => m.top + ih - (v / maxV) * ih;

  for (let i = 0; i <= 4; i++) {
    const v = (maxV / 4) * i;
    el("line", { x1: m.left, x2: m.left + iw, y1: y(v), y2: y(v), class: i === 0 ? "axis" : "gridline" }, svg);
    el("text", { x: m.left - 6, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = fmt.compact(v);
  }
  const ticks = Math.min(pts.length, Math.max(2, Math.floor(iw / 90)));
  for (let k = 0; k < ticks; k++) {
    const i = Math.round((k / Math.max(1, ticks - 1)) * (pts.length - 1));
    el("text", { x: x(i), y: H - 8, "text-anchor": "middle", class: "tick" }, svg).textContent = opts.xLabel(pts[i].t);
  }

  for (const s of opts.series) {
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[s.key] || 0).toFixed(1)}`).join("");
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round",
      "stroke-linecap": "round" }, svg);
    // Direct label at the line end (legend is also rendered by the caller).
    const last = pts[pts.length - 1][s.key] || 0;
    el("text", { x: m.left + iw + 8, y: y(last) + 4, class: "direct-label" }, svg).textContent = s.label;
  }

  // Crosshair + tooltip
  const cross = el("line", { y1: m.top, y2: m.top + ih, class: "crosshair", visibility: "hidden" }, svg);
  const dots = opts.series.map((s) =>
    el("circle", { r: 4, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" }, svg)
  );
  const hit = el("rect", { x: m.left, y: m.top, width: iw, height: ih, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (evt) => {
    const r = svg.getBoundingClientRect();
    const px = ((evt.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((px - m.left) / iw) * (pts.length - 1))));
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    opts.series.forEach((s, k) => {
      dots[k].setAttribute("cx", x(i));
      dots[k].setAttribute("cy", y(pts[i][s.key] || 0));
      dots[k].setAttribute("visibility", "visible");
    });
    const rows = opts.series
      .map((s) => `<div class="tip-row"><span class="swatch" style="background:${s.color}"></span>${s.label}<b>${fmt.int(pts[i][s.key])}</b></div>`)
      .join("");
    showTip(evt, `<div class="tip-title">${opts.xLabel(pts[i].t, true)}</div>${rows}`);
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTip();
  });
}

/**
 * Bar chart, single series.
 * opts: {data:[{label,value,tip}], color, horizontal, height, valueFormat}
 */
export function barChart(container, opts) {
  container.innerHTML = "";
  const W = width(container);
  const data = opts.data;
  const vf = opts.valueFormat || fmt.int;
  const maxV = niceMax(Math.max(1, ...data.map((d) => d.value || 0)));

  if (opts.horizontal) {
    const rowH = 30;
    const labelW = Math.min(180, W * 0.38);
    const m = { top: 4, right: 64, bottom: 4, left: labelW };
    const H = m.top + m.bottom + data.length * rowH;
    const iw = W - m.left - m.right;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart", role: "img",
      "aria-label": opts.ariaLabel || "Bar chart" });
    container.appendChild(svg);
    el("line", { x1: m.left, x2: m.left, y1: m.top, y2: H - m.bottom, class: "axis" }, svg);
    data.forEach((d, i) => {
      const yy = m.top + i * rowH;
      const bw = Math.max(0, ((d.value || 0) / maxV) * iw);
      const label = el("text", { x: m.left - 8, y: yy + rowH / 2 + 4, "text-anchor": "end", class: "cat-label" }, svg);
      label.textContent = d.label.length > 26 ? d.label.slice(0, 25) + "…" : d.label;
      if (bw > 0) {
        const r = Math.min(4, bw / 2);
        const h = rowH - 10;
        el("path", {
          d: `M${m.left},${yy + 5}h${bw - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 -${r},${r}h-${bw - r}z`,
          fill: opts.color,
        }, svg);
      }
      el("text", { x: m.left + bw + 6, y: yy + rowH / 2 + 4, class: "value-label" }, svg).textContent = vf(d.value);
      const hit = el("rect", { x: 0, y: yy, width: W, height: rowH, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", (evt) => showTip(evt, d.tip || `<b>${d.label}</b>: ${vf(d.value)}`));
      hit.addEventListener("mouseleave", hideTip);
    });
    return;
  }

  const H = opts.height || 220;
  const m = { top: 12, right: 8, bottom: 28, left: 44 };
  const iw = W - m.left - m.right;
  const ih = H - m.top - m.bottom;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart", role: "img",
    "aria-label": opts.ariaLabel || "Bar chart" });
  container.appendChild(svg);
  const y = (v) => m.top + ih - (v / maxV) * ih;
  for (let i = 0; i <= 4; i++) {
    const v = (maxV / 4) * i;
    el("line", { x1: m.left, x2: m.left + iw, y1: y(v), y2: y(v), class: i === 0 ? "axis" : "gridline" }, svg);
    el("text", { x: m.left - 6, y: y(v) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = fmt.compact(v);
  }
  const slot = iw / data.length;
  const bw = Math.max(2, slot - 2); // 2px surface gap between adjacent bars
  const every = Math.ceil(data.length / Math.max(1, Math.floor(iw / 36)));
  data.forEach((d, i) => {
    const bx = m.left + i * slot + 1;
    const bh = ih - (y(d.value || 0) - m.top);
    if (bh > 0) {
      const r = Math.min(4, bw / 2, bh);
      el("path", {
        d: `M${bx},${m.top + ih}v-${bh - r}a${r},${r} 0 0 1 ${r},-${r}h${bw - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${bh - r}z`,
        fill: opts.color,
      }, svg);
    }
    if (i % every === 0)
      el("text", { x: bx + bw / 2, y: H - 8, "text-anchor": "middle", class: "tick" }, svg).textContent = d.label;
    const hit = el("rect", { x: bx - 1, y: m.top, width: slot, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (evt) => showTip(evt, d.tip || `<b>${d.label}</b>: ${vf(d.value)}`));
    hit.addEventListener("mouseleave", hideTip);
  });
}
