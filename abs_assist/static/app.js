// KBO ABS Assist dashboard. Plain JS, no build step.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
// An API error's message: FastAPI sends a string, or a list of field problems for a 422.
const detail = (body, status) => Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail || `Error ${status}`;
// The body as JSON; a non-JSON error page (e.g. a proxy's 502) becomes a readable error instead of a parse error.
const body = (r) => r.json().catch(() => ({ detail: `Error ${r.status}` }));
const get = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(detail(await body(r), r.status)); return r.json(); };
// Each view keeps only its newest request: a slow earlier response never overwrites a newer one.
const latest = {};
const fresh = (view) => { latest[view] = (latest[view] || 0) + 1; const mine = latest[view]; return () => latest[view] === mine; };
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();
let META = null;

// ---- the zone chart: x in cm (-45..45), height in zone units (-0.7..1.7) ----
const W = 520, H = 560, X0 = 40, Y0 = 20, PW = 460, PH = 500;
const sx = (x) => X0 + ((x + 45) / 90) * PW;
const sy = (h) => Y0 + ((1.7 - h) / 2.4) * PH;

function zoneFrame() {
  const half = 47.18 / 2;
  return `<rect x="${X0}" y="${Y0}" width="${PW}" height="${PH}" fill="#fafafb" stroke="#e3e5ea"/>
    <rect x="${sx(-half)}" y="${sy(1)}" width="${sx(half) - sx(-half)}" height="${sy(0) - sy(1)}" fill="none" stroke="#15161a" stroke-width="2"/>
    <text x="${sx(half) + 6}" y="${sy(1) + 4}" font-size="11" fill="#666c78">top</text>
    <text x="${sx(half) + 6}" y="${sy(0) + 4}" font-size="11" fill="#666c78">bottom</text>
    <text x="${X0 + PW / 2}" y="${H - 6}" font-size="11" fill="#666c78" text-anchor="middle">← left · middle of the plate · right → (47.18 cm zone)</text>`;
}

function dot(p, r = 4, extra = "") {
  const fill = p.strike ? "#c30452" : "#4f7cac";
  return `<circle cx="${sx(p.x)}" cy="${sy(p.h_mid)}" r="${r}" fill="${fill}" fill-opacity=".55" ${extra}/>`;
}

function showWhy(p) {
  const drop = ((p.h_mid - p.h_end) * 100).toFixed(1);
  $("zWhy").innerHTML = `<div class="big">${p.strike ? '<span class="pill s">Strike</span>' : '<span class="pill b">Ball</span>'}</div>
    <p>${esc(p.why)}</p>
    <table><tr><td>Pitch</td><td>${esc(p.type)}, ${p.kmh} km/h</td></tr>
    <tr><td>Height at the middle of the plate</td><td>${p.h_mid.toFixed(2)} of the zone</td></tr>
    <tr><td>Height at the back of the plate</td><td>${p.h_end.toFixed(2)} (dropped ${drop}% of the zone)</td></tr>
    <tr><td>Left-right at the middle</td><td>${p.x.toFixed(1)} cm</td></tr></table>
    <p class="muted">ABS checks the top and bottom at both the middle and the back of the plate, and the sides once, at the middle.</p>`;
}

async function drawZone() {
  const current = fresh("zone");
  const data = await get(`/api/pitches?${qs({ pitcher_team: $("zTeam").value, pitcher: $("zPitcher").value, pitch_type: $("zType").value })}`);
  if (!current()) return;
  $("zCount").textContent = `${data.shown.toLocaleString()} of ${data.total.toLocaleString()} taken pitches`;
  $("zPlot").innerHTML = zoneFrame() + data.points.map((p, i) => dot(p, 3.5, `data-i="${i}" style="cursor:pointer"`)).join("");
  $("zPlot").onclick = (e) => { const i = e.target.dataset.i; if (i !== undefined) showWhy(data.points[i]); };
  const team = $("zTeam").value || "All teams";
  const lost = await get(`/api/tool/strikes_lost_at_back?${qs({ team: $("zTeam").value || "all" })}`);
  if (!current()) return;
  const types = Object.entries(lost.by_pitch_type).map(([k, v]) => `${esc(k)} ${v}`).join(" · ");
  $("zLost").innerHTML = `<div class="big">${lost.strikes_lost}</div><p>${esc(team)} pitches that were inside the zone at the middle of the plate
    but dropped below it by the back edge, so ABS called them balls (${pct(lost.share_of_takes)} of taken pitches).</p><p class="muted">${types}</p>`;
}

