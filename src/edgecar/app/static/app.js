// EdgeCar AI — in-car app. Plain JS, no dependencies; every value is set with textContent.
"use strict";

const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const INTENT = {
  summary: "Summary", trend: "Trend", extreme: "Peak day", compare: "Comparison", explain_change: "Why it changed",
  list_events: "Events", events_before: "Root cause", anomalies: "Health check", status: "Now",
};
const state = { meta: null, vehicle: null, role: "driver", busy: false, rec: null };

// ------------------------------------------------------------------------------------------------ helpers
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function svgText(parent, attrs, text) {
  svgEl("text", attrs, parent).textContent = text;
}
function fmtNum(v) {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  return String(+v.toFixed(2));
}
function day(iso, withYear) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MON[m - 1]}${withYear ? " " + y : ""}`;
}
function shortX(s, full) {
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return day(s, full);
  if (/^\d{2}:\d{2}:\d{2}$/.test(s)) return full ? s : s.slice(0, 5);
  return s;
}
function span(win) {
  if (!win) return "";
  let [a, b] = win;
  if (b.slice(11, 19) === "00:00:00" && b > a) { // windows end at an exclusive midnight: show the last day included
    const d = new Date(`${b.slice(0, 10)}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() - 1);
    b = d.toISOString().slice(0, 10);
  }
  if (+a.slice(0, 4) < 2000) return `all data to ${day(b, true)}`; // the "all" window starts at the epoch
  if (a.slice(0, 10) === b.slice(0, 10)) return day(a, true);
  return `${day(a, a.slice(0, 4) !== b.slice(0, 4))} – ${day(b, true)}`;
}
function tickLabel(t, ticks) {
  const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
  return t.toFixed(Math.max(0, Math.min(3, -Math.floor(Math.log10(step) + 1e-9))));
}
function niceTicks(min, max, n = 4) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => (max - min) / s <= n);
  const lo = Math.floor(min / step) * step;
  const out = [];
  for (let v = lo; v < max + step * 0.999; v += step) out.push(+v.toFixed(10));
  return out;
}
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: body instanceof Blob ? { "Content-Type": "audio/wav" } : { "Content-Type": "application/json" },
    body: body instanceof Blob ? body : JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status} ${r.statusText}`);
  return r;
}

// ------------------------------------------------------------------------------------------------- charts
function lineChart(chart) {
  const names = Object.keys(chart.series);
  const wrap = h("div", "chart");
  if (!chart.unit && names.length > 1) {
    // series with their own units (°C, km/h): one small panel each, sharing the time axis
    names.forEach((n, i) => wrap.appendChild(linePanel(chart.x, { [n]: chart.series[n] }, "", chart.highlight, i, 110, n)));
    return wrap;
  }
  wrap.appendChild(linePanel(chart.x, chart.series, chart.unit, chart.highlight, 0, 190, chart.unit));
  if (names.length > 1) {
    const lg = h("div", "legend");
    names.forEach((n, i) => {
      const s = h("span", null, n);
      const sw = h("i");
      sw.style.background = `var(--s${(i % 4) + 1})`;
      s.prepend(sw);
      lg.appendChild(s);
    });
    wrap.appendChild(lg);
  }
  return wrap;
}

function linePanel(x, series, unit, highlight, colour, H, title) {
  const W = 640, L = 46, R = 12, T = 8, B = 22;
  const box = h("div");
  if (title) box.appendChild(h("p", "chart-title", title));
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title || "chart" });
  const vals = Object.values(series).flat().filter((v) => v != null);
  if (!vals.length) return box;
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals));
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const step = (W - L - R) / Math.max(1, x.length - 1);
  const sx = (i) => L + (x.length === 1 ? (W - L - R) / 2 : i * step);
  const sy = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0 || 1));
  for (const t of ticks) {
    svgEl("line", { class: "grid", x1: L, x2: W - R, y1: sy(t), y2: sy(t) }, svg);
    svgText(svg, { class: "axis", x: L - 6, y: sy(t) + 4, "text-anchor": "end" }, tickLabel(t, ticks));
  }
  for (const i of [...new Set([0, Math.floor((x.length - 1) / 2), x.length - 1])]) {
    const anchor = i === 0 ? "start" : i === x.length - 1 ? "end" : "middle";
    svgText(svg, { class: "axis", x: sx(i), y: H - 5, "text-anchor": anchor }, shortX(x[i]));
  }
  const hi = highlight ? x.indexOf(highlight) : -1;
  if (hi >= 0) svgEl("line", { class: "hl", x1: sx(hi), x2: sx(hi), y1: T, y2: H - B }, svg);
  Object.values(series).forEach((ys, k) => {
    let d = "", pen = false;
    ys.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${sx(i).toFixed(1)} ${sy(v).toFixed(1)}`;
      pen = true;
    });
    svgEl("path", { class: "series", d, stroke: `var(--s${((k + colour) % 4) + 1})` }, svg);
    if (hi >= 0 && ys[hi] != null && k === 0) svgEl("circle", { cx: sx(hi), cy: sy(ys[hi]), r: 4.5, fill: "var(--warn)" }, svg);
  });
  const cross = svgEl("line", { class: "cross", y1: T, y2: H - B, visibility: "hidden" }, svg);
  const tip = svgEl("text", { class: "tip", y: T + 12, visibility: "hidden" }, svg);
  const hit = svgEl("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) * W) / r.width;
    const i = Math.max(0, Math.min(x.length - 1, Math.round((px - L) / (step || 1))));
    const vs = Object.values(series).map((ys) => ys[i]).filter((v) => v != null).map(fmtNum);
    cross.setAttribute("x1", sx(i));
    cross.setAttribute("x2", sx(i));
    cross.setAttribute("visibility", "visible");
    const right = sx(i) > W * 0.6;
    tip.setAttribute("x", sx(i) + (right ? -8 : 8));
    tip.setAttribute("text-anchor", right ? "end" : "start");
    tip.textContent = `${shortX(x[i], true)}   ${vs.join(" · ")} ${unit || ""}`;
    tip.setAttribute("visibility", "visible");
  });
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    tip.setAttribute("visibility", "hidden");
  });
  box.appendChild(svg);
  return box;
}

