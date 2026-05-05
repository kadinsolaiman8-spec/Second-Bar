const q = (selector, root = document) => root.querySelector(selector);
const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  page: "status",
  alertFilter: "all",
  selectedAlertId: 1,
  alerts: [],
};

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
  const health = [
    ["Local Dashboard", "ok", "This localhost page and alert renderer are loading correctly."],
    ["Market Clock", "ok", `${ms.title}. ${ms.next}.`],
    ["Alert Output Page", "ok", "Alert cards are ready with entry, stop loss, targets, score, and details."],
    ["Scanner Process", "wait", "Run the Discord bot separately with main.py. Trade scans only run during regular market hours."],
    ["Market Data/API", "wait", "The bot modules use yfinance/Finnhub; this monitor does not call external APIs."],
    ["Discord Connection", "wait", "Discord status depends on the bot process and token, not this static dashboard."],
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

function setPage(page) {
  state.page = page;
  qa(".page").forEach((section) => section.classList.toggle("active", section.id === page));
  qa(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.section === page));
}

function init() {
  state.alerts = baseAlerts.map((alert) => ({ ...alert }));
  renderMarket();
  renderHealth();
  renderSchedule();
  renderFilters();
  renderAlerts();

  qa("[data-section]").forEach((button) => {
    button.addEventListener("click", () => setPage(button.dataset.section));
  });

  q("#refreshStatus").addEventListener("click", () => {
    renderMarket();
    renderHealth();
    renderAlerts();
  });
  q("#addDemoAlert").addEventListener("click", addDemoAlert);
  q("#clearDemoAlerts").addEventListener("click", () => {
    state.alerts = baseAlerts.map((alert) => ({ ...alert }));
    state.selectedAlertId = state.alerts[0].id;
    renderAlerts();
  });

  setInterval(() => {
    renderMarket();
    renderHealth();
    renderAlertsNotice();
  }, 30000);
}

window.addEventListener("DOMContentLoaded", init);
