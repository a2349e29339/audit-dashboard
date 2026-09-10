/* Personal audit dashboard — vanilla JS, hand-rolled SVG charts. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  view: "overview",
  range: "6m",
  gran: "weekly",
  spendMode: "variable",
  flowMonth: "",
  txnRange: "3m",
  categories: [],
  accounts: [],
};

const fmtUSD = (n, opts = {}) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, ...opts });
const fmtUSDc = n =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const fmtCompact = n => {
  const a = Math.abs(n);
  if (a >= 1e6) return (n < 0 ? "-" : "") + "$" + (a / 1e6).toFixed(1) + "M";
  if (a >= 1e3) {
    const k = a / 1e3;
    return (n < 0 ? "-" : "") + "$" + (k < 10 && k % 1 !== 0 ? k.toFixed(1) : Math.round(k)) + "k";
  }
  return fmtUSD(n);
};
const monthLabel = ym => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString("en-US", { month: "short" });
};

function rangeDates(key) {
  const today = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  const som = new Date(today.getFullYear(), today.getMonth(), 1);
  switch (key) {
    case "tm": return { start: iso(som), end: iso(today) };
    case "lm": {
      const s = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      const e = new Date(today.getFullYear(), today.getMonth(), 0);
      return { start: iso(s), end: iso(e) };
    }
    case "3m": return { start: iso(new Date(today.getFullYear(), today.getMonth() - 2, 1)), end: iso(today) };
    case "6m": return { start: iso(new Date(today.getFullYear(), today.getMonth() - 5, 1)), end: iso(today) };
    case "ytd": return { start: `${today.getFullYear()}-01-01`, end: iso(today) };
    case "1y": return { start: iso(new Date(today.getFullYear() - 1, today.getMonth(), 1)), end: iso(today) };
    default: return { start: "1970-01-01", end: iso(today) };
  }
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status}`);
  return data;
}
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.display = "block";
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.style.display = "none"), 2600);
}

/* ---------------- tooltip ---------------- */
const tip = $("#tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + 14, top = y - h - 10;
  if (left + w > innerWidth - 8) left = x - w - 14;
  if (top < 8) top = y + 14;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
const hideTip = () => (tip.style.display = "none");

/* ---------------- svg helpers ---------------- */
const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}
function niceTicks(max, count = 4) {
  if (max <= 0) return [0, 1];
  const step0 = max / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0);
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}
// Rounded top corners only, anchored to the baseline (4px data-end radius)
function barPath(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ---------------- charts ---------------- */

function renderCashflow(container, monthly) {
  container.innerHTML = "";
  if (!monthly.length) { container.innerHTML = '<div class="empty">No activity in this range.</div>'; return; }
  const W = container.clientWidth || 1080, H = 240;
  const pad = { l: 46, r: 10, t: 10, b: 26 };
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });
  const maxV = Math.max(...monthly.flatMap(m => [m.income, m.spending]), 1);
  const ticks = niceTicks(maxV);
  const top = ticks[ticks.length - 1];
  const y = v => pad.t + (1 - v / top) * (H - pad.t - pad.b);
  const iw = (W - pad.l - pad.r) / monthly.length;

  for (const tv of ticks) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(tv), y2: y(tv), class: tv === 0 ? "baseline" : "gridline" }));
    const lbl = svgEl("text", { x: pad.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "axis-label tick-num" });
    lbl.textContent = fmtCompact(tv);
    svg.appendChild(lbl);
  }

  const groupW = Math.min(iw * 0.62, 56);
  const barW = (groupW - 2) / 2; // 2px gap between adjacent bars
  monthly.forEach((m, i) => {
    const cx = pad.l + iw * i + iw / 2;
    const x0 = cx - groupW / 2;
    const series = [
      { key: "Income", v: m.income, color: "var(--series-1)" },
      { key: "Spending", v: m.spending, color: "var(--series-2)" },
    ];
    series.forEach((s, si) => {
      const bx = x0 + si * (barW + 2);
      const by = y(s.v), bh = H - pad.b - by;
      if (bh > 0.5) svg.appendChild(svgEl("path", { d: barPath(bx, by, barW, bh, 4), fill: s.color }));
    });
    const lbl = svgEl("text", { x: cx, y: H - 8, "text-anchor": "middle", class: "axis-label" });
    lbl.textContent = monthLabel(m.month);
    svg.appendChild(lbl);
    // hover target spans the whole month column
    const hit = svgEl("rect", { x: pad.l + iw * i, y: pad.t, width: iw, height: H - pad.t - pad.b, fill: "transparent" });
    hit.addEventListener("mousemove", e => {
      const net = m.income - m.spending;
      showTip(
        `<div class="t-title">${monthLabel(m.month)} ${m.month.slice(0, 4)}</div>` +
        `<div class="t-row"><span class="t-key"><span class="swatch" style="background:var(--series-1)"></span>Income</span><b>${fmtUSD(m.income)}</b></div>` +
        `<div class="t-row"><span class="t-key"><span class="swatch" style="background:var(--series-2)"></span>Spending</span><b>${fmtUSD(m.spending)}</b></div>` +
        `<div class="t-row"><span>Net</span><b>${net >= 0 ? "+" : ""}${fmtUSD(net)}</b></div>`, e.clientX, e.clientY);
    });
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(hit);
  });
  container.appendChild(svg);
}

