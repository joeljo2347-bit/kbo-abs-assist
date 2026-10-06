// KBO ABS Assist dashboard. Plain JS, no build step.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const get = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error((await r.json()).detail || r.status); return r.json(); };
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
  const data = await get(`/api/pitches?${qs({ pitcher_team: $("zTeam").value, pitcher: $("zPitcher").value, pitch_type: $("zType").value })}`);
  $("zCount").textContent = `${data.shown.toLocaleString()} of ${data.total.toLocaleString()} taken pitches`;
  $("zPlot").innerHTML = zoneFrame() + data.points.map((p, i) => dot(p, 3.5, `data-i="${i}" style="cursor:pointer"`)).join("");
  $("zPlot").onclick = (e) => { const i = e.target.dataset.i; if (i !== undefined) showWhy(data.points[i]); };
  const team = $("zTeam").value || "LG Twins";
  const lost = await get(`/api/tool/strikes_lost_at_back?${qs({ team })}`);
  const types = Object.entries(lost.by_pitch_type).map(([k, v]) => `${esc(k)} ${v}`).join(" · ");
  $("zLost").innerHTML = `<div class="big">${lost.strikes_lost}</div><p>${esc(team)} pitches that were inside the zone at the middle of the plate
    but dropped below it by the back edge, so ABS called them balls (${pct(lost.share_of_takes)} of taken pitches).</p><p class="muted">${types}</p>`;
}

// ---- selects ----
function fill(sel, items, first) {
  sel.innerHTML = (first ? `<option value="">${first}</option>` : "") + items.map((x) => `<option>${esc(x)}</option>`).join("");
}

function setupZone() {
  fill($("zTeam"), META.teams, "All teams");
  $("zTeam").value = "LG Twins";
  const pitchers = () => fill($("zPitcher"), $("zTeam").value ? META.roster[$("zTeam").value].pitchers : [], "All pitchers");
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
  const row = (r) => `<tr><td>${esc(r.pitch)}</td><td class="num">${r.batter_value_after.toFixed(3)}</td>
    <td class="num">${pct(r.whiff_chance_if_swung_at)}</td><td class="num">${pct(r.called_strike_chance_if_taken)}</td></tr>`;
  return `<p class="muted">Batter's value now: ${rec.batter_value_now.toFixed(3)} runs. Lower after the pitch is better for us.</p>
    <table><tr><th>Best</th><th class="num">Batter value after</th><th class="num">Whiff if swung</th><th class="num">Strike if taken</th></tr>
    ${rec.best.map(row).join("")}<tr><th>Avoid</th><th></th><th></th><th></th></tr>${rec.worst.map(row).join("")}</table>`;
}

async function drawMatchup() {
  const [b, s] = COUNT, pitcher = $("mPitcher").value, batter = $("mBatter").value;
  const [rec, pred, take] = await Promise.all([
    get(`/api/tool/recommend_pitch?${qs({ pitcher, batter, balls: b, strikes: s })}`),
    get(`/api/tool/predict_next_pitch?${qs({ pitcher, balls: b, strikes: s, previous_pitch: "" })}`),
    get(`/api/tool/take_guide?${qs({ batter, balls: b, strikes: s })}`)]);
  $("mRec").innerHTML = recTable(rec);
  $("mPred").innerHTML = bars(pred) + `<p class="muted">From his history in this kind of count; it updates with every pitch collected.</p>`;
  $("mTake").innerHTML = take.take.length ? `<table>${take.take.map((t) => `<tr><td>${esc(t.pitch)}</td><td class="num">${pct(t.p_called_strike)} called strike if taken</td></tr>`).join("")}</table>`
    : `<p class="muted">Nothing clearly better to take in this count.</p>`;
}

function setupMatchup() {
  const teams = META.teams;
  fill($("mTeam"), teams); fill($("mBTeam"), teams);
  $("mTeam").value = "LG Twins"; $("mBTeam").value = teams.find((t) => t !== "LG Twins");
  const refill = () => { fill($("mPitcher"), META.roster[$("mTeam").value].pitchers); fill($("mBatter"), META.roster[$("mBTeam").value].batters); };
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
  $("lInfo").textContent = `${esc(GAME.away)} at ${esc(GAME.home)} · pitch ${AT + 1} of ${GAME.pitches.length}`;
  AT += 1;
}

async function loadGame() {
  clearInterval(TIMER); TIMER = null; $("lPlay").textContent = "▶ Play";
  $("lInfo").textContent = "Loading…";
  GAME = await get(`/api/live/${$("lGame").value}`); AT = 0; stepLive();
}

function setupLive() {
  $("lGame").max = META.games - 1; $("lGame").value = Math.floor(META.games * 0.9);
  $("lLoad").onclick = loadGame; $("lStep").onclick = stepLive;
  $("lPlay").onclick = () => {
    if (TIMER) { clearInterval(TIMER); TIMER = null; $("lPlay").textContent = "▶ Play"; return; }
    TIMER = setInterval(stepLive, 700); $("lPlay").textContent = "❚❚ Pause";
  };
}

// ---- AI coach ----
async function ask() {
  const question = $("cQ").value.trim();
  if (!question) return;
  $("cStatus").textContent = "Thinking…"; $("cA").textContent = ""; $("cTools").textContent = "";
  try {
    const r = await fetch("/api/coach", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    const out = await r.json();
    if (!r.ok) throw new Error(out.detail);
    $("cA").textContent = out.answer;
    $("cTools").textContent = `Tools used: ${out.tools_used.join(", ") || "none"}${out.corrected ? " · an unsupported number was sent back and fixed" : ""}`;
  } catch (e) { $("cA").textContent = String(e.message || e); }
  $("cStatus").textContent = "";
}

function setupCoach() {
  const lg = META.roster["LG Twins"], opp = META.teams.find((t) => t !== "LG Twins");
  const ideas = [`What should ${lg.pitchers[0]} throw ${META.roster[opp].batters[0]} with a 1-2 count?`,
    `How many strikes do LG Twins pitchers lose at the back of the plate, and on which pitches?`,
    `What is ${lg.pitchers[1]} likely to throw when he's behind 2-0?`];
  $("cChips").innerHTML = ideas.map((q) => `<button>${esc(q)}</button>`).join("");
  $("cChips").onclick = (e) => { if (e.target.tagName === "BUTTON") { $("cQ").value = e.target.textContent; ask(); } };
  $("cAsk").onclick = ask;
}

// ---- tabs and start ----
document.querySelector("nav").onclick = (e) => {
  const v = e.target.dataset.v; if (!v) return;
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("on", s.id === v));
};

(async () => {
  META = await get("/api/meta");
  $("meta").textContent = `Simulated 2025 season · ${META.games} games · ${META.pitches.toLocaleString()} pitches · 2025 ABS zone`;
  setupZone(); setupMatchup(); setupLive(); setupCoach();
})();
