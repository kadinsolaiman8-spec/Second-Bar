const q = (selector, root = document) => root.querySelector(selector);
const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  page: "status",
  alertFilter: "all",
  selectedAlertId: 1,
  alerts: [],
};

/** Set by refreshFromBackend(): health + scan payloads from FastAPI */
let backendHealth = { loaded: false, health: null, scan: null };

const baseAlerts = [
  {
    id: 1,
    ticker: "NVDA",
    givenAt: "May 5, 2026 at 10:12 AM ET",
    status: "Live alert",
    confidence: 91,
    strategy: "VWAP Momentum Breakout",
    direction: "BUY",
    entry: 135.42,
    stop: 133.20,
    tp1: 139.86,
    tp2: 143.19,
    tp3: 146.52,
    rr: "2.0:1",
    rsi: 58,
    rvol: "1.9x",
    vwap: "0.8% above",
    note: "Strong volume confirmation with price holding above VWAP.",
  },
  {
    id: 2,
    ticker: "AMD",
    givenAt: "May 5, 2026 at 10:28 AM ET",
    status: "Watching",
    confidence: 84,
    strategy: "EMA Pullback in Trend",
    direction: "BUY",
    entry: 164.20,
    stop: 161.70,
    tp1: 169.20,
    tp2: 172.95,
    tp3: 176.70,
    rr: "2.0:1",
    rsi: 54,
    rvol: "1.5x",
    vwap: "0.3% above",
    note: "Pullback is valid, but confidence sits below strong-alert threshold.",
  },
  {
    id: 3,
    ticker: "CRWD",
    givenAt: "May 5, 2026 at 11:04 AM ET",
    status: "High conviction",
    confidence: 96,
    strategy: "Bollinger Band Squeeze Breakout",
    direction: "BUY",
    entry: 312.84,
    stop: 307.62,
    tp1: 323.28,
    tp2: 331.11,
    tp3: 338.94,
    rr: "2.0:1",
    rsi: 62,
    rvol: "2.3x",
    vwap: "1.1% above",
    note: "Squeeze expansion, high RVOL, and clean risk structure.",
  },
];

const schedules = [
  ["Premarket", "4:00 AM ET", "No trade alerts are generated before the regular session opens."],
  ["Market Open", "9:30 AM ET", "ORB range starts. The market status should show LIVE."],
  ["Midday Quiet", "11:30 AM - 3:00 PM ET", "Scanning continues, but automatic alerts are intentionally quieter."],
  ["Market Close", "4:00 PM ET", "The status banner should show MARKET CLOSED and no new trade alerts."],
];

function indicatorRsi(indicators) {
  if (!indicators || typeof indicators !== "object") return "—";
  const rsi = indicators.rsi;
  if (rsi && typeof rsi === "object" && rsi.value != null) return rsi.value;
  if (typeof rsi === "number") return rsi;
  return "—";
}

function formatRr(rr) {
  if (rr == null) return "—";
  if (typeof rr === "number") return `${rr.toFixed(2)}:1`;
  return String(rr);
}

function mapSignalToAlert(sig, idx) {
  const total = sig.score_data && typeof sig.score_data.total === "number" ? sig.score_data.total : 0;
  const rsi = indicatorRsi(sig.indicators);
  return {
    id: idx + 1,
    ticker: sig.ticker || "—",
    givenAt: formatGivenAt(),
    status: total >= 85 ? "Live alert" : "Watching",
    confidence: total,
    strategy: sig.strategy || "—",
    direction: sig.direction || "BUY",
    entry: Number(sig.entry_price) || 0,
    stop: Number(sig.stop) || 0,
    tp1: Number(sig.tp1) || 0,
    tp2: Number(sig.tp2) || 0,
    tp3: Number(sig.tp3) || 0,
    rr: formatRr(sig.rr),
    rsi,
    rvol: "—",
    vwap: "—",
    note: `Regime: ${sig.regime || "?"} · ${sig.market_state_label || "market state unknown"}`,
  };
}

function applyScanToAlerts(scanJson) {
  if (!scanJson || !Array.isArray(scanJson.signals)) return;
  const mapped = scanJson.signals.map(mapSignalToAlert);
  if (!mapped.length) return;
  state.alerts = mapped;
  state.selectedAlertId = mapped[0].id;
}