function renderCategoryBars(container, byCat) {
  container.innerHTML = "";
  if (!byCat.length) { container.innerHTML = '<div class="empty">No categorized spending yet.</div>'; return; }
  const rows = byCat.slice(0, 10);
  const other = byCat.slice(10).reduce((s, c) => s + c.amount, 0);
  if (other > 0) rows.push({ category: "Other", amount: other });
  const W = container.clientWidth || 520;
  const rowH = 30, pad = { l: 130, r: 66, t: 4, b: 4 };
  const H = pad.t + pad.b + rows.length * rowH;
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });
  const maxV = Math.max(...rows.map(r => r.amount));
  const x = v => pad.l + (v / maxV) * (W - pad.l - pad.r);

  rows.forEach((r, i) => {
    const cy = pad.t + i * rowH + rowH / 2;
    const name = svgEl("text", { x: pad.l - 10, y: cy + 4, "text-anchor": "end", fill: "var(--ink-2)", "font-size": 12 });
    name.textContent = r.category.length > 17 ? r.category.slice(0, 16) + "…" : r.category;
    svg.appendChild(name);
    const bw = Math.max(x(r.amount) - pad.l, 2);
    const bar = svgEl("path", {
      d: `M${pad.l},${cy - 8} L${pad.l + bw - 4},${cy - 8} Q${pad.l + bw},${cy - 8} ${pad.l + bw},${cy - 4} L${pad.l + bw},${cy + 4} Q${pad.l + bw},${cy + 8} ${pad.l + bw - 4},${cy + 8} L${pad.l},${cy + 8} Z`,
      fill: r.category === "Uncategorized" ? "var(--seq-250)" : "var(--seq-450)",
    });
    svg.appendChild(bar);
    const val = svgEl("text", { x: pad.l + bw + 8, y: cy + 4, fill: "var(--ink)", "font-size": 12, "font-weight": 600, class: "tick-num" });
    val.textContent = fmtUSD(r.amount);
    svg.appendChild(val);
    const hit = svgEl("rect", { x: 0, y: pad.t + i * rowH, width: W, height: rowH, fill: "transparent" });
    hit.addEventListener("mousemove", e =>
      showTip(`<div class="t-title">${r.category}</div><div class="t-row"><span>Spent</span><b>${fmtUSDc(r.amount)}</b></div>`, e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
    hit.style.cursor = "pointer";
    hit.addEventListener("click", () => {
      if (r.category === "Other") return;
      const cat = state.categories.find(c => c.name === r.category);
      switchView("transactions");
      $("#txnCategory").value = r.category === "Uncategorized" ? "uncategorized" : (cat ? cat.id : "");
      loadTransactions();
    });
    svg.appendChild(hit);
  });
  container.appendChild(svg);
}

function renderLineSeries(container, pts, opts) {
  // opts: { value: p => number, label: string, color: cssvar, height, floorZero }
  container.innerHTML = "";
  if (pts.length < 2) { container.innerHTML = '<div class="empty">Not enough data in this range.</div>'; return; }
  const W = container.clientWidth || 1080, H = opts.height || 170;
  const pad = { l: 52, r: 12, t: 10, b: 22 };
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });
  const vals = pts.map(opts.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || Math.max(Math.abs(max), 1);
  let lo = min - span * 0.12, hi = max + span * 0.12;
  if (opts.floorZero && min >= 0) lo = 0;
  const x = i => pad.l + (i / (pts.length - 1)) * (W - pad.l - pad.r);
  const y = v => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  for (const tv of [lo, (lo + hi) / 2, hi]) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(tv), y2: y(tv), class: "gridline" }));
    const lbl = svgEl("text", { x: pad.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "axis-label tick-num" });
    lbl.textContent = fmtCompact(tv);
    svg.appendChild(lbl);
  }
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(opts.value(p)).toFixed(1)}`).join(" ");
  svg.appendChild(svgEl("path", { d, fill: "none", stroke: opts.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  [0, Math.floor((pts.length - 1) / 2), pts.length - 1].forEach(i => {
    const lbl = svgEl("text", { x: x(i), y: H - 6, "text-anchor": i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle", class: "axis-label" });
    lbl.textContent = pts[i].date;
    svg.appendChild(lbl);
  });

  const cross = svgEl("line", { y1: pad.t, y2: H - pad.b, class: "gridline", "stroke-dasharray": "3 3", visibility: "hidden" });
  const dot = svgEl("circle", { r: 4, fill: opts.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  svg.appendChild(cross); svg.appendChild(dot);
  const hit = svgEl("rect", { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: H - pad.t - pad.b, fill: "transparent" });
  hit.addEventListener("mousemove", e => {
    const rect = svg.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (W / rect.width);
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (pts.length - 1))));
    const v = opts.value(pts[i]);
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(v)); dot.setAttribute("visibility", "visible");
    const delta = i > 0 ? v - opts.value(pts[i - 1]) : null;
    showTip(
      `<div class="t-title">${pts[i].date}</div>` +
      `<div class="t-row"><span>${opts.label}</span><b>${fmtUSD(v)}</b></div>` +
      (delta !== null ? `<div class="t-row"><span>vs. prior</span><b>${delta >= 0 ? "+" : ""}${fmtUSD(delta)}</b></div>` : ""),
      e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  svg.appendChild(hit);
  container.appendChild(svg);
}

async function loadTrips() {
  const d = await api("/api/trips");
  const cov = d.trips.filter(t => t.premium_vs_baseline !== null);
  const totCov = cov.reduce((a, t) => a + t.total, 0);
  const premCov = cov.reduce((a, t) => a + t.premium_vs_baseline, 0);
  $("#tripsDesc").innerHTML =
    (cov.length
      ? `<b>${cov.length} trip${cov.length > 1 ? "s" : ""}</b> since full coverage began: ${fmtUSD(totCov)} total, ` +
        `about <b>${fmtUSD(premCov)}</b> more than staying home (your at-home baseline runs ${fmtUSD(d.baseline_weekly)}/week). `
      : "No trips detected in the covered period. ") +
    "All cards' charges during trip dates count, plus travel booked up to 45 days prior. Click a trip for the breakdown.";
  $("#tripsList").innerHTML = d.trips.length ? d.trips.map(t => `
    <details class="trip">
      <summary>
        <span class="when">${t.start.slice(0, 10)}${t.days > 1 ? " → " + t.end.slice(5) : ""}</span>
        <span class="where">${escapeHtml(t.location)}</span>
        <span class="prem">${t.premium_vs_baseline === null ? "before full coverage"
          : (t.premium_vs_baseline >= 0 ? "+" : "") + fmtUSD(t.premium_vs_baseline) + " vs staying home"}</span>
        <span class="amt">${fmtUSD(t.total)}</span>
      </summary>
      <div class="trip-txns">
        ${t.prepaid_travel > 0 ? `<p class="note">incl. ${fmtUSD(t.prepaid_travel)} of travel booked before the trip</p>` : ""}
        <table><tbody>
          ${t.transactions.map(x => `
            <tr><td>${x.date.slice(5)}</td>
                <td><div class="desc-main">${escapeHtml(x.description)}</div>
                    <div class="desc-sub">${escapeHtml(x.category)} · ${escapeHtml(x.account || "")}</div></td>
                <td class="num">${fmtUSDc(x.amount)}</td></tr>`).join("")}
        </tbody></table>
      </div>
    </details>`).join("")
    : '<div class="empty">No trips detected yet.</div>';
}

async function loadTimeseries() {
  const { start, end } = rangeDates(state.range);
  const d = await api(`/api/timeseries?granularity=${state.gran}&start=${start}&end=${end}&spend=${state.spendMode}`);
  $("#tsSpendLabel").textContent = state.spendMode === "variable"
    ? "Variable spending — fixed bills excluded, unlinked-card payments spread over 30 days"
    : "All spending — rent spread over its month";
  renderLineSeries($("#tsSpend"), d.points, {
    value: p => p.spend, label: "Spending", color: "var(--series-2)", height: 160, floorZero: true,
  });
  renderLineSeries($("#tsNetWorth"), d.points, {
    value: p => p.net_worth, label: "Net worth", color: "var(--series-1)", height: 160,
  });
}

function sparkline(container, hist) {
  if (hist.length < 2) return;
  const W = 300, H = 36;
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}`, class: "acct-spark", preserveAspectRatio: "none" });
  const vals = hist.map(h => h.balance);
  const min = Math.min(...vals), max = Math.max(...vals), span = (max - min) || 1;
  const x = i => (i / (hist.length - 1)) * (W - 4) + 2;
  const y = v => 4 + (1 - (v - min) / span) * (H - 8);
  const d = hist.map((h, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(h.balance).toFixed(1)}`).join(" ");
  svg.appendChild(svgEl("path", { d, class: "spark-line" }));
  container.appendChild(svg);
}

/* ---------------- views ---------------- */

/* ---------- "Right now" row + upcoming strip ---------- */

async function loadRightNow() {
  const today = new Date();
  const d30 = new Date(today - 30 * 86400e3).toISOString().slice(0, 10);
  const [o, f, ts] = await Promise.all([
    api("/api/overview?start=" + d30),
    api("/api/forecast"),
    api(`/api/timeseries?granularity=daily&start=${d30}&end=${today.toISOString().slice(0, 10)}`),
  ]);
  const nwDelta = ts.points.length > 1
    ? ts.points[ts.points.length - 1].net_worth - ts.points[0].net_worth : null;
  const low = f.low, danger = low && low.balance < 300;
  const week = f.events.filter(e => e.date <= new Date(today - -7 * 86400e3).toISOString().slice(0, 10));
  const billsOut = week.filter(e => e.amount < 0);
  const tiles = [
    { label: "Net worth", value: fmtUSD(o.net_worth),
      hint: nwDelta === null ? "—"
        : o.nw_attrib
          ? `${nwDelta >= 0 ? "+" : ""}${fmtUSD(nwDelta)} in 30d: saved ${fmtUSD(o.nw_attrib.contributions)} · ` +
            `market ${o.nw_attrib.market >= 0 ? "+" : ""}${fmtUSD(o.nw_attrib.market)} · ` +
            `cash ${(nwDelta - o.nw_attrib.invest_delta) >= 0 ? "+" : ""}${fmtUSD(nwDelta - o.nw_attrib.invest_delta)}`
          : `${nwDelta >= 0 ? "+" : ""}${fmtUSD(nwDelta)} over 30 days`,
      pos: nwDelta > 0 },
    { label: "Checking", value: fmtUSD(f.start_balance ?? o.cash),
      hint: low ? `bottoms at ${fmtUSD(low.balance)} on ${low.date.slice(5)} after scheduled bills${danger ? " ⚠" : ""}` : "no forecast" },
    { label: "Bills next 7 days", value: fmtUSD(billsOut.reduce((a, e) => a - e.amount, 0)),
      hint: billsOut.slice(0, 2).map(e => e.name).join(" · ") || "nothing scheduled" },
  ];
  $("#rnTiles").innerHTML = tiles.map(t =>
    `<div class="tile"><div class="label">${t.label}</div><div class="value${t.pos ? " pos" : ""}">${t.value}</div><div class="hint">${t.hint}</div></div>`).join("");
  const soon = f.events.filter(e => e.date <= new Date(today - -14 * 86400e3).toISOString().slice(0, 10));
  $("#upcomingStrip").innerHTML = soon.length
    ? `<span title="Scheduled items only — paychecks, rent, autopays at their typical amounts. Doesn't guess at day-to-day card spending.">Scheduled, next 2 weeks:</span> ` +
      soon.map(e => `${e.date.slice(5).replace("-", "/")} <b style="color:${e.amount > 0 ? "var(--good-text)" : "var(--ink)"}">${e.amount > 0 ? "+" : ""}${fmtUSD(e.amount)}</b> ${escapeHtml(e.name)}`).join(" · ")
    : "";
}

/* ---------- last-full-month equation row ---------- */

function monthWindow(offsetMonths, spanMonths = 1) {
  const t = new Date();
  const start = new Date(t.getFullYear(), t.getMonth() - offsetMonths, 1);
  const end = new Date(t.getFullYear(), t.getMonth() - offsetMonths + spanMonths, 0);
  const iso = d => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end), label: start.toLocaleString("en-US", { month: "long", year: "numeric" }) };
}

async function loadMonthRow() {
  const cur = monthWindow(1);                 // last full month
  const base = monthWindow(4, 3);             // the 3 months before it
  const [m, b] = await Promise.all([
    api(`/api/overview?start=${cur.start}&end=${cur.end}`),
    api(`/api/overview?start=${base.start}&end=${base.end}`),
  ]);
  $("#monthTitle").textContent = cur.label;
  const avg = k => b[k] / 3;
  const cmp = (v, a, goodWhenUp) => {
    if (!a) return "";
    const pct = Math.round(100 * (v - a) / Math.abs(a));
    if (Math.abs(pct) < 3) return `<span>≈ 3-mo avg</span>`;
    const up = pct > 0;
    const cls = (up === goodWhenUp) ? "up" : "down";
    return `<span class="${cls}">${up ? "▲" : "▼"} ${Math.abs(pct)}%</span> vs 3-mo avg`;
  };
  const invested = m.saved_from_bank + (m.bankroll_flow > 0 ? m.bankroll_flow : 0);
  const investedAvg = avg("saved_from_bank") + Math.max(avg("bankroll_flow"), 0);
  const cells = [
    { label: "Income", v: m.income_total, cmp: cmp(m.income_total, avg("income_total"), true) },
    { op: "−" },
    { label: "Spending", v: m.spend_total, cmp: cmp(m.spend_total, avg("spend_total"), false) },
    { op: "−" },
    { label: "To investments & bankroll", v: invested, cmp: cmp(invested, investedAvg, true) },
    { op: "=" },
    { label: "Kept in cash", v: m.leftover, cmp: cmp(m.leftover, avg("leftover"), true), pos: m.leftover > 0 },
  ];
  $("#eqRow").innerHTML = cells.map(c => c.op
    ? `<div class="eq-op">${c.op}</div>`
    : `<div class="eq-tile"><div class="label">${c.label}</div>
         <div class="value${c.pos ? " pos" : ""}" style="${c.label === "Kept in cash" && c.v < 0 ? "color:var(--critical)" : ""}">${fmtUSD(c.v)}</div>
         <div class="cmp">${c.cmp}</div></div>`).join("");
  $("#eqNote").innerHTML =
    `On top of this, ${fmtUSD(m.saved_payroll)} went to retirement straight from payroll (incl. employer match) — ` +
    `all in, <b>${m.wealth_rate}%</b> of total compensation became assets this month.`;
}

async function loadOverview() {
  const { start, end } = rangeDates(state.range);
  const o = await api(`/api/overview?start=${start}&end=${end}`);

  $("#syncStatus").textContent = o.has_simplefin
    ? (o.last_sync ? `SimpleFIN connected · last sync ${new Date(o.last_sync * 1000).toLocaleString()}` : "SimpleFIN connected · never synced")
    : (o.has_demo ? "Demo data · SimpleFIN not connected" : "Local only · no data yet");
  if (o.last_sync_error)
    $("#syncStatus").innerHTML += ` · <span style="color:var(--critical)" title="${escapeHtml(o.last_sync_error)}">⚠ last sync failed — ${escapeHtml(o.last_sync_error.split(" — ")[0].slice(0, 70))}</span>`;

  // Auto-sync on open if data is stale (launchd also syncs every 4h in the background)
  if (o.has_simplefin && !window._autoSynced
      && (!o.last_sync || Date.now() / 1000 - o.last_sync > 6 * 3600)) {
    window._autoSynced = true;
    $("#syncStatus").textContent += " · auto-syncing…";
    post("/api/sync?days=30")
      .then(r => { toast(`Auto-synced: ${r.added} new transactions`); refreshAll(); })
      .catch(() => toast("Auto-sync failed — see Settings"));
  }

  renderCategoryBars($("#categoryChart"), o.by_category);

  // First run: show only the welcome card until there's at least one account
  const firstRun = o.account_count === 0;
  $("#view-overview").classList.toggle("first-run", firstRun);
  $("#onboarding").hidden = !firstRun;
}

function catOptions(selected) {
  const groups = { income: [], expense: [], transfer: [] };
  for (const c of state.categories) groups[c.kind]?.push(c);
  const opt = c => `<option value="${c.id}"${c.id === selected ? " selected" : ""}>${c.name}</option>`;
  return `<option value=""${selected == null ? " selected" : ""}>— none —</option>` +
    `<optgroup label="Expenses">${groups.expense.map(opt).join("")}</optgroup>` +
    `<optgroup label="Income">${groups.income.map(opt).join("")}</optgroup>` +
    `<optgroup label="Transfers">${groups.transfer.map(opt).join("")}</optgroup>`;
}

async function loadTransactions() {
  const { start, end } = rangeDates(state.txnRange);
  const params = new URLSearchParams({ start, end });
  if ($("#txnAccount").value) params.set("account", $("#txnAccount").value);
  if ($("#txnCategory").value) params.set("category", $("#txnCategory").value);
  if ($("#txnSearch").value.trim()) params.set("search", $("#txnSearch").value.trim());
  if ($("#txnInvestment").checked) params.set("investment", "1");
  const txns = await api("/api/transactions?" + params);
  $("#txnEmpty").hidden = txns.length > 0;
  state.selected = new Set();
  updateBulkBar();
  $("#txnBody").innerHTML = txns.map(t => `
    <tr data-id="${t.id}">
      <td><input type="checkbox" class="txn-sel" data-id="${t.id}"></td>
      <td class="num" style="text-align:left">${t.date}</td>
      <td><span class="desc-sub">${t.account || ""}</span></td>
      <td><div class="desc-main">${escapeHtml(t.description || t.payee || "—")}</div>
          ${t.pending ? '<span class="pending-tag">pending</span>' : ""}</td>
      <td><select class="cat-pick${t.category_id == null ? " uncat" : ""}" data-id="${t.id}">${catOptions(t.category_id)}</select>
          <button class="btn secondary btn-sm rule-btn" data-id="${t.id}" data-desc="${escapeHtml(t.description || t.payee || "")}" data-cat="${t.category_id ?? ""}" title="Always categorize this merchant">+ rule</button></td>
      <td class="num ${t.amount > 0 ? "amount-pos" : ""}">${t.amount > 0 ? "+" : ""}${fmtUSDc(t.amount)}</td>
    </tr>`).join("");
  $$("#txnBody .txn-sel").forEach(cb => cb.addEventListener("change", () => {
    cb.checked ? state.selected.add(cb.dataset.id) : state.selected.delete(cb.dataset.id);
    updateBulkBar();
  }));
  $$("#txnBody .rule-btn").forEach(btn => btn.addEventListener("click", () => openRuleRow(btn)));
  $$("#txnBody .cat-pick").forEach(sel => sel.addEventListener("change", async () => {
    await post("/api/transactions/" + encodeURIComponent(sel.dataset.id),
      { category_id: sel.value ? Number(sel.value) : null });
    sel.classList.toggle("uncat", !sel.value);
    toast("Category updated");
  }));
}

/* ---------------- money flow (sankey) ---------------- */

async function loadFlow() {
  const q = state.flowMonth ? `&month=${state.flowMonth}` : "";
  const d = await api("/api/flow?months=6" + q);
  const el = $("#flowChart");

  // Month selector: Average + each available month
  const today = new Date();
  const curYM = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  $("#flowMonthSeg").innerHTML =
    `<button data-fm="" class="${state.flowMonth ? "" : "active"}">Average</button>` +
    (d.available_months || []).map(m =>
      `<button data-fm="${m}" class="${state.flowMonth === m ? "active" : ""}">${monthLabel(m)}${m === curYM ? "*" : ""}</button>`).join("");

  if (!d.incomes.length && !d.outflows.length) {
    el.innerHTML = '<div class="empty">Not enough data yet — sync or import at least one full month.</div>';
    return;
  }
  const fmtMonth = ym => monthLabel(ym) + " " + ym.slice(0, 4);
  $("#flowDesc").textContent = (state.flowMonth
    ? `${fmtMonth(state.flowMonth)}${state.flowMonth === curYM ? " (month to date)" : ""} actuals. `
    : `Average per month over ${d.months.length === 1 ? fmtMonth(d.months[0]) :
        fmtMonth(d.months[0]) + " – " + fmtMonth(d.months[d.months.length - 1])}` +
      ` (${d.months.length} full month${d.months.length > 1 ? "s" : ""}). `) +
    "Color = kind of money: blue essentials, orange lifestyle, teal saved. " +
    "Click any bar, ribbon, or label for the transactions behind it. " +
    "Transfers and card payoffs excluded; refunds net against their category.";

  // ----- sources column -----
  const sources = d.incomes.map(x => ({ name: x.name, v: x.monthly }));
  if (d.payroll_direct > 0.5) sources.push({ name: "Payroll deferrals (401k/Roth)", v: d.payroll_direct });
  if (d.employer_match > 0.5) sources.push({ name: "Employer 401(k) match", v: d.employer_match });
  if (d.bankroll_net < -0.5) sources.push({ name: "From betting bankroll", v: -d.bankroll_net });
  const trueIncome = sources.reduce((a, n) => a + n.v, 0);
  const deficit = d.net < -0.5 ? -d.net : 0;

  // ----- categories -> groups -----
  const GROUP_OF = {
    "Housing": "Essentials", "Utilities": "Essentials", "Insurance": "Essentials",
    "Groceries": "Essentials", "Gas & Fuel": "Essentials", "Health": "Essentials",
    "Commuter Benefit": "Essentials", "Fees & Charges": "Essentials", "Pets": "Essentials",
  };
  const GROUPS = [
    { name: "Essentials", color: "var(--series-1)", cats: [] },
    { name: "Lifestyle & fun", color: "var(--series-2)", cats: [] },
    { name: "Saved & invested", color: "var(--series-3)", cats: [] },
  ];
  const outRaw = d.outflows.slice(0, 12);
  const otherV = d.outflows.slice(12).reduce((a, x) => a + x.monthly, 0);
  if (otherV > 0.5) outRaw.push({ name: "Other spending", monthly: otherV });
  for (const c of outRaw) {
    const g = GROUP_OF[c.name] === "Essentials" ? GROUPS[0] : GROUPS[1];
    g.cats.push({ name: c.name, v: c.monthly });
  }
  for (const inv of d.investments) GROUPS[2].cats.push({ name: inv.name, v: inv.monthly });
  if (d.bankroll_net > 0.5) GROUPS[2].cats.push({ name: "Betting bankroll", v: d.bankroll_net });
  if (d.net > 0.5) GROUPS[2].cats.push({ name: "Leftover cash", v: d.net });
  for (const g of GROUPS) {
    g.cats.sort((a, b) => b.v - a.v);
    g.v = g.cats.reduce((a, c) => a + c.v, 0);
  }
  renderFlowSankey(el, sources.filter(s => s.v > 0.5),
                   GROUPS.filter(g => g.v > 0.5), trueIncome, deficit);
}

async function showFlowDetail(n) {
  // Group hubs: the chart already knows the exact members — render locally
  if (n.kind === "group" && n.cats) {
    $("#fdTitle").textContent = n.name;
    $("#fdMeta").textContent = `${fmtUSD(n.v)}/mo`;
    $("#fdExplain").textContent = "Per-month averages. Click a category leaf on the chart for its transactions.";
    $("#fdExplain").hidden = false;
    $("#fdBody").innerHTML = `<table><tbody>${n.cats.map(c =>
      `<tr><td class="desc-main">${escapeHtml(c.name)}</td><td class="num">${fmtUSD(c.v)}/mo</td></tr>`).join("")}</tbody></table>`;
    const p = $("#flowDetail");
    p.hidden = false; p.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }
  const q = state.flowMonth ? `&month=${state.flowMonth}` : "";
  const d = await api(`/api/flow/detail?months=6&node=${encodeURIComponent(n.name)}&kind=${n.kind || "category"}${q}`)
    .catch(e => ({ title: n.name, explanation: e.message, transactions: [] }));
  $("#fdTitle").textContent = d.title;
  $("#fdMeta").textContent = `${d.window || ""}${d.count ? ` · ${d.count} transaction${d.count > 1 ? "s" : ""}` : ""}` +
    (d.truncated ? " (showing largest 120)" : "");
  $("#fdExplain").textContent = d.explanation || "";
  $("#fdExplain").hidden = !d.explanation;
  if (d.breakdown) {
    $("#fdBody").innerHTML = `<table><tbody>${d.breakdown.map(b =>
      `<tr><td class="desc-main">${escapeHtml(b.name)}</td><td class="num">${fmtUSD(b.amount)}/mo</td></tr>`).join("")}</tbody></table>`;
  } else if (d.transactions && d.transactions.length) {
    $("#fdBody").innerHTML = `<table><tbody>${d.transactions.map(t => `
      <tr><td style="white-space:nowrap">${t.date}</td>
          <td><div class="desc-main">${escapeHtml(t.description)}</div>
              <div class="desc-sub">${escapeHtml(t.category)} · ${escapeHtml(t.account || "")}</div></td>
          <td class="num ${t.amount > 0 ? "amount-pos" : ""}">${t.amount > 0 ? "+" : ""}${fmtUSDc(t.amount)}</td></tr>`).join("")}
      </tbody></table>`;
  } else {
    $("#fdBody").innerHTML = "";
  }
  const panel = $("#flowDetail");
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderFlowSankey(container, sources, groups, totalIncome, deficit = 0) {
  container.innerHTML = "";
  $("#flowDetail").hidden = true;
  const cats = groups.flatMap(g => g.cats.map(c => ({ ...c, color: g.color, group: g.name, kind: "category" })));
  for (const s of sources) s.kind = "source";
  for (const g of groups) g.kind = "group";
  const W = container.clientWidth || 1000;
  const SRC_GAP = 14, GRP_GAP = 40, CAT_GAP = 8, CLUSTER_GAP = 30;
  const H = Math.max(500, Math.min(820, cats.length * 42 + groups.length * CLUSTER_GAP + 140));
  const pad = { t: 52, b: 16 };
  const labelW = Math.min(190, W * 0.20), barW = 10;
  const span = W - 2 * labelW - barW;
  const xSrc = labelW, xTot = labelW + span * 0.30, xGrp = labelW + span * 0.62, xCat = W - labelW - barW;
  const totalOut = groups.reduce((a, g) => a + g.v, 0);
  const totalIn = sources.reduce((a, n) => a + n.v, 0);
  const total = Math.max(totalIn, totalOut, 1);
  const plotH = H - pad.t - pad.b;
  const catGapsTotal = (cats.length - groups.length) * CAT_GAP + (groups.length - 1) * CLUSTER_GAP;
  const k = Math.min(
    (plotH - (sources.length - 1) * SRC_GAP) / Math.max(totalIn, 1),
    (plotH - (groups.length - 1) * GRP_GAP) / Math.max(totalOut, 1),
    (plotH - catGapsTotal) / Math.max(totalOut, 1));

  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });

  // stack sources and group hubs
  let y = pad.t;
  for (const n of sources) { n.h = Math.max(n.v * k, 3); n.y = y; y += n.h + SRC_GAP; }
  y = pad.t;
  for (const g of groups) { g.h = Math.max(g.v * k, 4); g.y = y; y += g.h + GRP_GAP; }
  // stack leaves: tight within a group, a visible break between groups
  y = pad.t;
  for (let gi = 0; gi < groups.length; gi++) {
    for (const c of cats) {
      if (c.group !== groups[gi].name) continue;
      c.h = Math.max(c.v * k, 3); c.y = y; y += c.h + CAT_GAP;
    }
    y += CLUSTER_GAP - CAT_GAP;
  }

  // faces of the total hub
  let yIn = pad.t, yOut = pad.t;
  for (const n of sources) { n.ty = yIn; yIn += n.h; }
  for (const g of groups) { g.ty = yOut; yOut += g.h; }
  const totH = Math.max(yIn, yOut) - pad.t;
  for (const g of groups) {
    let gy = g.y;
    for (const c of cats) if (c.group === g.name) { c.gy = gy; gy += c.h; }
  }

  const ribbon = (x1, y1, x2, y2, h1, h2, color) => {
    const mx = (x1 + x2) / 2;
    return svgEl("path", {
      d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2} L${x2},${y2 + h2} C${mx},${y2 + h2} ${mx},${y1 + h1} ${x1},${y1 + h1} Z`,
      fill: color, "fill-opacity": "0.33",
    });
  };
  const tipFor = n => `<div class="t-title">${escapeHtml(n.name)}</div>` +
    `<div class="t-row"><span>Per month</span><b>${fmtUSD(n.v)}</b></div>` +
    (totalIncome > 0 ? `<div class="t-row"><span>Share of income</span><b>${Math.round(100 * n.v / totalIncome)}%</b></div>` : "") +
    (n.group ? `<div class="t-row"><span>Group</span><b>${escapeHtml(n.group)}</b></div>` : "");
  function hover(elm, n, baseOp) {
    elm.style.cursor = "pointer";
    elm.addEventListener("mousemove", e => showTip(tipFor(n), e.clientX, e.clientY));
    elm.addEventListener("mouseenter", () => elm.setAttribute("fill-opacity", elm.tagName === "path" ? "0.62" : "1"));
    elm.addEventListener("mouseleave", () => { hideTip(); elm.setAttribute("fill-opacity", elm.tagName === "path" ? baseOp : "1"); });
    elm.addEventListener("click", () => showFlowDetail(n));
  }

  // ribbons under everything
  for (const n of sources) {
    const r = ribbon(xSrc + barW, n.y, xTot, n.ty, n.h, n.h, n.color || "var(--muted)");
    r.setAttribute("fill-opacity", "0.18");
    hover(r, n, "0.18"); svg.appendChild(r);
  }
  for (const g of groups) {
    const r = ribbon(xTot + barW, g.ty, xGrp, g.y, g.h, g.h, g.color);
    hover(r, g, "0.33"); svg.appendChild(r);
  }
  for (const c of cats) {
    const r = ribbon(xGrp + barW, c.gy, xCat, c.y, c.h, c.h, c.color);
    hover(r, c, "0.33"); svg.appendChild(r);
  }

  // node bars
  for (const n of sources) {
    const b = svgEl("rect", { x: xSrc, y: n.y, width: barW, height: n.h, rx: 3, fill: n.color || "var(--muted)" });
    hover(b, n, "1"); svg.appendChild(b);
  }
  const totBar = svgEl("rect", { x: xTot, y: pad.t, width: barW, height: Math.max(totH, 4), rx: 3, fill: "var(--baseline)" });
  hover(totBar, { name: "Total income", v: totalIncome, kind: "total" }, "1");
  svg.appendChild(totBar);
  // Overspend: outflows exceed income, so the bottom of the hub is fed by
  // nothing — mark that unfunded stretch in red instead of inventing a source
  if (deficit > 0.5) {
    // Hatched (not solid) so it reads as a gap being covered, not a kind of income
    const defs = svgEl("defs", {});
    const pat = svgEl("pattern", { id: "hatch", patternUnits: "userSpaceOnUse", width: 5, height: 5 });
    pat.appendChild(svgEl("path", { d: "M0,5 L5,0", stroke: "var(--neg)", "stroke-width": 1.4 }));
    defs.appendChild(pat);
    svg.appendChild(defs);
    const inH = yIn - pad.t;
    const defNode = { name: "Overspend", v: deficit, kind: "source" };
    const base = svgEl("rect", { x: xTot, y: pad.t + inH, width: barW,
      height: Math.max(totH - inH, 3), rx: 3, fill: "var(--neg)", "fill-opacity": "0.25",
      stroke: "var(--neg)", "stroke-width": 1 });
    const hatchR = svgEl("rect", { x: xTot, y: pad.t + inH, width: barW,
      height: Math.max(totH - inH, 3), rx: 3, fill: "url(#hatch)" });
    hover(base, defNode, "0.25"); hover(hatchR, defNode, "1");
    svg.appendChild(base); svg.appendChild(hatchR);
    const t = svgEl("text", { x: xTot + barW + 8, y: pad.t + inH + Math.max(totH - inH, 3) / 2 + 4,
      "text-anchor": "start", fill: "var(--neg)", "font-size": 11.5, "font-weight": 650,
      "paint-order": "stroke", stroke: "var(--surface)", "stroke-width": 4, "stroke-linejoin": "round" });
    t.textContent = `over by ${fmtUSD(deficit)}`;
    hover(t, defNode, "1");
    svg.appendChild(t);
  }
  for (const g of groups) {
    const b = svgEl("rect", { x: xGrp, y: g.y, width: barW, height: g.h, rx: 3, fill: g.color });
    hover(b, g, "1"); svg.appendChild(b);
  }
  for (const c of cats) {
    const b = svgEl("rect", { x: xCat, y: c.y, width: barW, height: c.h, rx: 3, fill: c.color });
    hover(b, c, "1"); svg.appendChild(b);
  }

  // hub labels ABOVE their bars (name, then bold value) — the reference style
  const hubLabel = (x, yTop, name, value, node, anchor = "middle") => {
    const t1 = svgEl("text", { x, y: yTop - 22, "text-anchor": anchor, fill: "var(--ink-2)",
      "font-size": 11.5, "font-weight": 600,
      "paint-order": "stroke", stroke: "var(--surface)", "stroke-width": 4, "stroke-linejoin": "round" });
    t1.textContent = name;
    const t2 = svgEl("text", { x, y: yTop - 8, "text-anchor": anchor, fill: "var(--ink)",
      "font-size": 12.5, "font-weight": 700, class: "tick-num",
      "paint-order": "stroke", stroke: "var(--surface)", "stroke-width": 4, "stroke-linejoin": "round" });
    t2.textContent = value;
    if (node) { hover(t1, node, "1"); hover(t2, node, "1"); }
    svg.appendChild(t1); svg.appendChild(t2);
  };
  hubLabel(xTot + barW / 2, pad.t, "Total income", fmtUSD(totalIncome) + "/mo",
           { name: "Total income", v: totalIncome, kind: "total" });
  for (const g of groups)
    hubLabel(xGrp + barW / 2, g.y,
      `${g.name} (${Math.round(100 * g.v / totalIncome)}%)`, fmtUSD(g.v), g);

  // outer labels: two lines (name / value), with collision avoidance
  const maxChars = Math.max(9, Math.floor((labelW - 14) / 6.3));
  function labels(nodes, anchorX, anchor) {
    let lastY = -Infinity;
    for (const n of nodes) {
      let cy = Math.max(n.y + n.h / 2, lastY + 30);
      lastY = cy;
      const t1 = svgEl("text", { x: anchorX, y: cy - 2, "text-anchor": anchor,
        fill: "var(--ink-2)", "font-size": 11.5, "font-weight": 550 });
      t1.textContent = n.name.length > maxChars ? n.name.slice(0, maxChars - 1) + "…" : n.name;
      const t2 = svgEl("text", { x: anchorX, y: cy + 12, "text-anchor": anchor,
        fill: "var(--ink)", "font-size": 12.5, "font-weight": 700, class: "tick-num" });
      t2.textContent = fmtUSD(n.v);
      hover(t1, n, "1"); hover(t2, n, "1");
      svg.appendChild(t1); svg.appendChild(t2);
    }
  }
  labels(sources, xSrc - 8, "end");
  labels(cats, xCat + barW + 8, "start");
  container.appendChild(svg);

  // Plain-math reconciliation footer — the sentence version of the diagram
  const spent = groups.filter(g => g.name !== "Saved & invested").reduce((a, g) => a + g.v, 0);
  const savedG = groups.find(g => g.name === "Saved & invested");
  const leftover = savedG ? (savedG.cats.find(c => c.name === "Leftover cash") || { v: 0 }).v : 0;
  const foot = document.createElement("p");
  foot.className = "note";
  foot.style.cssText = "margin:10px 2px 0;font-size:13px";
  foot.innerHTML = deficit > 0.5
    ? `Money in <b>${fmtUSD(totalIncome)}</b>/mo − money out <b>${fmtUSD(totalOut)}</b>/mo: ` +
      `<b style="color:var(--neg)">you went over by ${fmtUSD(deficit)}/mo</b> (the hatched notch).`
    : `Money in <b>${fmtUSD(totalIncome)}</b>/mo — fully allocated: spent ${fmtUSD(spent)}, ` +
      `saved & invested ${fmtUSD(savedG ? savedG.v : 0)}` +
      (leftover > 0.5 ? ` <b style="color:var(--good-text)">(incl. ${fmtUSD(leftover)} simply left in cash)</b>` : "") + ".";
  container.appendChild(foot);
}



