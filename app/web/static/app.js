// PolyFlow — multi-page site + real-time paper-trading terminal.
// Presentation + a client-side paper portfolio (localStorage). The paper
// portfolio is a frontend simulation marked against live/simulated prices;
// PolyFlow's data + the server-side /trading endpoints are unchanged.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const app = () => $("#app");

// ---------- format ----------
const fmtUsd = (n) => { n = +n || 0; const s = n < 0 ? "-" : "", a = Math.abs(n);
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(1) + "M"; if (a >= 1e3) return s + "$" + (a / 1e3).toFixed(1) + "K"; return s + "$" + a.toFixed(0); };
const money = (n) => (n < 0 ? "-$" : "$") + Math.abs(+n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pctOf = (v) => Math.round((+v || 0) * 100);
const shortAddr = (w) => (w ? w.slice(0, 6) + "…" + w.slice(-4) : "—");
const _LEAGUES = [["NBA", ["nba"]], ["NFL", ["nfl", "super bowl"]], ["NHL", ["nhl", "stanley cup"]],
  ["MLB", ["mlb", "world series"]], ["Soccer", ["soccer", "premier league", "la liga", "uefa", "fifa",
  "world cup", "champions league", "serie a", "bundesliga", "ligue 1", "mls", "epl"]],
  ["UFC", ["ufc", "mma"]], ["Tennis", ["tennis", "atp", "wta", "wimbledon", "grand slam"]],
  ["Golf", ["golf", "pga", "masters"]], ["Boxing", ["boxing", "heavyweight"]]];
function leagueOf(q) {
  const s = (q || "").toLowerCase();
  for (const [name, keys] of _LEAGUES) if (keys.some((k) => s.includes(k))) return name;
  if (s.includes(" vs ") || s.includes(" vs.") || s.includes(" @ ")) return "Match";
  return "Other";
}

async function getJSON(u) { const r = await fetch(u); if (!r.ok) throw new Error(u + " → " + r.status); return r.json(); }
async function postJSON(u) { const r = await fetch(u, { method: "POST" }); return r.json(); }

// ---------- cached data ----------
const store = {};
const load = (k, u, f) => (store[k] = store[k] || getJSON(u).catch(() => f));
const loadMarkets = () => load("markets", "/markets?limit=500&sort=volume", []);
const loadBoard = () => load("board", "/traders/leaderboard?limit=500", []);
const loadRecs = () => load("recs", "/recommendations?limit=500", []);
const loadBacktest = () => load("bt", "/backtest?min_confidence=0.6", {});

// ---------- shared UI ----------
const tip = $("#tooltip");
function bindTip(el, text) {
  el.addEventListener("mouseenter", () => { tip.textContent = text; tip.classList.add("show"); });
  el.addEventListener("mousemove", (e) => { tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 12) + "px"; tip.style.top = e.clientY + 18 + "px"; });
  el.addEventListener("mouseleave", () => tip.classList.remove("show"));
}
function ring(pct, size = 168, sw = 13) {
  const r = (size - sw) / 2, c = 2 * Math.PI * r, off = c * (1 - pct), cx = size / 2;
  return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle class="track" cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke-width="${sw}"/>
    <circle class="fill" cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke-width="${sw}" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${off}"/>
    <text x="50%" y="50%" transform="rotate(90 ${cx} ${cx})" text-anchor="middle" dy="-4" fill="#fff" font-size="${size * .26}" font-weight="800" font-family="Inter">${Math.round(pct * 100)}</text>
    <text x="50%" y="50%" transform="rotate(90 ${cx} ${cx})" text-anchor="middle" dy="${size * .16}" fill="#9a9aa6" font-size="${size * .088}" font-weight="600" font-family="Inter">CONFIDENCE</text></svg>`;
}
const animRings = (root = document) => requestAnimationFrame(() => $$(".ring .fill", root).forEach((f) => (f.style.strokeDashoffset = f.dataset.off)));
let revealObs;
function observeReveals(root = document) {
  if (!revealObs) revealObs = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); revealObs.unobserve(e.target); } }), { threshold: .1 });
  $$(".reveal:not(.in)", root).forEach((el) => revealObs.observe(el));
}
function toast(msg) { let t = $(".toast"); if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); } t.textContent = msg; t.classList.add("show"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 2600); }

// ---------- live price map (WS + simulation) ----------
const LP = {};       // token -> current price
const OPEN = {};     // token -> session-open price (for % change)
let priceCells = {}; // markets page cells
function priceClass(p) { return p >= 0.6 ? "hi" : p < 0.3 ? "lo" : ""; }
function updateMarketCell(token, price) {
  const cell = priceCells[String(token)]; if (!cell || !cell.el) return;
  const prev = parseInt(cell.el.textContent) / 100 || price;
  cell.el.innerHTML = `${pctOf(price)}<span style="font-size:1rem;color:var(--muted)">% Yes</span>`;
  cell.el.classList.remove("hi", "lo"); if (priceClass(price)) cell.el.classList.add(priceClass(price));
  if (cell.bar) cell.bar.style.width = price * 100 + "%";
  cell.el.classList.remove("flash-up", "flash-down"); void cell.el.offsetWidth; cell.el.classList.add(price >= prev ? "flash-up" : "flash-down");
  setTimeout(() => cell.el.classList.remove("flash-up", "flash-down"), 600);
}
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/prices`);
  ws.onclose = () => setTimeout(connectWS, 2500);
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } if (m.type !== "price") return;
    const t = String(m.token_id), p = +m.price; LP[t] = p; if (OPEN[t] == null) OPEN[t] = p; updateMarketCell(t, p); };
}

// ---------- price history + charts ----------
const HIST = {};
function seedHist(t, cur) {
  if (HIST[t]) return; const n = 120, a = []; let v = cur;
  for (let i = 0; i < n; i++) { v = Math.max(.02, Math.min(.98, v * (1 + (Math.random() - .5) * .02))); a.push(v); }
  a[n - 1] = cur; HIST[t] = a;
}
function pushHist(t) { if (!HIST[t]) return; HIST[t].push(LP[t]); if (HIST[t].length > 240) HIST[t].shift(); }
function setupCanvas(c, h) { const dpr = Math.min(devicePixelRatio || 1, 2), w = c.clientWidth || 300; c.width = w * dpr; c.height = h * dpr; const x = c.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); return { x, w, h }; }
function drawSpark(c, arr) {
  if (!c || !arr || arr.length < 2) return; const { x, w, h } = setupCanvas(c, c.clientHeight || 34);
  const min = Math.min(...arr), max = Math.max(...arr), rng = (max - min) || 1;
  const X = (i) => i / (arr.length - 1) * w, Y = (v) => h - ((v - min) / rng) * (h - 4) - 2, up = arr[arr.length - 1] >= arr[0];
  x.clearRect(0, 0, w, h);
  x.beginPath(); x.moveTo(0, h); arr.forEach((v, i) => x.lineTo(X(i), Y(v))); x.lineTo(w, h); x.closePath();
  const g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, up ? "rgba(0,200,5,.28)" : "rgba(255,80,0,.28)"); g.addColorStop(1, "transparent"); x.fillStyle = g; x.fill();
  x.beginPath(); arr.forEach((v, i) => (i ? x.lineTo(X(i), Y(v)) : x.moveTo(X(i), Y(v)))); x.strokeStyle = up ? "#00C805" : "#FF5000"; x.lineWidth = 1.5; x.stroke();
}
function drawCandles(c, arr, entry, opts = {}) {
  if (!c || !arr || arr.length < 4) return; const H = c.clientHeight || 300; const { x, w, h } = setupCanvas(c, H);
  const padR = 54, padB = 4, plotW = w - padR, plotH = h - padB;
  const C = Math.min(46, Math.floor(arr.length / 3)), k = Math.max(1, Math.floor(arr.length / C)), candles = [];
  for (let i = 0; i < C; i++) { const seg = arr.slice(i * k, (i + 1) * k); if (!seg.length) continue; candles.push({ o: seg[0], c: seg[seg.length - 1], hi: Math.max(...seg), lo: Math.min(...seg) }); }
  let min = Math.min(...candles.map((c) => c.lo)), max = Math.max(...candles.map((c) => c.hi));
  if (entry) { min = Math.min(min, entry); max = Math.max(max, entry); }
  const pad = (max - min) * .12 || .02; min -= pad; max += pad; const rng = max - min || 1, Y = (v) => plotH - ((v - min) / rng) * plotH;
  x.clearRect(0, 0, w, h);
  x.strokeStyle = "#141417"; x.fillStyle = "#63636e"; x.font = "10px 'JetBrains Mono',monospace"; x.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const yy = plotH / 4 * i, val = max - rng / 4 * i; x.beginPath(); x.moveTo(0, yy); x.lineTo(plotW, yy); x.stroke(); x.fillText(Math.round(val * 100) + "%", plotW + 6, yy + 3); }
  const cw = plotW / candles.length, bw = Math.max(2, cw * .62);
  candles.forEach((cd, i) => { const cx = i * cw + cw / 2, up = cd.c >= cd.o, col = up ? "#00C805" : "#FF5000"; x.strokeStyle = col; x.fillStyle = col;
    x.beginPath(); x.moveTo(cx, Y(cd.hi)); x.lineTo(cx, Y(cd.lo)); x.stroke(); x.fillRect(cx - bw / 2, Math.min(Y(cd.o), Y(cd.c)), bw, Math.max(2, Math.abs(Y(cd.c) - Y(cd.o)))); });
  // Moving-average overlay (technical touch).
  if (opts.ma) {
    const win = 5; x.strokeStyle = "#4f9dff"; x.lineWidth = 1.5; x.beginPath();
    for (let i = 0; i < candles.length; i++) { const s = candles.slice(Math.max(0, i - win + 1), i + 1); const m = s.reduce((a, d) => a + d.c, 0) / s.length; const cx = i * cw + cw / 2; i ? x.lineTo(cx, Y(m)) : x.moveTo(cx, Y(m)); }
    x.stroke();
  }
  const last = arr[arr.length - 1], ly = Y(last), lastUp = candles[candles.length - 1].c >= candles[candles.length - 1].o;
  x.setLineDash([4, 4]); x.strokeStyle = "#9a9aa6"; x.beginPath(); x.moveTo(0, ly); x.lineTo(plotW, ly); x.stroke(); x.setLineDash([]);
  x.fillStyle = lastUp ? "#00C805" : "#FF5000"; x.fillRect(plotW, ly - 8, padR, 16); x.fillStyle = "#001a00"; x.font = "bold 10px 'JetBrains Mono'"; x.fillText(Math.round(last * 100) + "%", plotW + 6, ly + 3);
  if (entry) { const ey = Y(entry); x.setLineDash([2, 3]); x.strokeStyle = "#e8b84b"; x.beginPath(); x.moveTo(0, ey); x.lineTo(plotW, ey); x.stroke(); x.setLineDash([]); x.fillStyle = "#e8b84b"; x.font = "9px 'JetBrains Mono'"; x.fillText("entry " + Math.round(entry * 100) + "%", 4, ey - 4); }
}
function drawAllSparks() { $$("canvas[data-spark]").forEach((c) => drawSpark(c, HIST[c.dataset.spark])); }