function barChart(chart) {
  const name = Object.keys(chart.series)[0];
  const vals = chart.series[name];
  const W = 640, row = 32, L = 220, R = 70, H = vals.length * row + 6;
  const max = Math.max(...vals.map((v) => Math.abs(v ?? 0))) || 1;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": name });
  vals.forEach((v, i) => {
    const y = 3 + i * row;
    const w = ((W - L - R) * Math.abs(v ?? 0)) / max;
    svgText(svg, { class: "label", x: L - 10, y: y + 20, "text-anchor": "end" }, chart.x[i]);
    svgEl("rect", { x: L, y: y + 6, width: Math.max(2, w), height: row - 12, rx: 4, fill: "var(--s1)" }, svg);
    svgText(svg, { class: "value", x: L + w + 8, y: y + 20 }, v == null ? "—" : fmtNum(v));
  });
  const wrap = h("div", "chart");
  wrap.append(h("p", "chart-title", `${name} (${chart.unit})`), svg);
  return wrap;
}

function waterfallChart(chart) {
  // Before → each factor's contribution → now. Totals are drawn from a floor just below the smallest running value
  // so the contributions are readable; every bar carries its number.
  const deltas = chart.series[Object.keys(chart.series)[0]];
  const rows = [{ label: "Before", from: null, to: chart.start }];
  let acc = chart.start;
  chart.x.forEach((label, i) => {
    const d = deltas[i] ?? 0;
    rows.push({ label, from: acc, to: acc + d, d });
    acc += d;
  });
  rows.push({ label: "Now", from: null, to: chart.end });
  const level = rows.flatMap((r) => (r.from == null ? [r.to] : [r.from, r.to]));
  const lo = Math.min(...level), hiV = Math.max(...level);
  const floor = lo - (hiV - lo) * 0.35 - 0.01;
  const W = 640, row = 30, L = 230, R = 64, H = rows.length * row + 6;
  const sx = (v) => L + ((W - L - R) * (v - floor)) / (hiV - floor || 1);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "contributions" });
  rows.forEach((r, i) => {
    const y = 3 + i * row;
    const a = r.from == null ? floor : Math.min(r.from, r.to);
    const b = r.from == null ? r.to : Math.max(r.from, r.to);
    const fill = r.from == null ? "#4a6078" : r.d > 0 ? "var(--warn)" : "var(--rules)";
    svgText(svg, { class: "label", x: L - 10, y: y + 19, "text-anchor": "end" }, r.label);
    svgEl("rect", { x: sx(a), y: y + 6, width: Math.max(2, sx(b) - sx(a)), height: row - 12, rx: 3, fill }, svg);
    const txt = r.from == null ? fmtNum(r.to) : `${r.d > 0 ? "+" : ""}${fmtNum(r.d)}`;
    svgText(svg, { class: "value", x: sx(b) + 7, y: y + 19 }, txt);
  });
  const wrap = h("div", "chart");
  wrap.append(h("p", "chart-title", `Before → now, ${chart.unit} · amber adds, green saves`), svg);
  return wrap;
}