// ---- selects ----
// The club the views open on: the LG Twins when the data has them, else the first team.
const homeTeam = () => (META.teams.includes("LG Twins") ? "LG Twins" : META.teams[0] || "");
const roster = (team) => META.roster[team] || { pitchers: [], batters: [] };

function fill(sel, items, first) {
  sel.innerHTML = (first ? `<option value="">${first}</option>` : "") + items.map((x) => `<option>${esc(x)}</option>`).join("");
}

function setupZone() {
  fill($("zTeam"), META.teams, "All teams");
  $("zTeam").value = homeTeam();
  const pitchers = () => fill($("zPitcher"), roster($("zTeam").value).pitchers, "All pitchers");
  pitchers();
  fill($("zType"), ["fastball", "sinker", "slider", "changeup", "splitter", "curveball"], "All pitch types");
  $("zTeam").onchange = () => { pitchers(); drawZone(); };
  $("zPitcher").onchange = drawZone; $("zType").onchange = drawZone;
  drawZone();
}

// ---- matchup ----
let COUNT = [0, 0];

function bars(dist) {
  return Object.entries(dist).map(([k, v]) => `<div style="display:flex;gap:8px;align-items:center;margin:4px 0">
    <span style="width:80px">${esc(k)}</span><div class="bar" style="width:${Math.round(v * 220)}px"></div><span class="muted">${pct(v)}</span></div>`).join("");
}

function recTable(rec) {
  const row = (r) => `<tr><td>${esc(r.pitch)}</td><td class="num">${r.batter_value_after_runs.toFixed(3)}</td>
    <td class="num">${pct(r.whiff_chance_if_swung_at)}</td><td class="num">${pct(r.called_strike_chance_if_taken)}</td></tr>`;
  return `<p class="muted">Batter's value now: ${rec.batter_value_now_runs.toFixed(3)} runs. Lower after the pitch is better for us.</p>
    <table><tr><th>Best</th><th class="num">Batter value after</th><th class="num">Whiff if swung</th><th class="num">Strike if taken</th></tr>
    ${rec.best.map(row).join("")}<tr><th>Avoid</th><th></th><th></th><th></th></tr>${rec.worst.map(row).join("")}</table>`;
}

async function drawMatchup() {
  const [b, s] = COUNT, pitcher = $("mPitcher").value, batter = $("mBatter").value, current = fresh("matchup");
  const [rec, pred, take] = await Promise.all([
    get(`/api/tool/recommend_pitch?${qs({ pitcher, batter, balls: b, strikes: s })}`),
    get(`/api/tool/predict_next_pitch?${qs({ pitcher, balls: b, strikes: s, previous_pitch: "" })}`),
    get(`/api/tool/take_guide?${qs({ batter, balls: b, strikes: s })}`)]);
  if (!current()) return;
  $("mRec").innerHTML = recTable(rec);
  $("mPred").innerHTML = bars(pred) + `<p class="muted">From his history in this kind of count; it updates with every pitch collected.</p>`;
  $("mTake").innerHTML = take.take.length ? `<table>${take.take.map((t) => `<tr><td>${esc(t.pitch)}</td><td class="num">${pct(t.p_called_strike)} called strike if taken</td></tr>`).join("")}</table>`
    : `<p class="muted">Nothing clearly better to take in this count.</p>`;
}

