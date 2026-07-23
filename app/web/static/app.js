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
function drawCandles(c, arr, entry) {
  if (!c || !arr || arr.length < 4) return; const H = c.clientHeight || 300; const { x, w, h } = setupCanvas(c, H);
  const padR = 52, padB = 4, plotW = w - padR, plotH = h - padB;
  const C = Math.min(44, Math.floor(arr.length / 3)), k = Math.max(1, Math.floor(arr.length / C)), candles = [];
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
  <section class="section wrap"><div class="section-head reveal"><span class="kicker">TOP SIGNAL</span><h2>The strongest consensus, right now.</h2></div>
    <div class="spotlight reveal">${top ? `<div class="gauge">${ring(top.confidence)}<span class="lab">consensus confidence</span></div>
      <div class="spot-body"><h3>${esc(top.question || top.condition_id)}</h3><div class="spot-meta">
        <div><div class="k">Signal</div><div class="v"><span class="tag ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</span></div></div>
        <div><div class="k">Consensus</div><div class="v">${fmtUsd(top.consensus_size_usd)}</div></div>
        <div><div class="k">Backers</div><div class="v">${top.supporter_count}</div></div>
        <div><div class="k">Agreement</div><div class="v">${pctOf(top.rationale?.agreement)}%</div></div></div>
        <div style="margin-top:24px"><a class="pill" href="#/trading">Paper trade this</a></div></div>` : `<p class="muted">Signals warming up…</p>`}</div></section>
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
async function signalsPage() {
  const recs = await loadRecs();
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">HIGH-CONVICTION</span><h1>Signals the <span class="accent">winners</span> are backing.</h1><p>Each is a score-weighted consensus of top-ranked traders. Filter by confidence.</p></section>
  <section class="wrap section" style="padding-top:20px"><div class="filters" id="cf"><button class="chip-btn on" data-min="0">All</button><button class="chip-btn" data-min="0.6">60%+</button><button class="chip-btn" data-min="0.7">70%+</button><button class="chip-btn" data-min="0.8">80%+</button></div><div id="sg" class="grid"></div></section>`;
  const grid = $("#sg");
  const render = (min) => { grid.innerHTML = recs.filter((r) => r.confidence >= min).slice(0, 30).map((r) => { const yes = (r.outcome || "").toLowerCase() === "yes";
    return `<article class="card reveal"><div class="card-head"><div><div class="card-kicker ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</div><div class="card-q">${esc((r.question || r.condition_id).slice(0, 90))}</div></div>${ring(r.confidence, 72, 7)}</div>
    <div class="card-foot"><div><div class="k">Consensus</div><div class="v">${fmtUsd(r.consensus_size_usd)}</div></div><div><div class="k">Backers</div><div class="v">${r.supporter_count}</div></div><div><div class="k">Entry</div><div class="v">${(r.avg_entry_price || 0).toFixed(2)}</div></div></div></article>`; }).join("") || `<p class="muted">No signals at this threshold.</p>`;
    animRings(grid); observeReveals(grid); };
  render(0);
  $$("#cf .chip-btn").forEach((b) => b.addEventListener("click", () => { $$("#cf .chip-btn").forEach((x) => x.classList.remove("on")); b.classList.add("on"); render(+b.dataset.min); }));
}

// ============================================================
// Traders
// ============================================================
async function tradersPage() {
  const board = await loadBoard();
  app().innerHTML = `<section class="page-head wrap reveal"><span class="kicker">LEADERBOARD</span><h1>Ranked by <span class="accent">results</span>, not noise.</h1><p>A composite score from four independent, cohort-normalized dimensions.</p></section>
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
function loadPF() { try { const p = JSON.parse(localStorage.getItem(PF_KEY)); if (p && p.positions) return p; } catch {} return { cash: 10000, start: 10000, positions: {} }; }
function savePF(p) { localStorage.setItem(PF_KEY, JSON.stringify(p)); }
let PF = loadPF();
let instruments = [], tradeToken = null;

function pfValue() { let inv = 0; for (const [t, p] of Object.entries(PF.positions)) inv += p.shares * (LP[t] ?? p.avg); return { invested: Object.values(PF.positions).reduce((a, p) => a + p.cost, 0), holdings: inv, total: PF.cash + inv }; }

async function tradingPage() {
  const [recs, markets] = await Promise.all([loadRecs(), loadMarkets()]);
  const byTok = {}; markets.forEach((m) => (m.clob_token_ids || []).forEach((t, i) => (byTok[String(t)] = { cat: m.category, price: +((m.outcome_prices || [])[i]) })));
  instruments = recs.slice(0, 40).map((r) => { const t = String(r.asset); const seed = r.current_price || byTok[t]?.price || .5;
    if (LP[t] == null) LP[t] = seed; if (OPEN[t] == null) OPEN[t] = LP[t];
    return { token: t, name: r.question || r.condition_id, outcome: r.outcome, dir: r.direction, conf: r.confidence, consensus: r.consensus_size_usd, cat: byTok[t]?.cat || "Other" }; });
  instruments.forEach((x) => seedHist(x.token, LP[x.token] ?? .5));
  const cats = ["All", ...[...new Set(instruments.map((i) => i.cat))].slice(0, 8)];

  app().innerHTML = `
  <section class="page-head wrap reveal" style="padding-bottom:8px"><span class="kicker">PAPER TERMINAL</span><h1>Trade the consensus, <span class="accent">risk-free</span>.</h1>
    <p>Deposit virtual capital, buy the top calculated trades, and watch your money move in real time. No real funds — this is a live paper simulation.</p></section>
  <section class="wrap" style="padding-bottom:80px">
    <div class="pf-bar reveal">
      <div class="pf-item"><div class="k">Portfolio value</div><div class="v" id="pf-value">$0.00</div></div>
      <div class="pf-item"><div class="k">Total P&L</div><div class="v" id="pf-pnl">$0.00</div></div>
      <div class="pf-item"><div class="k">Cash</div><div class="v" id="pf-cash">$0.00</div></div>
      <div class="pf-item"><div class="k">Invested</div><div class="v" id="pf-inv">$0.00</div></div>
      <div class="pf-item"><div class="k">Open positions</div><div class="v" id="pf-npos">0</div></div>
      <div class="pf-actions"><button class="mini-btn" id="pf-deposit">+ Deposit</button><button class="mini-btn sell" id="pf-reset">Reset</button></div>
    </div>

    <h3 style="margin:10px 0 14px">Your positions</h3>
    <div class="panel" style="margin-bottom:30px"><table class="data"><thead><tr><th>Market</th><th>Side</th><th class="r">Shares</th><th class="r hide">Avg</th><th class="r">Price</th><th class="r">Value</th><th class="r">P&L</th><th></th></tr></thead><tbody id="holdings"></tbody></table></div>

    <h3 style="margin:0 0 14px">Top trades <span class="muted" style="font-family:var(--sans);font-size:.9rem;font-weight:400">— PolyFlow's calculated signals, live</span></h3>
    <div class="tabs" id="cats">${cats.map((c, i) => `<button class="chip-btn ${i === 0 ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("")}</div>
    <div class="panel"><table class="data"><thead><tr><th>Market</th><th>Signal</th><th class="r">Price (Yes)</th><th class="r">24h</th><th class="hide">Chart</th><th class="r hide">Conf</th><th class="r">Your pos.</th><th></th></tr></thead><tbody id="insts"></tbody></table></div>
    <p class="muted" style="font-size:.82rem;margin-top:10px">Tip: click any row to open its live chart.</p>

    <div class="panel algo reveal" style="margin-top:34px;padding:28px">
      <h3 style="margin-bottom:6px">How are the top trades calculated?</h3>
      <p class="muted" style="margin-bottom:18px">Yes — it's a deterministic algorithm, not a black box. Two stages:</p>
      <div class="two-col">
        <div><div class="algo-step"><b>1 · Rank every trader</b><p class="muted">Each trader gets a composite score from four cohort-normalized dimensions: profitability (ROI + PnL), consistency (win-rate + Sharpe-like ratio), sizing discipline, and risk-adjusted return (Sortino + drawdown).</p></div></div>
        <div><div class="algo-step"><b>2 · Aggregate the winners' capital</b><p class="muted">For each market, the top-ranked traders' positions are pooled per outcome, weighted by <em>trader score × capital</em>. The outcome with the most score-weighted money becomes the signal.</p></div></div>
      </div>
      <div class="formula">confidence = 0.5 × agreement + 0.3 × avg&nbsp;trader&nbsp;score + 0.2 × consensus&nbsp;size</div>
      <p class="muted" style="font-size:.85rem">Only signals above the confidence floor are shown. <a href="#/performance" style="color:var(--green)">See the backtested edge →</a></p>
    </div>

    <div class="two-col" style="margin-top:34px">
      <div class="banner paper" style="margin:0"><i class="dot"></i><div><div class="b-title">How this works</div><p>Prices update live (WebSocket + simulated ticks). Buy a signal, and your position value & P&L move with the market — exactly like a real book, with virtual money.</p></div></div>
      <div class="panel" style="padding:22px"><h3 style="margin-bottom:8px">Automated strategy (server)</h3><p class="muted" style="font-size:.88rem;margin-bottom:14px">PolyFlow can also auto-execute paper orders from its own risk engine.</p>
        <div style="display:flex;gap:10px"><button class="mini-btn" id="srv-exec">Run server paper run</button><a class="mini-btn" href="#/performance" style="text-decoration:none">View edge</a></div>
        <div id="srv-out" class="muted" style="font-size:.85rem;margin-top:12px"></div></div>
    </div>
  </section>

  <div class="modal-bg" id="ticket"><div class="modal">
    <span class="close-x" id="tk-close">×</span>
    <h3 id="tk-name">—</h3><div class="sub" id="tk-sub">—</div>
    <div class="big-price" id="tk-price">—</div><div class="muted" id="tk-side">—</div>
    <canvas id="tk-chart" style="width:100%;height:130px;margin:14px 0 4px"></canvas>
    <div class="amt-row"><span>$</span><input id="tk-amt" type="number" min="1" value="500" /></div>
    <div class="quick"><button class="chip-btn" data-q="100">$100</button><button class="chip-btn" data-q="500">$500</button><button class="chip-btn" data-q="1000">$1K</button><button class="chip-btn" data-q="max">Max</button></div>
    <div class="preview"><span>Est. shares</span><b id="tk-shares">0</b></div>
    <div class="preview"><span>Cash after</span><b id="tk-after">$0</b></div>
    <div class="modal-actions"><button class="pill" id="tk-buy">Buy YES</button></div>
  </div></div>`;

  // category filter
  let cat = "All";
  $$("#cats .chip-btn").forEach((b) => b.addEventListener("click", () => { $$("#cats .chip-btn").forEach((x) => x.classList.remove("on")); b.classList.add("on"); cat = b.dataset.cat; renderInsts(cat); }));
  renderInsts("All");
  renderHoldings();
  updateSummary();

  // buttons
  $("#pf-reset").addEventListener("click", () => { if (confirm("Reset your paper portfolio to $10,000?")) { PF = { cash: 10000, start: 10000, positions: {} }; savePF(PF); renderInsts(cat); renderHoldings(); updateSummary(); toast("Portfolio reset to $10,000"); } });
  $("#pf-deposit").addEventListener("click", () => { PF.cash += 5000; PF.start += 5000; savePF(PF); updateSummary(); toast("Deposited $5,000 virtual cash"); });
  $("#srv-exec").addEventListener("click", async (e) => { e.target.disabled = true; e.target.textContent = "Running…"; try { const r = await postJSON("/trading/execute"); const pnl = await getJSON("/trading/pnl?mode=paper").catch(() => ({})); $("#srv-out").innerHTML = r.enabled ? `Server placed <b style="color:var(--green)">${r.submitted}</b> order(s), skipped ${r.skipped}. Book PnL: ${money(pnl.total_pnl || 0)}.` : `Server trading disabled (set POLYFLOW_TRADING_ENABLED=true).`; } catch { $("#srv-out").textContent = "Server trading unavailable."; } e.target.disabled = false; e.target.textContent = "Run server paper run"; });

  // ticket
  const tk = $("#ticket");
  const closeTk = () => tk.classList.remove("show");
  $("#tk-close").addEventListener("click", closeTk);
  tk.addEventListener("click", (e) => { if (e.target === tk) closeTk(); });
  $("#tk-amt").addEventListener("input", updateTicket);
  $$("#ticket .quick .chip-btn").forEach((b) => b.addEventListener("click", () => { $("#tk-amt").value = b.dataset.q === "max" ? Math.floor(PF.cash) : b.dataset.q; updateTicket(); }));
  $("#tk-buy").addEventListener("click", doBuy);

  // live loop: WS updates LP; here we also simulate drift so money always moves
  pageTimers.push(setInterval(() => {
    instruments.forEach((x) => { const t = x.token; const base = LP[t] ?? .5; LP[t] = Math.max(.02, Math.min(.98, base * (1 + (Math.random() - .5) * .02))); pushHist(t); });
    Object.keys(PF.positions).forEach((t) => { if (LP[t] == null) LP[t] = PF.positions[t].avg; });
    tickTerminal(); drawAllSparks(); redrawExpanded();
  }, 1300));
  drawAllSparks();
}

let expandedToken = null;
function renderInsts(cat) {
  const tb = $("#insts"); if (!tb) return;
  const list = instruments.filter((x) => cat === "All" || x.cat === cat);
  tb.innerHTML = list.map((x) => { const yes = (x.outcome || "").toLowerCase() === "yes"; const p = LP[x.token] ?? .5; const open = OPEN[x.token] ?? p; const chg = ((p - open) / open) * 100;
    const held = PF.positions[x.token];
    return `<tr data-token="${x.token}" class="inst-row">
      <td><div style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" class="tw" data-w="${esc(x.name)}">${esc(x.name)}</div><div class="muted" style="font-size:.72rem">${esc(x.cat)}</div></td>
      <td><span class="tag ${yes ? "yes" : "no"}" style="font-size:.8rem">${yes ? "▲ YES" : "▼ NO"}</span></td>
      <td class="r tprice" data-p>${pctOf(p)}%</td>
      <td class="r chg ${chg >= 0 ? "up" : "down"}" data-chg>${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%</td>
      <td class="hide"><canvas class="spark" data-spark="${x.token}" style="width:88px;height:34px"></canvas></td>
      <td class="r hide metric">${pctOf(x.conf)}%</td>
      <td class="r pos-cell" data-pos>${held ? `<span class="pv">${money(held.shares * p)}</span>` : "—"}</td>
      <td class="r">${held ? `<button class="mini-btn sell" data-sell="${x.token}">Sell</button>` : `<button class="mini-btn" data-buy="${x.token}">Trade</button>`}</td></tr>`; }).join("") || `<tr><td colspan="8" class="empty">No instruments in this category.</td></tr>`;
  $$(".tw", tb).forEach((el) => bindTip(el, el.dataset.w));
  $$("[data-buy]", tb).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); openTicket(b.dataset.buy); }));
  $$("[data-sell]", tb).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); sellAll(b.dataset.sell); }));
  $$(".inst-row", tb).forEach((tr) => tr.addEventListener("click", () => toggleDetail(tr, tr.dataset.token)));
  drawAllSparks();
  if (expandedToken && list.some((x) => x.token === expandedToken)) { const tr = tb.querySelector(`tr[data-token="${expandedToken}"]`); if (tr) { const tok = expandedToken; expandedToken = null; toggleDetail(tr, tok); } }
}

function toggleDetail(tr, token) {
  const existing = tr.nextElementSibling;
  $$(".detail-row").forEach((d) => d.remove());
  if (expandedToken === token) { expandedToken = null; return; }
  expandedToken = token;
  const x = instruments.find((i) => i.token === token) || {}; const held = PF.positions[token];
  const yes = (x.outcome || "").toLowerCase() === "yes"; const p = LP[token] ?? .5;
  const dr = document.createElement("tr"); dr.className = "detail-row"; dr.dataset.detail = token;
  dr.innerHTML = `<td colspan="8"><div class="chart-wrap">
    <div class="chart-head"><div><div class="ch-name">${esc(x.name || token)}</div><div class="muted" style="font-size:.8rem">${esc(x.cat || "")} · <span class="tag ${yes ? "yes" : "no"}">${yes ? "BUY YES" : "BUY NO"}</span> · conf ${pctOf(x.conf)}%</div></div>
      <div class="ch-price" data-chp>${pctOf(p)}%</div></div>
    <canvas class="big-chart" data-bigchart="${token}" style="width:100%;height:300px"></canvas>
    <div class="ch-stats" data-chstats></div>
    <div class="ch-actions">${held ? `<button class="pill" data-buy2="${token}">Buy more</button><button class="pill red" data-sell2="${token}">Close position</button>` : `<button class="pill" data-buy2="${token}">Buy this signal</button>`}</div>
  </div></td>`;
  tr.after(dr);
  $("[data-buy2]", dr)?.addEventListener("click", () => openTicket(token));
  $("[data-sell2]", dr)?.addEventListener("click", () => sellAll(token));
  renderDetailStats(token); redrawExpanded();
}

function renderDetailStats(token) {
  const box = $(`.detail-row[data-detail="${token}"] [data-chstats]`); if (!box) return;
  const held = PF.positions[token], p = LP[token] ?? .5;
  if (held) { const val = held.shares * p, pnl = val - held.cost, pct = held.cost ? pnl / held.cost * 100 : 0;
    box.innerHTML = `<div><span class="k">Your value</span><b>${money(val)}</b></div><div><span class="k">Entry</span><b>${pctOf(held.avg)}%</b></div><div><span class="k">Shares</span><b>${held.shares.toFixed(0)}</b></div><div><span class="k">P&L</span><b class="${pnl >= 0 ? "stat-pos" : "stat-neg"}">${money(pnl)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</b></div>`;
  } else box.innerHTML = `<div><span class="k">No position</span><b>—</b></div><div><span class="k">Last</span><b>${pctOf(p)}% Yes</b></div>`;
}
function redrawExpanded() {
  if (!expandedToken) return; const c = $(`canvas[data-bigchart="${expandedToken}"]`); if (!c) return;
  drawCandles(c, HIST[expandedToken], PF.positions[expandedToken]?.avg);
  const chp = $(`.detail-row[data-detail="${expandedToken}"] [data-chp]`); if (chp) chp.textContent = pctOf(LP[expandedToken] ?? .5) + "%";
  renderDetailStats(expandedToken);
}

function renderHoldings() {
  const tb = $("#holdings"); if (!tb) return;
  const entries = Object.entries(PF.positions);
  if (!entries.length) { tb.innerHTML = `<tr><td colspan="8" class="empty">No positions yet — buy a top trade below to start.</td></tr>`; return; }
  tb.innerHTML = entries.map(([t, p]) => { const price = LP[t] ?? p.avg; const val = p.shares * price; const pnl = val - p.cost; const pct = p.cost ? (pnl / p.cost) * 100 : 0; const yes = (p.outcome || "").toLowerCase() === "yes";
    return `<tr data-hold="${t}"><td><div style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</div></td>
      <td><span class="tag ${yes ? "yes" : "no"}" style="font-size:.78rem">${yes ? "YES" : "NO"}</span></td>
      <td class="r">${p.shares.toFixed(0)}</td><td class="r hide">${p.avg.toFixed(2)}</td>
      <td class="r" data-hp>${pctOf(price)}%</td><td class="r" data-hv>${money(val)}</td>
      <td class="r ${pnl >= 0 ? "stat-pos" : "stat-neg"}" data-hpnl>${money(pnl)} <span style="font-size:.75rem">(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span></td>
      <td class="r"><button class="mini-btn sell" data-sell="${t}">Close</button></td></tr>`; }).join("");
  $$("[data-sell]", tb).forEach((b) => b.addEventListener("click", () => sellAll(b.dataset.sell)));
}

function updateSummary() {
  const v = pfValue(); const pnl = v.total - PF.start;
  const set = (id, txt, cls) => { const el = $(id); if (!el) return; el.textContent = txt; if (cls !== undefined) el.className = "v " + cls; };
  set("#pf-value", money(v.total)); set("#pf-cash", money(PF.cash)); set("#pf-inv", money(v.holdings));
  set("#pf-npos", String(Object.keys(PF.positions).length));
  const el = $("#pf-pnl"); if (el) { el.textContent = `${money(pnl)} (${PF.start ? (pnl / PF.start * 100).toFixed(1) : 0}%)`; el.className = "v " + (pnl > 0 ? "stat-pos" : pnl < 0 ? "stat-neg" : ""); }
}

function tickTerminal() {
  // update instrument rows in place
  $$("#insts tr[data-token]").forEach((tr) => { const t = tr.dataset.token; const p = LP[t] ?? .5; const open = OPEN[t] ?? p; const chg = ((p - open) / open) * 100;
    const pe = tr.querySelector("[data-p]"); if (pe) { pe.textContent = pctOf(p) + "%"; pe.classList.add("flash-cell"); setTimeout(() => pe.classList.remove("flash-cell"), 600); }
    const ce = tr.querySelector("[data-chg]"); if (ce) { ce.textContent = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%"; ce.className = "r chg " + (chg >= 0 ? "up" : "down"); }
    const pc = tr.querySelector("[data-pos]"); const held = PF.positions[t]; if (pc && held) pc.innerHTML = `<span class="pv">${money(held.shares * p)}</span>`; });
  // update holdings in place
  $$("#holdings tr[data-hold]").forEach((tr) => { const t = tr.dataset.hold; const p = PF.positions[t]; if (!p) return; const price = LP[t] ?? p.avg; const val = p.shares * price; const pnl = val - p.cost; const pct = p.cost ? pnl / p.cost * 100 : 0;
    tr.querySelector("[data-hp]").textContent = pctOf(price) + "%"; tr.querySelector("[data-hv]").textContent = money(val);
    const pe = tr.querySelector("[data-hpnl]"); pe.innerHTML = `${money(pnl)} <span style="font-size:.75rem">(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</span>`; pe.className = "r " + (pnl >= 0 ? "stat-pos" : "stat-neg"); });
  updateSummary();
}

function openTicket(token) {
  tradeToken = token; const x = instruments.find((i) => i.token === token); if (!x) return;
  const yes = (x.outcome || "").toLowerCase() === "yes";
  $("#tk-name").textContent = x.name.slice(0, 70); $("#tk-sub").textContent = x.cat;
  $("#tk-side").innerHTML = `Signal: <span class="tag ${yes ? "yes" : "no"}">${yes ? "▲ BUY YES" : "▼ BUY NO"}</span> · conf ${pctOf(x.conf)}%`;
  $("#tk-buy").textContent = yes ? "Buy YES" : "Buy NO"; $("#tk-buy").className = "pill" + (yes ? "" : " red");
  $("#tk-amt").value = 500; updateTicket(); $("#ticket").classList.add("show");
  seedHist(token, LP[token] ?? .5); requestAnimationFrame(() => drawSpark($("#tk-chart"), HIST[token]));
}
function updateTicket() {
  if (!tradeToken) return; const p = LP[tradeToken] ?? .5; const amt = Math.max(0, +$("#tk-amt").value || 0);
  $("#tk-price").textContent = pctOf(p) + "% Yes";
  $("#tk-shares").textContent = (amt / p).toFixed(0); $("#tk-after").textContent = money(PF.cash - amt);
  $("#tk-buy").disabled = amt <= 0 || amt > PF.cash;
}
function doBuy() {
  const x = instruments.find((i) => i.token === tradeToken); if (!x) return; const p = LP[tradeToken] ?? .5; const amt = +$("#tk-amt").value || 0;
  if (amt <= 0 || amt > PF.cash) return;
  const shares = amt / p; const pos = PF.positions[tradeToken] || { shares: 0, cost: 0, avg: p, name: x.name, outcome: x.outcome };
  pos.shares += shares; pos.cost += amt; pos.avg = pos.cost / pos.shares; PF.positions[tradeToken] = pos; PF.cash -= amt; savePF(PF);
  $("#ticket").classList.remove("show"); renderInsts($("#cats .chip-btn.on")?.dataset.cat || "All"); renderHoldings(); updateSummary();
  toast(`Bought ${shares.toFixed(0)} shares for ${money(amt)}`);
}
function sellAll(token) {
  const p = PF.positions[token]; if (!p) return; const price = LP[token] ?? p.avg; const proceeds = p.shares * price; const pnl = proceeds - p.cost;
  PF.cash += proceeds; delete PF.positions[token]; savePF(PF);
  renderInsts($("#cats .chip-btn.on")?.dataset.cat || "All"); renderHoldings(); updateSummary();
  toast(`Closed position · ${pnl >= 0 ? "+" : ""}${money(pnl)}`);
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
// Router
// ============================================================
const routes = { "": homePage, signals: signalsPage, traders: tradersPage, markets: marketsPage, trading: tradingPage, performance: perfPage };
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