// ---------- signal feed canvas ----------
function startSignalFeed() {
  const canvas = $("#signalCanvas"); if (!canvas) return; const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = Math.min(devicePixelRatio || 1, 2), DX = 2, samples = [], labels = [], phase = 0, nextSpike = 50, hold = 0, sC = null, sD = 1;
  const resize = () => { W = canvas.clientWidth; H = canvas.clientHeight || 400; canvas.width = W * dpr; canvas.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const need = Math.ceil(W / DX) + 2; while (samples.length < need) samples.push({ y: .5, c: null }); while (samples.length > need) samples.shift(); };
  addEventListener("resize", resize); resize();
  (function frame() {
    if (!document.body.contains(canvas)) return; phase += .05;
    let y = .5 + .12 * Math.sin(phase) + .05 * Math.sin(phase * 2.3) + (Math.random() - .5) * .02, c = null;
    if (--nextSpike <= 0 && hold === 0) { hold = 6; sD = Math.random() < .62 ? 1 : -1; sC = sD === 1 ? "#00C805" : "#FF5000"; nextSpike = 90 + Math.random() * 130 | 0; labels.push({ x: W, y: sD === 1 ? 40 : H - 30, t: sD === 1 ? "BUY YES ▲" : "BUY NO ▼", c: sC, life: 1 }); }
    if (hold > 0) { const k = hold / 6; y = .5 - sD * .36 * k; c = sC; hold--; }
    samples.push({ y: Math.max(.05, Math.min(.95, y)), c }); samples.shift();
    ctx.clearRect(0, 0, W, H); ctx.strokeStyle = "#101013"; ctx.lineWidth = 1;
    for (let gx = W % 90; gx < W; gx += 90) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke(); }
    const Y = (v) => v * H; ctx.beginPath(); ctx.moveTo(0, H); samples.forEach((s, i) => ctx.lineTo(i * DX, Y(s.y))); ctx.lineTo((samples.length - 1) * DX, H); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "rgba(0,200,5,.16)"); g.addColorStop(1, "rgba(0,200,5,0)"); ctx.fillStyle = g; ctx.fill();
    ctx.lineWidth = 2; ctx.lineJoin = "round"; for (let i = 1; i < samples.length; i++) { ctx.beginPath(); ctx.moveTo((i - 1) * DX, Y(samples[i - 1].y)); ctx.lineTo(i * DX, Y(samples[i].y)); ctx.strokeStyle = samples[i].c || "#00C805"; ctx.stroke(); }
    ctx.font = "700 12px 'JetBrains Mono',monospace"; labels = labels.filter((l) => l.life > 0);
    labels.forEach((l) => { l.x -= DX; l.life -= 1 / 70; ctx.globalAlpha = Math.max(0, l.life); ctx.fillStyle = l.c; ctx.fillText(l.t, l.x - 32, l.y); ctx.globalAlpha = 1; });
    requestAnimationFrame(frame);
  })();
}

// ---------- phone ----------
let pageTimers = [];
function clearTimers() { pageTimers.forEach((t) => clearInterval(t)); pageTimers = []; }
function startPhone() { const s = $("#phoneScreen"); if (!s) return; let step = 0; s.className = "screen s0"; pageTimers.push(setInterval(() => { step = (step + 1) % 4; s.className = "screen s" + step; }, 1500)); }

// ============================================================
// Home
// ============================================================
async function homePage() {
  const [markets, board, recs, bt] = await Promise.all([loadMarkets(), loadBoard(), loadRecs(), loadBacktest()]);
  const top = recs[0], yes = top && (top.outcome || "").toLowerCase() === "yes";
  app().innerHTML = `
  <header class="hero">
    <div class="hero-bg">
      <canvas id="signalCanvas"></canvas>
      <video id="heroVideo" class="hero-video" style="opacity:0" autoplay muted loop playsinline preload="auto"><source src="/static/hero.mp4" type="video/mp4"></video>
      <div class="hero-veil"></div>
    </div>
    <div class="hero-inner">
      <div class="hero-copy reveal in">
        <span class="eyebrow"><i class="dot live"></i> Live prediction-market intelligence</span>
        <h1>Follow the <span class="accent">smartest money</span> on Polymarket.</h1>
        <p class="lede">PolyFlow ranks every trader by real profitability, consistency and risk — then turns the winners' conviction into live signals you can paper-trade in real time.</p>
        <div class="hero-cta"><a class="pill lg" href="#/trading">Start paper trading</a><a class="text-link" href="#/signals">See live signals →</a></div>
        <div class="trust"><span><b>${board.length}</b> traders ranked</span><span><b>${markets.length}</b> markets</span><span><b>${pctOf(bt.hit_rate)}%</b> backtested hit-rate</span></div>
      </div>
      <div class="phone-stage reveal in"><div class="glow"></div>
        <div class="device"><div class="notch"></div>
          <div id="phoneScreen" class="screen s0">
            <div class="app-top"><span class="app-logo"><b>◆</b> PolyFlow</span><span class="app-bal">$1,240.00</span></div>
            <div class="app-body">
              <div class="sig-card"><div class="sig-cat">▲ HIGH CONVICTION</div>
                <div class="sig-q">Will BTC close above $150k this year?</div>
                <div class="sig-ring"><span>82<small>%</small></span></div>
                <div class="sig-meta">312 top traders · $48K consensus</div></div>
              <button class="buy-btn">Buy YES · 0.62</button>
            </div>
            <div class="cursor"></div>
            <div class="confirm"><div class="check">✓</div><div class="c-title">Order placed</div><div class="c-sub">+$50 YES @ 0.62</div></div>
          </div></div>
      </div>
    </div>
  </header>
  <section class="section wrap"><div class="section-head reveal"><span class="kicker">HOW IT WORKS</span><h2>From raw data to <span class="accent">conviction</span>.</h2></div>
    <div class="steps"><div class="step reveal"><div class="n">1</div><h3>Ingest</h3><p>Stream markets, trades and positions across 500+ Polymarket markets in real time.</p></div>
      <div class="step reveal"><div class="n">2</div><h3>Rank</h3><p>Score every trader on profitability, consistency, sizing and risk-adjusted return.</p></div>
      <div class="step reveal"><div class="n">3</div><h3>Signal</h3><p>Distill the winners' capital into weighted consensus signals with a confidence score.</p></div></div></section>
  <section class="section wrap"><div class="section-head reveal"><span class="kicker">TOP POSITION RIGHT NOW</span><h2>The bet the best traders <span class="accent">most agree</span> on.</h2></div>
    <div class="spotlight reveal">${top ? `<div class="gauge">${ring(top.confidence)}<span class="lab">confidence</span></div>
      <div class="spot-body"><h3>${esc(top.question || top.condition_id)}</h3>
        <p class="trade-plain" style="margin-top:14px"><b>${top.supporter_count} top traders</b> are backing <b class="${yes ? "cy" : "cn"}">${yes ? "YES" : "NO"}</b> with ${fmtUsd(top.consensus_size_usd)} — ${pctOf(top.rationale?.agreement)}% of the smart money agrees.</p>
        <div class="spot-meta">
        <div><div class="k">Position</div><div class="v"><span class="tag ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</span></div></div>
        <div><div class="k">Buy at</div><div class="v">${Math.round((top.current_price || top.avg_entry_price || .5) * 100)}¢</div></div>
        <div><div class="k">$100 pays</div><div class="v">$${(100 / (top.current_price || top.avg_entry_price || .5)).toFixed(0)}</div></div></div>
        <div style="margin-top:24px"><a class="pill" href="#/trading">Paper-trade this →</a></div></div>` : `<p class="muted">Signals warming up…</p>`}</div></section>
  <section class="wrap"><div class="stat-band">
    <div class="reveal"><div class="stat-num">${markets.length}</div><div class="stat-label">Markets tracked</div></div>
    <div class="reveal"><div class="stat-num">${board.length}</div><div class="stat-label">Traders ranked</div></div>
    <div class="reveal"><div class="stat-num">${recs.length}</div><div class="stat-label">Live signals</div></div>
    <div class="reveal"><div class="stat-num accent">${pctOf(bt.hit_rate)}%</div><div class="stat-label">Backtested hit-rate</div></div></div></section>
  <section class="cta wrap"><h2 class="reveal">See what the winners are <span class="accent">buying</span>.</h2>
    <p class="reveal">Deposit virtual capital and watch the consensus move your money in real time.</p>
    <div class="hero-cta center reveal"><a class="pill lg" href="#/trading">Open paper terminal</a><a class="pill ghost lg" href="#/performance">See the edge</a></div></section>`;
  animRings(app()); startSignalFeed(); startPhone();
  const v = $("#heroVideo"); if (v) { v.addEventListener("canplay", () => (v.style.opacity = "1")); v.addEventListener("error", () => (v.style.display = "none")); }
}