/* ---------------- gambling ---------------- */

function renderFunding(container, monthly) {
  container.innerHTML = "";
  if (!monthly.length) { container.innerHTML = '<div class="empty">No bankroll movements found.</div>'; return; }
  const W = container.clientWidth || 1080, H = 220;
  const pad = { l: 46, r: 10, t: 10, b: 26 };
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });
  const maxV = Math.max(...monthly.flatMap(m => [m.deposited, m.withdrawn]), 1);
  const ticks = niceTicks(maxV);
  const top = ticks[ticks.length - 1];
  const y = v => pad.t + (1 - v / top) * (H - pad.t - pad.b);
  const iw = (W - pad.l - pad.r) / monthly.length;
  for (const tv of ticks) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(tv), y2: y(tv), class: tv === 0 ? "baseline" : "gridline" }));
    const lbl = svgEl("text", { x: pad.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "axis-label tick-num" });
    lbl.textContent = fmtCompact(tv);
    svg.appendChild(lbl);
  }
  const groupW = Math.min(iw * 0.62, 56), barW = (groupW - 2) / 2;
  monthly.forEach((m, i) => {
    const cx = pad.l + iw * i + iw / 2, x0 = cx - groupW / 2;
    [{ v: m.deposited, c: "var(--series-2)" }, { v: m.withdrawn, c: "var(--series-1)" }].forEach((s, si) => {
      const by = y(s.v), bh = H - pad.b - by;
      if (bh > 0.5) svg.appendChild(svgEl("path", { d: barPath(x0 + si * (barW + 2), by, barW, bh, 4), fill: s.c }));
    });
    const lbl = svgEl("text", { x: cx, y: H - 8, "text-anchor": "middle", class: "axis-label" });
    lbl.textContent = monthLabel(m.month);
    svg.appendChild(lbl);
    const hit = svgEl("rect", { x: pad.l + iw * i, y: pad.t, width: iw, height: H - pad.t - pad.b, fill: "transparent" });
    hit.addEventListener("mousemove", e => showTip(
      `<div class="t-title">${monthLabel(m.month)} ${m.month.slice(0, 4)}</div>` +
      `<div class="t-row"><span class="t-key"><span class="swatch" style="background:var(--series-2)"></span>Deposited</span><b>${fmtUSD(m.deposited)}</b></div>` +
      `<div class="t-row"><span class="t-key"><span class="swatch" style="background:var(--series-1)"></span>Withdrawn</span><b>${fmtUSD(m.withdrawn)}</b></div>`,
      e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(hit);
  });
  container.appendChild(svg);
}

function renderPLBars(container, byMonth) {
  container.innerHTML = "";
  if (!byMonth.length) return;
  const W = container.clientWidth || 1080, H = 200;
  const pad = { l: 50, r: 10, t: 12, b: 24 };
  const svg = svgEl("svg", { width: "100%", height: H, viewBox: `0 0 ${W} ${H}` });
  const maxAbs = Math.max(...byMonth.map(m => Math.abs(m.profit)), 1);
  const y = v => pad.t + (1 - (v + maxAbs) / (2 * maxAbs)) * (H - pad.t - pad.b);
  const iw = (W - pad.l - pad.r) / byMonth.length;
  for (const tv of [-maxAbs, 0, maxAbs]) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(tv), y2: y(tv), class: tv === 0 ? "baseline" : "gridline" }));
    const lbl = svgEl("text", { x: pad.l - 8, y: y(tv) + 4, "text-anchor": "end", class: "axis-label tick-num" });
    lbl.textContent = fmtCompact(tv);
    svg.appendChild(lbl);
  }
  byMonth.forEach((m, i) => {
    const bw = Math.min(iw * 0.5, 40);
    const x0 = pad.l + iw * i + (iw - bw) / 2;
    const y0 = y(Math.max(m.profit, 0)), y1 = y(Math.min(m.profit, 0));
    svg.appendChild(svgEl("rect", { x: x0, y: y0, width: bw, height: Math.max(y1 - y0, 1.5), rx: 3,
      fill: m.profit >= 0 ? "var(--series-1)" : "var(--neg)" }));
    const lbl = svgEl("text", { x: x0 + bw / 2, y: H - 6, "text-anchor": "middle", class: "axis-label" });
    lbl.textContent = monthLabel(m.month);
    svg.appendChild(lbl);
    const hit = svgEl("rect", { x: pad.l + iw * i, y: pad.t, width: iw, height: H - pad.t - pad.b, fill: "transparent" });
    hit.addEventListener("mousemove", e => showTip(
      `<div class="t-title">${monthLabel(m.month)} ${m.month.slice(0, 4)}</div>` +
      `<div class="t-row"><span>Bet P/L</span><b>${m.profit >= 0 ? "+" : ""}${fmtUSDc(m.profit)}</b></div>` +
      `<div class="t-row"><span>Staked</span><b>${fmtUSD(m.staked)}</b></div>`, e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
    svg.appendChild(hit);
  });
  container.appendChild(svg);
}

async function loadGambling() {
  const d = await api("/api/gambling/summary");
  const br = d.bankroll;
  const tiles = [
    { label: "Bankroll (est.)", value: br.estimate === null ? "—" : fmtUSD(br.estimate),
      hint: br.estimate === null ? "save a snapshot to start tracking" : `snapshot ${br.snapshot_date} ± transfers since` },
    { label: "Net funded", value: fmtUSD(d.net_funded), hint: `${fmtUSD(d.deposited)} in · ${fmtUSD(d.withdrawn)} out (loaded history)` },
    { label: "Implied P/L", value: d.implied_pl === null ? "—" : (d.implied_pl >= 0 ? "+" : "") + fmtUSD(d.implied_pl),
      hint: "bankroll + withdrawals − deposits", pos: d.implied_pl > 0 },
  ];
  if (d.tools && d.tools.total > 0.5) {
    tiles.push({ label: "Tools & research", value: fmtUSD(d.tools.last90 / 3) + "/mo",
      hint: `OddsJam etc. — ${fmtUSD(d.tools.total)} lifetime (counts as spending, not bankroll)` });
  }
  if (d.bets) {
    tiles.push({ label: "Bet P/L (Pikkit)", value: (d.bets.profit >= 0 ? "+" : "") + fmtUSD(d.bets.profit),
      hint: `${d.bets.count} settled · ROI ${d.bets.roi_pct}% · win ${d.bets.win_rate}%`, pos: d.bets.profit > 0 });
  }
  $("#gamblingTiles").innerHTML = tiles.map(t =>
    `<div class="tile"><div class="label">${t.label}</div><div class="value${t.pos ? " pos" : ""}">${t.value}</div><div class="hint">${t.hint}</div></div>`).join("");

  renderFunding($("#gamblingFundingChart"), d.monthly);
  $("#platformBody").innerHTML = d.platforms.map(p => `
    <tr class="plat-row" data-plat="${escapeHtml(p.name)}" style="cursor:pointer" title="Click for this app's transactions">
        <td class="desc-main">${escapeHtml(p.name)}</td>
        <td class="num">${fmtUSDc(p.deposited)}</td>
        <td class="num">${fmtUSDc(p.withdrawn)}</td>
        <td class="num">${fmtUSDc(p.net)}</td></tr>`).join("")
    || '<tr><td colspan="4" class="empty">No platform flows found.</td></tr>';
  $$("#platformBody .plat-row").forEach(tr => tr.addEventListener("click", async () => {
    const pd = await api("/api/gambling/platform?name=" + encodeURIComponent(tr.dataset.plat));
    $("#platformDetail").hidden = false;
    $("#platformDetail").innerHTML =
      `<p class="note"><b>${escapeHtml(pd.name)}</b> — ${pd.transactions.length} transactions. ` +
      `Negative = money to the app, positive = money back. ` +
      `Anything misfiled? Fix its category on the Transactions tab (set it to “Gambling & Betting”) and it moves here.</p>` +
      `<table><tbody>${pd.transactions.map(t => `
        <tr><td style="white-space:nowrap">${t.date}</td>
            <td><div class="desc-main">${escapeHtml(t.description)}</div>
                <div class="desc-sub">${escapeHtml(t.account || "")}</div></td>
            <td class="num ${t.amount > 0 ? "amount-pos" : ""}">${t.amount > 0 ? "+" : ""}${fmtUSDc(t.amount)}</td></tr>`).join("")}
      </tbody></table>`;
    $("#platformDetail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));

  const snaps = await api("/api/gambling/snapshots");
  $("#snapList").innerHTML = snaps.length
    ? "History: " + snaps.slice(0, 5).map(s => `${s.date} — ${fmtUSD(s.amount)}`).join(" · ")
    : "No snapshots yet.";

  if (d.bets) {
    $("#betsSummary").innerHTML =
      `<p class="note">${d.bets.count} settled bets, ${fmtUSD(d.bets.staked)} staked, ` +
      `<b>${d.bets.profit >= 0 ? "+" : ""}${fmtUSDc(d.bets.profit)}</b> profit` +
      (d.bets.pending ? ` · ${d.bets.pending} pending (${fmtUSD(d.bets.pending_stake)} at risk)` : "") + "</p>";
    renderPLBars($("#betsPLChart"), d.bets.by_month);
    $("#betsBookTable").hidden = false;
    $("#betsBookBody").innerHTML = d.bets.by_book.map(b => `
      <tr><td class="desc-main">${escapeHtml(b.book)}</td><td class="num">${b.count}</td>
          <td class="num">${fmtUSDc(b.staked)}</td>
          <td class="num ${b.profit > 0 ? "amount-pos" : ""}">${b.profit >= 0 ? "+" : ""}${fmtUSDc(b.profit)}</td>
          <td class="num">${b.staked ? Math.round(100 * b.profit / b.staked) + "%" : "—"}</td></tr>`).join("");
  } else {
    $("#betsSummary").innerHTML = '<p class="note">No bets imported yet — export your history from Pikkit and import it above for P/L, ROI, and per-book breakdowns.</p>';
    $("#betsPLChart").innerHTML = "";
    $("#betsBookTable").hidden = true;
  }
}

$("#btnAddSnapshot").addEventListener("click", async () => {
  const amount = Number($("#snapAmount").value);
  if (!(amount >= 0)) return toast("Enter the current bankroll amount");
  await post("/api/gambling/snapshots", { amount, date: $("#snapDate").value || undefined });
  $("#snapAmount").value = "";
  toast("Bankroll snapshot saved");
  loadGambling();
  loadOverview().catch(() => {});
});

$("#btnImportBets").addEventListener("click", async () => {
  const file = $("#betsFile").files[0];
  const st = $("#betsStatus");
  if (!file) { st.textContent = "Choose the Pikkit CSV export first."; st.className = "note err"; return; }
  const fd = new FormData();
  fd.append("file", file);
  $("#btnImportBets").disabled = true;
  try {
    const r = await api("/api/gambling/import", { method: "POST", body: fd });
    st.textContent = `Imported ${r.added} bets, ${r.settled_updates} pending bets settled.`;
    st.className = "note ok";
    loadGambling();
  } catch (e) {
    st.textContent = e.message; st.className = "note err";
  } finally { $("#btnImportBets").disabled = false; }
});

/* ---------------- income ---------------- */

const CADENCE_LABEL = { weekly: "weekly", biweekly: "biweekly", semimonthly: "2×/month",
                        monthly: "monthly", irregular: "irregular" };
const SRC_STATUS = {
  ok:    { icon: "✓", label: "On track", cls: "ok" },
  late:  { icon: "▲", label: "Late",     cls: "late" },
  short: { icon: "●", label: "Short",    cls: "short" },
};

async function loadIncome() {
  const d = await api("/api/income?months=4");
  const hasSources = d.sources.length > 0;
  $("#srcEmpty").hidden = hasSources;

  // Watch panel
  $("#incomeWatch").innerHTML = hasSources ? d.sources.map(s => {
    const st = SRC_STATUS[s.status];
    return `<div class="watch-row">
      <span class="badge-status ${st.cls}"><span class="icon">${st.icon}</span>${st.label}</span>
      <div class="flag-body">
        <div class="flag-title">${escapeHtml(s.name)}</div>
        <div class="flag-meta">${fmtUSDc(s.amount)} ${CADENCE_LABEL[s.cadence]} · expected ≈ ${fmtUSD(s.expected_monthly)}/mo
          ${s.last_date ? ` · last: ${s.last_date} (${fmtUSDc(s.last_amount)})` : ""}</div>
        ${s.status !== "ok" ? `<div class="flag-detail">${escapeHtml(s.status_detail)}</div>` : ""}
      </div>
      <button class="btn secondary btn-sm" data-delsrc="${s.id}">Delete</button>
    </div>`;
  }).join("") : '<div class="empty">Add an income source below to start the watch.</div>';

  // Expected vs actual grid
  if (hasSources || Object.values(d.other).some(v => v > 0)) {
    const head = `<tr><th>Source</th>${d.months.map(m => `<th class="num">${monthLabel(m)} ${m.slice(2, 4)}</th>`).join("")}<th class="num">Expected/mo</th></tr>`;
    const currentMonth = d.months[d.months.length - 1];
    const rows = d.sources.map(s => `<tr>
      <td><div class="desc-main">${escapeHtml(s.name)}</div></td>
      ${d.months.map(m => {
        const v = s.monthly[m] || 0;
        // current (partial) month is judged by the watch, not the monthly total
        const shortfall = m !== currentMonth && s.cadence !== "irregular" && v < 0.9 * s.expected_monthly;
        return `<td class="num">${v ? fmtUSD(v) : "—"}${shortfall ? ' <span class="delta-bad" title="Below expected">▼</span>' : ""}</td>`;
      }).join("")}
      <td class="num" style="color:var(--muted)">${fmtUSD(s.expected_monthly)}</td>
    </tr>`);
    rows.push(`<tr>
      <td><div class="desc-main">Other / unassigned</div>
          <div class="desc-sub">inflows matching no source</div></td>
      ${d.months.map(m => `<td class="num">${d.other[m] ? fmtUSD(d.other[m]) : "—"}</td>`).join("")}
      <td class="num" style="color:var(--muted)">—</td>
    </tr>`);
    const totalRow = `<tr class="total-row">
      <td><b>Total</b></td>
      ${d.months.map(m => {
        const t = d.sources.reduce((a, s) => a + (s.monthly[m] || 0), 0) + (d.other[m] || 0);
        return `<td class="num"><b>${fmtUSD(t)}</b></td>`;
      }).join("")}
      <td class="num" style="color:var(--muted)">${fmtUSD(d.sources.reduce((a, s) => a + s.expected_monthly, 0))}</td>
    </tr>`;
    const examples = d.other_examples.length
      ? `<p class="note" style="margin-top:10px">Largest unassigned inflows: ${d.other_examples.slice(0, 4)
          .map(e => `${escapeHtml(e.description.slice(0, 28))} (${fmtUSD(e.amount)}, ${e.date})`).join(" · ")}</p>`
      : "";
    $("#incomeGrid").innerHTML = `<table><thead>${head}</thead><tbody>${rows.join("")}${totalRow}</tbody></table>${examples}`;
  } else {
    $("#incomeGrid").innerHTML = '<div class="empty">No income data yet.</div>';
  }

  $$("#incomeWatch [data-delsrc]").forEach(btn => btn.addEventListener("click", async () => {
    await api("/api/income/sources/" + btn.dataset.delsrc, { method: "DELETE" });
    loadIncome();
  }));
}

/* ---- bulk select + rule-from-row ---- */
function merchantKey(desc) {
  let d = (desc || "").toLowerCase();
  for (const pre of ["aplpay ", "apple pay ", "tst* ", "tst*", "spo*", "sq *", "paypal *", "orig co name:"])
    if (d.startsWith(pre)) d = d.slice(pre.length);
  d = d.replace(/[#*]?\d[\d\-\/:]*/g, " ").replace(/\s+/g, " ").trim();
  return d.split(" ").filter(t => t.length > 1).slice(0, 2).join(" ");
}
function updateBulkBar() {
  const n = state.selected ? state.selected.size : 0;
  $("#bulkBar").hidden = n === 0;
  $("#bulkCount").textContent = `${n} selected →`;
  if (n && !$("#bulkCategory").options.length) $("#bulkCategory").innerHTML = catOptions(null);
}
$("#txnSelAll").addEventListener("change", e => {
  $$("#txnBody .txn-sel").forEach(cb => { cb.checked = e.target.checked; e.target.checked ? state.selected.add(cb.dataset.id) : state.selected.delete(cb.dataset.id); });
  updateBulkBar();
});
$("#btnBulkClear").addEventListener("click", () => { state.selected.clear(); $$("#txnBody .txn-sel").forEach(cb => cb.checked = false); $("#txnSelAll").checked = false; updateBulkBar(); });
$("#btnBulkApply").addEventListener("click", async () => {
  const cid = $("#bulkCategory").value ? Number($("#bulkCategory").value) : null;
  const r = await post("/api/transactions/bulk", { ids: [...state.selected], category_id: cid });
  toast(`${r.updated} transactions recategorized`);
  refreshAll();
});
function openRuleRow(btn) {
  const tr = btn.closest("tr");
  const existing = tr.nextElementSibling;
  if (existing && existing.classList.contains("rule-row")) { existing.remove(); return; }
  const row = document.createElement("tr");
  row.className = "rule-row";
  row.innerHTML = `<td colspan="6"><div class="rule-form">
      <span>Always categorize descriptions containing</span>
      <input class="rf-pattern" value="${escapeHtml(merchantKey(btn.dataset.desc))}" style="width:200px">
      <span>as</span><select class="rf-cat">${catOptions(btn.dataset.cat ? Number(btn.dataset.cat) : null)}</select>
      <button class="btn btn-sm rf-go">Create rule &amp; apply</button>
      <span class="note">Applies to every past and future match (manual edits are never overwritten).</span>
    </div></td>`;
  tr.after(row);
  row.querySelector(".rf-go").addEventListener("click", async () => {
    const pattern = row.querySelector(".rf-pattern").value.trim();
    const cid = row.querySelector(".rf-cat").value;
    if (!pattern || !cid) return toast("Pattern and category are required");
    await post("/api/rules", { pattern, category_id: Number(cid), priority: 50 });
    const r = await post("/api/rules/apply");
    toast(`Rule added — ${r.changed} transactions updated`);
    refreshAll();
  });
}

/* ---- rule suggestions (Settings) ---- */
async function loadRuleSuggestions() {
  const s = await api("/api/rules/suggestions");
  const el = $("#ruleSuggest");
  if (!s.length) { el.innerHTML = '<p class="note">No repeat merchants waiting for a rule — nice.</p>'; return; }
  el.innerHTML = `<p class="note"><b>Suggested rules</b> — merchants that keep landing in Miscellaneous or uncategorized, biggest first:</p>` +
    s.map((g, i) => `<div class="suggest-row">
      <span class="ex" title="${escapeHtml(g.example)}">${escapeHtml(g.example.slice(0, 34))} · ${g.count}× · ${fmtUSD(g.total)}</span>
      <input class="sg-pattern" value="${escapeHtml(g.pattern)}" style="width:150px">
      <select class="sg-cat">${catOptions(null)}</select>
      <button class="btn btn-sm sg-go" data-i="${i}">Add rule</button>
    </div>`).join("");
  $$("#ruleSuggest .sg-go").forEach(btn => btn.addEventListener("click", async () => {
    const row = btn.closest(".suggest-row");
    const pattern = row.querySelector(".sg-pattern").value.trim(), cid = row.querySelector(".sg-cat").value;
    if (!pattern || !cid) return toast("Pick a category first");
    await post("/api/rules", { pattern, category_id: Number(cid), priority: 50 });
    const r = await post("/api/rules/apply");
    toast(`Rule added — ${r.changed} transactions updated`);
    refreshAll();
  }));
}

/* ---- office-day economics ---- */
async function loadOfficeDays() {
  const d = await api("/api/officedays");
  $("#officeCard").hidden = !d.enabled;
  if (!d.enabled) return;
  const extra = d.office_spend - d.wfh_spend;
  $("#officeDesc").textContent =
    `Last ${d.weeks} weeks, using "${d.marker}" as your in-office marker: ${d.office_per_week} office days/week. ` +
    `An office weekday runs ${fmtUSD(d.office_spend)} in variable spending vs ${fmtUSD(d.wfh_spend)} at home — ` +
    `${extra >= 0 ? "about " + fmtUSD(extra) + " more" : "about " + fmtUSD(-extra) + " less"} per office day` +
    ` (≈ ${fmtUSD(Math.abs(extra) * d.office_per_week * 4.33)}/month${extra >= 0 ? " for going in" : " saved by going in"}).`;
  $("#officeTiles").innerHTML = [
    { label: "Office days / week", value: d.office_per_week, hint: `${d.office_days} days in window` },
    { label: "Office day spend", value: fmtUSD(d.office_spend), hint: `food, transport, groceries part: ${fmtUSD(d.office_cluster)}` },
    { label: "Home day spend", value: fmtUSD(d.wfh_spend), hint: `same categories: ${fmtUSD(d.wfh_cluster)}` },
  ].map(t => `<div class="tile"><div class="label">${t.label}</div><div class="value">${t.value}</div><div class="hint">${t.hint}</div></div>`).join("");
}

const SEV_ICON = { critical: "⛔", serious: "▲", warning: "●" };
const SEV_LABEL = { critical: "Critical", serious: "Unusual", warning: "Check" };
const FLAG_LABEL = { large: "Large outflow", duplicate: "Possible duplicate", new_merchant: "New merchant", uncategorized: "Uncategorized" };

async function loadFlags() {
  const flags = await api("/api/flags");
  $("#flagCount").hidden = flags.length === 0;
  $("#flagCount").textContent = flags.length;
  $("#flagEmpty").hidden = flags.length > 0;
  $("#flagList").innerHTML = flags.map(f => `
    <div class="flag-row" data-id="${f.id}">
      <div class="flag-badges">${f.reasons.map(r =>
        `<span class="badge-status ${r.severity}"><span class="icon">${SEV_ICON[r.severity]}</span>${FLAG_LABEL[r.type] || r.type}</span>`).join("")}
      </div>
      <div class="flag-body">
        <div class="flag-title">${escapeHtml(f.description || f.payee || "—")}</div>
        <div class="flag-meta">${f.date} · ${f.account}${f.category ? " · " + f.category : ""}</div>
        <div class="flag-detail">${f.reasons.map(r => escapeHtml(r.detail)).join(" · ")}</div>
      </div>
      <div class="flag-amount">${f.amount > 0 ? "+" : ""}${fmtUSDc(f.amount)}</div>
      <button class="btn secondary btn-sm" data-review="${f.id}">Mark reviewed</button>
    </div>`).join("");
  $$("#flagList [data-review]").forEach(btn => btn.addEventListener("click", async () => {
    await post("/api/transactions/" + encodeURIComponent(btn.dataset.review), { reviewed: true });
    btn.closest(".flag-row").remove();
    const left = $$("#flagList .flag-row").length;
    $("#flagCount").textContent = left;
    $("#flagCount").hidden = left === 0;
    $("#flagEmpty").hidden = left > 0;
  }));
}

const TYPE_LABEL = { checking: "Checking", savings: "Savings", credit: "Credit card", retirement: "Retirement", investment: "Investment", unknown: "Unassigned" };

async function loadAccounts() {
  state.accounts = await api("/api/accounts");
  const sel = $("#txnAccount");
  const cur = sel.value;
  sel.innerHTML = '<option value="">All accounts</option>' +
    state.accounts.filter(a => !a.hidden).map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
  sel.value = cur;

  $("#acctGrid").innerHTML = state.accounts.length ? state.accounts.map(a => `
    <div class="acct-card" style="${a.hidden ? "opacity:.5" : ""}">
      <div class="acct-head">
        <div><div class="acct-name">${escapeHtml(a.name)}</div>
             <div class="acct-org">${escapeHtml(a.org_name || "")}${a.is_demo ? " · demo" : ""}</div></div>
        <div class="acct-bal">${fmtUSDc(a.balance)}</div>
      </div>
      <div class="acct-spark-slot" data-spark="${a.id}"></div>
      ${a.holdings && a.holdings.length ? `<table class="holdings"><tbody>${a.holdings.map(h => `
        <tr><td class="desc-sub">${escapeHtml(h.symbol || h.description)}</td>
            <td class="desc-sub">${h.shares ? (+h.shares).toFixed(h.shares % 1 ? 3 : 0) + " sh" : ""}</td>
            <td class="num desc-sub">${fmtUSDc(h.market_value)}</td></tr>`).join("")}
      </tbody></table>` : ""}
      <div class="acct-foot">
        <select data-type="${a.id}">
          ${Object.entries(TYPE_LABEL).map(([v, l]) => `<option value="${v}"${a.type === v ? " selected" : ""}>${l}</option>`).join("")}
        </select>
        <button class="btn secondary btn-sm" data-hide="${a.id}">${a.hidden ? "Unhide" : "Hide"}</button>
      </div>
    </div>`).join("")
    : '<div class="empty">No accounts yet — seed demo data or connect SimpleFIN in Settings.</div>';

  for (const a of state.accounts) {
    const slot = $(`[data-spark="${CSS.escape(a.id)}"]`);
    if (slot && a.history) sparkline(slot, a.history);
  }
  $$("#acctGrid [data-type]").forEach(sel2 => sel2.addEventListener("change", async () => {
    await post("/api/accounts/" + encodeURIComponent(sel2.dataset.type), { type: sel2.value });
    toast("Account type updated"); refreshAll();
  }));
  $$("#acctGrid [data-hide]").forEach(btn => btn.addEventListener("click", async () => {
    const a = state.accounts.find(x => x.id === btn.dataset.hide);
    await post("/api/accounts/" + encodeURIComponent(a.id), { hidden: !a.hidden });
    refreshAll();
  }));
}

async function loadCategoriesAndRules() {
  state.categories = await api("/api/categories");
  const catSel = $("#txnCategory");
  const cur = catSel.value;
  catSel.innerHTML = '<option value="">All categories</option><option value="uncategorized">Uncategorized</option>' +
    state.categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");
  catSel.value = cur;
  $("#ruleCategory").innerHTML = state.categories.map(c => `<option value="${c.id}">${c.name}</option>`).join("");

  const rules = await api("/api/rules");
  $("#rulesBody").innerHTML = rules.map(r => `
    <tr><td><code>${escapeHtml(r.pattern)}</code></td><td>${r.category}</td>
        <td class="num" style="text-align:left">${r.priority}</td>
        <td><button class="btn secondary btn-sm" data-delrule="${r.id}">Delete</button></td></tr>`).join("");
  $$("#rulesBody [data-delrule]").forEach(btn => btn.addEventListener("click", async () => {
    await api("/api/rules/" + btn.dataset.delrule, { method: "DELETE" });
    loadCategoriesAndRules();
  }));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- wiring ---------------- */

function switchView(v) {
  state.view = v;
  $$("nav.tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(el => el.classList.toggle("active", el.id === "view-" + v));
  if (v === "flow") loadFlow().catch(err => toast(err.message));  // re-render at real width
  if (v === "gambling") loadGambling().catch(err => toast(err.message));
}

function refreshAll() {
  loadOverview().catch(err => toast(err.message));
  loadRightNow().catch(() => {});
  loadMonthRow().catch(() => {});
  loadTrips().catch(() => {});
  loadTimeseries().catch(() => {});
  loadFlags().catch(() => {});
  loadAccounts().catch(() => {});
  loadIncome().catch(() => {});
  loadGambling().catch(() => {});
  loadPrefs().catch(() => {});
  loadOfficeDays().catch(() => {});
  loadCategoriesAndRules().then(() => { loadTransactions(); loadRuleSuggestions().catch(() => {}); }).catch(() => {});
}

$("#tabs").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (b) switchView(b.dataset.view);
});

$("#rangeSeg").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  state.range = b.dataset.range;
  $$("#rangeSeg button").forEach(x => x.classList.toggle("active", x === b));
  loadOverview().catch(err => toast(err.message));
  loadTimeseries().catch(() => {});
});
$("#granSeg").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  state.gran = b.dataset.g;
  $$("#granSeg button").forEach(x => x.classList.toggle("active", x === b));
  loadTimeseries().catch(err => toast(err.message));
});
async function loadPrefs() {
  const p = await api("/api/prefs");
  $("#prefState").value = p.home_state;
  $("#prefDefer").value = p.k401_defer_pct;
  $("#prefMatch").value = p.k401_match_pct;
  $("#prefNames").value = p.display_names.map(([pat, name]) => `${pat} = ${name}`).join("\n");
  $("#prefOffice").value = p.office_pattern || "";
}
$("#btnSavePrefs").addEventListener("click", async () => {
  const names = $("#prefNames").value.split("\n")
    .map(l => l.split("=")).filter(a => a.length >= 2)
    .map(a => [a[0].trim(), a.slice(1).join("=").trim()]).filter(a => a[0] && a[1]);
  await post("/api/prefs", {
    home_state: $("#prefState").value,
    k401_defer_pct: $("#prefDefer").value,
    k401_match_pct: $("#prefMatch").value,
    office_pattern: $("#prefOffice").value,
    display_names: names,
  });
  $("#prefsStatus").textContent = "Saved ✓";
  setTimeout(() => $("#prefsStatus").textContent = "", 2500);
  refreshAll();
});

$("#btnObDemo").addEventListener("click", async () => {
  const r = await post("/api/demo/seed");
  toast(`Loaded ${r.transactions} demo transactions — explore away`);
  refreshAll();
});
$("#btnObConnect").addEventListener("click", () => switchView("settings"));

$("#fdClose").addEventListener("click", () => { $("#flowDetail").hidden = true; });
$("#flowMonthSeg").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  state.flowMonth = b.dataset.fm;
  loadFlow().catch(err => toast(err.message));
});
$("#spendModeSeg").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  state.spendMode = b.dataset.m;
  $$("#spendModeSeg button").forEach(x => x.classList.toggle("active", x === b));
  loadTimeseries().catch(err => toast(err.message));
});
$("#txnRangeSeg").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  state.txnRange = b.dataset.range;
  $$("#txnRangeSeg button").forEach(x => x.classList.toggle("active", x === b));
  loadTransactions();
});
$("#txnAccount").addEventListener("change", loadTransactions);
$("#txnCategory").addEventListener("change", loadTransactions);
$("#txnInvestment").addEventListener("change", loadTransactions);
let searchT;
$("#txnSearch").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(loadTransactions, 250); });