// ------------------------------------------------------------------------------------------------- tables
function table(cols, rows) {
  const wrap = h("div", "table-wrap");
  const t = h("table");
  const tr = h("tr");
  cols.forEach(([label, , num]) => tr.appendChild(h("th", num ? "num" : null, label)));
  t.appendChild(h("thead")).appendChild(tr);
  const tb = h("tbody");
  for (const r of rows) {
    const row = h("tr");
    for (const [, get, num] of cols) {
      const v = get(r);
      const td = h("td", num ? "num" : null);
      if (v instanceof Node) td.appendChild(v);
      else td.textContent = v ?? "";
      row.appendChild(td);
    }
    tb.appendChild(row);
  }
  t.appendChild(tb);
  wrap.appendChild(t);
  return wrap;
}
const sev = (s) => h("span", `sev ${s}`, s);

function timeline(rows, facts) {
  const ol = h("ol", "timeline");
  const rootTime = facts.root_cause_time ? facts.root_cause_time.slice(11, 19) : null;
  for (const r of rows) {
    const li = h("li", r.code === facts.root_cause_code && r.time === rootTime ? "root" : null);
    li.append(h("time", null, r.time), document.createTextNode(r.text));
    if (r.source) li.appendChild(h("span", "src", r.source));
    ol.appendChild(li);
  }
  if (facts.anchor_time) {
    const li = h("li", "anchor");
    li.append(h("time", null, facts.anchor_time.slice(11, 19)), document.createTextNode(`Warning raised (${facts.anchor_code})`));
    ol.appendChild(li);
  }
  return ol;
}

function evidence(ans) {
  const out = [];
  const c = ans.chart;
  if (c && c.type === "line") out.push(lineChart(c));
  if (c && c.type === "bar") out.push(barChart(c));
  if (c && c.type === "waterfall") out.push(waterfallChart(c));
  const rows = ans.table || [];
  if (ans.intent === "list_events" && rows.length) {
    out.push(table([
      ["Time", (r) => r.time], ["Severity", (r) => sev(r.severity)], ["Source", (r) => r.source],
      ["Event", (r) => `${r.title}${r.code && !r.title.includes(r.code) ? ` (${r.code})` : ""}`],
    ], rows));
  }
  if (ans.intent === "events_before" && rows.length) out.push(timeline(rows, ans.facts || {}));
  if (ans.intent === "anomalies" && rows.length) {
    out.push(table([
      ["Trip", (r) => r.time], ["kWh/100 km", (r) => r.kwh_per_100km, 1], ["Expected", (r) => r.expected, 1], ["z", (r) => r.z, 1],
    ], rows));
  }
  return out;
}

// --------------------------------------------------------------------------------------------------- cards
const TIME_LINE = /^\d{2}:\d{2}:\d{2} — |^…and \d+ more\.$/;

function routeLabel(ans) {
  const t = ans.route === "model" ? `${(ans.ms / 1000).toFixed(2)} s` : `${ans.ms < 10 ? ans.ms.toFixed(1) : Math.round(ans.ms)} ms`;
  return `${{ rules: "Rules", model: "Model", declined: "Declined" }[ans.route] || ans.route} · ${t}`;
}