function setupMatchup() {
  const teams = META.teams;
  fill($("mTeam"), teams); fill($("mBTeam"), teams);
  $("mTeam").value = homeTeam(); $("mBTeam").value = teams.find((t) => t !== homeTeam()) || homeTeam();
  const refill = () => { fill($("mPitcher"), roster($("mTeam").value).pitchers); fill($("mBatter"), roster($("mBTeam").value).batters); };
  refill();
  $("mCounts").innerHTML = [0, 1, 2, 3].flatMap((b) => [0, 1, 2].map((s) => `<button data-c="${b}-${s}">${b}-${s}</button>`)).join("");
  $("mCounts").onclick = (e) => { const c = e.target.dataset.c; if (!c) return; COUNT = c.split("-").map(Number);
    [...$("mCounts").children].forEach((x) => x.classList.toggle("on", x.dataset.c === c)); drawMatchup(); };
  $("mCounts").children[0].classList.add("on");
  ["mTeam", "mBTeam"].forEach((id) => ($(id).onchange = () => { refill(); drawMatchup(); }));
  ["mPitcher", "mBatter"].forEach((id) => ($(id).onchange = drawMatchup));
  drawMatchup();
}

// ---- live game replay ----
let GAME = null, AT = 0, TIMER = null;

function nowCard(p) {
  const top = p.predicted.map(([k, v]) => `${esc(k)} ${pct(v)}`).join(" · ") || "no history yet";
  return `<p><b>${p.half === "top" ? "Top" : "Bottom"} ${p.inning}</b> · ${esc(p.pitcher)} to ${esc(p.batter)} · count ${p.count}</p>
    <p>Predicted: ${top}</p>
    <p>Thrown: <b>${esc(p.type)}</b>, ${p.kmh} km/h ${p.hit ? '<span class="pill s">called it</span>' : ""}</p>
    <p>${p.swing ? `Swing: ${esc(p.result.replace("_", " "))}` : (p.strike ? '<span class="pill s">Strike</span> ' : '<span class="pill b">Ball</span> ') + esc(p.why)}
    ${p.pa_result ? ` · <b>${esc(p.pa_result.replace("_", " "))}</b>` : ""}</p>`;
}

function stepLive() {
  if (!GAME || AT >= GAME.pitches.length) { clearInterval(TIMER); TIMER = null; $("lPlay").textContent = "▶ Play"; return; }
  const shown = GAME.pitches.slice(Math.max(0, AT - 11), AT + 1), p = GAME.pitches[AT];
  $("lPlot").innerHTML = zoneFrame() + shown.map((q, i) => dot(q, i === shown.length - 1 ? 9 : 4)).join("");
  $("lNow").innerHTML = nowCard(p);
  const seen = GAME.pitches.slice(0, AT + 1), right = seen.filter((q) => q.hit).length;
  $("lAcc").textContent = `${pct(right / seen.length)} (${right}/${seen.length})`;
  const alerts = seen.flatMap((q) => q.alerts.map((a) => `<div class="alert"><b>${esc(q.pitcher)}</b>, ${q.half} ${q.inning}: ${esc(a)}</div>`));
  $("lAlerts").innerHTML = alerts.length ? alerts.reverse().join("") : "None yet.";
  $("lInfo").textContent = `${GAME.away} at ${GAME.home} · pitch ${AT + 1} of ${GAME.pitches.length}`;
  AT += 1;
}

async function loadGame() {
  clearInterval(TIMER); TIMER = null; $("lPlay").textContent = "▶ Play";
  $("lInfo").textContent = "Loading…";
  const current = fresh("live");
  let game;
  try { game = await get(`/api/live/${$("lGame").value}`); } catch (e) { if (current()) { GAME = null; $("lInfo").textContent = String(e.message || e); } return; }
  if (!current()) return;  // a newer game was picked while this one loaded
  GAME = game; AT = 0; stepLive();
}

function setupLive() {
  fill($("lGame"), META.game_ids.map(String));
  $("lGame").value = String(META.game_ids[Math.floor(META.game_ids.length * 0.9)] ?? "");
  $("lLoad").onclick = loadGame; $("lGame").onchange = loadGame; $("lStep").onclick = stepLive;
  $("lPlay").onclick = () => {
    if (TIMER) { clearInterval(TIMER); TIMER = null; $("lPlay").textContent = "▶ Play"; return; }
    TIMER = setInterval(stepLive, 700); $("lPlay").textContent = "❚❚ Pause";
  };
}

// ---- AI coach: a conversation ----
let CONVO = null;
function markdown(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
}

// Visual blocks next to an answer: each has one job and reads at a glance.
const ZROWS = ["above", "high", "middle", "low", "below"];
const ZCOLS = ["off", "edge", "inside", "middle", "inside", "edge", "off"];