/* settings */
$("#btnConnect").addEventListener("click", async () => {
  const token = $("#setupToken").value.trim();
  if (!token) return toast("Paste a setup token first");
  $("#btnConnect").disabled = true;
  try {
    const r = await post("/api/simplefin/connect", { setup_token: token });
    $("#setupToken").value = "";
    $("#sfStatus").textContent = r.message;
    $("#sfStatus").className = "note ok";
    refreshAll();
  } catch (e) {
    $("#sfStatus").textContent = e.message;
    $("#sfStatus").className = "note err";
  } finally { $("#btnConnect").disabled = false; }
});
$("#btnSync").addEventListener("click", async () => {
  $("#btnSync").disabled = true;
  $("#sfStatus").textContent = "Syncing… (this can take a minute)";
  $("#sfStatus").className = "note";
  try {
    const r = await post("/api/sync?days=" + $("#syncDays").value);
    $("#sfStatus").textContent = `Synced ${r.accounts} accounts, ${r.added} new transactions.` + (r.warnings ? ` Warnings: ${r.warnings}` : "");
    $("#sfStatus").className = "note ok";
    refreshAll();
  } catch (e) {
    $("#sfStatus").textContent = e.message;
    $("#sfStatus").className = "note err";
  } finally { $("#btnSync").disabled = false; }
});
$("#btnDisconnect").addEventListener("click", async () => {
  await post("/api/simplefin/disconnect");
  $("#sfStatus").textContent = "Disconnected. The stored access URL was deleted.";
  $("#sfStatus").className = "note";
  refreshAll();
});
$("#btnSeedDemo").addEventListener("click", async () => {
  const r = await post("/api/demo/seed");
  toast(`Seeded ${r.transactions} demo transactions`);
  refreshAll();
});
$("#btnClearDemo").addEventListener("click", async () => {
  await post("/api/demo/clear");
  toast("Demo data cleared");
  refreshAll();
});
$("#btnAddRule").addEventListener("click", async () => {
  const pattern = $("#rulePattern").value.trim();
  if (!pattern) return toast("Enter a pattern");
  await post("/api/rules", { pattern, category_id: Number($("#ruleCategory").value), priority: Number($("#rulePriority").value) || 100 });
  $("#rulePattern").value = "";
  toast("Rule added — click Re-apply to categorize existing transactions");
  loadCategoriesAndRules();
});
$("#btnApplyRules").addEventListener("click", async () => {
  const r = await post("/api/rules/apply");
  toast(`Rules re-applied — ${r.changed} transactions updated`);
  refreshAll();
});
$("#btnAddSource").addEventListener("click", async () => {
  const body = {
    name: $("#srcName").value.trim(),
    pattern: $("#srcPattern").value.trim(),
    amount: Number($("#srcAmount").value),
    cadence: $("#srcCadence").value,
  };
  if (!body.name || !body.pattern || !(body.amount > 0)) return toast("Name, pattern, and amount are required");
  try {
    await post("/api/income/sources", body);
    $("#srcName").value = $("#srcPattern").value = $("#srcAmount").value = "";
    toast("Income source added");
    loadIncome();
  } catch (e) { toast(e.message); }
});