// ============================================================
// Signals
// ============================================================
function tradeCard(r, cls = "reveal") {
  const yes = (r.outcome || "").toLowerCase() === "yes";
  const price = +(r.current_price || r.avg_entry_price || 0.5);
  const cents = Math.round(price * 100);
  const win = 100 / (price || 0.5);
  const profit = win - 100;
  const side = yes ? "YES" : "NO";
  const lead = r.rationale?.top_traders?.[0];
  const leadLine = lead ? `<div class="lead-line">⧉ Lead trader <b>${shortAddr(lead.wallet)}</b> · skill ${Math.round((lead.score || 0) * 100)}</div>` : "";
  return `<article class="card trade-card ${cls}">
    <div class="card-head">
      <div><div class="card-kicker ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</div>
        <div class="card-q">${esc((r.question || r.condition_id).slice(0, 92))}</div></div>
      ${ring(r.confidence, 72, 7)}
    </div>
    <p class="trade-plain"><b>${r.supporter_count} top traders</b> are backing <b class="${yes ? "cy" : "cn"}">${side}</b> with ${fmtUsd(r.consensus_size_usd)}. You profit if this resolves <b>${side}</b>.</p>
    <div class="trade-payout">Buy ${side} ≈ <b>${cents}¢</b> · $100 → <b>$${win.toFixed(0)}</b> if correct <span class="prof">(+$${profit.toFixed(0)})</span></div>
    ${leadLine}
    <a class="pill sm" href="#/trading?t=${esc(r.asset)}">Copy & paper-trade →</a>
  </article>`;
}

async function signalsPage() {
  const recs = await loadRecs();
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">POSITIONS TO TAKE</span><h1>What the <span class="accent">best traders</span> are buying.</h1>
    <p>Each card is a position PolyFlow suggests. It's the outcome that the highest-ranked traders are backing with the most money. <b>Confidence</b> (the ring) rises when more top traders agree, with more capital behind them.</p></section>
  <section class="wrap section" style="padding-top:16px">
    <div class="filters" id="cf"><span class="filter-lbl">Min confidence:</span><button class="chip-btn on" data-min="0">All</button><button class="chip-btn" data-min="0.6">60%+</button><button class="chip-btn" data-min="0.7">70%+</button><button class="chip-btn" data-min="0.8">80%+</button></div>
    <div id="sg" class="grid"></div></section>`;
  const grid = $("#sg");
  const render = (min) => {
    grid.innerHTML = recs.filter((r) => r.confidence >= min).slice(0, 30).map((r) => tradeCard(r)).join("")
      || `<p class="muted">No positions at this confidence level.</p>`;
    animRings(grid); observeReveals(grid);
  };
  render(0);
  $$("#cf .chip-btn").forEach((b) => b.addEventListener("click", () => { $$("#cf .chip-btn").forEach((x) => x.classList.remove("on")); b.classList.add("on"); render(+b.dataset.min); }));
}

// ============================================================
// Traders
// ============================================================
async function tradersPage() {
  const board = await loadBoard();
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">LEADERBOARD</span><h1>Ranked by <span class="accent">results</span>, not noise.</h1><p>Every trader gets a <b>Composite score (0–100)</b> — higher means more skilled. It blends the four things below, each measured relative to all other traders. <b>PolyFlow only builds signals from the traders at the top of this list.</b></p></section>
  <section class="wrap section" style="padding-top:20px"><div class="explain">
    <div class="ex reveal"><div class="icon">📈</div><h4>Profitability</h4><p>Realized + unrealized PnL and ROI across every position.</p></div>
    <div class="ex reveal"><div class="icon">🎯</div><h4>Consistency</h4><p>Win rate, low-variance returns and a real track record.</p></div>
    <div class="ex reveal"><div class="icon">⚖️</div><h4>Sizing</h4><p>Conviction on winners without over-concentration.</p></div>
    <div class="ex reveal"><div class="icon">🛡️</div><h4>Risk-adjusted</h4><p>Return per unit of downside and drawdown control.</p></div></div>
    <div class="panel reveal"><table class="data" id="lb"><thead><tr><th>Rank</th><th>Trader</th><th>Composite</th><th class="r hide">Profit</th><th class="r hide">Consist</th><th class="r hide">Sizing</th><th class="r hide">Risk</th></tr></thead><tbody></tbody></table></div></section>`;
  const tb = $("#lb tbody");
  tb.innerHTML = board.slice(0, 25).map((t) => { const cp = pctOf(t.composite), g = t.rank <= 3, m = (v) => `<td class="metric r hide">${pctOf(v)}</td>`;
    return `<tr><td><span class="rank ${g ? "g" : ""}">${t.rank}</span></td><td><div class="trader"><span class="av"></span><div style="min-width:0"><div class="nm">${esc(t.username || "Trader")}</div><div class="adr tw" data-w="${esc(t.proxy_wallet)}">${shortAddr(t.proxy_wallet)}</div></div></div></td>
    <td><div class="comp"><span class="val">${cp}</span><span class="track"><i data-w="${cp}" style="background:${cp >= 85 ? "var(--green)" : cp >= 70 ? "var(--green-2)" : "#4a4a55"}"></i></span></div></td>${m(t.profitability)}${m(t.consistency)}${m(t.sizing)}${m(t.risk_adjusted)}</tr>`; }).join("");
  $$(".adr.tw", tb).forEach((el) => bindTip(el, el.dataset.w));
  requestAnimationFrame(() => $$(".comp .track > i", tb).forEach((i) => (i.style.width = i.dataset.w + "%")));
}