function zoneBlock(b) {
  const col = b.side === "middle" ? 3 : 4 + ["inside", "edge", "off"].indexOf(b.side), row = ZROWS.indexOf(b.height);
  const cells = ZROWS.map((_, r) => ZCOLS.map((__, c) => {
    const hit = r === row && c === col;
    return `<rect x="${c * 30}" y="${r * 28}" width="30" height="28" fill="${hit ? "#c30452" : "#f2f3f5"}" stroke="#fff" stroke-width="2"/>`;
  }).join("")).join("");
  return `<div class="vz"><svg viewBox="-2 -2 214 144" width="214" height="144" role="img" aria-label="Throw ${esc(b.label)} in the highlighted spot">
    ${cells}<rect x="30" y="28" width="150" height="84" fill="none" stroke="#15161a" stroke-width="2.5"/>
    <text x="${col * 30 + 15}" y="${row * 28 + 18}" font-size="10" font-weight="700" fill="#fff" text-anchor="middle">here</text></svg>
    <div class="vz-cap">Catcher's view. The black box is the ABS zone; the red square is where to throw the ${esc(b.label)} (or the same spot on the other side).</div></div>`;
}

function tilesBlock(b) {
  return `<div class="vtiles">${b.items.map((i) => `<div class="vtile"><div class="vv">${esc(i.value)}</div>
    <div class="vl">${esc(i.label)}</div>${i.note ? `<div class="vn">${esc(i.note)}</div>` : ""}</div>`).join("")}</div>`;
}

function barsBlock(b) {
  const max = Math.max(...b.items.map((i) => i.value)) || 1;
  return `<div class="vbars"><div class="vt">${esc(b.title)}</div>${b.items.map((i) => `<div class="vrow" title="${esc(i.label)}: ${esc(i.display)}">
    <span class="vlab">${esc(i.label)}</span><span class="vtrack"><span class="vfill${i.highlight ? " on" : ""}" style="width:${(i.value / max) * 100}%"></span></span>
    <span class="vval">${esc(i.display)}</span></div>`).join("")}</div>`;
}

function tableBlock(b) {
  const bold = new Set(b.bold.map(([c, r]) => `${r}:${c}`));
  return `<div class="vt">${esc(b.title)}</div><table class="vtab"><tr>${b.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
    ${b.rows.map((row, r) => `<tr>${row.map((v, c) => `<td>${bold.has(`${r}:${c}`) ? `<b>${esc(v)}</b>` : esc(v)}</td>`).join("")}</tr>`).join("")}</table>
    ${b.bold.length ? '<div class="vn">Bold: the highest in each column.</div>' : ""}`;
}

function visualHtml(b) {
  if (b.type === "headline") return `<div class="vhead"><div class="vl">${esc(b.label)}</div><div class="vbig">${esc(b.text)}</div></div>`;
  if (b.type === "zone") return zoneBlock(b);
  if (b.type === "tiles") return tilesBlock(b);
  if (b.type === "bars") return barsBlock(b);
  if (b.type === "table") return tableBlock(b);
  return `<div class="vn">${esc(b.text)}</div>`;
}

function bubble(html, who, meta = "") {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.innerHTML = html + (meta ? `<span class="meta">${esc(meta)}</span>` : "");
  $("cLog").appendChild(div); $("cLog").scrollTop = $("cLog").scrollHeight;
  return div;
}

let PENDING = false;

async function ask() {
  const question = $("cQ").value.trim();
  if (!question || PENDING) return;  // one question at a time
  const current = fresh("coach");
  PENDING = true; $("cQ").value = ""; $("cAsk").disabled = true;
  bubble(esc(question), "me");
  const wait = bubble("Looking it up…", "ai wait"), started = Date.now();
  try {
    const r = await fetch("/api/coach", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: CONVO }) });
    const out = await body(r);
    if (!current()) return;  // "New conversation" was pressed while this was pending
    if (!r.ok) throw new Error(detail(out, r.status));
    CONVO = out.conversation_id; wait.remove();
    const secs = ((Date.now() - started) / 1000).toFixed(1);
    const meta = `${out.tools_used.length ? `From: ${[...new Set(out.tools_used)].join(", ")} · ` : ""}${secs}s`;
    const card = out.visuals.length ? `<div class="vcard">${out.visuals.map(visualHtml).join("")}</div>` : "";
    bubble(markdown(out.answer) + card, "ai", meta);
  } catch (e) { if (current()) { wait.remove(); bubble(esc(String(e.message || e)), "ai"); } }
  finally { if (current()) { PENDING = false; $("cAsk").disabled = false; $("cQ").focus(); } }  // a stale request leaves the newer one's state alone
}