async function refreshFromBackend() {
  try {
    const [healthRes, scanRes] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/scan/latest"),
    ]);
    if (!healthRes.ok || !scanRes.ok) throw new Error("bad status");
    const health = await healthRes.json();
    const scan = await scanRes.json();
    backendHealth = { loaded: true, health, scan };
    applyScanToAlerts(scan);
  } catch {
    backendHealth = { loaded: false, health: null, scan: null };
  }
}

function money(value) {
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function etParts() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

function formatGivenAt(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZoneName: "short",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.month} ${values.day}, ${values.year} at ${values.hour}:${values.minute} ${values.dayPeriod} ${values.timeZoneName}`;
}

function marketState() {
  const parts = etParts();
  const weekday = parts.weekday;
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  const total = hour * 60 + minute;
  const isWeekday = !["Sat", "Sun"].includes(weekday);
  const open = 9 * 60 + 30;
  const close = 16 * 60;

  if (!isWeekday) {
    return {
      key: "closed",
      title: "MARKET CLOSED",
      message: "It is the weekend in Eastern Time. No trade alerts are generated until the next regular market session.",
      next: "Next open: Monday 9:30 AM ET",
      time: `${parts.hour}:${parts.minute}:${parts.second} ET`,
    };
  }
  if (total < open) {
    return {
      key: "pre",
      title: "PREMARKET",
      message: "The regular session has not opened yet. No trade alerts are generated before 9:30 AM ET.",
      next: "Opens today at 9:30 AM ET",
      time: `${parts.hour}:${parts.minute}:${parts.second} ET`,
    };
  }
  if (total >= open && total < close) {
    return {
      key: "open",
      title: "MARKET LIVE",
      message: "Regular market hours are active. Scanner alerts can be treated as live market outputs.",
      next: "Closes today at 4:00 PM ET",
      time: `${parts.hour}:${parts.minute}:${parts.second} ET`,
    };
  }
  return {
    key: "closed",
    title: "MARKET CLOSED",
    message: "The regular session is over. No new trade alerts are generated after market close.",
    next: "Next open: next weekday 9:30 AM ET",
    time: `${parts.hour}:${parts.minute}:${parts.second} ET`,
  };
}

function renderMarket() {
  const ms = marketState();
  const panel = q("#marketPanel");
  panel.className = `market-panel ${ms.key}`;
  q("#marketTitle").textContent = ms.title;
  q("#marketMessage").textContent = ms.message;
  q("#etTime").textContent = ms.time;
  q("#nextEvent").textContent = ms.next;

  const pill = q("#topMarketPill");
  pill.className = `market-pill ${ms.key}`;
  pill.textContent = ms.title;
}

function statusBadge(status) {
  const key = status === "ok" ? "ok" : status === "off" ? "off" : "wait";
  const label = status === "ok" ? "Working" : status === "off" ? "Not connected" : "Standby";
  return `<span class="status-badge ${key}">${label}</span>`;
}

function renderHealth() {
  const ms = marketState();
  const bh = backendHealth.health;
  const apiOk = backendHealth.loaded && bh && bh.status === "ok";
  const schedOk = apiOk && bh.scheduler_running;
  const scanMeta = backendHealth.scan;
  const lastScan = scanMeta && scanMeta.last_scan_at ? String(scanMeta.last_scan_at) : "never";

  const health = [
    ["Local Dashboard", "ok", "Static UI loaded from the Python server."],
    ["Market Clock", "ok", `${ms.title}. ${ms.next}.`],
    ["HTTP API", apiOk ? "ok" : "off", apiOk ? "Backend reachable at /api/* on this origin." : "Start uvicorn backend (see README); opened file:// won't reach APIs."],
    ["Scanner scheduler", schedOk ? "ok" : apiOk ? "wait" : "off", schedOk ? `Interval ~${bh.scan_interval_seconds}s` : "Scheduler status unknown until API is up."],
    ["Latest scan snapshot", scanMeta && scanMeta.signals && scanMeta.signals.length ? "ok" : "wait", `Last run: ${lastScan}${scanMeta && scanMeta.last_error ? ` — error: ${scanMeta.last_error}` : ""}`],
    ["Market Data/API", apiOk ? "wait" : "off", "OHLCV via yfinance / optional Alpha Vantage & Polygon keys in .env"],
  ];

  q("#healthGrid").innerHTML = health
    .map(([title, status, detail]) => `
      <article class="health-card">
        <div class="health-head">
          <h4>${escapeHtml(title)}</h4>
          ${statusBadge(status)}
        </div>
        <p>${escapeHtml(detail)}</p>
      </article>
    `)
    .join("");
}

function renderSchedule() {
  q("#scheduleGrid").innerHTML = schedules
    .map(([title, time, detail]) => `
      <article class="schedule-card">
        <h4>${escapeHtml(title)}</h4>
        <p><strong>${escapeHtml(time)}</strong></p>
        <p>${escapeHtml(detail)}</p>
      </article>
    `)
    .join("");
}

function alertLevel(alert) {
  if (alert.confidence >= 95) return "high";
  if (alert.confidence >= 85) return "high";
  if (alert.confidence >= 70) return "medium";
  return "low";
}

function renderFilters() {
  const filters = [
    ["all", "All"],
    ["high", "Strong"],
    ["medium", "Watching"],
  ];
  q("#alertFilters").innerHTML = filters
    .map(([id, label]) => `<button class="filter-button ${state.alertFilter === id ? "active" : ""}" type="button" data-filter="${id}">${label}</button>`)
    .join("");
  qa("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.alertFilter = button.dataset.filter;
      renderFilters();
      renderAlerts();
    });
  });
}

function visibleAlerts() {
  return state.alerts.filter((alert) => {
    if (state.alertFilter === "all") return true;
    return alertLevel(alert) === state.alertFilter;
  });
}

function renderAlertsNotice() {
  const ms = marketState();
  const notice = q("#alertsNotice");
  const addButton = q("#addDemoAlert");
  const isOpen = ms.key === "open";

  notice.className = `alerts-notice ${isOpen ? "open" : "closed"}`;
  notice.innerHTML = isOpen
    ? `<strong>Market live.</strong> New trade alerts can appear during regular hours.`
    : `<strong>Market closed.</strong> No new trade alerts are generated right now. The feed below is regular-hours alert history only.`;

  addButton.disabled = !isOpen;
  addButton.textContent = isOpen ? "Add Demo Alert" : "Market Closed";
}

function renderAlerts() {
  const ms = marketState();
  const alerts = visibleAlerts();
  renderAlertsNotice();
  q("#alertsFeed").innerHTML = alerts.length
    ? alerts.map((alert) => renderAlertCard(alert, ms)).join("")
    : `<article class="health-card"><h4>No alerts in this view</h4><p>Try another filter. New trade alerts only appear during regular market hours.</p></article>`;

  qa("[data-alert-id]").forEach((card) => {
    card.addEventListener("click", () => {
      state.selectedAlertId = Number(card.dataset.alertId);
      renderAlerts();
      renderAlertDetail();
    });
  });
  renderAlertDetail();
}

function renderAlertCard(alert, ms) {
  const level = alertLevel(alert);
  const color = level === "high" ? "var(--green)" : level === "medium" ? "var(--amber)" : "var(--red)";
  const selected = alert.id === state.selectedAlertId ? "selected" : "";
  const sessionLabel = ms.key === "open" ? alert.status : "Regular-hours alert";
  return `
    <article class="alert-card ${selected}" data-alert-id="${alert.id}" style="--alert-color:${color}">
      <div class="alert-head">
        <div class="alert-title">
          <h4>${escapeHtml(alert.ticker)} ${escapeHtml(alert.direction)} - ${escapeHtml(alert.strategy)}</h4>
          <small>Alert Given: ${escapeHtml(alert.givenAt)} / ${escapeHtml(sessionLabel)}</small>
        </div>
        <span class="alert-badge ${level}">${alert.confidence}/100</span>
      </div>
      <div class="level-grid">
        <div class="level"><span>Entry</span><b>${money(alert.entry)}</b></div>
        <div class="level stop"><span>Stop Loss</span><b>${money(alert.stop)}</b></div>
        <div class="level"><span>TP1</span><b>${money(alert.tp1)}</b></div>
        <div class="level"><span>TP2</span><b>${money(alert.tp2)}</b></div>
        <div class="level"><span>R:R</span><b>${escapeHtml(alert.rr)}</b></div>
      </div>
      <div class="alert-actions">
        <button class="small-button primary" type="button">View Details</button>
        <button class="small-button" type="button">Risk Plan</button>
      </div>
    </article>
  `;
}

function renderAlertDetail() {
  const alert = state.alerts.find((item) => item.id === state.selectedAlertId) || state.alerts[0];
  if (!alert) {
    q("#alertDetail").innerHTML = `<h4>No alert selected</h4>`;
    return;
  }
  const risk = Math.abs(alert.entry - alert.stop);
  q("#alertDetail").innerHTML = `
    <p class="eyebrow">Selected alert</p>
    <h4>${escapeHtml(alert.ticker)} Trade Plan</h4>
    <div class="detail-list">
      <div class="detail-row"><span>Alert Given</span><b>${escapeHtml(alert.givenAt)}</b></div>
      <div class="detail-row"><span>Strategy</span><b>${escapeHtml(alert.strategy)}</b></div>
      <div class="detail-row"><span>Confidence</span><b>${alert.confidence}/100</b></div>
      <div class="detail-row"><span>Entry</span><b>${money(alert.entry)}</b></div>
      <div class="detail-row"><span>Stop Loss</span><b>${money(alert.stop)}</b></div>
      <div class="detail-row"><span>Risk / Share</span><b>${money(risk)}</b></div>
      <div class="detail-row"><span>TP1</span><b>${money(alert.tp1)}</b></div>
      <div class="detail-row"><span>TP2</span><b>${money(alert.tp2)}</b></div>
      <div class="detail-row"><span>TP3</span><b>${money(alert.tp3)}</b></div>
      <div class="detail-row"><span>RSI</span><b>${alert.rsi}</b></div>
      <div class="detail-row"><span>RVOL</span><b>${escapeHtml(alert.rvol)}</b></div>
      <div class="detail-row"><span>VWAP</span><b>${escapeHtml(alert.vwap)}</b></div>
    </div>
    <div class="plain-box">${escapeHtml(alert.note)}</div>
  `;
}

function addDemoAlert() {
  if (marketState().key !== "open") {
    renderAlertsNotice();
    return;
  }

  const tickers = ["AAPL", "TSLA", "META", "COIN", "PLTR", "MSFT", "PANW"];
  const strategies = ["VWAP Momentum Breakout", "Opening Range Breakout", "EMA Pullback in Trend", "Fibonacci Confluence Reversal", "Bollinger Band Squeeze Breakout"];
  const ticker = tickers[Math.floor(Math.random() * tickers.length)];
  const entry = 40 + Math.random() * 380;
  const atr = Math.max(0.7, entry * 0.015);
  const confidence = 70 + Math.floor(Math.random() * 28);
  const alert = {
    id: Math.max(...state.alerts.map((item) => item.id), 0) + 1,
    ticker,
    givenAt: formatGivenAt(),
    status: confidence >= 85 ? "Live alert" : "Watching",
    confidence,
    strategy: strategies[Math.floor(Math.random() * strategies.length)],
    direction: "BUY",
    entry,
    stop: entry - atr,
    tp1: entry + 2 * atr,
    tp2: entry + 3.5 * atr,
    tp3: entry + 5 * atr,
    rr: "2.0:1",
    rsi: 45 + Math.floor(Math.random() * 22),
    rvol: `${(1.1 + Math.random() * 1.4).toFixed(1)}x`,
    vwap: `${(0.1 + Math.random() * 1.2).toFixed(1)}% above`,
    note: "Demo alert added locally to show how new bot outputs would appear in the feed.",
  };
  state.alerts.unshift(alert);
  state.selectedAlertId = alert.id;
  renderAlerts();
}

function formatPct(x) {
  if (typeof x !== "number" || Number.isNaN(x)) return "—";
  return `${x >= 0 ? "+" : ""}${x.toFixed(2)}%`;
}

function renderBacktestResults(payload) {
  const el = q("#backtestResults");
  if (!payload || !payload.result) {
    el.innerHTML = "";
    return;
  }
  const r = payload.result;
  const trades = Array.isArray(r.trades) ? r.trades : [];
  const sym = escapeHtml(String(r.symbol || "—"));
  const startD = escapeHtml(String(r.start_date || "?"));
  const endD = escapeHtml(String(r.end_date || "?"));

  const cards = [
    ["Symbol", sym],
    ["Window", `${startD} → ${endD}`],
    ["Total return", escapeHtml(formatPct(r.total_return))],
    ["Buy & hold", escapeHtml(formatPct(r.buy_hold_return))],
    ["Max drawdown", escapeHtml(formatPct(r.max_drawdown))],
    ["Trades", escapeHtml(String(r.num_trades ?? "—"))],
    ["Win rate", typeof r.win_rate === "number" ? escapeHtml(`${r.win_rate.toFixed(1)}%`) : "—"],
    ["Profit factor", typeof r.profit_factor === "number" ? escapeHtml(r.profit_factor.toFixed(2)) : "—"],
  ];

  const tradeRows = trades
    .map(
      (t) => `
    <tr>
      <td>${escapeHtml(String(t.entry_date ?? ""))}</td>
      <td>${escapeHtml(String(t.exit_date ?? ""))}</td>
      <td>${escapeHtml(String(t.side ?? ""))}</td>
      <td>${escapeHtml(String(t.entry_price ?? ""))}</td>
      <td>${escapeHtml(String(t.exit_price ?? ""))}</td>
      <td>${escapeHtml(formatPct(t.pnl_pct))}</td>
      <td>${escapeHtml(String(t.bars_held ?? ""))}</td>
    </tr>`,
    )
    .join("");

  const truncated =
    typeof r.num_trades === "number" && r.num_trades > trades.length
      ? `<p class="backtest-muted">Showing first ${trades.length} trades (${r.num_trades} total).</p>`
      : "";

  el.innerHTML = `
    <article class="health-card">
      <p class="eyebrow">Summary</p>
      <div class="bt-summary-grid">
        ${cards
          .map(
            ([label, html]) => `
          <div class="bt-summary-card">
            <span>${escapeHtml(label)}</span>
            <b>${html}</b>
          </div>`,
          )
          .join("")}
      </div>
    </article>
    <article class="health-card">
      <p class="eyebrow">Trades</p>
      ${truncated}
      <div class="trades-scroll">
        <table class="trades-table">
          <thead>
            <tr>
              <th>Entry</th>
              <th>Exit</th>
              <th>Side</th>
              <th>Entry px</th>
              <th>Exit px</th>
              <th>PnL %</th>
              <th>Bars</th>
            </tr>
          </thead>
          <tbody>${tradeRows || `<tr><td colspan="7">No trades in window.</td></tr>`}</tbody>
        </table>
      </div>
    </article>
  `;
}

async function submitBacktest() {
  const statusEl = q("#backtestStatus");
  const btRun = q("#btRun");
  const tickerInput = q("#btTicker");
  const ticker = tickerInput.value.trim().toUpperCase();
  if (!ticker) {
    statusEl.textContent = "Enter a ticker.";
    return;
  }
  btRun.disabled = true;
  statusEl.textContent = "Running backtest…";
  renderBacktestResults(null);
  try {
    const res = await fetch("/api/quant/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker,
        period: q("#btPeriod").value,
        interval: q("#btInterval").value,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = body.detail;
      const msg = typeof detail === "string" ? detail : JSON.stringify(detail || body);
      statusEl.textContent = `Error (${res.status}): ${msg}`;
      return;
    }
    statusEl.textContent = `Completed ${ticker} · ${body.result?.num_trades ?? 0} trades.`;
    renderBacktestResults(body);
  } catch (e) {
    statusEl.textContent = `Request failed: ${e && e.message ? e.message : String(e)}`;
  } finally {
    btRun.disabled = false;
  }
}

function setPage(page) {
  state.page = page;
  qa(".page").forEach((section) => section.classList.toggle("active", section.id === page));
  qa(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.section === page));
}

async function init() {
  state.alerts = baseAlerts.map((alert) => ({ ...alert }));
  renderMarket();
  await refreshFromBackend();
  renderHealth();
  renderSchedule();
  renderFilters();
  renderAlerts();

  qa("[data-section]").forEach((button) => {
    button.addEventListener("click", () => setPage(button.dataset.section));
  });

  q("#refreshStatus").addEventListener("click", async () => {
    renderMarket();
    await refreshFromBackend();
    renderHealth();
    renderAlerts();
  });

  const runBtn = q("#runScan");
  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      try {
        const res = await fetch("/api/scan/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (!res.ok) throw new Error(await res.text());
        await refreshFromBackend();
        renderAlerts();
        renderHealth();
      } catch (e) {
        console.error(e);
      } finally {
        runBtn.disabled = false;
      }
    });
  }

  q("#addDemoAlert").addEventListener("click", addDemoAlert);
  q("#clearDemoAlerts").addEventListener("click", () => {
    state.alerts = baseAlerts.map((alert) => ({ ...alert }));
    state.selectedAlertId = state.alerts[0].id;
    renderAlerts();
  });

  const btRun = q("#btRun");
  if (btRun) {
    btRun.addEventListener("click", () => submitBacktest().catch(console.error));
  }

  setInterval(() => {
    renderMarket();
    refreshFromBackend().then(() => {
      renderHealth();
      renderAlertsNotice();
    });
  }, 30000);
}

window.addEventListener("DOMContentLoaded", () => {
  init().catch(console.error);
});