// ============================================================
// Markets
// ============================================================
async function marketsPage() {
  const markets = await loadMarkets();
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">MARKETS <i class="dot live"></i></span><h1>Prices moving in <span class="accent">real time</span>.</h1><p>The highest-volume Polymarket markets we track, streaming live.</p></section>
  <section class="wrap section" style="padding-top:20px"><div id="mg" class="grid markets"></div></section>`;
  priceCells = {}; const grid = $("#mg");
  grid.innerHTML = markets.slice(0, 24).map((m) => { const t = String((m.clob_token_ids || [])[0]), p = +((m.outcome_prices || [])[0] ?? .5);
    return `<article class="card market-card reveal"><div class="card-head"><span class="cat-pill">${esc(m.category || "Market")}</span><span class="vol-badge">${fmtUsd(m.volume)} vol</span></div>
    <div class="card-q tw" data-w="${esc(m.question)}" style="margin-top:14px">${esc(m.question.slice(0, 78))}</div>
    <div class="price-row"><div class="mprice ${priceClass(p)}" data-token="${t}">${pctOf(p)}<span style="font-size:1rem;color:var(--muted)">% Yes</span></div></div>
    <div class="bar"><i data-token="${t}" style="width:${p * 100}%"></i></div></article>`; }).join("");
  markets.slice(0, 24).forEach((m) => { const t = String((m.clob_token_ids || [])[0]); priceCells[t] = { el: $(`.mprice[data-token="${t}"]`, grid), bar: $(`.bar > i[data-token="${t}"]`, grid) }; });
  $$(".card-q.tw", grid).forEach((el) => bindTip(el, el.dataset.w));
  observeReveals(grid);
}

// ============================================================
// TRADING TERMINAL (real-time paper portfolio)
// ============================================================
const PF_KEY = "polyflow_pf_v1";
const BOT_DEFAULTS = { minEdge: 0.06, kelly: 0.3, maxPerTrade: 0.12, tp: 0.4, sl: 0.25, maxPos: 6 };
function loadPF() {
  let p; try { p = JSON.parse(localStorage.getItem(PF_KEY)); } catch {}
  if (!p || !p.positions) p = { cash: 10000, start: 10000, positions: {} };
  p.trades = p.trades || []; p.equity = p.equity || [];
  p.bot = p.bot || { enabled: false, bankroll: 0, cash: 0, positions: {}, log: [], realized: 0, equity: [], params: { ...BOT_DEFAULTS } };
  p.bot.params = { ...BOT_DEFAULTS, ...(p.bot.params || {}) };
  return p;
}
function savePF(p) { localStorage.setItem(PF_KEY, JSON.stringify(p)); }
let PF = loadPF();

const now = () => Date.now();
function logTrade(action, token, pos, shares, price, amount, pnl) {
  PF.trades.push({ ts: now(), action, token, name: pos.name, outcome: pos.outcome, shares, price, amount, pnl: pnl ?? null });
  if (PF.trades.length > 500) PF.trades.shift();
}
function snapshotEquity() { PF.equity.push({ t: now(), v: pfValue().total }); if (PF.equity.length > 600) PF.equity.shift(); }
function drawEquity(canvas, series, base) {
  if (!canvas) return; const H = canvas.clientHeight || 200; const { x, w, h } = setupCanvas(canvas, H);
  const vals = series.map((s) => s.v); if (base != null) vals.push(base);
  if (vals.length < 2) { x.clearRect(0, 0, w, h); x.fillStyle = "#63636e"; x.font = "13px Inter"; x.fillText("No history yet — make a trade to start your equity curve.", 12, h / 2); return; }
  const min = Math.min(...vals), max = Math.max(...vals), rng = (max - min) || 1;
  const X = (i) => (i / (series.length - 1)) * (w - 4) + 2, Y = (v) => h - ((v - min) / rng) * (h - 16) - 8;
  const up = series[series.length - 1].v >= (base ?? series[0].v);
  x.clearRect(0, 0, w, h);
  if (base != null) { const by = Y(base); x.setLineDash([4, 4]); x.strokeStyle = "#3a3a42"; x.beginPath(); x.moveTo(0, by); x.lineTo(w, by); x.stroke(); x.setLineDash([]); }
  x.beginPath(); x.moveTo(X(0), h); series.forEach((s, i) => x.lineTo(X(i), Y(s.v))); x.lineTo(X(series.length - 1), h); x.closePath();
  const g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, up ? "rgba(0,200,5,.22)" : "rgba(255,80,0,.22)"); g.addColorStop(1, "transparent"); x.fillStyle = g; x.fill();
  x.beginPath(); series.forEach((s, i) => (i ? x.lineTo(X(i), Y(s.v)) : x.moveTo(X(i), Y(s.v)))); x.strokeStyle = up ? "#00C805" : "#FF5000"; x.lineWidth = 2; x.stroke();
}
let instruments = [], selToken = null, orderSide = "buy", curCat = "All";
const traderAssets = {}; // wallet -> Set(asset) for copy-trade status

function pfValue() { let inv = 0; for (const [t, p] of Object.entries(PF.positions)) inv += p.shares * (LP[t] ?? p.avg); return { invested: Object.values(PF.positions).reduce((a, p) => a + p.cost, 0), holdings: inv, total: PF.cash + inv }; }

async function tradingPage() {
  const [recs, markets] = await Promise.all([loadRecs(), loadMarkets()]);
  const byTok = {}; markets.forEach((m) => (m.clob_token_ids || []).forEach((t, i) => (byTok[String(t)] = { cat: m.category, price: +((m.outcome_prices || [])[i]) })));
  instruments = recs.slice(0, 40).map((r) => { const t = String(r.asset); const seed = r.current_price || byTok[t]?.price || .5;
    if (LP[t] == null) LP[t] = seed; if (OPEN[t] == null) OPEN[t] = LP[t]; seedHist(t, LP[t]);
    const lead = r.rationale?.top_traders?.[0] || null;
    return { token: t, name: r.question || r.condition_id, outcome: r.outcome, conf: r.confidence, consensus: r.consensus_size_usd, backers: r.supporter_count, cat: leagueOf(r.question || ""), lead }; });
  // Sport sub-tabs, most-populated first.
  const counts = {}; instruments.forEach((i) => (counts[i.cat] = (counts[i.cat] || 0) + 1));
  const cats = ["All", ...Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 7)];
  const q = new URLSearchParams((location.hash.split("?")[1]) || "");
  const wanted = q.get("t");
  selToken = wanted && instruments.some((i) => i.token === wanted) ? wanted : instruments[0]?.token;
  orderSide = "buy"; curCat = "All";

  app().innerHTML = `
  <section class="page-head wrap reveal" style="padding-bottom:8px"><span class="kicker">PAPER TERMINAL</span><h1>Trade like the <span class="accent">pros</span> — fake money, real prices.</h1>
    <p>Copy the top traders' higher-upside positions, read the live chart, and manage your book like a real terminal. Nothing here uses real funds.</p></section>
  <section class="wrap" style="padding-bottom:80px">
    <div class="pf-bar reveal">
      <div class="pf-item"><div class="k">Portfolio value</div><div class="v" id="pf-value">$0.00</div></div>
      <div class="pf-item"><div class="k">Total P&L</div><div class="v" id="pf-pnl">$0.00</div></div>
      <div class="pf-item"><div class="k">Cash</div><div class="v" id="pf-cash">$0.00</div></div>
      <div class="pf-item"><div class="k">Invested</div><div class="v" id="pf-inv">$0.00</div></div>
      <div class="pf-item"><div class="k">Open positions</div><div class="v" id="pf-npos">0</div></div>
      <div class="pf-actions"><button class="mini-btn" id="pf-deposit">+ $5k</button><button class="mini-btn sell" id="pf-reset">Reset</button></div>
    </div>

    <div class="terminal reveal">
      <div class="watchlist">
        <div class="wl-head"><span>Top trades</span><span class="muted" id="wl-count"></span></div>
        <div class="tabs sm" id="cats">${cats.map((c, i) => `<button class="chip-btn ${i === 0 ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("")}</div>
        <div class="wl-list" id="wl"></div>
      </div>
      <div class="chartcol">
        <div class="chart-top" id="chart-top"></div>
        <canvas id="mainChart" style="width:100%;height:340px"></canvas>
        <div class="order" id="order"></div>
      </div>
    </div>

    <h3 style="margin:34px 0 14px">Your positions</h3>
    <div class="panel"><table class="data"><thead><tr><th>Market</th><th>Side</th><th class="r">Shares</th><th class="r hide">Avg</th><th class="r">Last</th><th class="r">Value</th><th class="r">P&L</th><th>Copied trader</th><th></th></tr></thead><tbody id="pos"></tbody></table></div>

    <div class="two-col" style="margin-top:34px">
      <div class="panel algo" style="padding:26px"><h3 style="margin-bottom:6px">How are these positions chosen?</h3>
        <p class="muted" style="font-size:.9rem;margin-bottom:12px">PolyFlow ranks every trader (profit · consistency · sizing · risk), then for each market pools the top traders' bets weighted by <em>skill × money</em>. The side they back becomes the position. We only show <b>higher-upside</b> picks (≈15–65¢) — not near-certain favorites.</p>
        <a class="mini-btn" href="#/performance" style="text-decoration:none">See the edge →</a></div>
      <div class="panel" style="padding:22px"><h3 style="margin-bottom:8px">Copy trading</h3><p class="muted" style="font-size:.9rem;margin-bottom:12px">Each position is copied from a real top trader. In your positions table, the <b>Copied trader</b> column shows whether they're <span class="stat-pos">still holding</span> or have <span class="stat-neg">exited</span> — your cue to sell or hold.</p>
        <button class="mini-btn" id="srv-exec">Run server auto-strategy</button><div id="srv-out" class="muted" style="font-size:.85rem;margin-top:10px"></div></div>
    </div>
  </section>`;

  $$("#cats .chip-btn").forEach((b) => b.addEventListener("click", () => { $$("#cats .chip-btn").forEach((x) => x.classList.remove("on")); b.classList.add("on"); curCat = b.dataset.cat; renderWatchlist(); }));
  $("#pf-reset").addEventListener("click", () => { if (confirm("Reset your paper portfolio to $10,000?")) { PF = { cash: 10000, start: 10000, positions: {} }; savePF(PF); renderWatchlist(); renderPositions(); renderOrder(); updateSummary(); toast("Portfolio reset to $10,000"); } });
  $("#pf-deposit").addEventListener("click", () => { PF.cash += 5000; PF.start += 5000; savePF(PF); updateSummary(); renderOrder(); toast("Deposited $5,000 virtual cash"); });
  $("#srv-exec").addEventListener("click", async (e) => { e.target.disabled = true; e.target.textContent = "Running…"; try { const r = await postJSON("/trading/execute"); const pnl = await getJSON("/trading/pnl?mode=paper").catch(() => ({})); $("#srv-out").innerHTML = r.enabled ? `Server placed <b style="color:var(--green)">${r.submitted}</b> order(s). Book PnL: ${money(pnl.total_pnl || 0)}.` : `Server auto-trading disabled (POLYFLOW_TRADING_ENABLED=false).`; } catch { $("#srv-out").textContent = "Server trading unavailable."; } e.target.disabled = false; e.target.textContent = "Run server auto-strategy"; });

  renderWatchlist(); selectToken(selToken); renderPositions(); updateSummary();

  pageTimers.push(setInterval(() => {
    instruments.forEach((x) => { const t = x.token; const base = LP[t] ?? .5; LP[t] = Math.max(.02, Math.min(.98, base * (1 + (Math.random() - .5) * .02))); pushHist(t); });
    Object.keys(PF.positions).forEach((t) => { if (LP[t] == null) LP[t] = PF.positions[t].avg; });
    tickTerminal();
  }, 1300));
}

// ---- watchlist (left column) ----
function renderWatchlist() {
  const wl = $("#wl"); if (!wl) return;
  const list = instruments.filter((x) => curCat === "All" || x.cat === curCat);
  $("#wl-count").textContent = list.length + " markets";
  wl.innerHTML = list.map((x) => {
    const yes = (x.outcome || "").toLowerCase() === "yes"; const p = LP[x.token] ?? .5;
    const open = OPEN[x.token] ?? p; const chg = ((p - open) / open) * 100;
    const held = PF.positions[x.token] ? " held" : "";
    return `<div class="wl-row${x.token === selToken ? " on" : ""}${held}" data-token="${x.token}">
      <div class="wl-main"><div class="wl-name">${esc(x.name.slice(0, 46))}</div>
        <div class="wl-sub"><span class="tag ${yes ? "yes" : "no"}">${yes ? "YES" : "NO"}</span> · ${x.cat}</div></div>
      <div class="wl-px"><div class="tprice" data-wp>${pctOf(p)}%</div><div class="chg ${chg >= 0 ? "up" : "down"}" data-wc>${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%</div></div></div>`;
  }).join("") || `<div class="empty">No markets in this category.</div>`;
  $$(".wl-row", wl).forEach((r) => r.addEventListener("click", () => selectToken(r.dataset.token)));
}

// ---- chart + order (right column) ----
function selectToken(token) {
  if (!token) return; selToken = token;
  $$("#wl .wl-row").forEach((r) => r.classList.toggle("on", r.dataset.token === token));
  renderChartTop(); renderOrder(); redrawMain();
}

function renderChartTop() {
  const el = $("#chart-top"); const x = instruments.find((i) => i.token === selToken); if (!el || !x) return;
  const yes = (x.outcome || "").toLowerCase() === "yes"; const p = LP[selToken] ?? .5;
  const open = OPEN[selToken] ?? p; const chg = ((p - open) / open) * 100;
  const arr = HIST[selToken] || [p]; const hi = Math.max(...arr), lo = Math.min(...arr);
  const lead = x.lead ? `following <b>${shortAddr(x.lead.wallet)}</b> (score ${Math.round((x.lead.score || 0) * 100)})` : "";
  el.innerHTML = `<div class="ct-l"><div class="ct-name">${esc(x.name)}</div>
      <div class="ct-sub"><span class="tag ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</span> · ${esc(x.cat)} · conf ${pctOf(x.conf)}% · ${x.backers} backers ${lead ? "· " + lead : ""}</div></div>
    <div class="ct-r"><div class="ct-price" data-ctp>${pctOf(p)}%</div>
      <div class="chg ${chg >= 0 ? "up" : "down"}" data-ctc>${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%</div>
      <div class="ct-hilo">H ${pctOf(hi)}% · L ${pctOf(lo)}%</div></div>`;
}

function redrawMain() {
  const c = $("#mainChart"); if (!c || !selToken) return;
  drawCandles(c, HIST[selToken], PF.positions[selToken]?.avg, { ma: true });
}

function renderOrder() {
  const el = $("#order"); const x = instruments.find((i) => i.token === selToken); if (!el || !x) return;
  const held = PF.positions[selToken]; const p = LP[selToken] ?? .5;
  const sideBtns = `<div class="ord-side"><button class="ord-tab ${orderSide === "buy" ? "on" : ""}" data-side="buy">Buy</button><button class="ord-tab sell ${orderSide === "sell" ? "on" : ""}" data-side="sell" ${held ? "" : "disabled"}>Sell</button></div>`;
  let body;
  if (orderSide === "buy") {
    body = `<div class="amt-row"><span>$</span><input id="o-amt" type="number" min="1" value="500"/></div>
      <div class="quick">${[100, 500, 1000, "Max"].map((q) => `<button class="chip-btn" data-q="${q}">${q === "Max" ? "Max" : "$" + q}</button>`).join("")}</div>
      <div class="preview"><span>Buy at</span><b>${pctOf(p)}¢</b></div>
      <div class="preview"><span>Est. shares</span><b id="o-shares">0</b></div>
      <div class="preview"><span>If it wins</span><b id="o-win" class="stat-pos">—</b></div>
      <div class="preview"><span>Cash after</span><b id="o-after">—</b></div>
      <button class="pill lg ord-go" id="o-buy">Buy ${(x.outcome || "").toUpperCase()}</button>
      ${x.lead ? `<button class="mini-btn copy" id="o-copy">⧉ Copy ${shortAddr(x.lead.wallet)}'s exact trade</button>` : ""}`;
  } else {
    body = `<div class="quick">${["25%", "50%", "100%"].map((q) => `<button class="chip-btn" data-sq="${q}">${q}</button>`).join("")}</div>
      <div class="preview"><span>You hold</span><b>${held ? held.shares.toFixed(0) + " sh · " + money(held.shares * p) : "—"}</b></div>
      <div class="preview"><span>Sell price</span><b>${pctOf(p)}¢</b></div>
      <div class="preview"><span>Est. proceeds</span><b id="o-proceeds">—</b></div>
      <button class="pill lg red ord-go" id="o-sell">Sell selected</button>
      <div class="alerts">
        <div class="al-title">Exit alerts</div>
        <div class="al-row"><label>🎯 Take-profit ¢</label><input id="al-tp" type="number" min="1" max="99" value="${held?.tp ? Math.round(held.tp * 100) : ""}" placeholder="e.g. 80"/></div>
        <div class="al-row"><label>🛑 Stop-loss ¢</label><input id="al-sl" type="number" min="1" max="99" value="${held?.sl ? Math.round(held.sl * 100) : ""}" placeholder="e.g. 30"/></div>
        <label class="al-auto"><input type="checkbox" id="al-auto" ${held?.auto ? "checked" : ""}/> Auto-close when hit</label>
        <button class="mini-btn" id="al-save">Save alerts</button>
      </div>`;
  }
  el.innerHTML = `<div class="ord-head">Order ticket</div>${sideBtns}<div class="ord-body">${body}</div>`;
  $$(".ord-tab", el).forEach((b) => b.addEventListener("click", () => { if (b.disabled) return; orderSide = b.dataset.side; renderOrder(); }));
  if (orderSide === "buy") {
    const amtEl = $("#o-amt"); let sellFrac = null;
    const upd = () => { const amt = Math.max(0, +amtEl.value || 0); const shares = amt / p;
      $("#o-shares").textContent = shares.toFixed(0);
      $("#o-win").textContent = "+" + money(shares - amt) + " (" + Math.round((1 - p) / p * 100) + "%)";
      $("#o-after").textContent = money(PF.cash - amt); $("#o-buy").disabled = amt <= 0 || amt > PF.cash; };
    amtEl.addEventListener("input", upd);
    $$(".quick .chip-btn", el).forEach((b) => b.addEventListener("click", () => { amtEl.value = b.dataset.q === "Max" ? Math.floor(PF.cash) : b.dataset.q; upd(); }));
    $("#o-buy").addEventListener("click", () => buy(selToken, +amtEl.value || 0, x.lead?.wallet));
    if ($("#o-copy")) $("#o-copy").addEventListener("click", () => buy(selToken, Math.min(500, Math.floor(PF.cash)), x.lead?.wallet, true));
    upd();
  } else {
    let frac = 1;
    const updS = () => { const sh = (held?.shares || 0) * frac; $("#o-proceeds").textContent = money(sh * p); };
    $$(".quick .chip-btn", el).forEach((b) => b.addEventListener("click", () => { $$(".quick .chip-btn", el).forEach((z) => z.classList.remove("on")); b.classList.add("on"); frac = parseInt(b.dataset.sq) / 100; updS(); }));
    $("#o-sell").addEventListener("click", () => sellFraction(selToken, frac));
    $("#al-save")?.addEventListener("click", () => { const tp = +$("#al-tp").value, sl = +$("#al-sl").value; saveAlerts(selToken, tp ? tp / 100 : null, sl ? sl / 100 : null, $("#al-auto").checked); });
    updS();
  }
}

// ---- positions table with copy-trade status ----
function renderPositions() {
  const tb = $("#pos"); if (!tb) return;
  const entries = Object.entries(PF.positions);
  if (!entries.length) { tb.innerHTML = `<tr><td colspan="9" class="empty">No positions yet — pick a trade on the left and buy it.</td></tr>`; return; }
  tb.innerHTML = entries.map(([t, p]) => { const price = LP[t] ?? p.avg; const val = p.shares * price; const pnl = val - p.cost; const pct = p.cost ? pnl / p.cost * 100 : 0; const yes = (p.outcome || "").toLowerCase() === "yes";
    const copy = p.src ? `<span class="copytag" data-src="${p.src}" data-asset="${t}">checking ${shortAddr(p.src)}…</span>` : `<span class="muted">—</span>`;
    return `<tr data-hold="${t}"><td><div style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</div></td>
      <td><span class="tag ${yes ? "yes" : "no"}" style="font-size:.78rem">${yes ? "YES" : "NO"}</span></td>
      <td class="r">${p.shares.toFixed(0)}</td><td class="r hide">${p.avg.toFixed(2)}</td>
      <td class="r" data-hp>${pctOf(price)}%</td><td class="r" data-hv>${money(val)}</td>
      <td class="r ${pnl >= 0 ? "stat-pos" : "stat-neg"}" data-hpnl>${money(pnl)} <span style="font-size:.75rem">(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span></td>
      <td>${copy}</td>
      <td class="r"><button class="mini-btn" data-view="${t}">Chart</button> <button class="mini-btn sell" data-close="${t}">Close</button></td></tr>`; }).join("");
  $$("[data-close]", tb).forEach((b) => b.addEventListener("click", () => sellFraction(b.dataset.close, 1)));
  $$("[data-view]", tb).forEach((b) => b.addEventListener("click", () => { selectToken(b.dataset.view); document.querySelector(".terminal")?.scrollIntoView({ behavior: "smooth" }); }));
  updateCopyStatuses();
}

async function loadTraderAssets(wallet) {
  if (traderAssets[wallet]) return traderAssets[wallet];
  try { const t = await getJSON("/traders/" + wallet); traderAssets[wallet] = new Set((t.positions || []).map((p) => String(p.asset))); }
  catch { traderAssets[wallet] = null; }
  return traderAssets[wallet];
}
async function updateCopyStatuses() {
  for (const tag of $$(".copytag")) {
    const held = await loadTraderAssets(tag.dataset.src);
    if (held == null) { tag.textContent = "trader n/a"; tag.className = "copytag muted"; continue; }
    if (held.has(tag.dataset.asset)) { tag.innerHTML = `<span class="dot" style="width:6px;height:6px"></span> still holding`; tag.className = "copytag stat-pos"; }
    else { tag.innerHTML = "⚠ exited — consider selling"; tag.className = "copytag stat-neg"; }
  }
}

function updateSummary() {
  const v = pfValue(); const pnl = v.total - PF.start;
  const set = (id, txt, cls) => { const el = $(id); if (!el) return; el.textContent = txt; if (cls !== undefined) el.className = "v " + cls; };
  set("#pf-value", money(v.total)); set("#pf-cash", money(PF.cash)); set("#pf-inv", money(v.holdings));
  set("#pf-npos", String(Object.keys(PF.positions).length));
  const el = $("#pf-pnl"); if (el) { el.textContent = `${money(pnl)} (${PF.start ? (pnl / PF.start * 100).toFixed(1) : 0}%)`; el.className = "v " + (pnl > 0 ? "stat-pos" : pnl < 0 ? "stat-neg" : ""); }
}

function tickTerminal() {
  // watchlist prices
  $$("#wl .wl-row").forEach((r) => { const t = r.dataset.token; const p = LP[t] ?? .5; const open = OPEN[t] ?? p; const chg = ((p - open) / open) * 100;
    const wp = r.querySelector("[data-wp]"); if (wp) wp.textContent = pctOf(p) + "%";
    const wc = r.querySelector("[data-wc]"); if (wc) { wc.textContent = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%"; wc.className = "chg " + (chg >= 0 ? "up" : "down"); } });
  // chart header + main chart
  if (selToken) {
    const p = LP[selToken] ?? .5, open = OPEN[selToken] ?? p, chg = ((p - open) / open) * 100;
    const ctp = $("[data-ctp]"); if (ctp) ctp.textContent = pctOf(p) + "%";
    const ctc = $("[data-ctc]"); if (ctc) { ctc.textContent = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%"; ctc.className = "chg " + (chg >= 0 ? "up" : "down"); }
    redrawMain();
  }
  // positions in place
  $$("#pos tr[data-hold]").forEach((tr) => { const t = tr.dataset.hold; const pos = PF.positions[t]; if (!pos) return; const price = LP[t] ?? pos.avg; const val = pos.shares * price; const pnl = val - pos.cost; const pct = pos.cost ? pnl / pos.cost * 100 : 0;
    tr.querySelector("[data-hp]").textContent = pctOf(price) + "%"; tr.querySelector("[data-hv]").textContent = money(val);
    const pe = tr.querySelector("[data-hpnl]"); pe.innerHTML = `${money(pnl)} <span style="font-size:.75rem">(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span>`; pe.className = "r " + (pnl >= 0 ? "stat-pos" : "stat-neg"); });
  checkAlerts();
  updateSummary();
}

function buy(token, amt, src, isCopy) {
  const x = instruments.find((i) => i.token === token); if (!x) return;
  amt = Math.max(0, +amt || 0); const p = LP[token] ?? .5;
  if (amt <= 0 || amt > PF.cash) { toast("Not enough cash"); return; }
  const shares = amt / p;
  const pos = PF.positions[token] || { shares: 0, cost: 0, avg: p, name: x.name, outcome: x.outcome, src: src || null };
  pos.shares += shares; pos.cost += amt; pos.avg = pos.cost / pos.shares; if (src) pos.src = src;
  PF.positions[token] = pos; PF.cash -= amt;
  logTrade("BUY", token, pos, shares, p, amt); snapshotEquity(); savePF(PF);
  renderWatchlist(); selectToken(token); renderPositions(); updateSummary();
  toast(isCopy ? `Copied ${shortAddr(src)} · bought ${shares.toFixed(0)} shares` : `Bought ${shares.toFixed(0)} shares for ${money(amt)}`);
}
function sellFraction(token, frac) {
  const pos = PF.positions[token]; if (!pos) return; frac = Math.min(1, Math.max(0, frac || 1));
  const price = LP[token] ?? pos.avg; const sellSh = pos.shares * frac; const proceeds = sellSh * price;
  const costPart = pos.cost * frac; const pnl = proceeds - costPart;
  PF.cash += proceeds;
  logTrade("SELL", token, pos, sellSh, price, proceeds, pnl);
  if (frac >= 0.999) delete PF.positions[token];
  else { pos.shares -= sellSh; pos.cost -= costPart; }
  snapshotEquity(); savePF(PF);
  renderWatchlist(); selectToken(token); renderPositions(); updateSummary();
  toast(`Sold ${sellSh.toFixed(0)} shares · ${pnl >= 0 ? "+" : ""}${money(pnl)}`);
}

function saveAlerts(token, tp, sl, auto) {
  const pos = PF.positions[token]; if (!pos) return;
  pos.tp = tp || null; pos.sl = sl || null; pos.auto = !!auto; pos._tp = false; pos._sl = false;
  savePF(PF); renderPositions();
  toast(`Alerts set${tp ? ` · TP ${Math.round(tp * 100)}¢` : ""}${sl ? ` · SL ${Math.round(sl * 100)}¢` : ""}`);
}
function checkAlerts() {
  for (const [t, pos] of Object.entries(PF.positions)) {
    const price = LP[t] ?? pos.avg;
    if (pos.tp && price >= pos.tp && !pos._tp) { pos._tp = true; savePF(PF); toast(`🎯 Take-profit hit: ${pos.name.slice(0, 28)} @ ${pctOf(price)}¢`); if (pos.auto) { sellFraction(t, 1); return; } }
    if (pos.sl && price <= pos.sl && !pos._sl) { pos._sl = true; savePF(PF); toast(`🛑 Stop-loss hit: ${pos.name.slice(0, 28)} @ ${pctOf(price)}¢`); if (pos.auto) { sellFraction(t, 1); return; } }
  }
}

// ============================================================
// Performance
// ============================================================
async function perfPage() {
  const bt = await loadBacktest(); const pct = (v) => Math.round((v || 0) * 100);
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">PERFORMANCE</span><h1>Does it actually <span class="accent">work</span>?</h1><p>Backtested on resolved markets with a clean train/test split — ranked on past results, graded on unseen markets vs. following the crowd.</p></section>
  <section class="wrap section" style="padding-top:16px"><div class="perf-grid">
    <div class="perf-metric reveal"><div class="big accent">${pct(bt.hit_rate)}%</div><div class="lbl">PolyFlow hit-rate</div></div>
    <div class="perf-metric reveal"><div class="big">${pct(bt.baseline_hit_rate)}%</div><div class="lbl">Crowd baseline</div></div>
    <div class="perf-metric reveal"><div class="big">+${pct(bt.edge_vs_crowd)}<span style="font-size:1.4rem"> pts</span></div><div class="lbl">Edge over crowd</div></div>
    <div class="perf-metric reveal"><div class="big">${((bt.mean_roi || 0) * 100).toFixed(0)}%</div><div class="lbl">Avg ROI / signal</div></div></div>
    <div class="panel reveal" style="padding:32px"><h3 style="margin-bottom:22px">PolyFlow vs. the crowd</h3><div class="versus">
      <div class="row"><span class="name">PolyFlow signals</span><span class="track poly"><i data-w="${pct(bt.hit_rate)}"></i><span class="lab">${pct(bt.hit_rate)}%</span></span></div>
      <div class="row"><span class="name">Follow the favorite</span><span class="track crowd"><i data-w="${pct(bt.baseline_hit_rate)}"></i><span class="lab">${pct(bt.baseline_hit_rate)}%</span></span></div></div>
      <p class="muted" style="margin-top:20px">Backtested on ${bt.params?.test_markets ?? "—"} resolved markets · ${bt.n_recommendations ?? 0} graded signals · Brier ${bt.brier_score ?? "—"}.</p></div></section>`;
  requestAnimationFrame(() => $$(".versus .track > i").forEach((i) => (i.style.width = i.dataset.w + "%")));
}

// ============================================================
// Live signals page
// ============================================================
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function liveCard(r) {
  const yes = (r.outcome || "").toLowerCase() === "yes"; const p = +(r.current_price || r.avg_entry_price || .5);
  const cents = Math.round(p * 100), win = 100 / (p || .5);
  const badge = r.recent_trades > 0 ? `<span class="live-badge"><i></i>LIVE · ${r.recent_trades} recent trades</span>` : `<span class="live-badge dim"><i></i>active</span>`;
  return `<article class="card trade-card reveal">
    <div class="card-head"><div>${badge}<div class="card-q" style="margin-top:8px">${esc((r.question || r.condition_id).slice(0, 92))}</div></div>${ring(r.confidence, 72, 7)}</div>
    <div class="trade-payout">Buy <b>${yes ? "YES" : "NO"}</b> ≈ <b>${cents}¢</b> · $100 → <b>$${win.toFixed(0)}</b> if it hits <span class="prof">(+$${(win - 100).toFixed(0)})</span></div>
    <a class="pill sm" href="#/trading?t=${esc(r.asset)}">Jump in →</a></article>`;
}
async function livePage() {
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">LIVE NOW <i class="dot live"></i></span><h1>Games trading <span class="accent">right now</span>.</h1><p>Signals on the markets with the most live trading activity — jump in while the action is on. Auto-refreshes every 15s.</p></section>
    <section class="wrap section" style="padding-top:16px"><div id="live-grid" class="grid"></div></section>`;
  const grid = $("#live-grid");
  const load = async () => {
    const live = await getJSON("/recommendations/live?limit=36").catch(() => []);
    live.forEach((r) => { const t = String(r.asset); if (LP[t] == null) LP[t] = +(r.current_price || .5); });
    grid.innerHTML = live.length ? live.map(liveCard).join("") : `<p class="muted">No live signals right now — check back when games are on.</p>`;
    animRings(grid); observeReveals(grid);
  };
  await load(); pageTimers.push(setInterval(load, 15000));
}

// ============================================================
// Portfolio & history page
// ============================================================
async function portfolioPage() {
  const v = pfValue(), pnl = v.total - PF.start;
  const sells = PF.trades.filter((t) => t.action === "SELL"), wins = sells.filter((t) => t.pnl > 0).length;
  const realized = sells.reduce((a, t) => a + (t.pnl || 0), 0);
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">PORTFOLIO</span><h1>Your paper <span class="accent">account</span>.</h1><p>Equity curve, open positions, and every trade you've made.</p></section>
    <section class="wrap" style="padding-bottom:70px">
      <div class="pf-cards">
        <div class="pf-card"><div class="k">Portfolio value</div><div class="v">${money(v.total)}</div></div>
        <div class="pf-card"><div class="k">Total P&L</div><div class="v ${pnl >= 0 ? "stat-pos" : "stat-neg"}">${money(pnl)} (${PF.start ? (pnl / PF.start * 100).toFixed(1) : 0}%)</div></div>
        <div class="pf-card"><div class="k">Realized P&L</div><div class="v ${realized >= 0 ? "stat-pos" : "stat-neg"}">${money(realized)}</div></div>
        <div class="pf-card"><div class="k">Win rate</div><div class="v">${sells.length ? Math.round(wins / sells.length * 100) : 0}% <span class="muted" style="font-size:.8rem">(${sells.length})</span></div></div>
      </div>
      <div class="panel" style="padding:22px;margin:22px 0"><h3 style="margin-bottom:12px">Equity curve</h3><canvas id="eq" style="width:100%;height:220px"></canvas></div>
      <h3 style="margin:0 0 12px">Open positions</h3>
      <div class="panel" style="margin-bottom:26px"><table class="data"><thead><tr><th>Market</th><th>Side</th><th class="r">Shares</th><th class="r">Avg</th><th class="r">Last</th><th class="r">Value</th><th class="r">P&L</th></tr></thead><tbody id="pp"></tbody></table></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 12px"><h3>Trade history</h3><button class="mini-btn sell" id="clr">Clear history</button></div>
      <div class="panel"><table class="data"><thead><tr><th>Time</th><th>Action</th><th>Market</th><th>Side</th><th class="r">Shares</th><th class="r">Price</th><th class="r">Amount</th><th class="r">P&L</th></tr></thead><tbody id="tl"></tbody></table></div>
    </section>`;
  const series = PF.equity.length ? PF.equity : [{ t: now(), v: PF.start }];
  requestAnimationFrame(() => drawEquity($("#eq"), series, PF.start));
  const pp = $("#pp"), ent = Object.entries(PF.positions);
  pp.innerHTML = ent.length ? ent.map(([t, p]) => { const price = LP[t] ?? p.avg, val = p.shares * price, pl = val - p.cost, yes = (p.outcome || "").toLowerCase() === "yes";
    return `<tr><td>${esc(p.name.slice(0, 42))}</td><td><span class="tag ${yes ? "yes" : "no"}" style="font-size:.76rem">${yes ? "YES" : "NO"}</span></td><td class="r">${p.shares.toFixed(0)}</td><td class="r">${pctOf(p.avg)}¢</td><td class="r">${pctOf(price)}¢</td><td class="r">${money(val)}</td><td class="r ${pl >= 0 ? "stat-pos" : "stat-neg"}">${money(pl)}</td></tr>`; }).join("") : `<tr><td colspan="7" class="empty">No open positions.</td></tr>`;
  const tl = $("#tl");
  tl.innerHTML = PF.trades.length ? [...PF.trades].reverse().slice(0, 100).map((t) => { const yes = (t.outcome || "").toLowerCase() === "yes";
    return `<tr><td class="muted" style="font-size:.78rem">${new Date(t.ts).toLocaleString()}</td><td><span class="${t.action === "BUY" ? "stat-pos" : "stat-neg"}">${t.action}</span></td><td>${esc((t.name || "").slice(0, 34))}</td><td><span class="tag ${yes ? "yes" : "no"}" style="font-size:.72rem">${yes ? "YES" : "NO"}</span></td><td class="r">${(t.shares || 0).toFixed(0)}</td><td class="r">${pctOf(t.price)}¢</td><td class="r">${money(t.amount)}</td><td class="r ${t.pnl > 0 ? "stat-pos" : t.pnl < 0 ? "stat-neg" : ""}">${t.pnl != null ? money(t.pnl) : "—"}</td></tr>`; }).join("") : `<tr><td colspan="8" class="empty">No trades yet — start on the Trading or Auto page.</td></tr>`;
  $("#clr").addEventListener("click", () => { if (confirm("Clear trade history & equity curve?")) { PF.trades = []; PF.equity = []; savePF(PF); portfolioPage(); } });
}

// ============================================================
// Auto-trade page + quant bot
// ============================================================
function botValue() { let v = PF.bot.cash; for (const [t, p] of Object.entries(PF.bot.positions)) v += p.shares * (LP[t] ?? p.avg); return v; }
function botLog(msg) { PF.bot.log.unshift({ ts: now(), msg }); if (PF.bot.log.length > 60) PF.bot.log.pop(); }
function fundBot(amt) { amt = Math.min(PF.cash, Math.max(0, +amt || 0)); if (amt <= 0) return; PF.cash -= amt; PF.bot.cash += amt; PF.bot.bankroll += amt; savePF(PF); toast(`Funded bot with ${money(amt)}`); renderAuto(); }
function withdrawBot() { for (const [t, p] of Object.entries(PF.bot.positions)) PF.bot.cash += p.shares * (LP[t] ?? p.avg); PF.bot.positions = {}; PF.cash += PF.bot.cash; PF.bot.cash = 0; PF.bot.bankroll = 0; PF.bot.enabled = false; savePF(PF); toast("Withdrew bot funds to cash"); renderAuto(); }
function botBuy(c, stake) { const t = String(c.r.asset); const shares = stake / c.p; PF.bot.cash -= stake; PF.bot.positions[t] = { shares, cost: stake, avg: c.p, q: c.q, name: c.r.question || t, outcome: c.r.outcome }; botLog(`BUY ${(c.r.question || "").slice(0, 30)} @ ${pctOf(c.p)}¢ · edge +${Math.round(c.edge * 100)}% · ${money(stake)}`); }
function botSell(t, reason) { const p = PF.bot.positions[t]; if (!p) return; const price = LP[t] ?? p.avg, proceeds = p.shares * price, pnl = proceeds - p.cost; PF.bot.cash += proceeds; PF.bot.realized += pnl; botLog(`SELL ${(p.name || "").slice(0, 30)} @ ${pctOf(price)}¢ · ${reason} · ${pnl >= 0 ? "+" : ""}${money(pnl)}`); delete PF.bot.positions[t]; }
async function evaluateBot() {
  const bot = PF.bot; if (!bot.enabled) return;
  const live = await getJSON("/recommendations/live?limit=40").catch(() => []);
  live.forEach((r) => { const t = String(r.asset); if (LP[t] == null) LP[t] = +(r.current_price || .5); });
  [...Object.keys(bot.positions), ...live.map((r) => String(r.asset))].forEach((t) => { if (LP[t] != null) LP[t] = clamp(LP[t] * (1 + (Math.random() - .5) * .02), .02, .98); });
  for (const [t, p] of Object.entries(bot.positions)) { const price = LP[t] ?? p.avg, pct = (price - p.avg) / p.avg;
    if (price >= p.q) botSell(t, "target reached"); else if (pct >= bot.params.tp) botSell(t, "take-profit"); else if (pct <= -bot.params.sl) botSell(t, "stop-loss"); }
  const cands = live.map((r) => { const p = clamp(+(r.current_price || .5), .02, .98); const q = clamp(p + .6 * (r.confidence - .5) * (1 - p), .02, .98); return { r, p, q, edge: q / p - 1 }; })
    .filter((c) => c.edge >= bot.params.minEdge && c.p >= .1 && c.p <= .7 && !bot.positions[String(c.r.asset)])
    .sort((a, b) => b.edge - a.edge);
  for (const c of cands) { if (Object.keys(bot.positions).length >= bot.params.maxPos) break;
    const b = (1 - c.p) / c.p, fStar = Math.max(0, c.q - (1 - c.q) / b), f = Math.min(bot.params.maxPerTrade, bot.params.kelly * fStar);
    const stake = Math.min(bot.cash, f * bot.bankroll); if (stake < 10) continue; botBuy(c, stake); }
  bot.equity.push({ t: now(), v: botValue() }); if (bot.equity.length > 400) bot.equity.shift();
  savePF(PF); renderAuto();
}
async function autoPage() {
  const P = PF.bot.params;
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">AUTO-TRADE</span><h1>Let the <span class="accent">algorithm</span> trade for you.</h1><p>Fund the bot with paper cash. It scans <b>live</b> signals, estimates edge vs. the market price, sizes with fractional Kelly, and manages its own exits — fully automatic.</p></section>
    <section class="wrap" style="padding-bottom:70px"><div id="auto-root"></div>
      <div class="panel" style="padding:24px;margin-top:26px"><h3 style="margin-bottom:14px">How the algorithm works</h3>
        <div class="algo-steps">
          <div><b>1 · Estimate edge</b><p class="muted">For each live signal it computes a fair probability q = price + 0.6·(confidence−0.5)·(1−price), then edge = q ÷ price − 1. It only acts when edge ≥ ${Math.round(P.minEdge * 100)}%.</p></div>
          <div><b>2 · Size (fractional Kelly)</b><p class="muted">Stake = ${P.kelly}× Kelly fraction of the bankroll, capped at ${Math.round(P.maxPerTrade * 100)}% per trade and ${P.maxPos} open positions.</p></div>
          <div><b>3 · Exit</b><p class="muted">Sells at the fair-value target, at +${Math.round(P.tp * 100)}% take-profit, or −${Math.round(P.sl * 100)}% stop-loss — automatically.</p></div>
        </div>
        <p class="muted" style="font-size:.82rem;margin-top:14px">Paper only · trades exclusively the live signals · not financial advice.</p></div>
    </section>`;
  renderAuto();
  pageTimers.push(setInterval(() => { if (PF.bot.enabled) evaluateBot(); }, 4000));
}
function renderAuto() {
  const root = $("#auto-root"); if (!root) return; const bot = PF.bot, bv = botValue(), bpnl = bv - bot.bankroll;
  const controls = bot.bankroll <= 0
    ? `<div class="fund-row"><div class="amt-row" style="max-width:240px;margin:0"><span>$</span><input id="fund-amt" type="number" value="2000" min="1"/></div><button class="pill" id="fund-btn">Fund bot</button><span class="muted">Cash available: ${money(PF.cash)}</span></div>`
    : `<div class="fund-row"><button class="pill ${bot.enabled ? "red" : ""}" id="toggle-btn">${bot.enabled ? "⏸ Stop bot" : "▶ Start bot"}</button><button class="mini-btn" id="add-btn">+ $1k</button><button class="mini-btn sell" id="wd-btn">Withdraw all</button><span class="live-badge ${bot.enabled ? "" : "dim"}"><i></i>${bot.enabled ? "running" : "paused"}</span></div>`;
  root.innerHTML = `<div class="pf-cards">
      <div class="pf-card"><div class="k">Allocated</div><div class="v">${money(bot.bankroll)}</div></div>
      <div class="pf-card"><div class="k">Bot value</div><div class="v">${money(bv)}</div></div>
      <div class="pf-card"><div class="k">Bot P&L</div><div class="v ${bpnl >= 0 ? "stat-pos" : "stat-neg"}">${money(bpnl)} ${bot.bankroll ? `(${(bpnl / bot.bankroll * 100).toFixed(1)}%)` : ""}</div></div>
      <div class="pf-card"><div class="k">Realized</div><div class="v ${bot.realized >= 0 ? "stat-pos" : "stat-neg"}">${money(bot.realized)}</div></div></div>
    <div class="panel" style="padding:20px;margin:18px 0">${controls}</div>
    <div class="two-col">
      <div class="panel" style="padding:0;overflow:hidden"><div style="padding:16px 18px;font-weight:700">Bot positions</div><table class="data"><thead><tr><th>Market</th><th class="r">Entry</th><th class="r">Last</th><th class="r">Value</th><th class="r">P&L</th></tr></thead><tbody id="bot-pos"></tbody></table></div>
      <div class="panel" style="padding:16px 18px"><div style="font-weight:700;margin-bottom:10px">Decision log</div><div id="bot-log" class="bot-log"></div></div>
    </div>`;
  const bp = $("#bot-pos"), ent = Object.entries(bot.positions);
  bp.innerHTML = ent.length ? ent.map(([t, p]) => { const price = LP[t] ?? p.avg, val = p.shares * price, pl = val - p.cost;
    return `<tr><td>${esc((p.name || "").slice(0, 34))}</td><td class="r">${pctOf(p.avg)}¢</td><td class="r">${pctOf(price)}¢</td><td class="r">${money(val)}</td><td class="r ${pl >= 0 ? "stat-pos" : "stat-neg"}">${money(pl)}</td></tr>`; }).join("") : `<tr><td colspan="5" class="empty">${bot.enabled ? "Scanning live signals for edge…" : "No positions. Fund and start the bot."}</td></tr>`;
  $("#bot-log").innerHTML = bot.log.length ? bot.log.map((l) => `<div class="log-line"><span class="muted">${new Date(l.ts).toLocaleTimeString()}</span> ${esc(l.msg)}</div>`).join("") : `<div class="muted">No activity yet.</div>`;
  $("#fund-btn")?.addEventListener("click", () => fundBot($("#fund-amt").value));
  $("#toggle-btn")?.addEventListener("click", () => { PF.bot.enabled = !PF.bot.enabled; savePF(PF); if (PF.bot.enabled) { toast("Bot started"); evaluateBot(); } else { toast("Bot stopped"); renderAuto(); } });
  $("#add-btn")?.addEventListener("click", () => fundBot(1000));
  $("#wd-btn")?.addEventListener("click", () => { if (confirm("Withdraw all bot funds and close its positions?")) withdrawBot(); });
}

// ============================================================
// Router
// ============================================================
const routes = { "": homePage, live: livePage, signals: signalsPage, traders: tradersPage, markets: marketsPage, trading: tradingPage, auto: autoPage, portfolio: portfolioPage, performance: perfPage };
async function router() {
  clearTimers();
  const key = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "";
  $$("#nav .nav-links a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  window.scrollTo(0, 0);
  try { await (routes[key] || homePage)(); } catch (e) { app().innerHTML = `<section class="page-head wrap"><h1>Something went wrong</h1><p class="muted">${esc(e.message)}</p></section>`; }
  observeReveals();
}
addEventListener("hashchange", router);
addEventListener("scroll", () => $("#nav").classList.toggle("scrolled", scrollY > 16));
async function boot() {
  try { const h = await getJSON("/health"); const m = h.mode === "live" ? "live data" : "fixtures"; $("#mode-chip").textContent = m; $("#foot-mode").textContent = "Mode: " + m; } catch {}
  $("#foot-updated").textContent = "updated " + new Date().toLocaleTimeString();
  connectWS(); router();
}
boot();