function setupCoach() {
  $("cForm").onsubmit = (e) => { e.preventDefault(); ask(); };
  $("cQ").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } };
  $("cNew").onclick = () => { fresh("coach"); CONVO = null; PENDING = false; $("cAsk").disabled = false; $("cLog").innerHTML = ""; };
}

// ---- tabs and start ----
document.querySelector("nav").onclick = (e) => {
  const v = e.target.dataset.v; if (!v) return;
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("on", s.id === v));
};

(async () => {
  META = await get("/api/meta");
  if (!META.teams.length) { $("meta").textContent = "No pitches collected yet."; return; }
  $("meta").textContent = `Simulated season calibrated to KBO 2026 league totals · ${META.games} games · ${META.pitches.toLocaleString()} pitches · 2025 ABS zone rules`;
  setupScout(); setupZone(); setupMatchup(); setupLive(); setupCoach();
})();

// ---- Scouting: a pitcher against a team ----
// Each pitch type keeps one validated color everywhere (color follows the pitch, never its rank).
const PITCH_COLORS = { fastball: "#2a78d6", slider: "#eb6834", changeup: "#1baf7a", curveball: "#eda100", splitter: "#e87ba4", sinker: "#008300" };
let SCOUT = null, PICK = null, SIDE = "";
const num = (id) => ($(id).value.trim() === "" ? undefined : Number($(id).value));  // empty box = no limit

function arsenalTable(s) {
  if (!s.arsenal.length) return "No pitches match these filters.";
  const head = ["Pitch", "Usage", "Avg km/h", "Max", "H-break", "V-break", "Zone", "Chase", "Whiff", "Called strike", "AVG against", "Pitches"];
  const row = (a) => `<tr class="pick${PICK === a.pitch ? " sel" : ""}" data-p="${esc(a.pitch)}">
    <td><span class="swatch" style="background:${PITCH_COLORS[a.pitch]}"></span>${esc(a.pitch)}</td>
    <td><span class="usage" style="width:${Math.round(a.usage * 90)}px;background:${PITCH_COLORS[a.pitch]}"></span>${pct(a.usage)}</td>
    <td class="num">${a.avg_kmh}</td><td class="num">${a.max_kmh}</td><td class="num">${a.hb_cm} cm</td><td class="num">${a.ivb_cm} cm</td>
    <td class="num">${pct(a.zone)}</td><td class="num">${pct(a.chase)}</td><td class="num">${pct(a.whiff)}</td>
    <td class="num">${pct(a.called_strike)}</td><td class="num">${a.avg_against == null ? "–" : a.avg_against.toFixed(3).replace(/^0/, "")}</td>
    <td class="num">${a.pitches.toLocaleString()}</td></tr>`;
  return `<table class="ars"><tr>${head.map((h, i) => `<th${i > 1 ? ' class="num"' : ""}>${h}</th>`).join("")}</tr>${s.arsenal.map(row).join("")}</table>`;
}

function dotColor(type) { return !PICK || PICK === type ? PITCH_COLORS[type] : "#d3d6dc"; }

