// PolyFlow dashboard — vanilla JS, no build step. Talks to the PolyFlow REST API
// and subscribes to the /ws/prices WebSocket for live price ticks.

const $ = (id) => document.getElementById(id);
const fmtUsd = (n) => "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const short = (w) => (w ? w.slice(0, 6) + "…" + w.slice(-4) : "—");
let tickCount = 0;
const priceCells = {}; // token_id -> <td>

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function loadHealth() {
  try {
    const h = await getJSON("/health");
    const badge = $("mode-badge");
    badge.textContent = h.mode === "live" ? "LIVE data" : "fixtures";
    badge.style.color = h.mode === "live" ? "var(--green)" : "var(--amber)";
  } catch (e) { /* ignore */ }
}

async function loadRecommendations() {
  const recs = await getJSON("/recommendations?limit=25");
  $("stat-recs").textContent = recs.length;
  const tbody = $("recs-table").querySelector("tbody");
  tbody.innerHTML = recs.map((r) => {
    const conf = Math.round(r.confidence * 100);
    const cls = r.direction === "BUY" ? "buy" : "avoid";
    return `<tr>
      <td title="${r.question || ""}">${(r.question || r.condition_id).slice(0, 52)}</td>
      <td><span class="pill ${cls}">${r.direction} ${r.outcome}</span></td>
      <td><div class="conf-bar"><div class="conf-fill" style="width:${conf}%"></div>
          <span class="conf-text">${conf}%</span></div></td>
      <td>${r.supporter_count}</td>
      <td>${fmtUsd(r.consensus_size_usd)}</td>
    </tr>`;
  }).join("");
}

function scoreCell(v) {
  const pct = Math.round(v * 100);
  return `<span class="score">${pct}</span>`;
}

async function loadLeaderboard() {
  const board = await getJSON("/traders/leaderboard?limit=25");
  $("stat-traders").textContent = board.length >= 25 ? "25+" : board.length;
  const tbody = $("leaderboard-table").querySelector("tbody");
  tbody.innerHTML = board.map((t) => `<tr>
      <td>${t.rank}</td>
      <td class="mono" title="${t.proxy_wallet}">${t.username || short(t.proxy_wallet)}</td>
      <td>${scoreCell(t.composite)}</td>
      <td>${scoreCell(t.profitability)}</td>
      <td>${scoreCell(t.consistency)}</td>
      <td>${scoreCell(t.sizing)}</td>
      <td>${scoreCell(t.risk_adjusted)}</td>
    </tr>`).join("");
}

async function loadMarkets() {
  const markets = await getJSON("/markets?limit=25&sort=volume");
  $("stat-markets").textContent = markets.length >= 25 ? "25+" : markets.length;
  const tbody = $("markets-table").querySelector("tbody");
  tbody.innerHTML = "";
  for (const m of markets) {
    const yesToken = (m.clob_token_ids || [])[0];
    const price = (m.outcome_prices || [])[0];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td title="${m.question}">${m.question.slice(0, 46)}</td>
      <td>${m.category || "—"}</td>
      <td>${fmtUsd(m.volume)}</td>
      <td class="price" data-token="${yesToken}">${price != null ? price.toFixed(3) : "—"}</td>`;
    tbody.appendChild(tr);
    if (yesToken) priceCells[String(yesToken)] = tr.querySelector(".price");
  }
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/prices`);
  const badge = $("ws-badge");

  ws.onopen = () => { badge.textContent = "● live feed"; badge.className = "badge ws-on"; };
  ws.onclose = () => {
    badge.textContent = "○ reconnecting"; badge.className = "badge ws-off";
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type !== "price") return;
    tickCount += 1;
    $("stat-ticks").textContent = tickCount;
    const cell = priceCells[String(msg.token_id)];
    if (cell) {
      cell.textContent = Number(msg.price).toFixed(3);
      cell.classList.remove("price-flash");
      void cell.offsetWidth; // restart animation
      cell.classList.add("price-flash");
    }
  };
}

async function refresh() {
  try {
    await Promise.all([loadRecommendations(), loadLeaderboard(), loadMarkets(), loadHealth()]);
    $("updated").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    $("updated").textContent = "error: " + e.message;
  }
}

refresh();
connectWS();
setInterval(refresh, 15000); // periodic data refresh; prices stream live via WS