$("#btnImport").addEventListener("click", async () => {
  const file = $("#importFile").files[0];
  const name = $("#importName").value.trim();
  const st = $("#importStatus");
  if (!file || !name) { st.textContent = "Choose a file and give the account a name."; st.className = "note err"; return; }
  const fd = new FormData();
  fd.append("file", file);
  fd.append("account_name", name);
  fd.append("account_type", $("#importType").value);
  fd.append("invert", $("#importInvert").checked ? "1" : "0");
  $("#btnImport").disabled = true;
  st.textContent = "Importing…"; st.className = "note";
  try {
    const r = await api("/api/import", { method: "POST", body: fd });
    st.textContent = `Imported ${r.added} transactions (${r.format.toUpperCase()}), ` +
      `${r.skipped_duplicates} duplicates skipped, ${r.transfer_matched} matched as transfers.`;
    st.className = "note ok";
    refreshAll();
  } catch (e) {
    st.textContent = e.message; st.className = "note err";
  } finally { $("#btnImport").disabled = false; }
});

$("#importType").addEventListener("change", () => {
  $("#importInvert").checked = $("#importType").value === "credit";
});

$("#btnDetectTransfers").addEventListener("click", async () => {
  const r = await post("/api/transfers/detect");
  toast(`Transfer detection — ${r.changed} transactions marked as transfers`);
  refreshAll();
});

window.addEventListener("resize", () => {
  if (state.view === "overview") { loadOverview().catch(() => {}); loadTimeseries().catch(() => {}); }
  if (state.view === "flow") loadFlow().catch(() => {});
});

refreshAll();