function movementChart(s) {
  const X = (cm) => 230 + cm * 3.4, Y = (cm) => 200 - cm * 3.4;
  const grid = [-50, -25, 25, 50].map((v) => `<line x1="${X(v)}" x2="${X(v)}" y1="10" y2="390" stroke="#eef0f3"/><line x1="10" x2="450" y1="${Y(v)}" y2="${Y(v)}" stroke="#eef0f3"/>`).join("");
  const axes = `<line x1="${X(0)}" x2="${X(0)}" y1="10" y2="390" stroke="#c9ccd4"/><line x1="10" x2="450" y1="${Y(0)}" y2="${Y(0)}" stroke="#c9ccd4"/>
    <text x="446" y="${Y(0) - 6}" font-size="11" text-anchor="end" fill="#666c78">arm side →</text><text x="14" y="${Y(0) - 6}" font-size="11" fill="#666c78">← glove side</text>
    <text x="${X(0) + 6}" y="22" font-size="11" fill="#666c78">↑ rises more</text><text x="${X(0) + 6}" y="386" font-size="11" fill="#666c78">↓ drops more</text>`;
  const order = [...s.points].sort((a, b) => (a.type === PICK) - (b.type === PICK));
  const dots = order.filter((p) => p.hb != null && p.ivb != null).map((p) => `<circle cx="${X(p.hb)}" cy="${Y(p.ivb)}" r="3.5" fill="${dotColor(p.type)}" fill-opacity=".7"><title>${esc(p.type)}: ${p.kmh} km/h, H ${p.hb} cm, V ${p.ivb} cm</title></circle>`).join("");
  return grid + axes + dots;
}

function locationChart(s) {
  const order = [...s.points].sort((a, b) => (a.type === PICK) - (b.type === PICK));
  return zoneFrame() + order.map((p) => `<circle cx="${sx(p.x)}" cy="${sy(p.h)}" r="3.5" fill="${dotColor(p.type)}" fill-opacity=".7"><title>${esc(p.type)}, ${p.kmh} km/h: ${p.strike ? "in the zone" : "outside the zone"}, ${esc(p.result.replace("_", " "))}</title></circle>`).join("");
}

function drawScout() {
  const s = SCOUT;
  $("sHand").textContent = s.throws ? `Throws ${s.throws === "R" ? "right" : "left"}-handed` : "";
  $("sCount").textContent = `${s.pitches.toLocaleString()} pitches match`;
  $("sTable").innerHTML = arsenalTable(s);
  $("sMove").innerHTML = movementChart(s);
  $("sLoc").innerHTML = locationChart(s);
  $("sLegend").innerHTML = s.arsenal.map((a) => `<span><span class="swatch" style="background:${PITCH_COLORS[a.pitch]}"></span>${esc(a.pitch)} ${pct(a.usage)}</span>`).join("");
}

async function loadScout() {
  const current = fresh("scout");
  const data = await get(`/api/scout?${qs({ pitcher: $("sPitcher").value, opponent: $("sOpp").value, side: SIDE,
    kmh_min: num("kmhMin"), kmh_max: num("kmhMax"), hb_min: num("hbMin"), hb_max: num("hbMax"), ivb_min: num("ivbMin"), ivb_max: num("ivbMax") })}`);
  if (!current()) return;
  SCOUT = data;
  if (PICK && !SCOUT.arsenal.some((a) => a.pitch === PICK)) PICK = null;
  drawScout();
}

function setupScout() {
  fill($("sTeam"), META.teams); $("sTeam").value = homeTeam();
  fill($("sOpp"), META.teams, "All opponents");
  const pitchers = () => { fill($("sPitcher"), roster($("sTeam").value).pitchers); };
  pitchers();
  $("sTeam").onchange = () => { pitchers(); PICK = null; loadScout(); };
  $("sPitcher").onchange = () => { PICK = null; loadScout(); };
  $("sOpp").onchange = loadScout;
  ["kmhMin", "kmhMax", "hbMin", "hbMax", "ivbMin", "ivbMax"].forEach((id) => ($(id).onchange = loadScout));
  $("sSide").onclick = (e) => { if (e.target.dataset.s === undefined) return; SIDE = e.target.dataset.s;
    [...$("sSide").children].forEach((b) => b.classList.toggle("on", b === e.target)); loadScout(); };
  $("sTable").onclick = (e) => { const tr = e.target.closest("tr.pick"); if (!tr) return; PICK = PICK === tr.dataset.p ? null : tr.dataset.p; drawScout(); };
  $("sReset").onclick = () => { [["kmhMin", 100], ["kmhMax", 165], ["hbMin", -60], ["hbMax", 60], ["ivbMin", -60], ["ivbMax", 60]].forEach(([id, v]) => ($(id).value = v));
    SIDE = ""; [...$("sSide").children].forEach((b, i) => b.classList.toggle("on", i === 0)); PICK = null; loadScout(); };
  loadScout();
}