function fillCard(card, ans) {
  card.classList.remove("pending");
  const route = card.querySelector(".route");
  route.textContent = routeLabel(ans);
  route.className = `route ${ans.route}`;
  card.querySelector(".intent").textContent = INTENT[ans.intent] || (ans.route === "declined" ? "Can't help with that" : "");
  card.querySelector(".headline").textContent = ans.headline;
  const ul = card.querySelector(".details");
  ul.replaceChildren();
  const ev = evidence(ans);
  const tabular = (ans.intent === "list_events" || ans.intent === "events_before") && ans.table && ans.table.length;
  for (const d of ans.details) {
    if (tabular && TIME_LINE.test(d)) continue; // shown in the table / timeline instead
    ul.appendChild(h("li", /^(Likely root cause|Cause recorded)/.test(d) ? "cause" : null, d));
  }
  for (const n of ans.notes) ul.appendChild(h("li", "note", n));
  card.querySelector(".evidence").replaceChildren(...ev);
  const cmd = card.querySelector(".cmd");
  cmd.textContent = ans.command || "";
  cmd.title = ans.model_line ? `model wrote: ${ans.model_line}` : "";
  const meta = [];
  if (ans.window) meta.push(span(ans.window));
  if (ans.evidence) meta.push(`${ans.evidence} record${ans.evidence === 1 ? "" : "s"} used`);
  if (state.role !== "driver" && ans.timings) {
    const t = ans.timings;
    const parts = [];
    if (t.slm_total) parts.push(`model ${Math.round(t.slm_total)} ms`);
    if (t.engine) parts.push(`analytics ${t.engine.toFixed(1)} ms`);
    if (parts.length) meta.push(parts.join(" + "));
  }
  card.querySelector(".meta").textContent = meta.join(" · ");
  // The driver gets a glanceable answer (and hears it); evidence is one tap away. Service and fleet see everything.
  const more = card.querySelector(".more");
  const extra = ev.length || ul.children.length > 2;
  card.classList.toggle("compact", state.role === "driver" && extra);
  more.hidden = !(state.role === "driver" && extra);
  more.onclick = () => {
    const c = card.classList.toggle("compact");
    more.textContent = c ? "Show evidence" : "Hide evidence";
  };
}

function bubble(text, note) {
  const b = h("div", "q", text);
  if (note) b.appendChild(h("small", null, note));
  return b;
}

async function ask(question, note) {
  question = question.trim();
  if (!question || state.busy) return;
  state.busy = true;
  $("#empty")?.remove();
  const log = $("#log");
  log.appendChild(bubble(question, note));
  const card = $("#card").content.firstElementChild.cloneNode(true);
  card.classList.add("pending");
  card.querySelector(".headline").textContent = "Working";
  log.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "end" });
  try {
    const ans = await (await api("/api/ask", { question, vehicle: state.vehicle, role: state.role })).json();
    fillCard(card, ans);
    say(ans);
  } catch (err) {
    fillCard(card, { route: "declined", ms: 0, headline: `Something went wrong: ${err.message}`, details: [], notes: [] });
  } finally {
    state.busy = false;
    card.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

// ---------------------------------------------------------------------------------------------- vehicle rail
async function loadStatus() {
  const v = state.meta.vehicles.find((x) => x.id === state.vehicle);
  $("#v-name").textContent = v.name;
  const real = v.provenance.toLowerCase().includes("real") ? "Real trips" : "Data";
  $("#v-source").textContent = `${real}: ${v.source} · ${v.location} · ${day(v.first, true)} – ${day(v.last, true)}`;
  const sg = $("#suggest");
  sg.replaceChildren(...v.suggestions.map((q) => {
    const b = h("button", null, q);
    b.type = "button";
    b.onclick = () => ask(q);
    return b;
  }));
  try {
    const s = await (await api(`/api/status?vehicle=${encodeURIComponent(state.vehicle)}`)).json();
    const f = s.facts || {};
    const soc = f.soc_pct;
    const C = 2 * Math.PI * 50;
    const arc = $("#soc-arc");
    arc.style.strokeDasharray = `${soc == null ? 0 : (soc / 100) * C} ${C}`;
    arc.style.stroke = soc == null ? "var(--line)" : soc < 20 ? "var(--crit)" : soc < 40 ? "var(--warn)" : "var(--rules)";
    $("#soc").textContent = soc == null ? "—" : `${Math.round(soc)}%`;
    $("#range").textContent = f.range_km ? `≈ ${Math.round(f.range_km)} km` : "";
    $("#ambient").textContent = f.ambient_c == null ? "—" : `${f.ambient_c.toFixed(1)} °C`;
    $("#batt-temp").textContent = f.battery_temp_c == null ? "—" : `${f.battery_temp_c.toFixed(1)} °C`;
    $("#as-of").textContent = f.time ? `${day(f.time, true)}, ${f.time.slice(11, 16)}` : "—";
  } catch {
    $("#soc").textContent = "—";
  }
}

// ---------------------------------------------------------------------------------------------------- voice
async function startListening() {
  if (state.rec || state.busy) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/static/recorder.js");
    const node = new AudioWorkletNode(ctx, "recorder");
    const chunks = [];
    node.port.onmessage = (e) => chunks.push(e.data);
    ctx.createMediaStreamSource(stream).connect(node);
    node.connect(ctx.destination);
    state.rec = { stream, ctx, node, chunks, limit: setTimeout(stopListening, 9000) }; // the recogniser's limit
    $("#mic").classList.add("live");
  } catch (err) {
    alertCard(`Microphone unavailable: ${err.message}`);
  }
}

async function stopListening() {
  const rec = state.rec;
  if (!rec) return;
  state.rec = null;
  clearTimeout(rec.limit);
  $("#mic").classList.remove("live");
  rec.stream.getTracks().forEach((t) => t.stop());
  rec.node.disconnect();
  const rate = rec.ctx.sampleRate;
  await rec.ctx.close();
  const pcm = resample(concat(rec.chunks), rate, 16000);
  if (pcm.length < 16000 * 0.3) return; // a tap, not speech
  try {
    const r = await (await api("/api/listen", wav(pcm, 16000))).json();
    if (r.text) ask(r.text, `spoken · recognised on-device in ${Math.round(r.ms)} ms`);
  } catch (err) {
    alertCard(`Speech recognition failed: ${err.message}`);
  }
}

function concat(chunks) {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}
function resample(x, from, to) {
  if (from === to) return x;
  const n = Math.floor((x.length * to) / from);
  const out = new Float32Array(n);
  const r = from / to;
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * r), b = Math.min(x.length, Math.floor((i + 1) * r));
    let s = 0;
    for (let j = a; j < b; j++) s += x[j];
    out[i] = s / Math.max(1, b - a); // box filter: averages each output sample's input span
  }
  return out;
}
function wav(pcm, rate) {
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, pcm.length * 2, true);
  pcm.forEach((s, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return new Blob([buf], { type: "audio/wav" });
}

async function say(ans) {
  if (!state.meta.voice.speak || !$("#speak").checked) return;
  const text = state.role === "driver" ? ans.headline : [ans.headline, ...ans.details.slice(0, 2)].join(" ");
  try {
    const blob = await (await api("/api/speak", { text })).blob();
    const audio = new Audio(URL.createObjectURL(blob));
    audio.onended = () => URL.revokeObjectURL(audio.src);
    await audio.play();
  } catch { /* speech is a convenience; the answer is on screen */ }
}

function alertCard(msg) {
  const card = $("#card").content.firstElementChild.cloneNode(true);
  $("#empty")?.remove();
  $("#log").appendChild(card);
  fillCard(card, { route: "declined", ms: 0, headline: msg, details: [], notes: [] });
}

// ----------------------------------------------------------------------------------------------------- init
async function init() {
  state.meta = await (await api("/api/meta")).json();
  const sel = $("#vehicle");
  for (const v of state.meta.vehicles) {
    const o = h("option", null, `${v.name} · ${v.source.replace("VehId ", "#").replace(/^TUM .*/, "TU Munich")}`);
    o.value = v.id;
    sel.appendChild(o);
  }
  state.vehicle = state.meta.vehicles[0]?.id;
  sel.onchange = () => { state.vehicle = sel.value; loadStatus(); };
  $("#model-badge").textContent = state.meta.model ? `Model: ${state.meta.model.replace(/\.gguf$/, "")}` : "Rules only";
  document.querySelectorAll(".roles button").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll(".roles button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      state.role = b.dataset.role;
    };
  });
  $("#composer").onsubmit = (e) => {
    e.preventDefault();
    const q = $("#q");
    ask(q.value);
    q.value = "";
  };
  if (state.meta.voice.listen && navigator.mediaDevices) {
    const mic = $("#mic");
    mic.hidden = false;
    mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startListening(); });
    mic.addEventListener("pointerup", stopListening);
    mic.addEventListener("pointerleave", stopListening);
    document.addEventListener("keydown", (e) => {
      if (e.code === "Space" && document.activeElement !== $("#q") && !e.repeat) { e.preventDefault(); startListening(); }
    });
    document.addEventListener("keyup", (e) => { if (e.code === "Space" && state.rec) stopListening(); });
  }
  $("#speak-toggle").hidden = !state.meta.voice.speak;
  await loadStatus();
  $("#q").focus();
}

init();
