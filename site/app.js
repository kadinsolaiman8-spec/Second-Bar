const q = (selector, root = document) => root.querySelector(selector);
const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

const THEME_STORAGE_KEY = "tb-theme";

function getDocumentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* ignore quota / private mode */
  }
  const toggle = q("#themeToggle");
  const label = q("#theme-toggle-state-label");
  if (toggle) {
    const isDark = next === "dark";
    toggle.setAttribute("aria-pressed", isDark ? "true" : "false");
    if (label) {
      label.textContent = isDark ? "Dark mode" : "Light mode";
    }
  }
}

function initThemeToggle() {
  const toggle = q("#themeToggle");
  if (!toggle) {
    return;
  }
  applyTheme(getDocumentTheme());
  toggle.addEventListener("click", () => {
    applyTheme(getDocumentTheme() === "dark" ? "light" : "dark");
  });
}

const state = {
  page: "live",
  stockTicker: null,
};

/** Market session from GET /api/market/session; falls back to simplified Mon–Fri hours. */
let marketSessionState = {
  loaded: false,
  data: null,
  simplified: false,
};

/** Set by refreshFromBackend(): health + scan + journal payloads from FastAPI */
let backendHealth = {
  loaded: false,
  health: null,
  scan: null,
  journalStats: null,
  journalEquityCurve: null,
  journalOpenTrades: null,
};

/** Last ticker selected from the scanner or stock page — used to pre-fill the journal form. */
let lastSelectedScannerTicker = null;

/** Populated by loadQuantStrategies — includes optional wfo_supported from API */
let quantStrategiesMeta = [];

/** Dedup concurrent refreshFromBackend callers (polling + nav + buttons). */
let backendRefreshInFlight = null;

/** When SSE is connected, sparse HTTP polling is enough for drift recovery. */
let scannerSseStreaming = false;

const POLL_INTERVAL_MS = 30000;
const POLL_SLOW_MS_WHEN_SSE = 120000;
/** Trailing debounce for scan/snapshot SSE → DOM (latest payload wins). */
const SCANNER_LIVE_DOM_COALESCE_MS = 150;

let scannerLiveEventSource = null;
/** Latest scan payload waiting for coalesced DOM flush. */
let scannerLiveDomPending = null;
let scannerLiveDomTimerId = null;

function motionDisabled() {
  const motion = globalThis.TbMotion;
  if (motion?.getScale) return motion.getScale() === 0;
  try {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch {
    return false;
  }
}

function motionDurationMs(baseMs) {
  const motion = globalThis.TbMotion;
  if (motion?.durationMs) return motion.durationMs(baseMs);
  return baseMs;
}

/** Keeps CSS `dot--click-spin` duration and JS cleanup in sync (12% of the 10s idle cycle). */
const SIDE_NOTE_DOT_CLICK_SPIN_MS = 1200;

let sideNoteDotSpinClearTimerId = null;

function playSideNoteDotClickSpin(buttonEl) {
  if (motionDisabled()) return;
  const dot = buttonEl?.querySelector?.(".dot");
  if (!dot) return;
  if (sideNoteDotSpinClearTimerId != null) {
    window.clearTimeout(sideNoteDotSpinClearTimerId);
    sideNoteDotSpinClearTimerId = null;
  }
  dot.classList.remove("dot--click-spin");
  void dot.offsetWidth;
  dot.classList.add("dot--click-spin");
  sideNoteDotSpinClearTimerId = window.setTimeout(() => {
    dot.classList.remove("dot--click-spin");
    sideNoteDotSpinClearTimerId = null;
  }, motionDurationMs(SIDE_NOTE_DOT_CLICK_SPIN_MS) || SIDE_NOTE_DOT_CLICK_SPIN_MS);
}

/** Web Animations rotation for #refreshStatus icon (CSS keyframes on SVG are unreliable). */
let refreshStatusSpinAnimation = null;

function playRefreshStatusIconSpin(buttonEl) {
  const svg = buttonEl?.querySelector?.("svg");
  if (!buttonEl || typeof buttonEl.animate !== "function") return;
  if (motionDisabled()) {
    return;
  }
  try {
    refreshStatusSpinAnimation?.cancel?.();
  } catch {
    /* ignore */
  }
  const keyframes = [{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }];
  const timing = {
    duration: motionDurationMs(650) || 650,
    easing: "cubic-bezier(0.45, 0.05, 0.25, 1)",
    fill: "none",
  };
  let targetEl = buttonEl;
  let anim = null;
  if (svg && typeof svg.animate === "function") {
    svg.style.transformBox = "fill-box";
    svg.style.transformOrigin = "50% 55%";
    anim = svg.animate(keyframes, timing);
  }
  if (!anim) {
    buttonEl.style.transformOrigin = "center center";
    anim = buttonEl.animate(keyframes, timing);
  }
  if (!anim) return;
  refreshStatusSpinAnimation = anim;
  refreshStatusSpinAnimation.onfinish = () => {
    refreshStatusSpinAnimation = null;
  };
  refreshStatusSpinAnimation.oncancel = () => {
    refreshStatusSpinAnimation = null;
  };
}

/** Stock detail page: TradingView Lightweight Charts + polling. */
let stockLiveChartInstance = null;
let stockCandleSeries = null;
let stockChartResizeObserver = null;
let stockBarsPollTimerId = null;
/** Allow a small empty margin when panning left of the first bar (soft stop; see info control). */
const STOCK_CHART_SOFT_LEFT_PADDING_SEC = 3 * 60 * 60;
/** Unsubscribe for visible-range clamp (soft left bound). */
let stockChartVisibleRangeHandler = null;
let stockHistoryInfoDocHandlersBound = false;

const NAV_ORDER_STORAGE_KEY = "tb_sidebar_nav_order_v1";
const SIDEBAR_REORDERABLE_IDS = ["journal", "backtest", "settings"];
const SIDEBAR_NAV_DEFAULT_ORDER = [...SIDEBAR_REORDERABLE_IDS];

/** SortableJS instance for draggable sidebar tabs (excluding Home). */
let sidebarNavSortable = null;
/** Ignore clicks right after a reorder drag — mouseup can hit whichever row slid under the cursor. */
let sidebarNavDragSuppressClickUntil = 0;

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const BT_INTERVALS_QUANT = [
  ["1d", "Daily"],
  ["1wk", "Weekly"],
  ["1h", "Hourly"],
];
const BT_INTERVALS_DAY = [
  ["15m", "15 minutes"],
  ["30m", "30 minutes"],
  ["1h", "60 minutes (1h)"],
];

/** Quant: daily / weekly bars — long history available. */
const BT_PERIODS_QUANT = [
  ["6mo", "6 months"],
  ["1y", "1 year"],
  ["2y", "2 years"],
  ["5y", "5 years"],
  ["10y", "10 years"],
];

/**
 * Day-trading 15m/30m: Yahoo caps intraday history (~60d); only offer ranges that work.
 */
const BT_PERIODS_DAY_15_30 = [
  ["5d", "5 days"],
  ["1wk", "1 week"],
  ["1mo", "1 month"],
  ["2mo", "2 months"],
  ["60d", "60 days"],
];

/** Hourly (1h): ~730d Yahoo cap — quant hourly and day-trading 1h. */
const BT_PERIODS_1H_CAPPED = [
  ["1mo", "1 month"],
  ["2mo", "2 months"],
  ["3mo", "3 months"],
  ["6mo", "6 months"],
  ["1y", "1 year"],
  ["2y", "2 years"],
];

const BT_SETTINGS_BY_TICKER_KEY = "backtest_settings_by_ticker";
const BT_HISTORY_KEY = "backtest_run_history_v1";
const BT_HISTORY_CAP = 20;
const BT_HISTORY_TAB_PREVIEW_CAP = 4;

const BT_PERIOD_VALUE_LABEL = {
  "6mo": "6 months",
  "1y": "1 year",
  "2y": "2 years",
  "5y": "5 years",
  "10y": "10 years",
  "1mo": "1 month",
  "2mo": "2 months",
  "3mo": "3 months",
};

const BT_INTERVAL_VALUE_LABEL = {
  "1d": "Daily",
  "1wk": "Weekly",
  "1h": "Hourly",
  "15m": "15-minute",
};

/** Latest successful merged API payload (also duplicated into history snapshots). */
let btLastSuccessfulPayload = null;

/** History list row id whose summary is open (#backtest/result); null when collapsed. */
let btExpandedHistoryId = null;

/** Full run panel (ticker + details) visible; when false and history exists, minimal bar only. */
let btRunPanelDetailOpen = true;

function normalizeBtTicker(raw) {
  return String(raw ?? "")
    .trim()
    .toUpperCase();
}

function debounceBt(fn, ms) {
  let timerId = null;
  return (...args) => {
    if (timerId != null) clearTimeout(timerId);
    timerId = setTimeout(() => {
      timerId = null;
      fn(...args);
    }, ms);
  };
}

function readBtSettingsByTickerMap() {
  try {
    const raw = localStorage.getItem(BT_SETTINGS_BY_TICKER_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeBtSettingsByTickerMap(map) {
  try {
    localStorage.setItem(BT_SETTINGS_BY_TICKER_KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / private mode */
  }
}

function collectBtSettingsFromForm() {
  const modeEl = q("#btTradingMode");
  const mode = modeEl ? modeEl.value : "quant";
  const out = {
    trading_mode: mode,
    period: q("#btPeriod")?.value ?? "1y",
    interval: q("#btInterval")?.value ?? "1d",
  };
  if (mode === "day_trading") {
    out.day_strategy_id = q("#btDayStrategy")?.value ?? "";
  } else {
    out.quant_strategy_id = q("#btQuantStrategy")?.value ?? "hybrid";
  }
  return normalizeBtPersistedSettings(out);
}

/** Strips cross-mode fields so localStorage records stay valid per trading_mode. */
function normalizeBtPersistedSettings(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return settings;
  const mode = settings.trading_mode === "day_trading" ? "day_trading" : "quant";
  const next = { ...settings, trading_mode: mode };
  if (mode === "quant") {
    delete next.day_strategy_id;
  } else {
    delete next.quant_strategy_id;
  }
  return next;
}

function applyBtSettingsToForm(settings) {
  if (!settings || typeof settings !== "object") return;
  settings = normalizeBtPersistedSettings(settings);
  const modeEl = q("#btTradingMode");
  if (settings.trading_mode && modeEl) {
    modeEl.value = settings.trading_mode;
  }
  syncBacktestModeUi();
  const periodEl = q("#btPeriod");
  const intervalEl = q("#btInterval");
  const stratEl = q("#btDayStrategy");
  if (settings.interval && intervalEl) intervalEl.value = settings.interval;
  setBtPeriodOptions();
  if (settings.period && periodEl) {
    const allowed = new Set(Array.from(periodEl.options).map((o) => o.value));
    if (allowed.has(settings.period)) periodEl.value = settings.period;
  }
  if (settings.day_strategy_id && stratEl) stratEl.value = settings.day_strategy_id;
  const quantEl = q("#btQuantStrategy");
  if (quantEl) {
    const qid = settings.quant_strategy_id;
    const allowedQ = new Set(Array.from(quantEl.options).map((o) => o.value));
    quantEl.value = qid && allowedQ.has(qid) ? qid : "hybrid";
  }
  refreshBtQuickCardSummary();
}

function saveBtSettingsForTickerKey(tickerRaw) {
  const key = normalizeBtTicker(tickerRaw);
  if (!key) return;
  const map = readBtSettingsByTickerMap();
  map[key] = collectBtSettingsFromForm();
  writeBtSettingsByTickerMap(map);
  refreshBtQuickCardSummary();
}

const schedulePersistBtSettingsForTicker = debounceBt(() => {
  const tickerInput = q("#btTicker");
  saveBtSettingsForTickerKey(tickerInput ? tickerInput.value : "");
}, 400);

const scheduleRefreshBtQuickCardSummary = debounceBt(() => {
  refreshBtQuickCardSummary();
}, 200);

const BT_TICKER_TYPEWRITER_SAMPLES = ["SPY", "QQQ", "IWM", "NVDA", "MSFT"];

let btTickerTypewriterTimerIds = [];

function btTypewriterRandBetween(minMs, maxMs) {
  const lo = Math.min(minMs, maxMs);
  const hi = Math.max(minMs, maxMs);
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}

function btTypewriterMsPasteHold() {
  return btTypewriterRandBetween(1180, 1880);
}

function btTypewriterMsBeforeDelete() {
  return btTypewriterRandBetween(1560, 2460);
}

function btTypewriterMsPerDeleteChar() {
  return btTypewriterRandBetween(118, 198);
}

function btTypewriterMsBetweenSamples() {
  return btTypewriterRandBetween(720, 1180);
}

function btTickerTypewriterSlotEl() {
  const input = q("#btTicker");
  return input ? input.closest(".bt-ticker-input-slot") : null;
}

function btTickerTypewriterHudEls() {
  const hud = q("#btTickerTypewriterHud");
  const textEl = hud ? hud.querySelector(".bt-ticker-typewriter-text") : null;
  return { hud, textEl };
}

function clearBtTickerTypewriter() {
  btTickerTypewriterTimerIds.forEach(clearTimeout);
  btTickerTypewriterTimerIds = [];
  const input = q("#btTicker");
  const slot = btTickerTypewriterSlotEl();
  const { hud, textEl } = btTickerTypewriterHudEls();
  if (slot) slot.classList.remove("bt-ticker-input-slot--tw");
  if (hud) hud.hidden = true;
  if (textEl) textEl.textContent = "";
  if (input) {
    input.placeholder = "";
    input.removeAttribute("data-bt-typewriter");
  }
}

function scheduleBtTickerTw(fn, delayMs) {
  const timerId = window.setTimeout(() => {
    btTickerTypewriterTimerIds = btTickerTypewriterTimerIds.filter((t) => t !== timerId);
    fn();
  }, delayMs);
  btTickerTypewriterTimerIds.push(timerId);
}

function btTickerTypewriterShouldRun() {
  const input = q("#btTicker");
  if (!input) return false;
  if (state.page !== "backtest") return false;
  if (getBacktestHashRoute() === "history") return false;
  const detail = q("#btRunPanelDetail");
  if (!detail || detail.classList.contains("bt-run-panel-detail--collapsed")) return false;
  if (normalizeBtTicker(input.value) !== "") return false;
  if (document.activeElement === input) return false;
  if (motionDisabled()) return false;
  return true;
}

function runBtTickerTypewriterCycle(sampleIndex) {
  if (!btTickerTypewriterShouldRun()) {
    clearBtTickerTypewriter();
    return;
  }
  const input = q("#btTicker");
  const slot = btTickerTypewriterSlotEl();
  const { hud, textEl } = btTickerTypewriterHudEls();
  if (!input || !slot || !hud || !textEl) {
    clearBtTickerTypewriter();
    return;
  }
  const sample =
    BT_TICKER_TYPEWRITER_SAMPLES[sampleIndex % BT_TICKER_TYPEWRITER_SAMPLES.length];
  input.setAttribute("data-bt-typewriter", "1");
  slot.classList.add("bt-ticker-input-slot--tw");
  hud.hidden = false;
  textEl.textContent = sample;

  scheduleBtTickerTw(() => {
    if (!btTickerTypewriterShouldRun()) {
      clearBtTickerTypewriter();
      return;
    }
    let remaining = sample.length;
    const deleteOne = () => {
      if (!btTickerTypewriterShouldRun()) {
        clearBtTickerTypewriter();
        return;
      }
      if (remaining <= 0) {
        textEl.textContent = "";
        slot.classList.remove("bt-ticker-input-slot--tw");
        hud.hidden = true;
        input.removeAttribute("data-bt-typewriter");
        scheduleBtTickerTw(() => {
          if (btTickerTypewriterShouldRun()) runBtTickerTypewriterCycle(sampleIndex + 1);
        }, btTypewriterMsBetweenSamples());
        return;
      }
      remaining -= 1;
      textEl.textContent = sample.slice(0, remaining);
      scheduleBtTickerTw(deleteOne, btTypewriterMsPerDeleteChar());
    };
    scheduleBtTickerTw(deleteOne, btTypewriterMsBeforeDelete());
  }, btTypewriterMsPasteHold());
}

function maybeStartBtTickerTypewriter() {
  clearBtTickerTypewriter();
  if (!btTickerTypewriterShouldRun()) return;
  runBtTickerTypewriterCycle(0);
}

let homeWatchlistTypewriterTimerIds = [];

function scheduleHomeWatchlistTypewriterTw(fn, delayMs) {
  const timerId = window.setTimeout(() => {
    homeWatchlistTypewriterTimerIds = homeWatchlistTypewriterTimerIds.filter((t) => t !== timerId);
    fn();
  }, delayMs);
  homeWatchlistTypewriterTimerIds.push(timerId);
}

function clearHomeWatchlistTypewriter() {
  homeWatchlistTypewriterTimerIds.forEach(clearTimeout);
  homeWatchlistTypewriterTimerIds = [];
  const input = q("#homeWatchlistInput");
  const slot = input ? input.closest(".bt-ticker-input-slot") : null;
  const hud = q("#homeWatchlistTypewriterHud");
  const textEl = hud ? hud.querySelector(".bt-ticker-typewriter-text") : null;
  if (slot) slot.classList.remove("bt-ticker-input-slot--tw");
  if (hud) hud.hidden = true;
  if (textEl) textEl.textContent = "";
  if (input) input.removeAttribute("data-wl-typewriter");
}

function homeWatchlistTypewriterShouldRun() {
  const input = q("#homeWatchlistInput");
  if (!input) return false;
  if (state.page !== "live") return false;
  if (String(input.value).trim() !== "") return false;
  if (document.activeElement === input) return false;
  if (motionDisabled()) return false;
  return true;
}

function runHomeWatchlistTypewriterCycle(sampleIndex) {
  if (!homeWatchlistTypewriterShouldRun()) {
    clearHomeWatchlistTypewriter();
    return;
  }
  const input = q("#homeWatchlistInput");
  const slot = input ? input.closest(".bt-ticker-input-slot") : null;
  const hud = q("#homeWatchlistTypewriterHud");
  const textEl = hud ? hud.querySelector(".bt-ticker-typewriter-text") : null;
  if (!input || !slot || !hud || !textEl) {
    clearHomeWatchlistTypewriter();
    return;
  }
  const sample =
    BT_TICKER_TYPEWRITER_SAMPLES[sampleIndex % BT_TICKER_TYPEWRITER_SAMPLES.length];
  input.setAttribute("data-wl-typewriter", "1");
  slot.classList.add("bt-ticker-input-slot--tw");
  hud.hidden = false;
  textEl.textContent = sample;

  scheduleHomeWatchlistTypewriterTw(() => {
    if (!homeWatchlistTypewriterShouldRun()) {
      clearHomeWatchlistTypewriter();
      return;
    }
    let remaining = sample.length;
    const deleteOne = () => {
      if (!homeWatchlistTypewriterShouldRun()) {
        clearHomeWatchlistTypewriter();
        return;
      }
      if (remaining <= 0) {
        textEl.textContent = "";
        slot.classList.remove("bt-ticker-input-slot--tw");
        hud.hidden = true;
        input.removeAttribute("data-wl-typewriter");
        scheduleHomeWatchlistTypewriterTw(() => {
          if (homeWatchlistTypewriterShouldRun()) {
            runHomeWatchlistTypewriterCycle(sampleIndex + 1);
          }
        }, btTypewriterMsBetweenSamples());
        return;
      }
      remaining -= 1;
      textEl.textContent = sample.slice(0, remaining);
      scheduleHomeWatchlistTypewriterTw(deleteOne, btTypewriterMsPerDeleteChar());
    };
    scheduleHomeWatchlistTypewriterTw(deleteOne, btTypewriterMsBeforeDelete());
  }, btTypewriterMsPasteHold());
}

function maybeStartHomeWatchlistTypewriter() {
  clearHomeWatchlistTypewriter();
  if (!homeWatchlistTypewriterShouldRun()) return;
  runHomeWatchlistTypewriterCycle(0);
}

function wireBtSettingsPersistence() {
  ["#btTradingMode", "#btPeriod", "#btDayStrategy", "#btQuantStrategy"].forEach((sel) => {
    const el = q(sel);
    if (!el) return;
    el.addEventListener("change", schedulePersistBtSettingsForTicker);
    if (sel === "#btQuantStrategy") {
      el.addEventListener("change", updateQuantStrategyWfoFootnote);
    }
  });
  const intervalEl = q("#btInterval");
  if (intervalEl) {
    intervalEl.addEventListener("change", () => {
      setBtPeriodOptions();
      schedulePersistBtSettingsForTicker();
    });
  }
  const tickerEl = q("#btTicker");
  if (tickerEl) {
    tickerEl.addEventListener("input", schedulePersistBtSettingsForTicker);
    tickerEl.addEventListener("input", scheduleRefreshBtQuickCardSummary);
    tickerEl.addEventListener("input", () => {
      clearBtTickerTypewriter();
      if (normalizeBtTicker(tickerEl.value) === "") {
        scheduleBtTickerTw(() => maybeStartBtTickerTypewriter(), 160);
      }
    });
    tickerEl.addEventListener("focus", () => {
      clearBtTickerTypewriter();
    });
    tickerEl.addEventListener("blur", () => {
      scheduleBtTickerTw(() => maybeStartBtTickerTypewriter(), 180);
    });
  }
}

function readBtHistoryEntries() {
  try {
    const raw = localStorage.getItem(BT_HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeBtHistoryEntries(entries) {
  try {
    localStorage.setItem(BT_HISTORY_KEY, JSON.stringify(entries));
  } catch {
    /* ignore */
  }
}

function formatBtHistoryTime(ms) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(ms));
  } catch {
    return String(ms);
  }
}

function btPeriodHuman(val) {
  return BT_PERIOD_VALUE_LABEL[val] || val || "—";
}

function btIntervalHuman(val) {
  return BT_INTERVAL_VALUE_LABEL[val] || val || "—";
}

function btTradingModeHuman(mode) {
  return mode === "day_trading" ? "Intraday" : "Longer horizon";
}

function btQuantStrategyHuman(id) {
  const m = {
    hybrid: "Hybrid",
    mean_reversion: "Mean reversion",
    trend_following: "Trend following",
  };
  return m[id] || id || "—";
}

function btDayStrategyHuman(id) {
  if (!id) return "—";
  const list = dayStrategiesList && dayStrategiesList.length ? dayStrategiesList : [];
  const hit = list.find((s) => s.id === id);
  if (hit && hit.label) return hit.label;
  return String(id);
}

function syncMainTopHeader() {
  const eyebrow = q("#topEyebrow");
  const title = q("#topTitle");
  const topBar = q(".top");
  const pill = q("#topMarketPill");
  const refreshBtn = q("#refreshStatus");
  if (!eyebrow || !title) return;

  if (state.page === "backtest") {
    const route = getBacktestHashRoute();
    eyebrow.textContent = "Second Bar · Backtest";
    if (route === "result") {
      title.textContent = "Last run results";
    } else if (route === "history") {
      title.textContent = "Run history";
    } else {
      title.textContent = "Configure and run";
    }
    document.title = "Second Bar · Backtest";
  } else if (state.page === "health") {
    eyebrow.textContent = "Second Bar · Status";
    title.textContent = "Is everything working?";
    document.title = "Second Bar · Status";
  } else if (state.page === "journal") {
    eyebrow.textContent = "Second Bar · Journal";
    title.textContent = "Paper journal · Today";
    document.title = "Second Bar · Journal";
  } else if (state.page === "settings") {
    eyebrow.textContent = "Second Bar · Settings";
    title.textContent = "Preferences";
    document.title = "Second Bar · Settings";
  } else if (state.page === "symbol") {
    const tickerDisplay = normalizeHomeTickerToken(state.stockTicker || "") || "—";
    eyebrow.textContent = "Second Bar · Chart";
    title.textContent = tickerDisplay;
    document.title = `Second Bar · ${tickerDisplay}`;
  } else {
    eyebrow.textContent = "Second Bar · Home";
    title.textContent = "Session clock and live scanner";
    document.title = "Second Bar · Home";
  }

  if (topBar) {
    topBar.classList.toggle("top--health-spread", state.page === "health");
  }
  if (pill) {
    pill.hidden = state.page === "health" || state.page === "settings";
  }
  if (refreshBtn) {
    refreshBtn.hidden = state.page !== "health";
  }
}

function refreshBtQuickCardSummary() {
  const tickerKey = normalizeBtTicker(q("#btTicker")?.value ?? "");
  const summaryEl = q("#btSettingsSummary");
  const hintEl = q("#btCollapsedHint");
  if (!summaryEl || !hintEl) return;
  const map = readBtSettingsByTickerMap();
  const saved = tickerKey && map[tickerKey] ? map[tickerKey] : null;
  if (!tickerKey || !saved) {
    summaryEl.hidden = true;
    summaryEl.textContent = "";
    hintEl.textContent = "Pick a symbol, then open settings to choose horizon and mode.";
    refreshBtRunMinimalBar();
    return;
  }
  const modeH = btTradingModeHuman(saved.trading_mode);
  const periodH = btPeriodHuman(saved.period);
  const intervalH = btIntervalHuman(saved.interval);
  summaryEl.hidden = false;
  summaryEl.textContent = `Using: ${periodH} · ${intervalH} · ${modeH}`;
  hintEl.textContent = "Adjust horizon, bars, or mode in the panel.";
  refreshBtRunMinimalBar();
}

function refreshBtRunMinimalBar() {
  const tickEl = q("#btRunMinimalTicker");
  const metaEl = q("#btRunMinimalMeta");
  if (!tickEl || !metaEl) return;
  const t = normalizeBtTicker(q("#btTicker")?.value ?? "");
  tickEl.textContent = t || "—";
  const map = readBtSettingsByTickerMap();
  const saved = t && map[t] ? map[t] : null;
  if (!t || !saved) {
    metaEl.textContent = "Open settings to set horizon and mode for this symbol.";
    const minimal = q("#btRunPanelMinimal");
    if (minimal) {
      minimal.setAttribute("aria-label", "Backtest quick controls");
    }
    return;
  }
  const modeH = btTradingModeHuman(saved.trading_mode);
  const periodH = btPeriodHuman(saved.period);
  const intervalH = btIntervalHuman(saved.interval);
  metaEl.textContent = `${periodH} · ${intervalH} · ${modeH}`;
  const minimal = q("#btRunPanelMinimal");
  if (minimal) {
    minimal.setAttribute(
      "aria-label",
      `Current run: ${t}, ${periodH}, ${intervalH}, ${modeH}`,
    );
  }
}

function syncBtWorkAreaState() {
  const wrap = q("#btWorkArea");
  if (!wrap) return;
  if (state.page !== "backtest") {
    wrap.classList.remove("bt-work-area--fresh");
    return;
  }
  const route = getBacktestHashRoute();
  const entries = readBtHistoryEntries();
  const isFresh = entries.length === 0 && route === "setup";
  wrap.classList.toggle("bt-work-area--fresh", isFresh);
}

function buildBtHistoryRowMarkup(e) {
  const modeH = btTradingModeHuman(e.trading_mode);
  const stratLabel =
    e.trading_mode === "day_trading" && e.day_strategy_id
      ? escapeHtml(btDayStrategyHuman(e.day_strategy_id))
      : e.trading_mode === "quant" && e.quant_strategy_id
        ? escapeHtml(btQuantStrategyHuman(e.quant_strategy_id))
        : "—";
  const retLabel =
    typeof e.total_return === "number" && !Number.isNaN(e.total_return)
      ? escapeHtml(formatPct(e.total_return))
      : "—";
  const intervalH = escapeHtml(btIntervalHuman(String(e.interval || "")));
  return `
        <button type="button" class="bt-history-row" role="listitem" data-history-id="${escapeHtml(e.id)}" aria-expanded="false">
          <span class="bt-history-main"><b>${escapeHtml(e.ticker)}</b> · ${escapeHtml(modeH)} · ${intervalH}</span>
          <span class="bt-history-meta">${stratLabel} · ${escapeHtml(btPeriodHuman(String(e.period || "")))} · ${escapeHtml(formatBtHistoryTime(e.ranAt))}</span>
          <span class="bt-history-metric">Return <b>${retLabel}</b></span>
        </button>`;
}

function syncBtHistoryRowExpandedState() {
  if (state.page !== "backtest") return;
  const route = getBacktestHashRoute();
  const expandedId = route === "result" ? btExpandedHistoryId : null;
  qa("#btHistoryList .bt-history-row, #btHistoryListFull .bt-history-row").forEach((btn) => {
    const hid = btn.getAttribute("data-history-id");
    const isOpen = Boolean(expandedId && hid === expandedId);
    btn.classList.toggle("bt-history-row--expanded", isOpen);
    btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
  });
}

function wireBtHistoryListInteractivity(root) {
  if (!root) return;
  qa("[data-history-id]", root).forEach((btn) => {
    btn.addEventListener("click", () => {
      const hid = btn.getAttribute("data-history-id");
      const found = readBtHistoryEntries().find((x) => x.id === hid);
      if (!found || !found.snapshot) return;
      if (getBacktestHashRoute() === "result" && btExpandedHistoryId === hid) {
        navigateBacktestSetupHash();
        syncBacktestRouteUi();
        return;
      }
      btExpandedHistoryId = hid;
      btLastSuccessfulPayload = found.snapshot;
      if (readBtHistoryEntries().length > 0) {
        btRunPanelDetailOpen = false;
      }
      location.hash = "#backtest/result";
      const histSym = normalizeBtTicker(found.snapshot?.ticker || found.snapshot?.result?.symbol || "");
      if (histSym) {
        const ti = q("#btTicker");
        if (ti) ti.value = histSym;
      }
      void renderBacktestResults(found.snapshot);
      void refreshWfoValidationPanel();
      syncBacktestRouteUi();
    });
  });
  const seeMore = root.querySelector(".js-bt-history-see-more");
  if (seeMore) {
    seeMore.addEventListener("click", () => {
      navigateBacktestHistoryHash();
    });
  }
}

function renderBtHistoryInto(root, entries, options) {
  const previewCap = options && options.previewCap != null ? options.previewCap : null;
  const showSeeMoreCard = !!(options && options.showSeeMoreCard);
  if (!root) return;
  if (!entries.length) {
    root.innerHTML = `<p class="backtest-muted bt-history-empty">No runs yet. They'll show here after your first backtest in this browser.</p>`;
    syncBtWorkAreaState();
    return;
  }
  const capped =
    previewCap != null && entries.length > previewCap ? entries.slice(0, previewCap) : entries;
  const seeMore =
    showSeeMoreCard && previewCap != null && entries.length > previewCap
      ? `<button type="button" class="bt-history-see-more js-bt-history-see-more">See all runs (${entries.length})</button>`
      : "";
  const rowsHtml = capped.map((e) => buildBtHistoryRowMarkup(e)).join("");
  root.innerHTML =
    `<div class="bt-history-list-items" role="list">${rowsHtml}</div>` + seeMore;
  wireBtHistoryListInteractivity(root);
  syncBtWorkAreaState();
}

function renderBtHistoryList() {
  const entries = readBtHistoryEntries();
  renderBtHistoryInto(q("#btHistoryList"), entries, {
    previewCap: BT_HISTORY_TAB_PREVIEW_CAP,
    showSeeMoreCard: true,
  });
  renderBtHistoryInto(q("#btHistoryListFull"), entries, {
    previewCap: null,
    showSeeMoreCard: false,
  });
  updateBtRunPanelLayout();
  syncBtHistoryRowExpandedState();
}

let btRunPanelBiviewResizeObserver = null;

function syncBtRunPanelBiviewHeight() {
  const panel = q("#btRunPanel");
  const wrap = q(".bt-run-panel-biview");
  const detail = q("#btRunPanelDetail");
  const minimal = q("#btRunPanelMinimal");
  if (!panel || !wrap || !detail || !minimal) return;
  if (state.page !== "backtest") return;
  if (getBacktestHashRoute() === "history") return;
  if (panel.hidden) {
    wrap.style.minHeight = "";
    return;
  }
  const minimalOnly = panel.classList.contains("bt-run-panel--minimal-only");
  wrap.style.minHeight = "";
  void wrap.offsetHeight;
  const hDetail = Math.max(detail.scrollHeight, 48);
  const hMin = Math.max(minimal.scrollHeight, 48);
  wrap.style.minHeight = `${minimalOnly ? hMin : hDetail}px`;
}

function initBtRunPanelBiviewResizeObserver() {
  const wrap = q(".bt-run-panel-biview");
  const detail = q("#btRunPanelDetail");
  const minimal = q("#btRunPanelMinimal");
  if (!wrap || !detail || !minimal || typeof ResizeObserver === "undefined") return;
  if (btRunPanelBiviewResizeObserver != null) return;
  const observer = new ResizeObserver(() => {
    if (state.page !== "backtest") return;
    syncBtRunPanelBiviewHeight();
  });
  observer.observe(detail);
  observer.observe(minimal);
  btRunPanelBiviewResizeObserver = observer;
}

function updateBtRunPanelLayout() {
  const panel = q("#btRunPanel");
  const detail = q("#btRunPanelDetail");
  const minimal = q("#btRunPanelMinimal");
  if (!panel) return;
  if (state.page !== "backtest") return;
  if (getBacktestHashRoute() === "history") return;

  const hasHistory = readBtHistoryEntries().length > 0;
  const heading = q("#btHistoryHeading");
  if (heading) heading.textContent = hasHistory ? "Your runs" : "Recent runs";

  const showDetail = !hasHistory || btRunPanelDetailOpen;
  panel.classList.toggle("bt-run-panel--has-history", hasHistory);
  panel.classList.toggle("bt-run-panel--detail-visible", showDetail);
  panel.classList.toggle("bt-run-panel--minimal-only", hasHistory && !showDetail);

  if (detail) {
    detail.hidden = false;
    const collapsed = Boolean(hasHistory && !showDetail);
    detail.classList.toggle("bt-run-panel-detail--collapsed", collapsed);
    detail.toggleAttribute("inert", collapsed);
  }
  if (minimal) {
    minimal.hidden = false;
    const showMinimal = Boolean(hasHistory && !showDetail);
    minimal.classList.toggle("bt-run-panel-minimal--visible", showMinimal);
    minimal.toggleAttribute("inert", !showMinimal);
  }

  refreshBtRunMinimalBar();
  maybeStartBtTickerTypewriter();
  requestAnimationFrame(() => {
    syncBtRunPanelBiviewHeight();
    requestAnimationFrame(() => syncBtRunPanelBiviewHeight());
  });
}

function pushBtHistoryEntry(entry) {
  const next = [entry, ...readBtHistoryEntries()].slice(0, BT_HISTORY_CAP);
  writeBtHistoryEntries(next);
  renderBtHistoryList();
}

function getBacktestHashRoute() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  if (h === "backtest/history" || h.startsWith("backtest/history")) return "history";
  if (h === "backtest/result" || h.startsWith("backtest/result")) return "result";
  return "setup";
}

function syncBacktestRouteUi() {
  const resultsView = q("#btResultsView");
  const mainColumn = q("#btMainColumn");
  const tabBody = q("#btTabBody");
  const fullPage = q("#btHistoryFullPage");
  const runPanel = q("#btRunPanel");
  if (!resultsView || !mainColumn) return;
  if (state.page !== "backtest") return;
  const route = getBacktestHashRoute();

  if (route === "history") {
    btExpandedHistoryId = null;
    if (tabBody) tabBody.hidden = true;
    if (fullPage) fullPage.hidden = false;
    if (runPanel) runPanel.hidden = true;
    renderBtHistoryList();
    return;
  }

  if (tabBody) tabBody.hidden = false;
  if (fullPage) fullPage.hidden = true;
  if (runPanel) runPanel.hidden = false;

  const showResults = route === "result";
  resultsView.hidden = !showResults;

  if (route === "setup") {
    btExpandedHistoryId = null;
    const resEl = q("#backtestResults");
    if (resEl) resEl.innerHTML = "";
    btLastSuccessfulPayload = null;
  }

  renderBtHistoryList();
}

function navigateBacktestSetupHash() {
  location.hash = "#backtest";
}

function navigateBacktestResultHash() {
  location.hash = "#backtest/result";
}

function navigateBacktestHistoryHash() {
  location.hash = "#backtest/history";
}

function applyBacktestSymbolPrefillFromBridge() {
  const bridge = globalThis.TbSymbolResearchBridge;
  if (!bridge?.prefillBacktestTickerFromHash) return;
  const sym = bridge.prefillBacktestTickerFromHash();
  if (!sym) return;
  loadBtSettingsForTickerKey(sym);
  clearBtTickerTypewriter();
  refreshBtQuickCardSummary();
  btRunPanelDetailOpen = true;
  updateBtRunPanelLayout();
}

function normalizeAppHash() {
  return (location.hash || "").replace(/^#\/?/, "");
}

function initHashRouting() {
  window.addEventListener("hashchange", () => {
    landingPageFromHash();
  });
}

function landingPageFromHash() {
  const h = normalizeAppHash();
  const stockRouteMatch = /^stock\/([\w.\-]{1,20})$/i.exec(h);
  if (stockRouteMatch) {
    const parsedTicker = normalizeHomeTickerToken(stockRouteMatch[1]);
    const watchlistMembership = readHomeWatchlistTickers();
    if (!parsedTicker || !watchlistMembership.includes(parsedTicker)) {
      if (location.hash !== "#home") setAppHash("#home");
      return;
    }
    setPage("symbol", { fromHash: true, tickerSym: parsedTicker });
    return;
  }
  if (h === "journal") {
    setPage("journal", { fromHash: true });
    return;
  }
  if (h === "backtest" || h.startsWith("backtest")) {
    setPage("backtest", { fromHash: true });
    return;
  }
  if (h === "settings") {
    setPage("settings", { fromHash: true });
    return;
  }
  if (h === "health") {
    setPage("health", { fromHash: true });
    return;
  }
  if (h === "home" || h === "live") {
    setPage("live", { fromHash: true });
    return;
  }
  if (h === "status" || h === "alerts" || h.startsWith("alerts")) {
    setPage("live", { fromHash: true });
    return;
  }
  if (!h) {
    setPage("live", { fromHash: true });
    return;
  }
  setPage("live", { fromHash: true });
}

function setAppHash(hash) {
  const next = String(hash || "").startsWith("#") ? String(hash) : `#${hash}`;
  if (location.hash === next) return;
  history.replaceState(null, "", `${location.pathname}${location.search}${next}`);
}

function resetAppScrollPosition() {
  try {
    window.scrollTo(0, 0);
  } catch {
    /* ignore */
  }
}

function syncHashToPage(page, fromHash) {
  if (fromHash) return;
  if (page === "backtest") {
    if (!String(location.hash).includes("?") && location.hash !== "#backtest" && !String(location.hash).startsWith("#backtest/")) {
      setAppHash("#backtest");
    }
    return;
  }
  if (page === "journal") {
    if (location.hash !== "#journal") setAppHash("#journal");
    return;
  }
  if (page === "settings") {
    if (location.hash !== "#settings") setAppHash("#settings");
    return;
  }
  if (page === "symbol") {
    const tk = normalizeHomeTickerToken(state.stockTicker || "");
    if (tk && location.hash !== `#stock/${tk}`) setAppHash(`#stock/${tk}`);
    return;
  }
  const wantHash = page === "health" ? "#health" : "#home";
  if (location.hash !== wantHash) setAppHash(wantHash);
}

let dayStrategiesList = [];

async function loadQuantStrategies() {
  try {
    const res = await fetch("/api/quant/backtest/quant-strategies");
    if (!res.ok) return;
    const body = await res.json();
    if (!Array.isArray(body.strategies) || !body.strategies.length) return;
    quantStrategiesMeta = body.strategies;
    const sel = q("#btQuantStrategy");
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = body.strategies
      .map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.label)}</option>`)
      .join("");
    const allowed = new Set(body.strategies.map((s) => String(s.id)));
    sel.value = allowed.has(prev) ? prev : "hybrid";
    updateQuantStrategyWfoFootnote();
  } catch {
    /* keep built-in options */
  }
}

function setBtIntervalOptions(mode) {
  const sel = q("#btInterval");
  if (!sel) return;
  const pairs = mode === "day_trading" ? BT_INTERVALS_DAY : BT_INTERVALS_QUANT;
  const prev = sel.value;
  sel.innerHTML = pairs.map(([val, lab]) => `<option value="${val}">${lab}</option>`).join("");
  const allowed = new Set(pairs.map((p) => p[0]));
  sel.value = allowed.has(prev) ? prev : pairs[0][0];
}

function setBtPeriodOptions() {
  const periodSel = q("#btPeriod");
  const modeEl = q("#btTradingMode");
  const intervalEl = q("#btInterval");
  if (!periodSel || !modeEl || !intervalEl) return;
  const mode = modeEl.value;
  const interval = intervalEl.value;
  let pairs;
  if (interval === "1h") {
    pairs = BT_PERIODS_1H_CAPPED;
  } else if (mode === "day_trading") {
    pairs = BT_PERIODS_DAY_15_30;
  } else {
    pairs = BT_PERIODS_QUANT;
  }
  const prev = periodSel.value;
  periodSel.innerHTML = pairs.map(([val, lab]) => `<option value="${val}">${escapeHtml(lab)}</option>`).join("");
  const allowed = new Set(pairs.map((p) => p[0]));
  const fallback =
    allowed.has("1y") ? "1y" : pairs[0][0];
  periodSel.value = allowed.has(prev) ? prev : fallback;
}

async function loadDayStrategies() {
  try {
    const res = await fetch("/api/quant/backtest/day-strategies");
    if (!res.ok) return;
    const body = await res.json();
    if (Array.isArray(body.strategies)) dayStrategiesList = body.strategies;
  } catch {
    dayStrategiesList = [];
  }
}

function renderBtDayStrategyOptions() {
  const sel = q("#btDayStrategy");
  if (!sel) return;
  const fallback = [
    { id: "opening_range_breakout", label: "Opening range breakout" },
    { id: "vwap_mean_reversion", label: "VWAP mean reversion (long fade)" },
    { id: "momentum_pullback", label: "Momentum pullback (9 EMA reclaim)" },
    { id: "range_breakout", label: "Range breakout (Donchian-style)" },
  ];
  const list = dayStrategiesList.length ? dayStrategiesList : fallback;
  sel.innerHTML = list.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("");
}

function syncBacktestModeUi() {
  const modeEl = q("#btTradingMode");
  const qInner = q("#btQuantStrategyInner");
  const dInner = q("#btDayStrategyInner");
  if (!modeEl || !qInner || !dInner) return;
  const mode = modeEl.value;
  setBtIntervalOptions(mode);
  setBtPeriodOptions();
  dInner.hidden = mode !== "day_trading";
  qInner.hidden = mode !== "quant";
  updateQuantStrategyWfoFootnote();
}

function updateQuantStrategyWfoFootnote() {
  const note = q("#btQuantWfoNote");
  const sel = q("#btQuantStrategy");
  const mode = q("#btTradingMode")?.value;
  if (!note || !sel) return;
  if (mode !== "quant") {
    note.hidden = true;
    note.textContent = "";
    return;
  }
  const id = sel.value;
  const row = quantStrategiesMeta.find((s) => s.id === id);
  const unsupported = Boolean(row && row.wfo_supported === false);
  if (unsupported) {
    note.hidden = false;
    note.textContent =
      "Walk-forward parameter search is available for mean reversion and trend following in advanced workflows. This preset is limited to single-run backtests in this view.";
    return;
  }
  note.hidden = true;
  note.textContent = "";
}

let researchBridgeModulePromise = null;

function loadResearchBridgeModule() {
  if (!researchBridgeModulePromise) {
    researchBridgeModulePromise = import("./modules/research-bridge.js");
  }
  return researchBridgeModulePromise;
}

let backtestChartModulePromise = null;

function loadBacktestChartModule() {
  if (!backtestChartModulePromise) {
    backtestChartModulePromise = import("./modules/backtest-chart.js");
  }
  return backtestChartModulePromise;
}

async function refreshWfoValidationPanel() {
  const bridge = await loadResearchBridgeModule();
  bridge.ensureWfoPanelMounted();
  try {
    const res = await fetch("/api/quant/wfo/latest");
    if (!res.ok) throw new Error("latest");
    const body = await res.json();
    const list = Array.isArray(body.results) ? body.results : [];
    if (!list.length) {
      bridge.setWfoPanelEmpty(
        document,
        "No validation exports found yet. Add a walk-forward JSON export to your data folder, then reopen this tab.",
      );
      return;
    }
    const top = list[0];
    const slug = top && top.slug ? String(top.slug) : "";
    if (!slug) {
      bridge.setWfoPanelEmpty(document, "Latest validation entry is missing an id.");
      return;
    }
    const detailRes = await fetch(`/api/quant/wfo/results/${encodeURIComponent(slug)}`);
    if (!detailRes.ok) {
      bridge.setWfoPanelEmpty(document, "Could not load the latest saved validation run.");
      return;
    }
    const wrapped = await detailRes.json().catch(() => ({}));
    const btSymbol = normalizeBtTicker(q("#btTicker")?.value ?? "");
    bridge.populateWfoPanel(document, wrapped, {
      slug,
      ts: top.ts,
      symbol: btSymbol || undefined,
      scanPayload: backendHealth.scan,
    });
  } catch {
    bridge.setWfoPanelEmpty(document, "Validation summary is unavailable. Start the server and try again.");
  }
}

/**
 * ---------------------------------------------------------------------------
 * US equity regular session (Home market clock)
 * ---------------------------------------------------------------------------
 * Primary source: GET /api/market/session (NYSE calendar via backend).
 * Fallback: Mon–Fri 09:30–16:00 America/New_York with banner
 * "Using simplified hours" when the API is unavailable.
 * ---------------------------------------------------------------------------
 */
const MOCK_DAY_TRADE_LIQUID_UNIVERSE = [
  "SPY",
  "QQQ",
  "IWM",
  "AAPL",
  "MSFT",
  "NVDA",
  "META",
  "TSLA",
  "AMZN",
  "GOOGL",
];

const MOCK_DAY_STRATEGY_IDS = [
  "opening_range_breakout",
  "vwap_mean_reversion",
  "momentum_pullback",
  "range_breakout",
];

const MOCK_DAY_STRATEGY_LABEL_BY_ID = {
  opening_range_breakout: "Opening range breakout (first 2 session bars)",
  vwap_mean_reversion: "VWAP mean reversion (long fade)",
  momentum_pullback: "Momentum pullback (9 EMA reclaim)",
  range_breakout: "Range breakout (Donchian-style)",
};

const HOME_WATCHLIST_STORAGE_KEY = "tb_home_watchlist_v2";

/** NYSE/Nasdaq-listed equities use ticker symbols of at most 5 characters (longest common symbols such as GOOGL). */
const HOME_WATCHLIST_MAX_TICKER_CHARS = 5;
const HOME_WATCHLIST_MAX_SYMBOL_SLOTS = 12;

/** Worst-case raw length if every slot holds a max-length ticker plus commas (much tighter than open-ended caps). */
const HOME_WATCHLIST_INPUT_MAX_CHARS =
  HOME_WATCHLIST_MAX_SYMBOL_SLOTS * HOME_WATCHLIST_MAX_TICKER_CHARS +
  Math.max(0, HOME_WATCHLIST_MAX_SYMBOL_SLOTS - 1);

let homeScanRunInFlight = false;
/** Cleared whenever a new watchlist bar pulse starts so rapid Save clicks do not drop the class mid-animation. */
let watchlistCardRedFlashClearTimerId = null;

function hideHomeScanProgressStrip() {
  const strip = q("#homeScanProgress");
  if (!strip) return;
  strip.hidden = true;
}

function updateHomeScanProgressStrip(progressData) {
  const strip = q("#homeScanProgress");
  const metaEl = q("#homeScanProgressMeta");
  const batchEl = q("#homeScanProgressBatch");
  const fillEl = q("#homeScanProgressBarFill");
  const trackEl = strip?.querySelector?.(".scan-progress-bar-track");
  if (!strip || !metaEl || !fillEl || !trackEl) return;
  const total =
    typeof progressData.total_count === "number" && Number.isFinite(progressData.total_count)
      ? progressData.total_count
      : 0;
  const scanned =
    typeof progressData.scanned_count === "number" && Number.isFinite(progressData.scanned_count)
      ? progressData.scanned_count
      : 0;
  const elapsedRaw =
    typeof progressData.elapsed_seconds === "number" && Number.isFinite(progressData.elapsed_seconds)
      ? progressData.elapsed_seconds
      : 0;
  const alertsSoFar =
    typeof progressData.signals_so_far === "number" && Number.isFinite(progressData.signals_so_far)
      ? progressData.signals_so_far
      : 0;
  const pct = total > 0 ? Math.min(100, Math.round((scanned / total) * 100)) : 0;
  fillEl.style.width = `${pct}%`;
  trackEl.setAttribute("aria-valuenow", String(pct));
  trackEl.setAttribute("aria-valuemax", "100");
  const elapsedLabel =
    elapsedRaw < 60
      ? `${Math.round(elapsedRaw)}s`
      : `${Math.floor(elapsedRaw / 60)}m ${String(Math.round(elapsedRaw % 60)).padStart(2, "0")}s`;
  metaEl.textContent = `${scanned} / ${total} names · ${elapsedLabel} · ${alertsSoFar} setups surfaced so far`;
  const batch = Array.isArray(progressData.current_batch) ? progressData.current_batch : [];
  const showBatch = batch
    .map((t) => normalizeHomeTickerToken(t))
    .filter(Boolean)
    .slice(0, 12);
  if (batchEl) {
    batchEl.textContent =
      showBatch.length > 0 ? `This batch: ${showBatch.join(", ")}${batch.length > 12 ? "…" : ""}` : "";
  }
  strip.hidden = false;
}

function formatSuggestedTradePrice(x) {
  if (typeof x !== "number" || !Number.isFinite(x)) return null;
  return x.toFixed(2);
}

function normalizeHomeTickerToken(raw) {
  return String(raw ?? "")
    .trim()
    .toUpperCase()
    .replace(/^[^A-Z0-9.-]+|[^A-Z0-9.-]+$/g, "");
}

function dedupeTickerListPreserveOrder(tokens) {
  const seen = new Set();
  const out = [];
  for (const raw of tokens) {
    const t = normalizeHomeTickerToken(raw);
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}

function splitWatchlistRaw(rawString) {
  const s = String(rawString ?? "").trim();
  if (!s) return [];
  return [...new Set(s.split(/[,;\s\uFF0C\u3001]+/u).map(normalizeHomeTickerToken).filter(Boolean))];
}

function parseWatchlistTokens(raw) {
  if (raw == null) return [];
  const s = String(raw).trim();
  if (!s) return [];
  if (s.startsWith("[")) {
    try {
      const parsed = JSON.parse(s);
      if (Array.isArray(parsed)) {
        return [...new Set(parsed.map(normalizeHomeTickerToken).filter(Boolean))];
      }
    } catch {
      /* fall through: pasted text like "[AMD, NVDA]" */
    }
  }
  return splitWatchlistRaw(s);
}

/** Symbols persisted for the Home scanner (browser storage). */
function readStoredUserWatchlistTickers() {
  try {
    let raw = localStorage.getItem(HOME_WATCHLIST_STORAGE_KEY);
    if (raw == null || !String(raw).trim()) {
      raw = sessionStorage.getItem(HOME_WATCHLIST_STORAGE_KEY);
    }
    if (raw == null || !String(raw).trim()) {
      return [];
    }
    const s = String(raw).trim();
    if (s.startsWith("[")) {
      try {
        const parsedJson = JSON.parse(s);
        if (Array.isArray(parsedJson)) {
          return [...new Set(parsedJson.map(normalizeHomeTickerToken).filter(Boolean))];
        }
      } catch {
        return splitWatchlistRaw(s);
      }
    }
    return splitWatchlistRaw(s);
  } catch {
    return [];
  }
}

/** Saved watchlist symbols in user order (deduped, first occurrence kept). */
function readHomeWatchlistTickers() {
  return dedupeTickerListPreserveOrder(readStoredUserWatchlistTickers());
}

function writeHomeWatchlistTickers(tickers) {
  const merged = dedupeTickerListPreserveOrder(tickers);
  const payload = JSON.stringify(merged);
  try {
    localStorage.setItem(HOME_WATCHLIST_STORAGE_KEY, payload);
  } catch {
    try {
      sessionStorage.setItem(HOME_WATCHLIST_STORAGE_KEY, payload);
    } catch {
      /* ignore */
    }
  }
  return readHomeWatchlistTickers();
}

/** Keeps CSS `tb-watchlist-card-red-flash` duration and JS cleanup in sync (duration × 0.8 at 20% quicker). */
const WATCHLIST_CARD_RED_FLASH_MS = Math.round(680 * 0.8);

function pulseWatchlistCardRedFlash() {
  if (motionDisabled()) return;
  const bar = q(".home-watchlist-bar");
  if (!bar) return;
  if (watchlistCardRedFlashClearTimerId != null) {
    window.clearTimeout(watchlistCardRedFlashClearTimerId);
    watchlistCardRedFlashClearTimerId = null;
  }
  bar.classList.remove("watchlist-card-red-flash");
  void bar.offsetWidth;
  bar.classList.add("watchlist-card-red-flash");
  watchlistCardRedFlashClearTimerId = window.setTimeout(() => {
    bar.classList.remove("watchlist-card-red-flash");
    watchlistCardRedFlashClearTimerId = null;
  }, motionDurationMs(WATCHLIST_CARD_RED_FLASH_MS) || WATCHLIST_CARD_RED_FLASH_MS);
}

function scannerTableRowAttrs(sym, slideInTickers, watchlistedSet) {
  const wlHas = watchlistedSet && watchlistedSet.has(sym);
  const rowClasses = ["scanner-table-row"];
  let uxAttrs = "";
  if (wlHas) {
    rowClasses.push("clickable-scanner-row");
    uxAttrs = ` tabindex="0" role="button"`;
  } else {
    rowClasses.push("scanner-row-muted");
  }
  let styleAttrSlide = "";
  if (slideInTickers && slideInTickers.length) {
    const idxFound = slideInTickers.indexOf(sym);
    if (idxFound >= 0) {
      rowClasses.push("scanner-row-enter");
      const delaySec = idxFound * (0.048 * 0.8);
      styleAttrSlide = ` style="animation-delay: ${delaySec}s;"`;
    }
  }
  const clsJoined = rowClasses.join(" ");
  const watchListedAttr = wlHas ? "true" : "false";
  return ` data-ticker="${escapeHtml(sym)}" data-watchlisted="${watchListedAttr}" class="${clsJoined}"${uxAttrs}${styleAttrSlide}`;
}

function buildHomeScannerUniverse() {
  const watchlistPart = readHomeWatchlistTickers();
  const liquidPart = MOCK_DAY_TRADE_LIQUID_UNIVERSE.map(normalizeHomeTickerToken);
  return dedupeTickerListPreserveOrder([...watchlistPart, ...liquidPart]);
}

function signalScoreTotal(sig) {
  const sd = sig && typeof sig.score_data === "object" && sig.score_data ? sig.score_data : {};
  const t = sd.total;
  return typeof t === "number" && Number.isFinite(t) ? t : null;
}

function indexScanSignalsByTicker(signalList) {
  const map = new Map();
  for (const s of signalList) {
    if (!s || typeof s !== "object") continue;
    const sym = normalizeHomeTickerToken(s.ticker);
    if (!sym) continue;
    const prev = map.get(sym);
    if (!prev) {
      map.set(sym, s);
      continue;
    }
    const score = signalScoreTotal(s);
    const prevScore = signalScoreTotal(prev);
    const a = score != null ? score : -1;
    const b = prevScore != null ? prevScore : -1;
    if (a > b) map.set(sym, s);
  }
  return map;
}

function formatScannerStrategyHtml(sig) {
  const raw = sig.strategy != null ? String(sig.strategy).trim() : "";
  if (!raw || raw === "None") return "—";
  let labelExtra = "";
  for (const id of MOCK_DAY_STRATEGY_IDS) {
    if (raw === id || raw.toLowerCase().includes(id)) {
      labelExtra = MOCK_DAY_STRATEGY_LABEL_BY_ID[id] || "";
      break;
    }
  }
  const code = `<code>${escapeHtml(raw)}</code>`;
  return labelExtra ? `${code} · ${escapeHtml(labelExtra)}` : code;
}

function flushScannerLiveDomFromPending() {
  scannerLiveDomTimerId = null;
  const dataPayload = scannerLiveDomPending;
  scannerLiveDomPending = null;
  if (!dataPayload || typeof dataPayload !== "object") return;
  hideHomeScanProgressStrip();
  backendHealth.loaded = true;
  backendHealth.scan = dataPayload;
  renderMarket();
  renderHomeDashboardPanels();
  renderHomeScanner();
}

function scheduleScannerLiveDomFlush() {
  if (scannerLiveDomTimerId != null) {
    window.clearTimeout(scannerLiveDomTimerId);
  }
  scannerLiveDomTimerId = window.setTimeout(
    flushScannerLiveDomFromPending,
    SCANNER_LIVE_DOM_COALESCE_MS,
  );
}

function mergeScannerLivePayload(dataPayload) {
  if (!dataPayload || typeof dataPayload !== "object") return;
  scannerLiveDomPending = dataPayload;
  scheduleScannerLiveDomFlush();
}

function dashboardDashListItems(rowsArr, formatter) {
  const safeRows = Array.isArray(rowsArr) ? rowsArr : [];
  if (!safeRows.length) {
    return `<li><span>${escapeHtml("—")}</span></li>`;
  }
  return safeRows.slice(0, 12).map(formatter).join("");
}

function renderHomeDashboardPanels() {
  const breadthEl = q("#homeBreadthMeter");
  const moversEl = q("#homeMoversList");
  const suggestedBody = q("#homeSuggestedTradesBody");
  if (!breadthEl) return;
  const sp = backendHealth.scan;

  if (!backendHealth.loaded || !sp || typeof sp !== "object") {
    const msg = backendHealth.loaded
      ? "Run a scan to populate breadth and movers."
      : "Breadth and movers appear after the server connects.";
    breadthEl.innerHTML = `<p class="scanner-feed-empty">${escapeHtml(msg)}</p>`;
    if (moversEl) moversEl.innerHTML = "";
    if (suggestedBody) {
      suggestedBody.innerHTML = `<p class="scanner-feed-empty">${escapeHtml(
        backendHealth.loaded
          ? "Run a scan to surface ideas worth a closer look."
          : "Suggested trades load after the server connects.",
      )}</p>`;
    }
    globalThis.TbScanContext?.renderPanels?.(null, { loaded: backendHealth.loaded });
    return;
  }

  const br = typeof sp.breadth === "object" && sp.breadth !== null ? sp.breadth : {};
  const totalNames = typeof br.total === "number" ? br.total : 0;
  const bullishPct = typeof br.bullish_pct === "number" ? Math.max(0, br.bullish_pct) : 0;
  const bearishPct = typeof br.bearish_pct === "number" ? Math.max(0, br.bearish_pct) : 0;
  const neutralRemain = Math.max(0, 100 - bullishPct - bearishPct);

  breadthEl.innerHTML = `
    <div>${escapeHtml(String(totalNames))} names cached · bullish ${escapeHtml(String(bullishPct.toFixed(1)))}%</div>
    <div class="breadth-bar-shell" aria-hidden="true">
      <span class="breadth-bar-bullish" style="width:${bullishPct}%"></span>
      <span class="breadth-bar-neutral" style="width:${neutralRemain}%"></span>
      <span class="breadth-bar-bearish" style="width:${bearishPct}%"></span>
    </div>
    <div class="backtest-muted breadth-foot">A/D ${escapeHtml(String(br.ad_ratio ?? "—"))} · avg score ${escapeHtml(String(br.avg_score ?? "—"))}</div>
  `;

  if (moversEl) {
    moversEl.innerHTML = dashboardDashListItems(sp.movers, (mRow) => {
      const sym = normalizeHomeTickerToken(mRow.ticker);
      const chip = sym ? tickerChipHtml(sym) : escapeHtml(String(mRow.ticker ?? ""));
      return `<li><span>${chip}</span>${moverBioHtml(mRow)}</li>`;
    });
  }

  if (suggestedBody) {
    const wlSetSuggest = new Set(readHomeWatchlistTickers());
    const fromSrv = Array.isArray(sp.suggested_watchlist) ? sp.suggested_watchlist : [];
    const bestArr = Array.isArray(sp.best) ? sp.best : [];
    const fallbackTrades = bestArr.filter((bRow) => {
      const sc = typeof bRow.score === "number" && Number.isFinite(bRow.score) ? bRow.score : 0;
      return sc >= 20;
    });
    const trades = fromSrv.length ? fromSrv.slice(0, 8) : fallbackTrades.slice(0, 8);
    if (!trades.length) {
      suggestedBody.innerHTML = `<p class="scanner-feed-empty">${escapeHtml(
        "No standout setups in this snapshot yet—check back after the next scan.",
      )}</p>`;
    } else {
      suggestedBody.innerHTML = trades
        .map((row) => {
          const sym = normalizeHomeTickerToken(row.ticker);
          if (!sym) return "";
          const onWl = wlSetSuggest.has(sym);
          const scoreV = typeof row.score === "number" && Number.isFinite(row.score) ? Math.round(row.score) : "—";
          const stratHtml = formatScannerStrategyHtml({ strategy: row.strategy });
          const dir = row.direction != null ? String(row.direction).trim() : "";
          const ch = formatPct(row.change_pct);
          const narrativeHtml = globalThis.TbNarrative?.suggestedCardInnerHtml?.(row, sp) ?? "";
          const narrativeWideHtml = globalThis.TbNarrative?.suggestedCardInnerHtmlWide?.(row, sp) ?? "";
          const subLine = dir ? `${ch} · ${dir}` : ch;
          const wideSubLine = suggestedTradeWideSubLine(row);
          const historyCta = globalThis.TbSymbolResearchBridge?.checkHistoryCtaHtml?.(sym, "check-history-cta--suggested") ?? "";
          const compactBio = narrativeHtml
            ? `<div class="dash-bio dash-bio--compact">${narrativeHtml}</div>`
            : `<p class="suggested-trade-notes muted dash-bio dash-bio--compact">${escapeHtml(subLine)}</p>`;
          const wideBio = narrativeWideHtml
            ? `<div class="dash-bio dash-bio--wide">${narrativeWideHtml}</div>`
            : `<p class="suggested-trade-notes muted dash-bio dash-bio--wide">${escapeHtml(wideSubLine)}</p>`;
          return `<article class="suggested-trade-card suggested-trade-card--has-clickable-ticker">
            <div class="suggested-trade-card-head">${tickerChipHtml(sym)}<span class="suggested-trade-score">${escapeHtml(String(scoreV))}/100</span></div>
            <div class="suggested-trade-strat">${stratHtml}</div>
            ${compactBio}
            ${wideBio}
            <div class="suggested-trade-actions">${historyCta}<button type="button" class="button secondary suggested-trade-add" data-suggested-add="${escapeHtml(sym)}" ${onWl ? "disabled" : ""}>${escapeHtml(onWl ? "On your list" : "Add to watchlist")}</button></div>
          </article>`;
        })
        .filter(Boolean)
        .join("");
      globalThis.TbSkillMode?.applySkillMode?.(suggestedBody);
      globalThis.TbSymbolResearchBridge?.wireCheckHistoryButtons?.(suggestedBody);
    }
  }

  globalThis.TbScanContext?.renderPanels?.(sp, { loaded: true });
}

function renderJournalAside() {
  const rootAside = q("#journalAside");
  if (!rootAside) return;

  const jst = backendHealth.journalStats;
  const curveWrap = backendHealth.journalEquityCurve || {};
  const curvePtsRaw = curveWrap.curve;
  const curvePts = Array.isArray(curvePtsRaw) ? curvePtsRaw : [];

  if (!jst || typeof jst !== "object") {
    rootAside.innerHTML =
      `<p class="scanner-feed-empty">${escapeHtml("Journal unavailable until backend responds.")}</p>`;
    return;
  }

  const winsCount = jst.wins != null ? jst.wins : 0;
  const lossesCount = jst.losses != null ? jst.losses : 0;
  const wrNum = jst.win_rate;
  const winRateShown =
    typeof wrNum === "number" && Number.isFinite(wrNum)
      ? `${wrNum.toFixed(1)}%`
      : escapeHtml(String(wrNum ?? "—"));
  const openCt = jst.open_trade_count != null ? String(jst.open_trade_count) : "—";

  const rs = curvePts.map((pt) =>
    typeof pt.cumulative_r === "number" && Number.isFinite(pt.cumulative_r) ? pt.cumulative_r : 0,
  );
  const mins = rs.length ? Math.min(...rs, 0) : 0;
  const maxs = rs.length ? Math.max(...rs, 1) : 1;
  const spanR = Math.max(1e-6, maxs - mins);

  const sparkFragment =
    rs.length > 1
      ? `<div class="journal-equity-sparkline" aria-label="Cumulative R sparkline">${rs
          .slice(-48)
          .map((cumRVal) => {
            const frac = (cumRVal - mins) / spanR;
            const heightPct = Math.max(8, Math.round(frac * 100));
            const barTone = cumRVal < 0 ? "journal-equity-bar-loss" : "journal-equity-bar";
            const titleR = typeof cumRVal === "number" ? cumRVal.toFixed(4) : String(cumRVal);
            return `<span class="${barTone}" style="height:${heightPct}px" title="${escapeHtml(titleR)}"></span>`;
          })
          .join("")}</div>`
      : `<p class="backtest-muted">No closed trades yet for an equity stair.</p>`;

  rootAside.innerHTML = `
    <div class="journal-metrics-row">
      <div class="journal-metric"><span>W/L</span><b>${escapeHtml(String(winsCount))}/${escapeHtml(String(lossesCount))}</b></div>
      <div class="journal-metric"><span>Win rate</span><b>${escapeHtml(winRateShown)}</b></div>
      <div class="journal-metric"><span>Open</span><b>${escapeHtml(openCt)}</b></div>
    </div>
    <p class="scanner-detail-line">Avg R <b>${escapeHtml(String(jst.avg_r ?? "—"))}</b> · PF ${escapeHtml(String(jst.profit_factor ?? "—"))} · Expectancy ${escapeHtml(String(jst.expectancy ?? "—"))}</p>
    ${sparkFragment}
  `;
}

function renderJournalOpenTrades() {
  const bodyEl = q("#journalOpenTradesBody");
  if (!bodyEl) return;

  const data = backendHealth.journalOpenTrades;
  const tradesMap =
    data && typeof data === "object" && data.open_trades && typeof data.open_trades === "object"
      ? data.open_trades
      : {};
  const entries = Object.entries(tradesMap);

  if (entries.length === 0) {
    bodyEl.innerHTML = `<p class="scanner-feed-empty">No open positions.</p>`;
    return;
  }

  const rows = entries
    .map(([tradeId, trade]) => {
      const ticker = escapeHtml(String(trade.ticker ?? "—"));
      const direction = escapeHtml(String(trade.direction ?? "—"));
      const strategy = escapeHtml(String(trade.strategy ?? "—"));
      const entry =
        trade.entry_price != null ? escapeHtml(Number(trade.entry_price).toFixed(2)) : "—";
      const stop =
        trade.stop_loss != null ? escapeHtml(Number(trade.stop_loss).toFixed(2)) : "—";
      const tp1Val = Number(trade.tp1 ?? 0);
      const tp = tp1Val > 0 ? escapeHtml(tp1Val.toFixed(2)) : "—";
      const rawTime = String(trade.entry_time ?? "");
      const openedAt = rawTime ? escapeHtml(rawTime.replace("T", " ").slice(0, 16)) : "—";
      const safeId = escapeHtml(tradeId);
      return `<tr>
        <td><b>${ticker}</b> <span style="color:var(--muted);font-size:0.78rem">${direction}</span></td>
        <td>${strategy}</td>
        <td>${entry}</td>
        <td>${stop}</td>
        <td>${tp}</td>
        <td>${openedAt}</td>
        <td><button type="button" class="small-button" data-trade-id="${safeId}" data-close-trade="1">Close</button></td>
      </tr>`;
    })
    .join("");

  bodyEl.innerHTML = `
    <div class="trades-scroll">
      <table class="trades-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Strategy</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th>Opened</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  bodyEl.querySelectorAll("[data-close-trade='1']").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tid = btn.dataset.tradeId;
      if (tid) void closeJournalTrade(tid);
    });
  });
}

async function refreshJournal() {
  const statsPrior = backendHealth.journalStats;
  const equityPrior = backendHealth.journalEquityCurve;
  const openPrior = backendHealth.journalOpenTrades;
  try {
    const [statsRes, equityRes, openRes] = await Promise.all([
      fetch("/api/journal/stats"),
      fetch("/api/journal/equity"),
      fetch("/api/journal/trades/open"),
    ]);
    if (statsRes.ok) backendHealth.journalStats = await statsRes.json();
    else backendHealth.journalStats = statsPrior;
    if (equityRes.ok) backendHealth.journalEquityCurve = await equityRes.json();
    else backendHealth.journalEquityCurve = equityPrior;
    if (openRes.ok) backendHealth.journalOpenTrades = await openRes.json();
    else backendHealth.journalOpenTrades = openPrior;
  } catch {
    backendHealth.journalStats = statsPrior;
    backendHealth.journalEquityCurve = equityPrior;
    backendHealth.journalOpenTrades = openPrior;
  }
  renderJournalAside();
  renderJournalOpenTrades();
}

async function closeJournalTrade(tradeId) {
  const exitRaw = window.prompt("Exit price:");
  if (exitRaw === null) return;
  const exitPrice = parseFloat(exitRaw);
  if (!Number.isFinite(exitPrice) || exitPrice <= 0) {
    window.alert("Enter a valid price greater than zero.");
    return;
  }
  try {
    const res = await fetch(`/api/journal/trades/${encodeURIComponent(tradeId)}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exit_price: exitPrice, exit_reason: "manual" }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      console.warn("Close trade failed:", err.detail ?? res.statusText);
      return;
    }
    await refreshJournal();
  } catch (e) {
    console.warn("Close trade error:", e);
  }
}

async function submitJournalOpenTrade(event) {
  event.preventDefault();
  const statusEl = q("#journalOpenTradeStatus");
  const btn = q("#journalOpenTradeBtn");
  const form = q("#journalOpenTradeForm");
  if (!form || !btn) return;

  const ticker = String(q("#jtTicker")?.value ?? "")
    .trim()
    .toUpperCase();
  const direction = String(q("#jtDirection")?.value ?? "BUY");
  const entryPrice = parseFloat(q("#jtEntry")?.value ?? "");
  const stopLoss = parseFloat(q("#jtStop")?.value ?? "");
  const tp1Raw = parseFloat(q("#jtTp1")?.value ?? "");
  const tp2Raw = parseFloat(q("#jtTp2")?.value ?? "");
  const strategy = String(q("#jtStrategy")?.value ?? "").trim() || "Manual";

  if (!ticker) {
    if (statusEl) statusEl.textContent = "Symbol is required.";
    return;
  }
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) {
    if (statusEl) statusEl.textContent = "Entry price must be a positive number.";
    return;
  }
  if (!Number.isFinite(stopLoss) || stopLoss <= 0) {
    if (statusEl) statusEl.textContent = "Stop loss must be a positive number.";
    return;
  }

  const tp1 = Number.isFinite(tp1Raw) && tp1Raw > 0 ? tp1Raw : 0;
  const tp2 = Number.isFinite(tp2Raw) && tp2Raw > 0 ? tp2Raw : 0;

  btn.disabled = true;
  if (statusEl) statusEl.textContent = "";

  try {
    const res = await fetch("/api/journal/trades/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticker,
        direction,
        strategy,
        entry_price: entryPrice,
        stop_loss: stopLoss,
        tp1,
        tp2,
        score: 0,
        stock_type: "stable",
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (statusEl) statusEl.textContent = err.detail ?? "Could not open trade.";
      return;
    }
    form.reset();
    if (statusEl) statusEl.textContent = "";
    await refreshJournal();
  } catch (e) {
    console.warn("Open trade error:", e);
    if (statusEl) statusEl.textContent = "Could not reach the server.";
  } finally {
    btn.disabled = false;
  }
}

function initJournalTradeForm() {
  const form = q("#journalOpenTradeForm");
  if (!form || form.dataset.journalFormWired === "1") return;
  form.dataset.journalFormWired = "1";
  form.addEventListener("submit", (e) => void submitJournalOpenTrade(e));
}

function prefillJournalTickerFromContext() {
  const tickerEl = q("#jtTicker");
  if (!tickerEl || String(tickerEl.value).trim()) return;
  const candidate = lastSelectedScannerTicker || state.stockTicker;
  if (candidate) tickerEl.value = String(candidate).toUpperCase();
}

function navigateToStockTicker(rawSym) {
  const tkNorm = normalizeHomeTickerToken(rawSym);
  if (!tkNorm || !readHomeWatchlistTickers().includes(tkNorm)) return;
  lastSelectedScannerTicker = tkNorm;
  location.hash = `#stock/${tkNorm}`;
}

function tickerChipHtml(sym, options) {
  const opts = options && typeof options === "object" ? options : {};
  const safe = escapeHtml(sym);
  const symbolInner = `<span class="ticker-chip-dollar" aria-hidden="true">$</span><b>${safe}</b>`;
  if (opts.clickable === false) {
    return `<span class="ticker-chip"><b>${safe}</b></span>`;
  }
  return `<span class="ticker-chip ticker-chip--clickable" data-ticker="${safe}" tabindex="0" role="button">${symbolInner}</span>`;
}

const LAYOUT_HORIZONTAL_MQL = "(orientation: landscape) and (min-width: 720px)";
const LAYOUT_WIDE_DELAY_MS = 4000;

let layoutHorizontalActive = false;
let layoutWideReady = false;
let layoutWideDelayTimerId = null;

function isLayoutHorizontal() {
  try {
    return window.matchMedia(LAYOUT_HORIZONTAL_MQL).matches;
  } catch {
    return false;
  }
}

function applyLayoutWideDomState() {
  const horizontal = isLayoutHorizontal();
  try {
    document.documentElement.setAttribute("data-layout-horizontal", horizontal ? "1" : "0");
    document.documentElement.setAttribute(
      "data-layout-wide-ready",
      horizontal && layoutWideReady ? "1" : "0",
    );
  } catch {
    /* ignore */
  }
}

function clearLayoutWideDelayTimer() {
  if (layoutWideDelayTimerId != null) {
    window.clearTimeout(layoutWideDelayTimerId);
    layoutWideDelayTimerId = null;
  }
}

function onLayoutOrientationChange() {
  const horizontal = isLayoutHorizontal();

  if (!horizontal) {
    clearLayoutWideDelayTimer();
    layoutHorizontalActive = false;
    layoutWideReady = false;
    applyLayoutWideDomState();
    return;
  }

  layoutHorizontalActive = true;
  applyLayoutWideDomState();

  if (layoutWideReady || layoutWideDelayTimerId != null) return;

  layoutWideDelayTimerId = window.setTimeout(() => {
    layoutWideDelayTimerId = null;
    if (!isLayoutHorizontal()) return;
    layoutWideReady = true;
    applyLayoutWideDomState();
  }, LAYOUT_WIDE_DELAY_MS);
}

function initLayoutWideListener() {
  onLayoutOrientationChange();
  try {
    window.matchMedia(LAYOUT_HORIZONTAL_MQL).addEventListener("change", onLayoutOrientationChange);
  } catch {
    /* ignore */
  }
  window.addEventListener("resize", onLayoutOrientationChange);
}

function moverScoreHtml(scoreRaw) {
  const scoreStr =
    typeof scoreRaw === "number" && Number.isFinite(scoreRaw) ? String(Math.round(scoreRaw)) : escapeHtml(String(scoreRaw ?? "—"));
  return `<b class="mover-score">${scoreStr}</b>`;
}

function suggestedTradeWideSubLine(row) {
  const parts = [];
  const ch = formatPct(row.change_pct);
  if (ch && ch !== "—") parts.push(`${ch} session`);
  const dir = row.direction != null ? String(row.direction).trim() : "";
  if (dir && dir.toUpperCase() !== "NEUTRAL") parts.push(dir);
  const strat = row.strategy != null ? String(row.strategy).trim() : "";
  if (strat && strat !== "No Active Setup") parts.push(strat.replace(/_/g, " "));
  const scoreV = typeof row.score === "number" && Number.isFinite(row.score) ? Math.round(row.score) : null;
  if (scoreV != null) parts.push(`score ${scoreV}/100`);
  return parts.length ? parts.join(" · ") : "—";
}

function moverBioHtml(mRow) {
  const changeLabel = formatPct(mRow.change_pct);
  const scoreHtml = moverScoreHtml(mRow.score);
  const compactBio = `${escapeHtml(changeLabel)} · score ${scoreHtml}`;
  const wideParts = [escapeHtml(changeLabel)];
  const dir = mRow.direction != null ? String(mRow.direction).trim() : "";
  if (dir && dir.toUpperCase() !== "NEUTRAL") wideParts.push(escapeHtml(dir));
  const px = typeof mRow.price === "number" && Number.isFinite(mRow.price) ? mRow.price.toFixed(2) : null;
  if (px) wideParts.push(escapeHtml(`$${px}`));
  wideParts.push(`score ${scoreHtml}`);
  const wideBio = wideParts.join(" · ");
  return `<span class="dash-bio dash-bio--compact">${compactBio}</span><span class="dash-bio dash-bio--wide">${wideBio}</span>`;
}

function navigateToStockTickerOrAdd(rawSym) {
  const tkNorm = normalizeHomeTickerToken(rawSym);
  if (!tkNorm) return;
  const cur = readHomeWatchlistTickers();
  if (!cur.includes(tkNorm)) {
    writeHomeWatchlistTickers([tkNorm, ...cur]);
    applyHomeWatchlistInputFromStorage();
    renderHomeScanner();
    renderHomeDashboardPanels();
  }
  navigateToStockTicker(tkNorm);
}

function stopStockBarsPolling() {
  if (stockBarsPollTimerId != null) {
    window.clearInterval(stockBarsPollTimerId);
    stockBarsPollTimerId = null;
  }
}

function teardownStockChartUi() {
  if (stockLiveChartInstance && stockChartVisibleRangeHandler) {
    stockLiveChartInstance.timeScale().unsubscribeVisibleTimeRangeChange(stockChartVisibleRangeHandler);
    stockChartVisibleRangeHandler = null;
  }
  if (stockChartResizeObserver) {
    stockChartResizeObserver.disconnect();
    stockChartResizeObserver = null;
  }
  if (stockLiveChartInstance) {
    stockLiveChartInstance.remove();
    stockLiveChartInstance = null;
    stockCandleSeries = null;
  }
}

function readTbCssVar(cssVarToken, fallbackValue) {
  const trimmed = getComputedStyle(document.documentElement).getPropertyValue(cssVarToken).trim();
  return trimmed || fallbackValue;
}

function stockDeskColorPalette() {
  return {
    text: readTbCssVar("--ink", "#f2f3f5"),
    line: readTbCssVar("--line", "#393944"),
    green: readTbCssVar("--green", "#2f9d6a"),
    red: readTbCssVar("--red", "#d70015"),
  };
}

function scheduleStockDeskPollingBars(tickerSym) {
  stopStockBarsPolling();
  stockBarsPollTimerId = window.setInterval(() => {
    if (state.page === "symbol" && state.stockTicker === tickerSym)
      void reloadStockBarsPack(tickerSym);
  }, 90000);
}

function setStockHistoryRangePanelExpanded(expand) {
  const btnEl = q("#stockHistoryRangeInfoBtn");
  const panelEl = q("#stockHistoryRangePanel");
  if (!btnEl || !panelEl) return;
  const show = Boolean(expand);
  panelEl.hidden = !show;
  btnEl.setAttribute("aria-expanded", show ? "true" : "false");
}

function wireStockHistoryRangeInfoOnce() {
  const histBtn = q("#stockHistoryRangeInfoBtn");
  if (!histBtn || histBtn.dataset.stockDeskWire === "1") return;
  histBtn.dataset.stockDeskWire = "1";
  histBtn.addEventListener("click", (evt) => {
    evt.preventDefault();
    evt.stopPropagation();
    const panelEl = q("#stockHistoryRangePanel");
    const isOpen = Boolean(panelEl && !panelEl.hidden);
    setStockHistoryRangePanelExpanded(!isOpen);
  });
  if (stockHistoryInfoDocHandlersBound) return;
  stockHistoryInfoDocHandlersBound = true;
  document.addEventListener("keydown", (evt) => {
    if (evt.key !== "Escape") return;
    const panelElEsc = q("#stockHistoryRangePanel");
    if (panelElEsc && !panelElEsc.hidden) setStockHistoryRangePanelExpanded(false);
  });
  document.addEventListener("click", (evt) => {
    const btnDoc = q("#stockHistoryRangeInfoBtn");
    const panelDoc = q("#stockHistoryRangePanel");
    if (!btnDoc || !panelDoc || panelDoc.hidden) return;
    const tgt = evt.target;
    if (btnDoc.contains(tgt) || panelDoc.contains(tgt)) return;
    setStockHistoryRangePanelExpanded(false);
  });
}

function stockChartVisibleTimeAsUnix(secondsOrObject) {
  if (secondsOrObject == null) return null;
  if (typeof secondsOrObject === "number" && Number.isFinite(secondsOrObject)) return secondsOrObject;
  if (typeof secondsOrObject === "object" && typeof secondsOrObject.timestamp === "number")
    return secondsOrObject.timestamp;
  return null;
}

function paintStockCandleSeries(candlesNormalized, _barsMeta) {
  const hostMounted = q("#stockChartHost");
  const wrapBox = q("#stockChartWrap");
  const footerNote = q("#stockChartFootnote");
  if (!hostMounted || !wrapBox) return;
  if (!window.LightweightCharts) {
    if (footerNote) footerNote.textContent = "Chart library failed to load.";
    return;
  }
  if (!candlesNormalized || !candlesNormalized.length) {
    if (footerNote) footerNote.textContent = "No candles for this interval/period.";
    teardownStockChartUi();
    return;
  }
  const LC = window.LightweightCharts;
  const palDesk = stockDeskColorPalette();
  const plotRows = candlesNormalized.map((cv) => ({
    time: cv.time,
    open: cv.open,
    high: cv.high,
    low: cv.low,
    close: cv.close,
  }));
  const boxRect = wrapBox.getBoundingClientRect();
  const widthFit = Math.max(240, Math.floor(boxRect.width));
  const heightFit = Math.max(280, Math.min(520, Math.floor(Number(boxRect.height) || window.innerHeight * 0.45)));
  if (!stockLiveChartInstance) {
    stockLiveChartInstance = LC.createChart(hostMounted, {
      width: widthFit,
      height: heightFit,
      layout: {
        fontSize: 12,
        textColor: palDesk.text,
        background: { type: LC.ColorType.Solid, color: "transparent" },
      },
      grid: { vertLines: { color: palDesk.line }, horzLines: { color: palDesk.line } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: palDesk.line,
        fixLeftEdge: false,
      },
      rightPriceScale: { borderColor: palDesk.line },
    });
    stockCandleSeries = stockLiveChartInstance.addCandlestickSeries({
      upColor: palDesk.green,
      downColor: palDesk.red,
      borderVisible: false,
      wickUpColor: palDesk.green,
      wickDownColor: palDesk.red,
    });
    stockChartResizeObserver = new ResizeObserver(() => {
      const rSized = wrapBox.getBoundingClientRect();
      stockLiveChartInstance?.applyOptions({
        width: Math.max(240, Math.floor(rSized.width)),
        height: Math.max(280, Math.min(520, Math.floor(Number(rSized.height) || 360))),
      });
    });
    stockChartResizeObserver.observe(wrapBox);
  } else {
    stockLiveChartInstance.applyOptions({
      width: widthFit,
      height: heightFit,
      timeScale: { fixLeftEdge: false },
    });
  }
  stockCandleSeries?.setData(plotRows);
  const timeScaleApi = stockLiveChartInstance.timeScale();
  if (stockChartVisibleRangeHandler) {
    timeScaleApi.unsubscribeVisibleTimeRangeChange(stockChartVisibleRangeHandler);
    stockChartVisibleRangeHandler = null;
  }
  timeScaleApi.fitContent();
  const oldestBarUnix = plotRows[0].time;
  const minAllowedVisibleFromUnix = oldestBarUnix - STOCK_CHART_SOFT_LEFT_PADDING_SEC;
  let stockChartClampVisibleRange = false;
  const onStockVisibleTimeRange = (timeRange) => {
    if (stockChartClampVisibleRange || !timeRange) return;
    const rawFrom = stockChartVisibleTimeAsUnix(timeRange.from);
    const rawTo = stockChartVisibleTimeAsUnix(timeRange.to);
    if (rawFrom == null || rawTo == null) return;
    let nextFrom = rawFrom;
    let nextTo = rawTo;
    if (nextFrom < minAllowedVisibleFromUnix) nextFrom = minAllowedVisibleFromUnix;
    if (nextTo < nextFrom) nextTo = nextFrom;
    if (nextFrom === rawFrom && nextTo === rawTo) return;
    stockChartClampVisibleRange = true;
    try {
      timeScaleApi.setVisibleRange({ from: nextFrom, to: nextTo });
    } finally {
      stockChartClampVisibleRange = false;
    }
  };
  timeScaleApi.subscribeVisibleTimeRangeChange(onStockVisibleTimeRange);
  stockChartVisibleRangeHandler = onStockVisibleTimeRange;
  if (footerNote) {
    footerNote.textContent = `${plotRows.length} bars · updates while you stay on this page.`;
  }
}

async function reloadStockBarsPack(tickerSym) {
  const periodPick = q("#stockBarPeriod")?.value || "5d";
  const intervalPick = q("#stockBarInterval")?.value || "15m";
  const barsUrl = `/api/scan/ticker/${encodeURIComponent(tickerSym)}/bars?period=${encodeURIComponent(periodPick)}&interval=${encodeURIComponent(intervalPick)}`;
  try {
    const fetchedBars = await fetch(barsUrl);
    if (!fetchedBars.ok) {
      teardownStockChartUi();
      const footQuick = q("#stockChartFootnote");
      let noteLine =
        fetchedBars.status === 404 ? "No bars for this symbol and range." : "";
      if (!noteLine) {
        if (fetchedBars.status === 503) noteLine = "Price data is temporarily unavailable.";
        else if (fetchedBars.status === 422 || fetchedBars.status === 400)
          noteLine = "Couldn't load bars for those settings.";
        else noteLine = "Couldn't load bars.";
      }
      if (footQuick) footQuick.textContent = noteLine;
      return;
    }
    const bodyPack = await fetchedBars.json();
    const candList = Array.isArray(bodyPack.candles) ? bodyPack.candles : [];
    paintStockCandleSeries(candList, bodyPack);
  } catch (errPack) {
    const footQuick = q("#stockChartFootnote");
    if (footQuick)
      footQuick.textContent =
        typeof errPack?.message === "string" ? errPack.message : "Could not load bars.";
  }
}

function stockDetailFormatNumber(n) {
  if (n == null || n === "") return "—";
  if (typeof n === "number" && Number.isFinite(n)) {
    const a = Math.abs(n);
    if (a >= 1000) return n.toFixed(2);
    if (a < 0.01 && n !== 0) return n.toFixed(5);
    return n.toFixed(2);
  }
  return String(n);
}

function humanizeDetailKey(rawKey) {
  const s = String(rawKey).replace(/_/g, " ").trim();
  if (!s) return String(rawKey);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function buildStockIndicatorsDlHtml(indicators) {
  if (!indicators || typeof indicators !== "object") return "";
  const rows = [];
  const rsi = indicators.rsi;
  if (rsi && typeof rsi === "object" && rsi.value != null) {
    rows.push(
      `<div class="stock-detail-kv"><dt>RSI</dt><dd>${escapeHtml(String(rsi.value))}</dd></div>`,
    );
  }
  const macd = indicators.macd;
  if (macd && typeof macd === "object") {
    const hist = macd.histogram;
    if (hist != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>MACD histogram</dt><dd>${escapeHtml(stockDetailFormatNumber(hist))}</dd></div>`,
      );
    }
  }
  const adx = indicators.adx;
  if (adx && typeof adx === "object" && adx.value != null) {
    rows.push(
      `<div class="stock-detail-kv"><dt>ADX</dt><dd>${escapeHtml(String(adx.value))}</dd></div>`,
    );
  }
  const atr = indicators.atr;
  if (atr && typeof atr === "object") {
    if (atr.value != null)
      rows.push(
        `<div class="stock-detail-kv"><dt>ATR</dt><dd>${escapeHtml(stockDetailFormatNumber(atr.value))}</dd></div>`,
      );
    if (atr.pct != null)
      rows.push(
        `<div class="stock-detail-kv"><dt>ATR %</dt><dd>${escapeHtml(stockDetailFormatNumber(atr.pct))}</dd></div>`,
      );
  }
  const rvol = indicators.rvol;
  if (rvol && typeof rvol === "object" && rvol.rvol != null) {
    rows.push(
      `<div class="stock-detail-kv"><dt>Relative volume</dt><dd>${escapeHtml(stockDetailFormatNumber(rvol.rvol))}</dd></div>`,
    );
  }
  const vwap = indicators.vwap;
  if (vwap && typeof vwap === "object" && vwap.distance_pct != null) {
    rows.push(
      `<div class="stock-detail-kv"><dt>Vs VWAP</dt><dd>${escapeHtml(formatPct(vwap.distance_pct))}</dd></div>`,
    );
  }
  const bb = indicators.bollinger;
  if (bb && typeof bb === "object" && bb.pct_b != null) {
    rows.push(
      `<div class="stock-detail-kv"><dt>Bollinger %B</dt><dd>${escapeHtml(stockDetailFormatNumber(bb.pct_b))}</dd></div>`,
    );
  }
  if (!rows.length) return "";
  return `<dl class="stock-detail-dl">${rows.join("")}</dl>`;
}

function buildStockTickerAnalysisHtml(detailJson) {
  const snapState = detailJson.state && typeof detailJson.state === "object" ? detailJson.state : {};
  const ctx = detailJson.context && typeof detailJson.context === "object" ? detailJson.context : {};
  const regime = ctx.regime && typeof ctx.regime === "object" ? ctx.regime : {};
  const msBlob = ctx.market_state && typeof ctx.market_state === "object" ? ctx.market_state : {};

  const parts = [];
  const ctxBits = [];
  if (ctx.last_scan_at) ctxBits.push(`Last full scan ${escapeHtml(String(ctx.last_scan_at))}`);
  if (msBlob.label) ctxBits.push(`Market ${escapeHtml(String(msBlob.label))}`);
  if (regime.label) ctxBits.push(`Regime ${escapeHtml(String(regime.label))}`);
  if (ctxBits.length) {
    parts.push(`<p class="stock-detail-context-line">${ctxBits.join(" · ")}</p>`);
  }
  if (ctx.last_error) {
    parts.push(`<p class="stock-detail-warn">${escapeHtml(String(ctx.last_error))}</p>`);
  }

  const playbookHtml =
    globalThis.TbNarrative?.playbookSectionHtml?.(detailJson, backendHealth.scan) ?? "";
  const hasPlaybook = Boolean(playbookHtml);
  if (playbookHtml) parts.push(playbookHtml);

  const strat =
    snapState.strategy != null && String(snapState.strategy).trim() !== ""
      ? String(snapState.strategy)
      : "—";
  const dir = snapState.direction != null ? String(snapState.direction) : "NEUTRAL";
  const scoreV = snapState.score != null ? String(snapState.score) : "—";
  const prevSc = snapState.prev_score != null ? String(snapState.prev_score) : null;
  const confDd =
    prevSc != null
      ? `${escapeHtml(scoreV)} <span class="stock-detail-muted">(prior ${escapeHtml(prevSc)})</span>`
      : escapeHtml(scoreV);

  parts.push(`<div class="stock-detail-section">
    <p class="stock-detail-section-title">Snapshot</p>
    <dl class="stock-detail-dl">
      <div class="stock-detail-kv"><dt>Strategy</dt><dd><code>${escapeHtml(strat)}</code></dd></div>
      <div class="stock-detail-kv"><dt>Bias</dt><dd>${escapeHtml(dir)}</dd></div>
      <div class="stock-detail-kv"><dt>Confidence</dt><dd>${confDd} / 100</dd></div>
      <div class="stock-detail-kv"><dt>Price</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.price))} · ${escapeHtml(formatPct(snapState.change_pct))}</dd></div>
      <div class="stock-detail-kv"><dt>Agreement</dt><dd>Bullish ${escapeHtml(String(snapState.bullish_count ?? "—"))} · Bearish ${escapeHtml(String(snapState.bearish_count ?? "—"))}</dd></div>
      <div class="stock-detail-kv"><dt>Sector</dt><dd>${escapeHtml(String(snapState.sector ?? "—"))}</dd></div>
      <div class="stock-detail-kv"><dt>Bar time (scan)</dt><dd>${escapeHtml(String(snapState.last_scan ?? "—"))}</dd></div>
    </dl>
  </div>`);

  const hasLevel =
    snapState.entry_price != null ||
    snapState.stop != null ||
    snapState.tp1 != null ||
    snapState.tp2 != null ||
    snapState.tp3 != null;
  if (hasLevel) {
    const rows = [];
    if (snapState.entry_price != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Entry</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.entry_price))}</dd></div>`,
      );
    }
    if (snapState.stop != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Stop</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.stop))}</dd></div>`,
      );
    }
    if (snapState.tp1 != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Target 1</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.tp1))}</dd></div>`,
      );
    }
    if (snapState.tp2 != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Target 2</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.tp2))}</dd></div>`,
      );
    }
    if (snapState.tp3 != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Target 3</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.tp3))}</dd></div>`,
      );
    }
    if (snapState.rr != null) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Risk / reward</dt><dd>${escapeHtml(stockDetailFormatNumber(snapState.rr))}</dd></div>`,
      );
    }
    if (snapState.stop_type) {
      rows.push(
        `<div class="stock-detail-kv"><dt>Stop type</dt><dd>${escapeHtml(String(snapState.stop_type))}</dd></div>`,
      );
    }
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Levels from latest alert</p>
      <dl class="stock-detail-dl">${rows.join("")}</dl>
      <p class="stock-detail-muted-note">Shown when this symbol produced a passing alert in the most recent scan batch.</p>
    </div>`);
  }

  const ctx2 = [];
  if (snapState.regime != null) ctx2.push(`Regime ${escapeHtml(String(snapState.regime))}`);
  if (snapState.regime_quality != null) {
    ctx2.push(`Regime quality ${escapeHtml(String(Math.round(snapState.regime_quality)))}`);
  }
  if (snapState.signal_quality != null) {
    ctx2.push(`Signal quality ${escapeHtml(String(snapState.signal_quality))}`);
  }
  if (snapState.market_state_label) {
    ctx2.push(escapeHtml(String(snapState.market_state_label)));
  }
  if (snapState.tf_aligned != null) {
    ctx2.push(`Timeframes aligned: ${snapState.tf_aligned ? "Yes" : "No"}`);
  }
  if (snapState.prev_close_info) {
    ctx2.push(escapeHtml(String(snapState.prev_close_info)));
  }
  if (ctx2.length) {
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Symbol context</p>
      <p class="scanner-detail-line">${ctx2.join(" · ")}</p>
    </div>`);
  }

  const met = Array.isArray(snapState.conditions_met) ? snapState.conditions_met : [];
  const failed = Array.isArray(snapState.conditions_failed) ? snapState.conditions_failed : [];
  if (met.length || failed.length) {
    const metLis = met.map((x) => `<li>${escapeHtml(String(x))}</li>`).join("");
    const failLis = failed.map((x) => `<li>${escapeHtml(String(x))}</li>`).join("");
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Rule checks</p>
      ${met.length ? `<p class="stock-detail-subhead">Met</p><ul class="stock-detail-list">${metLis}</ul>` : ""}
      ${failed.length ? `<p class="stock-detail-subhead">Not met</p><ul class="stock-detail-list stock-detail-list-warn">${failLis}</ul>` : ""}
    </div>`);
  }

  const sd = snapState.score_data && typeof snapState.score_data === "object" ? snapState.score_data : {};
  const bd = sd.breakdown && typeof sd.breakdown === "object" ? sd.breakdown : null;
  const scannerReadPlain =
    typeof sd.explanation === "string" ? sd.explanation.trim() : "";
  if (scannerReadPlain && !hasPlaybook) {
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Scanner read</p>
      <p class="scanner-detail-line">${escapeHtml(scannerReadPlain)}</p>
    </div>`);
  }
  const scoreRows = [];
  if (typeof sd.total === "number") {
    scoreRows.push(
      `<div class="stock-detail-kv"><dt>Total</dt><dd>${escapeHtml(String(Math.round(sd.total)))} / 100</dd></div>`,
    );
  }
  if (bd) {
    for (const [k, v] of Object.entries(bd)) {
      if (typeof v === "number") {
        scoreRows.push(
          `<div class="stock-detail-kv"><dt>${escapeHtml(humanizeDetailKey(k))}</dt><dd>${escapeHtml(String(Math.round(v)))}</dd></div>`,
        );
      }
    }
  }
  if (scoreRows.length && !hasPlaybook) {
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Score breakdown</p>
      <dl class="stock-detail-dl">${scoreRows.join("")}</dl>
    </div>`);
  }

  const indHtml = buildStockIndicatorsDlHtml(snapState.indicators);
  if (indHtml) {
    parts.push(`<div class="stock-detail-section">
      <p class="stock-detail-section-title">Indicators (5m)</p>
      ${indHtml}
    </div>`);
  }

  if (!hasPlaybook) {
    const extras = [];
    if (snapState.ale_details != null && snapState.ale_details !== "") {
      const aleRaw =
        typeof snapState.ale_details === "object"
          ? JSON.stringify(snapState.ale_details, null, 2)
          : String(snapState.ale_details);
      extras.push(
        `<details class="stock-detail-raw"><summary>Scan extras</summary><pre class="scanner-json-snippet">${escapeHtml(aleRaw.slice(0, 6000))}</pre></details>`,
      );
    }
    if (snapState.flow_result != null && snapState.flow_result !== "") {
      const flowRaw =
        typeof snapState.flow_result === "object"
          ? JSON.stringify(snapState.flow_result, null, 2)
          : String(snapState.flow_result);
      extras.push(
        `<details class="stock-detail-raw"><summary>Order flow</summary><pre class="scanner-json-snippet">${escapeHtml(flowRaw.slice(0, 6000))}</pre></details>`,
      );
    }
    const rawInd = JSON.stringify(snapState.indicators ?? {}, null, 2);
    const rawScore = JSON.stringify(snapState.score_data ?? {}, null, 2);
    extras.push(
      `<details class="stock-detail-raw"><summary>All indicator fields (JSON)</summary><pre class="scanner-json-snippet">${escapeHtml(rawInd.slice(0, 12000))}</pre></details>`,
      `<details class="stock-detail-raw"><summary>Full score payload (JSON)</summary><pre class="scanner-json-snippet">${escapeHtml(rawScore.slice(0, 8000))}</pre></details>`,
    );
    parts.push(extras.join(""));
  }

  return parts.join("");
}

async function hydrateStockScannerFacts(tickerSym) {
  const factRoot = q("#stockDetailBody");
  if (!factRoot) return;
  factRoot.innerHTML = `<p class="scanner-feed-empty">${escapeHtml("Loading scanner snapshot…")}</p>`;
  try {
    const detailResponse = await fetch(`/api/scan/ticker/${encodeURIComponent(tickerSym)}`);
    if (!detailResponse.ok) {
      const problem = await detailResponse.json().catch(() => ({}));
      const detailPhrase =
        typeof problem.detail === "string" ? problem.detail : detailResponse.statusText;
      factRoot.innerHTML = `<p class="scanner-detail-line">${escapeHtml(
        detailPhrase,
      )} Run a scan from the home page so this symbol is in the scanner cache. Charts still load from the market.</p>`;
      return;
    }
    const detailJson = await detailResponse.json();
    factRoot.innerHTML = buildStockTickerAnalysisHtml(detailJson);
    globalThis.TbNarrative?.wireDisclosure?.(factRoot);
    globalThis.TbSkillMode?.applySkillMode?.(factRoot);
  } catch (errFact) {
    factRoot.innerHTML = `<p class="scanner-detail-line">${escapeHtml(errFact.message || String(errFact))}</p>`;
  }
}

async function refreshStockDesk(tickerSymToken) {
  const heading = q("#stockPageTitle");
  if (heading) heading.textContent = tickerSymToken;
  stopStockBarsPolling();
  await hydrateStockScannerFacts(tickerSymToken);
  globalThis.TbSymbolResearchBridge?.mountStockCta?.(tickerSymToken);
  await reloadStockBarsPack(tickerSymToken);
  scheduleStockDeskPollingBars(tickerSymToken);
}

function initStockDeskControlsOnce() {
  const goBackScanner = q("#stockBackHome");
  if (goBackScanner && goBackScanner.dataset.stockDeskWire !== "1") {
    goBackScanner.dataset.stockDeskWire = "1";
    goBackScanner.addEventListener("click", () => setPage("live"));
  }
  const ivSelect = q("#stockBarInterval");
  const pdSelect = q("#stockBarPeriod");
  const onRangeChange = () => {
    if (state.page === "symbol" && state.stockTicker) void reloadStockBarsPack(state.stockTicker);
  };
  if (ivSelect && ivSelect.dataset.stockDeskWire !== "1") {
    ivSelect.dataset.stockDeskWire = "1";
    ivSelect.addEventListener("change", onRangeChange);
  }
  if (pdSelect && pdSelect.dataset.stockDeskWire !== "1") {
    pdSelect.dataset.stockDeskWire = "1";
    pdSelect.addEventListener("change", onRangeChange);
  }
  wireStockHistoryRangeInfoOnce();
}

function readSidebarNavOrderPreference() {
  try {
    const raw = localStorage.getItem(NAV_ORDER_STORAGE_KEY);
    if (!raw) return [...SIDEBAR_NAV_DEFAULT_ORDER];
    const parsedArr = JSON.parse(raw);
    if (!Array.isArray(parsedArr)) return [...SIDEBAR_NAV_DEFAULT_ORDER];
    const kept = parsedArr.filter((id) => SIDEBAR_REORDERABLE_IDS.includes(id));
    const missing = SIDEBAR_REORDERABLE_IDS.filter((id) => !kept.includes(id));
    return [...kept, ...missing];
  } catch {
    return [...SIDEBAR_NAV_DEFAULT_ORDER];
  }
}

function applySidebarNavDomOrder(orderIds) {
  const listRoot = q("#navReorderList");
  if (!listRoot) return;
  const seq = orderIds.filter((id) => SIDEBAR_REORDERABLE_IDS.includes(id));
  const trailing = SIDEBAR_REORDERABLE_IDS.filter((id) => !seq.includes(id));
  const finalOrder = [...seq, ...trailing];
  finalOrder.forEach((sectionId) => {
    const rowEl = listRoot.querySelector(`.nav-row--reorderable[data-nav-section="${sectionId}"]`);
    if (rowEl) listRoot.appendChild(rowEl);
  });
}

function setSidebarNavReorderDragging(active) {
  const listRoot = q("#navReorderList");
  if (!listRoot) return;
  listRoot.classList.toggle("nav-reorder-is-dragging", Boolean(active));
}

function persistSidebarNavOrderFromDom() {
  const listRoot = q("#navReorderList");
  if (!listRoot) return;
  const orderedIds = qa(".nav-row--reorderable", listRoot)
    .map((row) => row.getAttribute("data-nav-section"))
    .filter((sid) => sid && SIDEBAR_REORDERABLE_IDS.includes(sid));
  try {
    localStorage.setItem(NAV_ORDER_STORAGE_KEY, JSON.stringify(orderedIds));
  } catch {
    /* ignore */
  }
}

function initSidebarNavDragSort() {
  applySidebarNavDomOrder(readSidebarNavOrderPreference());
  const listRoot = q("#navReorderList");
  if (!listRoot) return;
  if (typeof window.Sortable !== "function") {
    console.warn(
      "SortableJS did not load (check Network tab for Sortable.min.js). Sidebar tab reorder is disabled until it succeeds.",
    );
    return;
  }
  if (sidebarNavSortable?.destroy) {
    sidebarNavSortable.destroy();
    sidebarNavSortable = null;
  }
  const reduceMotionUi = motionDisabled();
  const coarsePointer =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  sidebarNavSortable = window.Sortable.create(listRoot, {
    animation: reduceMotionUi ? 0 : 240,
    easing: "cubic-bezier(0.25, 1, 0.32, 1)",
    handle: ".nav-drag-handle",
    draggable: ".nav-row--reorderable",
    ghostClass: "nav-sortable-ghost",
    chosenClass: "nav-sortable-chosen",
    dragClass: "nav-sortable-drag",
    direction: "vertical",
    swapThreshold: 0.65,
    invertSwap: false,
    /** Fallback drag for coarse pointers; mouse uses native path (more reliable with drag handles). */
    forceFallback: coarsePointer,
    fallbackOnBody: coarsePointer,
    fallbackTolerance: coarsePointer ? 10 : 0,
    onStart() {
      setSidebarNavReorderDragging(true);
    },
    onEnd() {
      setSidebarNavReorderDragging(false);
      sidebarNavDragSuppressClickUntil = performance.now() + 420;
      persistSidebarNavOrderFromDom();
    },
  });
}

function resetWatchlistStoredEmpty() {
  writeHomeWatchlistTickers([]);
  applyHomeWatchlistInputFromStorage();
  renderHomeScanner();
}

function confirmResetLocalTradingBotAccount() {
  if (
    !window.confirm(
      "Clear this browser’s Second Bar data?\nWatchlist, backtest history & saved settings, sidebar order, theme, animation speed, font size → defaults.",
    )
  ) {
    return;
  }
  try {
    localStorage.removeItem(HOME_WATCHLIST_STORAGE_KEY);
    localStorage.removeItem(BT_HISTORY_KEY);
    localStorage.removeItem(BT_SETTINGS_BY_TICKER_KEY);
    localStorage.removeItem(NAV_ORDER_STORAGE_KEY);
    localStorage.removeItem(THEME_STORAGE_KEY);
    localStorage.removeItem(globalThis.TbMotion?.STORAGE_KEY ?? "tb_motion_speed");
    localStorage.removeItem(globalThis.TbFontSize?.STORAGE_KEY ?? "tb_font_size");
  } catch {
    /* ignore */
  }
  try {
    sessionStorage.removeItem(HOME_WATCHLIST_STORAGE_KEY);
  } catch {
    /* ignore */
  }
  btLastSuccessfulPayload = null;
  btExpandedHistoryId = null;
  btRunPanelDetailOpen = true;
  applyTheme("light");
  globalThis.TbMotion?.setSpeed?.(globalThis.TbMotion?.DEFAULT_SPEED ?? 100);
  globalThis.TbFontSize?.setSize?.(globalThis.TbFontSize?.DEFAULT_SIZE ?? "medium");
  applyHomeWatchlistInputFromStorage();
  applySidebarNavDomOrder([...SIDEBAR_NAV_DEFAULT_ORDER]);
  try {
    localStorage.setItem(NAV_ORDER_STORAGE_KEY, JSON.stringify(SIDEBAR_NAV_DEFAULT_ORDER));
  } catch {
    /* ignore */
  }
  initSidebarNavDragSort();
  renderHomeScanner();
  renderBtHistoryList();
  syncBacktestRouteUi();
  setPage("live");
  if (location.hash !== "#home") setAppHash("#home");
  void refreshFromBackend();
}

function attachScannerInteractions() {
  const wrap = q("#scannerTableWrap");
  if (!wrap || wrap.dataset.scanInteractions === "wired") return;
  wrap.dataset.scanInteractions = "wired";

  wrap.addEventListener("click", (clickEvent) => {
    const ghostBtn = clickEvent.target?.closest?.("button[data-scanner-ghost-add]");
    if (ghostBtn && wrap.contains(ghostBtn)) {
      clickEvent.preventDefault();
      clickEvent.stopPropagation();
      const token = ghostBtn.getAttribute("data-scanner-ghost-add");
      const norm = token ? normalizeHomeTickerToken(token) : "";
      if (!norm) return;
      const cur = readHomeWatchlistTickers();
      if (cur.includes(norm)) return;
      writeHomeWatchlistTickers([...cur, norm]);
      applyHomeWatchlistInputFromStorage();
      renderHomeScanner();
      renderHomeDashboardPanels();
      return;
    }
    const trEl = clickEvent.target?.closest?.("tr[data-ticker]");
    if (!trEl || !wrap.contains(trEl)) return;
    if (trEl.getAttribute("data-watchlisted") !== "true") return;
    const token = trEl.getAttribute("data-ticker");
    if (!token || clickEvent.metaKey || clickEvent.ctrlKey) return;
    navigateToStockTicker(token);
  });

  wrap.addEventListener("keydown", (keyEvent) => {
    const trFocused = document.activeElement?.closest?.("tr[data-ticker]");
    if (!(keyEvent.target instanceof HTMLElement) || !wrap.contains(trFocused)) return;
    if (trFocused.getAttribute("data-watchlisted") !== "true") return;
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault();
    const token = trFocused.getAttribute("data-ticker");
    if (token) navigateToStockTicker(token);
  });
}


function attachSuggestedTradesPanelInteractions() {
  const root = q("#homeSuggestedTrades");
  if (!root || root.dataset.suggestedTradesWired === "1") return;
  root.dataset.suggestedTradesWired = "1";
  root.addEventListener("click", (clickEvent) => {
    const btn = clickEvent.target?.closest?.("button[data-suggested-add]");
    if (btn && root.contains(btn)) {
      clickEvent.preventDefault();
      const raw = btn.getAttribute("data-suggested-add");
      const norm = raw ? normalizeHomeTickerToken(raw) : "";
      if (!norm) return;
      const cur = readHomeWatchlistTickers();
      if (cur.includes(norm)) return;
      writeHomeWatchlistTickers([norm, ...cur]);
      applyHomeWatchlistInputFromStorage();
      renderHomeScanner();
      renderHomeDashboardPanels();
      return;
    }
    const chip = clickEvent.target?.closest?.(".ticker-chip--clickable[data-ticker]");
    if (!chip || !root.contains(chip)) return;
    if (clickEvent.metaKey || clickEvent.ctrlKey) return;
    clickEvent.preventDefault();
    clickEvent.stopPropagation();
    const token = chip.getAttribute("data-ticker");
    if (token) navigateToStockTickerOrAdd(token);
  });
}

function attachTickerChipInteractions() {
  const livePage = q("#live");
  if (!livePage || livePage.dataset.tickerChipsWired === "1") return;
  livePage.dataset.tickerChipsWired = "1";

  livePage.addEventListener("click", (clickEvent) => {
    const chip = clickEvent.target?.closest?.(".ticker-chip--clickable[data-ticker]");
    if (!chip || !livePage.contains(chip)) return;
    if (clickEvent.target?.closest?.("button[data-suggested-add]")) return;
    if (clickEvent.metaKey || clickEvent.ctrlKey) return;
    clickEvent.stopPropagation();
    const token = chip.getAttribute("data-ticker");
    if (token) navigateToStockTickerOrAdd(token);
  });

  livePage.addEventListener("keydown", (keyEvent) => {
    const chip = keyEvent.target?.closest?.(".ticker-chip--clickable[data-ticker]");
    if (!(chip instanceof HTMLElement) || !livePage.contains(chip)) return;
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault();
    keyEvent.stopPropagation();
    const token = chip.getAttribute("data-ticker");
    if (token) navigateToStockTickerOrAdd(token);
  });
}

function attachScannerFeedInteractions() {
  const root = q("#homeScannerSignalFeed");
  if (!root || root.dataset.scanFeedInteractions === "wired") return;
  root.dataset.scanFeedInteractions = "wired";

  root.addEventListener("click", (clickEvent) => {
    const card = clickEvent.target?.closest?.("article.scanner-feed-card[data-ticker]");
    if (!card || !root.contains(card)) return;
    if (clickEvent.metaKey || clickEvent.ctrlKey) return;
    const token = card.getAttribute("data-ticker");
    if (token) navigateToStockTicker(token);
  });

  root.addEventListener("keydown", (keyEvent) => {
    const cardFocused = document.activeElement?.closest?.("article.scanner-feed-card[data-ticker]");
    if (!(keyEvent.target instanceof HTMLElement) || !root.contains(cardFocused)) return;
    if (keyEvent.key !== "Enter" && keyEvent.key !== " ") return;
    keyEvent.preventDefault();
    const token = cardFocused.getAttribute("data-ticker");
    if (token) navigateToStockTicker(token);
  });
}

function startScannerEventSource() {
  if (typeof EventSource !== "function") return;
  if (scannerLiveEventSource) return;
  try {
    const esConnected = new EventSource("/api/scan/stream");
    scannerLiveEventSource = esConnected;
    esConnected.addEventListener("message", (messageEvent) => {
      scannerSseStreaming = true;
      try {
        const parsedPayload = JSON.parse(messageEvent.data);
        if (parsedPayload.type === "scan_progress" && parsedPayload.data) {
          updateHomeScanProgressStrip(parsedPayload.data);
        } else if (
          parsedPayload.type === "snapshot" ||
          parsedPayload.type === "scan"
        ) {
          mergeScannerLivePayload(parsedPayload.data);
        }
      } catch (_) {
        /* ignore malformed sse */
      }
    });
    esConnected.addEventListener("error", () => {
      scannerSseStreaming = false;
      scannerLiveEventSource?.close?.();
      scannerLiveEventSource = null;
      window.setTimeout(startScannerEventSource, 7800);
    });
  } catch (_) {
    scannerSseStreaming = false;
  }
}

function floorToUtcMinute(date) {
  return new Date(Math.floor(date.getTime() / 60000) * 60000);
}

function etWallClockFields(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  }).formatToParts(date);
  const map = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = p.value;
  }
  const hour = Number(map.hour);
  const minute = Number(map.minute);
  const totalMinutes = hour * 60 + minute;
  return { weekday: map.weekday, hour, minute, totalMinutes };
}

function isRthOpenAt(date = new Date()) {
  const { weekday, totalMinutes } = etWallClockFields(date);
  const isWeekday = !["Sat", "Sun"].includes(weekday);
  const open = 9 * 60 + 30;
  const close = 16 * 60;
  return isWeekday && totalMinutes >= open && totalMinutes < close;
}

function findNextRthSessionOpenUtcMinute(from = new Date()) {
  let t = floorToUtcMinute(from);
  let prevOpen = isRthOpenAt(new Date(t.getTime() - 60000));
  const maxSteps = 14 * 24 * 60;
  for (let i = 0; i < maxSteps; i++) {
    const openNow = isRthOpenAt(t);
    if (openNow && !prevOpen && t.getTime() >= floorToUtcMinute(from).getTime()) {
      return t;
    }
    prevOpen = openNow;
    t = new Date(t.getTime() + 60000);
  }
  return null;
}

function findUpcomingRthSessionCloseUtcMinute(from = new Date()) {
  let t = floorToUtcMinute(from);
  let prevOpen = isRthOpenAt(new Date(t.getTime() - 60000));
  const maxSteps = 24 * 60;
  for (let i = 0; i < maxSteps; i++) {
    const openNow = isRthOpenAt(t);
    if (!openNow && prevOpen) {
      return t;
    }
    prevOpen = openNow;
    t = new Date(t.getTime() + 60000);
  }
  return null;
}

/** Clock face: default locale picks 12h vs 24h; zone is the user's own (host timezone). */
function formatLocalWallClock(date = new Date()) {
  const opts = { hour: "numeric", minute: "2-digit" };
  try {
    return new Intl.DateTimeFormat(undefined, opts).format(date);
  } catch {
    const h = date.getHours();
    const m = date.getMinutes();
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }
}

function localTimeZoneShort(date = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(date);
    const zn = parts.find((p) => p.type === "timeZoneName");
    return zn && zn.value ? zn.value : "";
  } catch {
    return "";
  }
}

function formatRemainCompact(msRemain) {
  const minuteMs = 60000;
  const hourMs = 3600000;
  const dayMs = 24 * hourMs;
  let ms = msRemain;
  if (ms <= 0) return "0m";
  const days = Math.floor(ms / dayMs);
  ms -= days * dayMs;
  const hours = Math.floor(ms / hourMs);
  ms -= hours * hourMs;
  const minutes = Math.floor(ms / minuteMs);
  const parts = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return parts.join(" ");
}

function classifyMarketPhase(now = new Date()) {
  const { weekday, totalMinutes: total } = etWallClockFields(now);
  const isWeekday = !["Sat", "Sun"].includes(weekday);
  const open = 9 * 60 + 30;
  const close = 16 * 60;

  if (!isWeekday) {
    return {
      key: "closed",
      title: "MARKET CLOSED",
      message: "Weekend in Eastern Time. Clock counts down to the next weekday session open.",
    };
  }
  if (total < open) {
    return {
      key: "pre",
      title: "PREMARKET",
      message: "Regular session opens at 9:30 AM ET. The scanner stays quiet until then.",
    };
  }
  if (total >= open && total < close) {
    return {
      key: "open",
      title: "MARKET LIVE",
      message: "Regular session is open. The table below refreshes with your scan schedule.",
    };
  }
  return {
    key: "closed",
    title: "MARKET CLOSED",
    message: "After-hours in Eastern Time; regular session has ended for today.",
  };
}

async function fetchMarketSession() {
  try {
    const res = await fetch("/api/market/session");
    if (!res.ok) throw new Error("bad status");
    const data = await res.json();
    marketSessionState.loaded = true;
    marketSessionState.data = data;
    marketSessionState.simplified = false;
  } catch {
    marketSessionState.loaded = false;
    marketSessionState.data = null;
    marketSessionState.simplified = true;
  }
}

function marketSessionUiFromApi(data, now = new Date()) {
  const openNow = Boolean(data && data.is_session);
  const targetIso = openNow ? data.next_close_et : data.next_open_et;
  const targetUtc = targetIso ? new Date(targetIso) : null;
  const deltaMs = targetUtc ? Math.max(0, targetUtc.getTime() - now.getTime()) : 0;
  const countdownPhrase = openNow
    ? `Regular session closes in ${formatRemainCompact(deltaMs)}`
    : `Next regular session opens in ${formatRemainCompact(deltaMs)}`;

  let key = "closed";
  let title = "MARKET CLOSED";
  if (openNow) {
    key = "open";
    title = "MARKET LIVE";
  } else if (data && data.session_open_et) {
    const openTime = new Date(data.session_open_et);
    if (now < openTime) {
      key = "pre";
      title = "PREMARKET";
    }
  }

  const message = (data && data.label) || "Market status is unavailable.";
  const localClock = formatLocalWallClock(now);
  const localTz = localTimeZoneShort(now);

  return {
    key,
    title,
    message,
    localClock,
    localTz,
    countdownPhrase,
    pillShort: title,
    healthSummary: `${title}. ${countdownPhrase}.`,
  };
}

/** Simplified Mon–Fri clock when /api/market/session is unavailable. */
function marketSessionUiFallback(now = new Date()) {
  const phase = classifyMarketPhase(now);
  const openNow = phase.key === "open";
  const targetUtc = openNow ? findUpcomingRthSessionCloseUtcMinute(now) : findNextRthSessionOpenUtcMinute(now);
  const deltaMs = targetUtc ? Math.max(0, targetUtc.getTime() - now.getTime()) : 0;
  const countdownPhrase = openNow
    ? `Regular session closes in ${formatRemainCompact(deltaMs)}`
    : `Next regular session opens in ${formatRemainCompact(deltaMs)}`;

  const localClock = formatLocalWallClock(now);
  const localTz = localTimeZoneShort(now);

  return {
    key: phase.key,
    title: phase.title,
    message: phase.message,
    localClock,
    localTz,
    countdownPhrase,
    pillShort: phase.title,
    healthSummary: `${phase.title}. ${countdownPhrase}.`,
  };
}

/** Dashboard + Health summary for the NY session strip */
function marketSessionUi(now = new Date()) {
  if (marketSessionState.loaded && marketSessionState.data) {
    return marketSessionUiFromApi(marketSessionState.data, now);
  }
  return marketSessionUiFallback(now);
}

function renderMarketSessionHint() {
  const hintEl = q("#marketSessionHint");
  if (!hintEl) return;
  if (marketSessionState.simplified) {
    hintEl.textContent = "Using simplified hours.";
    return;
  }
  if (marketSessionState.loaded && marketSessionState.data) {
    hintEl.textContent =
      "Regular hours reflect the market calendar, including holidays and early closes.";
  }
}

function stablePick(seed, arr) {
  const n = String(seed)
    .split("")
    .reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  return arr[n % arr.length];
}

/** Plain-language score summary for alert cards; full text stays on the symbol desk. */
const SCANNER_FEED_EXPLANATION_MAX_CHARS = 140;

function truncateScannerExplanationForFeed(text, maxLen = SCANNER_FEED_EXPLANATION_MAX_CHARS) {
  const t = String(text).trim();
  if (!t.length) return "";
  if (t.length <= maxLen) return t;
  const cap = Math.max(1, maxLen - 1);
  return `${t.slice(0, cap)}…`;
}

function renderHomeScanner(slideInTickers = null) {
  const note = q("#homeScannerImplementationNote");
  const sampleBanner = q("#homeSampleDataBanner");
  const rowsEl = q("#homeScannerRows");
  const feedEl = q("#homeScannerSignalFeed");
  const badge = q("#homeScannerBadge");
  const runBtn = q("#homeRunScanBtn");
  if (!note || !rowsEl || !feedEl) return;

  const slideList = slideInTickers && slideInTickers.length ? slideInTickers : null;
  const tableSymbols = readHomeWatchlistTickers();
  const watchlistSetUnified = new Set(tableSymbols);
  const minuteBucket = Math.floor(Date.now() / 60000);
  const ms = marketSessionUi(new Date());
  const scanPayload = backendHealth.loaded ? backendHealth.scan : null;
  const signals = scanPayload && Array.isArray(scanPayload.signals) ? scanPayload.signals : [];
  const watchlistOnlySignals = signals.filter((s) =>
    watchlistSetUnified.has(normalizeHomeTickerToken(s.ticker)),
  );
  const syncHomeSkillUi = () => {
    globalThis.TbSkillMode?.applyAll?.();
    globalThis.TbSkillMode?.applyContextPanelCollapse?.();
  };
  const sigMap = indexScanSignalsByTicker(signals);
  const useFullMock = !backendHealth.loaded;
  const lastScanAt = scanPayload && scanPayload.last_scan_at ? String(scanPayload.last_scan_at) : null;
  const lastErr = scanPayload && scanPayload.last_error ? String(scanPayload.last_error) : "";

  if (runBtn) {
    const disableRun = !backendHealth.loaded || homeScanRunInFlight;
    runBtn.disabled = disableRun;
    runBtn.setAttribute("aria-busy", homeScanRunInFlight ? "true" : "false");
  }

  if (sampleBanner) {
    sampleBanner.hidden = !useFullMock;
  }

  if (useFullMock) {
    note.textContent = "Sample rows below are for layout only. Start the server for live scans.";
  } else if (lastErr) {
    note.textContent = `Connected, but the last scan reported an error${lastScanAt ? ` (${lastScanAt})` : ""}: ${lastErr}`;
  } else {
    note.textContent = lastScanAt
      ? `Showing the latest scan (${lastScanAt}). Rows with alerts include direction and confidence; others are idle.`
      : "Showing the latest scan. Rows with alerts include direction and confidence; others are idle.";
  }

  if (badge) {
    if (useFullMock) {
      badge.textContent = "Offline · sample data";
    } else if (watchlistOnlySignals.length) {
      badge.textContent = `Live · ${watchlistOnlySignals.length} alert${watchlistOnlySignals.length === 1 ? "" : "s"}`;
    } else {
      badge.textContent = "Live · no alerts";
    }
  }

  const scanVerbMockOpen = "Demo scan";
  const scanVerbLiveOpen = "Session scan";

  if (!tableSymbols.length) {
    const suggestedWlRaw =
      scanPayload && Array.isArray(scanPayload.suggested_watchlist) ? scanPayload.suggested_watchlist : [];
    const ideas = suggestedWlRaw
      .filter((item) => item && normalizeHomeTickerToken(item.ticker))
      .slice(0, 8);
    if (!useFullMock && ideas.length) {
      rowsEl.innerHTML = ideas
        .map((item) => {
          const sym = normalizeHomeTickerToken(item.ticker);
          const score =
            typeof item.score === "number" && Number.isFinite(item.score) ? Math.round(item.score) : "—";
          const stratCell = formatScannerStrategyHtml({ strategy: item.strategy });
          const ch = formatPct(item.change_pct);
          return `<tr data-ticker="${escapeHtml(sym)}" class="scanner-table-row scanner-table-row--ghost scanner-row-muted" data-watchlisted="false">
        <th scope="row">${tickerChipHtml(sym)}</th>
        <td>${escapeHtml("Suggested")}</td>
        <td>—</td>
        <td>${stratCell}</td>
        <td><div class="scanner-ghost-actions"><span>${escapeHtml(String(score))}/100 · ${escapeHtml(ch)}</span><button type="button" class="button secondary scanner-ghost-add-btn" data-scanner-ghost-add="${escapeHtml(sym)}">Add to watchlist</button></div></td>
      </tr>`;
        })
        .join("");
      feedEl.innerHTML = `<p class="scanner-feed-empty">${escapeHtml(
        "Your watchlist is empty—add symbols above or pick from the ideas below.",
      )}</p>`;
      syncHomeSkillUi();
      return;
    }
    rowsEl.innerHTML = `<tr class="scanner-table-row scanner-table-empty-row"><td colspan="5">Save one or more symbols in the watchlist field above to fill this table.</td></tr>`;
    feedEl.innerHTML = `<p class="scanner-feed-empty">Add symbols to your watchlist to see alerts here.</p>`;
    syncHomeSkillUi();
    return;
  }

  rowsEl.innerHTML = tableSymbols
    .map((sym) => {
      if (useFullMock) {
        const sid = stablePick(sym + minuteBucket, MOCK_DAY_STRATEGY_IDS);
        const label = MOCK_DAY_STRATEGY_LABEL_BY_ID[sid];
        const phase = stablePick(`${sym}-${minuteBucket}-p`, ["Listening", "Evaluating last bar", "Waiting bar close"]);
        const tf = stablePick(`${sym}-tf`, ["15m", "30m", "60m"]);
        const sigRoll = stablePick(`${sym}-${minuteBucket}-s`, [0, 1, 2, 3, 4]);
        const mockSig =
          ms.key !== "open"
            ? "—"
            : sigRoll === 0
              ? "Long · sample"
              : sigRoll === 1
                ? "Short · sample"
                : "No setup · sample";

        return `<tr${scannerTableRowAttrs(sym, slideList, watchlistSetUnified)}>
        <th scope="row">${tickerChipHtml(sym)}</th>
        <td>${ms.key === "open" ? `${escapeHtml(scanVerbMockOpen)} · ${escapeHtml(phase)}` : escapeHtml("Paused")}</td>
        <td>${escapeHtml(tf)}</td>
        <td><code>${escapeHtml(sid)}</code> · ${escapeHtml(label)}</td>
        <td>${escapeHtml(mockSig)}</td>
      </tr>`;
      }

      const sig = sigMap.get(sym);
      if (sig) {
        const score = signalScoreTotal(sig);
        const scoreLabel = score != null ? `${Math.round(score)}/100` : "—";
        const dir = sig.direction != null ? String(sig.direction) : "—";
        const msLab = sig.market_state_label != null ? String(sig.market_state_label).trim() : "";
        const regimeQ =
          typeof sig.regime_quality === "number" && Number.isFinite(sig.regime_quality)
            ? Math.round(sig.regime_quality)
            : null;
        const scanCell =
          ms.key === "open"
            ? escapeHtml(
                [scanVerbLiveOpen, msLab, regimeQ != null ? `regime Q ${regimeQ}` : ""].filter(Boolean).join(" · "),
              )
            : escapeHtml("Paused");
        const stratCell = formatScannerStrategyHtml(sig);
        const sigCell =
          globalThis.TbNarrative?.watchlistAlertSignalHtml?.(sig, scanPayload) ||
          `${escapeHtml(dir)} · conf ${escapeHtml(scoreLabel)}`;
        return `<tr${scannerTableRowAttrs(sym, slideList, watchlistSetUnified)}>
        <th scope="row">${tickerChipHtml(sym)}</th>
        <td>${scanCell}</td>
        <td>${escapeHtml("5m")}</td>
        <td>${stratCell}</td>
        <td>${sigCell}</td>
      </tr>`;
      }

      const idleScanCell = ms.key === "open" ? escapeHtml("Idle · listening") : escapeHtml("Paused");
      const idleSig = ms.key === "open" ? "No setup · idle" : "—";
      return `<tr${scannerTableRowAttrs(sym, slideList, watchlistSetUnified)}>
        <th scope="row">${tickerChipHtml(sym)}</th>
        <td>${idleScanCell}</td>
        <td>—</td>
        <td>—</td>
        <td>${escapeHtml(idleSig)}</td>
      </tr>`;
    })
    .join("");

  if (useFullMock) {
    const feedLines =
      ms.key !== "open"
        ? []
        : tableSymbols
            .filter((sym) => stablePick(`${sym}-feed-${minuteBucket}`, [0, 1, 2, 3]) === 0)
            .slice(0, 5)
            .map((sym) => {
              const sid = stablePick(sym + minuteBucket, MOCK_DAY_STRATEGY_IDS);
              const dir = stablePick(`${sym}-dir`, ["LONG", "SHORT"]);
              return `<article class="scanner-feed-card scanner-feed-card-clickable" data-ticker="${escapeHtml(sym)}" tabindex="0" role="button"><span class="scanner-feed-main">${tickerChipHtml(sym)} · ${escapeHtml(dir)} · <code>${escapeHtml(sid)}</code></span><span class="scanner-feed-note">${escapeHtml(
                MOCK_DAY_STRATEGY_LABEL_BY_ID[sid],
              )}</span><span class="scanner-feed-meta">sample</span></article>`;
            });

    feedEl.innerHTML =
      feedLines.length > 0
        ? `<p class="eyebrow scanner-feed-heading">Sample highlights</p>${feedLines.join("")}`
        : `<p class="scanner-feed-empty">Nothing to highlight right now—the session may be closed.</p>`;
    syncHomeSkillUi();
    return;
  }

  if (watchlistOnlySignals.length) {
    const top = [...watchlistOnlySignals]
      .sort((a, b) => (signalScoreTotal(b) ?? -1) - (signalScoreTotal(a) ?? -1))
      .slice(0, 8);
    const snapMeta = lastScanAt ? lastScanAt : "Latest snapshot";
    feedEl.innerHTML = `<p class="eyebrow scanner-feed-heading">Latest alerts</p>${top
      .map((s) => {
        const sym = normalizeHomeTickerToken(s.ticker);
        const dir = s.direction != null ? String(s.direction) : "—";
        const sc = signalScoreTotal(s);
        const scLabel = sc != null ? `${Math.round(sc)}/100` : "—";
        const rawSt = s.strategy != null ? String(s.strategy).trim() : "";
        const noteLine = rawSt ? escapeHtml(rawSt) : "—";
        const narrativeBlock = globalThis.TbNarrative?.feedCardInnerHtml?.(s, scanPayload) ?? "";
        const historyCta = globalThis.TbSymbolResearchBridge?.checkHistoryCtaHtml?.(sym, "check-history-cta--feed") ?? "";
        return `<article class="scanner-feed-card scanner-feed-card-clickable" data-ticker="${escapeHtml(sym)}" tabindex="0" role="button"><span class="scanner-feed-main">${tickerChipHtml(sym)} · ${escapeHtml(dir)} · conf ${escapeHtml(scLabel)}</span><span class="scanner-feed-note">${noteLine}</span>${narrativeBlock}<span class="scanner-feed-actions">${historyCta}</span><span class="scanner-feed-meta">${escapeHtml(snapMeta)}</span></article>`;
      })
      .join("")}`;
    globalThis.TbSymbolResearchBridge?.wireCheckHistoryButtons?.(feedEl);
    syncHomeSkillUi();
    return;
  }

  feedEl.innerHTML = `<p class="scanner-feed-empty">No alerts for your symbols in the last snapshot—run a scan or wait for the next pass.</p>`;
  syncHomeSkillUi();
}

async function runHomeScanNow() {
  if (!backendHealth.loaded || homeScanRunInFlight) return;
  hideHomeScanProgressStrip();
  homeScanRunInFlight = true;
  renderHomeScanner();
  try {
    const tickers = buildHomeScannerUniverse();
    const res = await fetch("/api/scan/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const detail = errBody.detail != null ? String(errBody.detail) : res.statusText;
      console.warn("Run scan failed:", detail);
    }
    await refreshFromBackend({ forceScanLatest: true });
  } catch (e) {
    console.warn(e);
    await refreshFromBackend({ forceScanLatest: true });
  } finally {
    homeScanRunInFlight = false;
    renderHomeScanner();
    maybeRenderHealth();
  }
}

function syncHomeWatchlistFauxPlaceholder() {
  const inputEl = q("#homeWatchlistInput");
  if (!inputEl) return;
  if (!String(inputEl.value).trim() && document.activeElement !== inputEl) {
    maybeStartHomeWatchlistTypewriter();
  } else {
    clearHomeWatchlistTypewriter();
  }
}

function clearHomeWatchlistInputDraft() {
  const input = q("#homeWatchlistInput");
  if (!input) return;
  input.value = "";
  syncHomeWatchlistFauxPlaceholder();
}

/** Mirrors stored watchlist into the field after explicit actions (save, reset, add from scanner, account reset); not used on initial page load. */
function applyHomeWatchlistInputFromStorage() {
  const inputEl = q("#homeWatchlistInput");
  if (!inputEl) return;
  const joined = readHomeWatchlistTickers().join(", ");
  inputEl.value = joined.slice(0, HOME_WATCHLIST_INPUT_MAX_CHARS);
  syncHomeWatchlistFauxPlaceholder();
}

function attachWatchlistInputGuards(inputEl) {
  inputEl.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key.length !== 1) return;
    const replaceSpan = (inputEl.selectionEnd ?? 0) - (inputEl.selectionStart ?? 0);
    if (replaceSpan > 0) return;
    if (inputEl.value.length < HOME_WATCHLIST_INPUT_MAX_CHARS) return;
    e.preventDefault();
    pulseWatchlistCardRedFlash();
  });

  inputEl.addEventListener("paste", (e) => {
    const pasted = e.clipboardData?.getData("text/plain") ?? "";
    const start = inputEl.selectionStart ?? 0;
    const end = inputEl.selectionEnd ?? 0;
    const cur = inputEl.value;
    const avail = HOME_WATCHLIST_INPUT_MAX_CHARS - cur.length + (end - start);
    if (pasted.length <= avail) return;
    e.preventDefault();
    pulseWatchlistCardRedFlash();
    if (avail <= 0) return;
    const chunk = pasted.slice(0, avail);
    inputEl.value = cur.slice(0, start) + chunk + cur.slice(end);
    inputEl.selectionStart = inputEl.selectionEnd = start + chunk.length;
    syncHomeWatchlistFauxPlaceholder();
    inputEl.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function initHomeWatchlistControls() {
  const inputEl = q("#homeWatchlistInput");
  const saveBtn = q("#homeWatchlistSaveBtn");
  const runBtn = q("#homeRunScanBtn");
  if (inputEl) {
    inputEl.maxLength = HOME_WATCHLIST_INPUT_MAX_CHARS;
    clearHomeWatchlistInputDraft();
    inputEl.setAttribute("readonly", "");
    inputEl.addEventListener(
      "focus",
      () => {
        inputEl.removeAttribute("readonly");
      },
      { once: true },
    );
    inputEl.addEventListener("input", syncHomeWatchlistFauxPlaceholder);
    inputEl.addEventListener("focus", syncHomeWatchlistFauxPlaceholder);
    inputEl.addEventListener("blur", syncHomeWatchlistFauxPlaceholder);
    attachWatchlistInputGuards(inputEl);
    window.requestAnimationFrame(() => clearHomeWatchlistInputDraft());
    window.setTimeout(() => clearHomeWatchlistInputDraft(), 150);
  }
  syncHomeWatchlistFauxPlaceholder();
  if (saveBtn && inputEl) {
    saveBtn.addEventListener("click", () => {
      const input = q("#homeWatchlistInput");
      if (!input) return;
      const beforeSet = new Set(readHomeWatchlistTickers());
      const beforeSorted = [...beforeSet].sort();
      const parsed = parseWatchlistTokens(input.value);
      const desiredSorted = [...new Set(parsed)].sort();
      const unchangedDuplicateSave =
        desiredSorted.length > 0 &&
        desiredSorted.length === beforeSorted.length &&
        desiredSorted.every((t, i) => t === beforeSorted[i]);

      writeHomeWatchlistTickers(parsed);
      input.value = readHomeWatchlistTickers().join(", ").slice(0, HOME_WATCHLIST_INPUT_MAX_CHARS);
      const addedOrdered = readHomeWatchlistTickers().filter((t) => !beforeSet.has(t));
      syncHomeWatchlistFauxPlaceholder();
      renderHomeScanner(addedOrdered.length ? addedOrdered : null);
      if (unchangedDuplicateSave) {
        pulseWatchlistCardRedFlash();
      }
    });
  }
  const resetWlBtn = q("#homeWatchlistResetBtn");
  if (resetWlBtn && resetWlBtn.dataset.wiredRl !== "1") {
    resetWlBtn.dataset.wiredRl = "1";
    resetWlBtn.addEventListener("click", () => resetWatchlistStoredEmpty());
  }
  if (runBtn) {
    runBtn.addEventListener("click", () => {
      void runHomeScanNow();
    });
  }
}

async function refreshFromBackend(options = {}) {
  const forceScanLatest = options.forceScanLatest === true;
  const skipScanLatestPoll = scannerSseStreaming && !forceScanLatest;
  if (backendRefreshInFlight) return backendRefreshInFlight;
  backendRefreshInFlight = (async () => {
    const journalStatsPrior = backendHealth.journalStats;
    const journalEquityPrior = backendHealth.journalEquityCurve;
    const journalOpenTradesPrior = backendHealth.journalOpenTrades;
    try {
      const scanFetch = skipScanLatestPoll
        ? Promise.resolve(null)
        : fetch("/api/scan/latest");
      const [healthRes, scanRes, journalStatsRes, journalEquityRes, journalOpenRes] = await Promise.all([
        fetch("/api/health"),
        scanFetch,
        fetch("/api/journal/stats"),
        fetch("/api/journal/equity"),
        fetch("/api/journal/trades/open"),
      ]);
      await fetchMarketSession();
      if (!healthRes.ok) throw new Error("bad status");
      if (scanRes != null && !scanRes.ok) throw new Error("bad status");
      const health = await healthRes.json();
      backendHealth.loaded = true;
      backendHealth.health = health;
      if (scanRes != null) {
        backendHealth.scan = await scanRes.json();
      }
      if (journalStatsRes.ok) {
        backendHealth.journalStats = await journalStatsRes.json();
      } else {
        backendHealth.journalStats = journalStatsPrior;
      }
      if (journalEquityRes.ok) {
        backendHealth.journalEquityCurve = await journalEquityRes.json();
      } else {
        backendHealth.journalEquityCurve = journalEquityPrior;
      }
      if (journalOpenRes.ok) {
        backendHealth.journalOpenTrades = await journalOpenRes.json();
      } else {
        backendHealth.journalOpenTrades = journalOpenTradesPrior;
      }
    } catch {
      backendHealth.loaded = false;
      backendHealth.health = null;
      backendHealth.scan = null;
      backendHealth.journalStats = journalStatsPrior;
      backendHealth.journalEquityCurve = journalEquityPrior;
      backendHealth.journalOpenTrades = journalOpenTradesPrior;
    } finally {
      backendRefreshInFlight = null;
      renderHomeDashboardPanels();
      renderJournalAside();
      renderJournalOpenTrades();
      renderHomeScanner();
      maybeRenderHealth();
    }
  })();
  return backendRefreshInFlight;
}

function renderMarket() {
  const ms = marketSessionUi();
  const panel = q("#marketPanel");
  if (panel) panel.className = `market-panel market-panel-hero ${ms.key}`;
  const titleEl = q("#marketTitle");
  if (titleEl) titleEl.textContent = ms.title;
  const countdownEl = q("#marketCountdown");
  if (countdownEl) countdownEl.textContent = ms.countdownPhrase;
  const messageEl = q("#marketMessage");
  if (messageEl) messageEl.textContent = ms.message;
  const etEl = q("#etTime");
  if (etEl) {
    etEl.textContent = ms.localTz ? `${ms.localClock} ${ms.localTz}` : ms.localClock;
  }
  const pill = q("#topMarketPill");
  if (pill) {
    pill.className = `market-pill ${ms.key}`;
    pill.textContent = ms.pillShort;
  }
  renderMarketSessionHint();
  renderHomeScanner();
}

function msUntilNextUtcMinute() {
  return 60000 - (Date.now() % 60000);
}

function startHomeMarketClock() {
  void fetchMarketSession().then(() => {
    renderMarket();
  });
  const tick = () => {
    renderMarket();
  };
  tick();
  setTimeout(() => {
    tick();
    setInterval(tick, 60000);
  }, msUntilNextUtcMinute());
}

function statusBadge(status) {
  const key = status === "ok" ? "ok" : status === "off" ? "off" : "wait";
  const label = status === "ok" ? "Working" : status === "off" ? "Not connected" : "Standby";
  return `<span class="status-badge ${key}">${label}</span>`;
}

function maybeRenderHealth() {
  if (state.page !== "health") return;
  renderHealth();
}

function renderHealth() {
  const grid = q("#healthGrid");
  if (!grid) return;
  const ms = marketSessionUi();
  const bh = backendHealth.health;
  const apiOk = backendHealth.loaded && bh && bh.status === "ok";
  const schedOk = apiOk && bh.scheduler_running;
  const scanMeta = backendHealth.scan;
  const lastScan =
    (bh && bh.last_scan_at != null && String(bh.last_scan_at)) ||
    (scanMeta && scanMeta.last_scan_at != null && String(scanMeta.last_scan_at)) ||
    "never";
  const uptimeSeconds = bh && typeof bh.uptime_seconds === "number" ? bh.uptime_seconds : null;
  const uptimeLabel =
    uptimeSeconds != null && Number.isFinite(uptimeSeconds) ? `${uptimeSeconds.toFixed(1)}s` : "—";
  const tickerCached = bh && bh.ticker_count != null ? String(bh.ticker_count) : "—";
  const dataMarketOpenLabel =
    bh && typeof bh.market_open === "boolean" ? (bh.market_open ? "open" : "closed") : "—";
  const regimeLabel =
    bh && bh.regime != null && bh.regime !== "" ? String(bh.regime) : "—";
  const regimeQual =
    bh && bh.regime_quality != null ? String(bh.regime_quality) : "—";
  const tapeLabel =
    bh && bh.market_state_label != null ? String(bh.market_state_label) : "—";
  const tapeCode =
    bh && bh.market_state != null && bh.market_state !== "" ? String(bh.market_state) : "—";
  const breakerOn = !!(bh && bh.circuit_breaker_active);
  const resumeEt =
    bh && bh.circuit_resume_time != null && bh.circuit_resume_time !== ""
      ? String(bh.circuit_resume_time)
      : "";

  const health = [
    ["Dashboard", "ok", "Page loaded successfully."],
    ["Market clock", "ok", ms.healthSummary],
    [
      "Scanner API",
      apiOk ? "ok" : "off",
      apiOk ? "The scanner service responded." : "Run the app locally so scans can load.",
    ],
    [
      "Scan scheduler",
      schedOk ? "ok" : apiOk ? "wait" : "off",
      schedOk
        ? `Automatic scans about every ${bh.scan_interval_seconds} seconds.`
        : apiOk
          ? "Scheduler status isn’t available yet."
          : "Unavailable until the scanner API is connected.",
    ],
    [
      "Latest snapshot",
      scanMeta && scanMeta.signals && scanMeta.signals.length ? "ok" : "wait",
      `Last scan: ${lastScan}${scanMeta && scanMeta.last_error ? ` — error: ${scanMeta.last_error}` : ""}`,
    ],
    [
      "Backend metrics",
      apiOk ? "ok" : "off",
      `Uptime ${uptimeLabel} · cached tickers ${tickerCached} · data market ${dataMarketOpenLabel}`,
    ],
    [
      "Regime & tape",
      apiOk ? "ok" : "off",
      `Regime ${regimeLabel} (${regimeQual}) · state ${tapeLabel} (${tapeCode})`,
    ],
    [
      "Circuit breaker",
      apiOk ? (breakerOn ? "wait" : "ok") : "off",
      breakerOn
        ? `Active${resumeEt ? ` · resume ${resumeEt}` : ""}`
        : apiOk
          ? "Not active."
          : "Unavailable until the scanner API is connected.",
    ],
    [
      "Market data",
      apiOk ? "wait" : "off",
      apiOk ? "Backtests use your configured price feeds when you run them." : "Connect the scanner API for full status.",
    ],
  ];

  grid.innerHTML = health
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


function formatPct(x) {
  if (typeof x !== "number" || Number.isNaN(x)) return "—";
  return `${x >= 0 ? "+" : ""}${x.toFixed(2)}%`;
}

async function renderBacktestResults(payload) {
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

  const chartMod = await loadBacktestChartModule();
  const sparkHtml = chartMod.equitySparklineHtml(trades, escapeHtml);

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

  let modeLine = "";
  if (payload.trading_mode === "day_trading") {
    const s = payload.day_strategy_id ? btDayStrategyHuman(payload.day_strategy_id) : "";
    modeLine = `<p class="backtest-muted">${escapeHtml(btTradingModeHuman("day_trading"))}${
      s ? ` · ${escapeHtml(s)}` : ""
    }</p>`;
  } else if (payload.trading_mode === "quant") {
    const s = payload.quant_strategy_id ? btQuantStrategyHuman(payload.quant_strategy_id) : "";
    modeLine = `<p class="backtest-muted">${escapeHtml(btTradingModeHuman("quant"))}${
      s ? ` · ${escapeHtml(s)}` : ""
    }</p>`;
  }

  el.innerHTML = `
    <article class="health-card">
      <p class="eyebrow">Summary</p>
      ${modeLine}
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
      <p class="eyebrow" style="margin-top:14px">Return curve</p>
      ${sparkHtml}
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

/** Cleared while a backtest request is in flight (server wait phase). */
let btProgressWaitTimer = null;

function clearBtProgressWaitTimer() {
  if (btProgressWaitTimer != null) {
    clearInterval(btProgressWaitTimer);
    btProgressWaitTimer = null;
  }
}

function setBacktestProgress(percent, message, isError = false) {
  const wrap = q("#btProgressWrap");
  const fill = q("#btProgressFill");
  const track = q("#btProgressTrack");
  const label = q("#btProgressLabel");
  if (!wrap || !fill || !track || !label) return;
  const pct = Math.max(0, Math.min(100, percent));
  wrap.hidden = false;
  fill.style.width = `${pct}%`;
  track.setAttribute("aria-valuenow", String(Math.round(pct)));
  label.textContent = message;
  wrap.classList.toggle("bt-progress-error", Boolean(isError));
}

function hideBacktestProgress() {
  const wrap = q("#btProgressWrap");
  if (wrap) {
    wrap.hidden = true;
    wrap.classList.remove("bt-progress-error");
  }
  const fill = q("#btProgressFill");
  if (fill) fill.style.width = "0%";
  const track = q("#btProgressTrack");
  if (track) track.setAttribute("aria-valuenow", "0");
}

function openBacktestSettingsDialog() {
  const dialog = q("#btSettingsDialog");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const tickerKey = normalizeBtTicker(q("#btTicker")?.value ?? "");
  const map = readBtSettingsByTickerMap();
  if (tickerKey && map[tickerKey]) {
    applyBtSettingsToForm(map[tickerKey]);
  }
  dialog.showModal();
  const modeEl = q("#btTradingMode");
  if (modeEl) modeEl.focus();
}

function initBacktestSettingsDialog() {
  const dialog = q("#btSettingsDialog");
  const continueBtn = q("#btContinue");
  const closeBtn = q("#btDialogClose");
  const runBtn = q("#btModalRun");
  const resetBtn = q("#btModalReset");
  if (!dialog || !continueBtn || !closeBtn) return;

  continueBtn.addEventListener("click", () => {
    openBacktestSettingsDialog();
  });

  closeBtn.addEventListener("click", () => {
    if (typeof dialog.close === "function") dialog.close();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && typeof dialog.close === "function") dialog.close();
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      const tickerKey = normalizeBtTicker(q("#btTicker")?.value ?? "");
      const map = readBtSettingsByTickerMap();
      const saved = tickerKey ? map[tickerKey] : null;
      const statusEl = q("#backtestStatus");
      if (!saved) {
        if (statusEl) statusEl.textContent = "No saved settings for this ticker yet.";
        return;
      }
      applyBtSettingsToForm(saved);
      saveBtSettingsForTickerKey(tickerKey);
      if (statusEl) statusEl.textContent = "";
    });
  }

  if (runBtn) {
    runBtn.addEventListener("click", () => submitBacktest().catch(console.error));
  }
}

async function submitBacktest() {
  const statusEl = q("#backtestStatus");
  const dialogEl = q("#btSettingsDialog");
  const tickerInput = q("#btTicker");
  const ticker = normalizeBtTicker(tickerInput?.value ?? "");
  clearBtProgressWaitTimer();
  if (dialogEl && dialogEl.open && typeof dialogEl.close === "function") {
    dialogEl.close();
  }
  if (!ticker) {
    statusEl.textContent = "Enter a ticker.";
    hideBacktestProgress();
    return;
  }
  const modalRun = q("#btModalRun");
  if (modalRun) modalRun.disabled = true;
  statusEl.textContent = "";
  void renderBacktestResults(null);
  setBacktestProgress(6, "Preparing request…");
  try {
    const mode = q("#btTradingMode").value;
    const payload = {
      ticker,
      period: q("#btPeriod").value,
      interval: q("#btInterval").value,
      trading_mode: mode,
    };
    if (mode === "day_trading") {
      payload.day_strategy_id = q("#btDayStrategy").value;
    } else {
      payload.quant_strategy_id = q("#btQuantStrategy").value;
    }
    saveBtSettingsForTickerKey(ticker);
    setBacktestProgress(18, "Sending request to server…");
    let waitPct = 28;
    setBacktestProgress(waitPct, "Waiting for server (fetching data & running engine)…");
    const reduceMotion = motionDisabled();
    const waitStep = reduceMotion ? 6 : 3;
    const waitMs = reduceMotion ? motionDurationMs(1100) || 1100 : motionDurationMs(550) || 550;
    btProgressWaitTimer = setInterval(() => {
      waitPct = Math.min(waitPct + waitStep, 82);
      setBacktestProgress(waitPct, "Waiting for server (fetching data & running engine)…");
    }, waitMs);

    const res = await fetch("/api/quant/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    clearBtProgressWaitTimer();

    setBacktestProgress(Math.max(waitPct, 86), "Reading response…");
    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail = body.detail;
      const msg = typeof detail === "string" ? detail : JSON.stringify(detail || body);
      setBacktestProgress(100, "Backtest failed.", true);
      statusEl.textContent = `Error (${res.status}): ${msg}`;
      return;
    }
    setBacktestProgress(96, "Rendering results…");
    statusEl.textContent = `Completed ${ticker} · ${body.result?.num_trades ?? 0} trades.`;
    const merged = { ...body, ...payload };
    btLastSuccessfulPayload = merged;
    btRunPanelDetailOpen = false;
    const historyId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    btExpandedHistoryId = historyId;
    pushBtHistoryEntry({
      id: historyId,
      ticker,
      trading_mode: merged.trading_mode ?? mode,
      day_strategy_id: merged.day_strategy_id ?? (mode === "day_trading" ? payload.day_strategy_id : null),
      quant_strategy_id:
        merged.quant_strategy_id ?? (mode === "quant" ? payload.quant_strategy_id : null),
      interval: merged.interval ?? payload.interval,
      period: merged.period ?? payload.period,
      ranAt: Date.now(),
      total_return: typeof merged.result?.total_return === "number" ? merged.result.total_return : null,
      snapshot: merged,
    });
    saveBtSettingsForTickerKey(ticker);
    await renderBacktestResults(merged);
    void refreshWfoValidationPanel();
    navigateBacktestResultHash();
    syncBacktestRouteUi();
    setBacktestProgress(100, "Complete.");
  } catch (e) {
    clearBtProgressWaitTimer();
    const errMsg = e && e.message ? e.message : String(e);
    setBacktestProgress(100, "Request failed.", true);
    statusEl.textContent = `Request failed: ${errMsg}`;
  } finally {
    clearBtProgressWaitTimer();
    if (modalRun) modalRun.disabled = false;
  }
}

function setPage(page, options = {}) {
  const fromHash = options.fromHash === true;
  const prevPage = state.page;
  if (prevPage === "symbol" && page !== "symbol") {
    stopStockBarsPolling();
    teardownStockChartUi();
    setStockHistoryRangePanelExpanded(false);
  }
  if (page === "symbol") {
    if (options.tickerSym) {
      state.stockTicker = normalizeHomeTickerToken(options.tickerSym);
    }
  } else {
    state.stockTicker = null;
  }
  state.page = page;
  if (prevPage !== page) {
    resetAppScrollPosition();
  }
  if (prevPage === "live" && page !== "live") {
    clearHomeWatchlistTypewriter();
  }
  if (prevPage === "backtest" && page !== "backtest") {
    clearBtTickerTypewriter();
    const biview = q(".bt-run-panel-biview");
    if (biview) biview.style.minHeight = "";
    const tabBody = q("#btTabBody");
    const fullPage = q("#btHistoryFullPage");
    const runPanel = q("#btRunPanel");
    const mainColumn = q("#btMainColumn");
    if (tabBody) tabBody.hidden = false;
    if (fullPage) fullPage.hidden = true;
    if (runPanel) runPanel.hidden = false;
    if (mainColumn) mainColumn.classList.remove("bt-main-column--results-open");
  }
  qa(".page").forEach((section) => {
    section.classList.toggle("active", section.id === page);
  });
  qa(".nav-button").forEach((button) => {
    const sectionId = button.dataset.section;
    const isActive = sectionId === page || (page === "symbol" && sectionId === "live");
    button.classList.toggle("active", isActive);
    if (isActive) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  if (page === "backtest") {
    applyBacktestSymbolPrefillFromBridge();
    syncBacktestRouteUi();
    updateQuantStrategyWfoFootnote();
    void refreshWfoValidationPanel();
    globalThis.TbSymbolResearchBridge?.onBacktestPageOpened?.();
  }
  if (page === "health" && prevPage !== "health") {
    void refreshFromBackend().then(() => {
      maybeRenderHealth();
    });
  }
  if (page === "journal") {
    initJournalTradeForm();
    prefillJournalTickerFromContext();
    renderJournalAside();
    renderJournalOpenTrades();
    void refreshJournal();
  }
  if (page === "live") {
    renderMarket();
    syncHomeWatchlistFauxPlaceholder();
  }
  if (page === "symbol" && state.stockTicker) {
    void refreshStockDesk(state.stockTicker);
  }
  syncHashToPage(page, fromHash);
  syncMainTopHeader();
}

async function init() {
  initThemeToggle();
  globalThis.TbSkillMode?.init?.();
  globalThis.TbMotion?.init?.();
  globalThis.TbFontSize?.init?.();
  initLayoutWideListener();
  initHomeWatchlistControls();
  await loadDayStrategies();
  await loadQuantStrategies();
  renderBtDayStrategyOptions();
  syncBacktestModeUi();
  const btMode = q("#btTradingMode");
  if (btMode) {
    btMode.addEventListener("change", syncBacktestModeUi);
    btMode.addEventListener("change", updateQuantStrategyWfoFootnote);
  }
  initBacktestSettingsDialog();
  initHashRouting();
  globalThis.TbSymbolResearchBridge?.init?.();
  window.addEventListener("hashchange", () => {
    const h = normalizeAppHash();
    if (state.page === "backtest" && (h === "backtest" || h.startsWith("backtest"))) {
      applyBacktestSymbolPrefillFromBridge();
      globalThis.TbSymbolResearchBridge?.onBacktestPageOpened?.();
    }
  });
  wireBtSettingsPersistence();
  initBtRunPanelBiviewResizeObserver();
  initStockDeskControlsOnce();
  const resetAccountTrigger = q("#resetAccountBtn");
  if (resetAccountTrigger && resetAccountTrigger.dataset.wiredResetAcct !== "1") {
    resetAccountTrigger.dataset.wiredResetAcct = "1";
    resetAccountTrigger.addEventListener("click", confirmResetLocalTradingBotAccount);
  }
  const sideNoteStatusBtn = q("#sideNoteStatusBtn");
  if (sideNoteStatusBtn && sideNoteStatusBtn.dataset.wiredSideNoteHealth !== "1") {
    sideNoteStatusBtn.dataset.wiredSideNoteHealth = "1";
    sideNoteStatusBtn.addEventListener("click", () => {
      playSideNoteDotClickSpin(sideNoteStatusBtn);
      setPage("health");
    });
  }
  const historyFullBack = q("#btHistoryFullBack");
  if (historyFullBack) historyFullBack.addEventListener("click", () => navigateBacktestSetupHash());
  const btRunExpand = q("#btRunPanelExpand");
  if (btRunExpand) {
    btRunExpand.addEventListener("click", () => {
      btRunPanelDetailOpen = true;
      updateBtRunPanelLayout();
    });
  }
  const btRunCollapse = q("#btRunPanelCollapse");
  if (btRunCollapse) {
    btRunCollapse.addEventListener("click", () => {
      btRunPanelDetailOpen = false;
      updateBtRunPanelLayout();
    });
  }
  const btRunQuick = q("#btRunPanelQuickConfigure");
  if (btRunQuick) {
    btRunQuick.addEventListener("click", () => {
      openBacktestSettingsDialog();
    });
  }
  initJournalTradeForm();
  initSidebarNavDragSort();
  landingPageFromHash();
  refreshBtQuickCardSummary();
  startHomeMarketClock();
  attachScannerInteractions();
  attachSuggestedTradesPanelInteractions();
  attachTickerChipInteractions();
  attachScannerFeedInteractions();
  startScannerEventSource();
  await refreshFromBackend();
  maybeRenderHealth();

  qa("[data-section]").forEach((button) => {
    button.addEventListener("click", (event) => {
      const inReorderStrip = Boolean(button.closest("#navReorderList"));
      if (inReorderStrip && performance.now() < sidebarNavDragSuppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const section = button.dataset.section;
      if (section === "backtest" && state.page === "backtest") {
        btRunPanelDetailOpen = true;
        const h = normalizeAppHash();
        if (h !== "backtest") {
          setAppHash("#backtest");
        }
        syncBacktestRouteUi();
        updateBtRunPanelLayout();
        syncBtHistoryRowExpandedState();
        return;
      }
      setPage(section);
    });
  });

  const refreshStatusBtn = q("#refreshStatus");
  if (refreshStatusBtn) {
    refreshStatusBtn.addEventListener("click", async () => {
      playRefreshStatusIconSpin(refreshStatusBtn);
      renderMarket();
      await refreshFromBackend({ forceScanLatest: true });
      maybeRenderHealth();
    });
  }

  const scheduleBackendPolling = () => {
    const delayMs = scannerSseStreaming ? POLL_SLOW_MS_WHEN_SSE : POLL_INTERVAL_MS;
    window.setTimeout(async () => {
      await refreshFromBackend();
      maybeRenderHealth();
      scheduleBackendPolling();
    }, delayMs);
  };
  scheduleBackendPolling();
}

function initGradientMouseTrack() {
  /** Angular acceleration scale toward target (rad/frame, scaled by tensionMult). Lower = slower, less snappy. */
  const TENSION_HOVER = 0.011;
  /** Pull back toward default angle when not hovering. */
  const TENSION_RESTORE = 0.009;
  /** Angular velocity decay per frame (0..1). Higher = heavier / less twitchy. */
  const FRICTION = 0.93;
  /** Max |angular velocity| per frame (rad) — soft limit on how fast the gradient can spin. */
  const MAX_VEL_RAD = 0.045;
  const GRADIENT_DEBUG_LOG_MS = 120;
  const GRADIENT_STOPS_LIGHT =
    "var(--mp-closed-bg0) 0%," +
    "color-mix(in srgb, var(--mp-closed-bg1) 48%, var(--mp-closed-bg0) 52%) 45%," +
    "var(--mp-closed-bg1) 100%";
  const GRADIENT_STOPS_LIGHT_RESET =
    "var(--side-reset-bg0) 0%," +
    "color-mix(in srgb, var(--side-reset-bg1) 48%, var(--side-reset-bg0) 52%) 45%," +
    "var(--side-reset-bg1) 100%";
  const GRADIENT_STOPS_LIGHT_OPEN =
    "var(--mp-open-bg0) 0%," +
    "color-mix(in srgb, var(--mp-open-bg1) 48%, var(--mp-open-bg0) 52%) 45%," +
    "var(--mp-open-bg1) 100%";
  const GRADIENT_STOPS_LIGHT_PRE =
    "var(--mp-pre-bg0) 0%," +
    "color-mix(in srgb, var(--mp-pre-bg1) 42%, var(--mp-pre-bg0) 58%) 45%," +
    "var(--mp-pre-bg1) 100%";
  const GRADIENT_STOPS_DARK_RESET =
    "var(--reset-purple-bg0) 0%," +
    "color-mix(in srgb, var(--reset-purple-bg1) 42%, var(--reset-purple-bg0) 58%) 45%," +
    "var(--reset-purple-bg1) 100%";

  function readGradientDebugEnabled() {
    try {
      if (typeof window === "undefined" || !window.location) return false;
      const params = new URLSearchParams(window.location.search);
      return params.get("debugGradient") === "1";
    } catch (_) {
      return false;
    }
  }

  const gradientDebug = readGradientDebugEnabled();

  /** Shortest signed angle from `fromRad` to `toRad` in [-π, π]. Stable vs Cartesian spring flips. */
  function shortestAngleDeltaRad(fromRad, toRad) {
    return Math.atan2(Math.sin(toRad - fromRad), Math.cos(toRad - fromRad));
  }

  function displayDegFromRad(angleRad) {
    const deg = (angleRad * 180) / Math.PI;
    return ((deg % 360) + 360) % 360;
  }

  function isLight() {
    return document.documentElement.getAttribute("data-theme") !== "dark";
  }

  function wire(el, defaultAngle, opts) {
    const lightOnly = Boolean(opts && opts.lightOnly);
    const darkStops = opts && opts.darkStops;

    if (!el) return;

    const homeRad = (defaultAngle * Math.PI) / 180;
    let angleRad = homeRad;
    let velRad = 0;
    let targetRad = homeRad;
    let tensionMult = 0;
    let hovering = false;
    let rafId = null;
    let debugLastLogMs = 0;

    function stopsForTheme() {
      if (lightOnly) {
        if (!isLight()) return GRADIENT_STOPS_LIGHT;
        if (el.classList.contains("open")) return GRADIENT_STOPS_LIGHT_OPEN;
        if (el.classList.contains("pre")) return GRADIENT_STOPS_LIGHT_PRE;
        return GRADIENT_STOPS_LIGHT;
      }
      return isLight() ? (opts && opts.lightStops) || GRADIENT_STOPS_LIGHT : darkStops || GRADIENT_STOPS_LIGHT;
    }

    function tick() {
      if (lightOnly && !isLight()) {
        el.style.background = "";
        rafId = null;
        return;
      }

      if (hovering) {
        const delta = shortestAngleDeltaRad(angleRad, targetRad);
        velRad += delta * TENSION_HOVER * tensionMult;
      } else {
        const delta = shortestAngleDeltaRad(angleRad, homeRad);
        velRad += delta * TENSION_RESTORE;
      }

      velRad *= FRICTION;
      velRad = Math.max(-MAX_VEL_RAD, Math.min(MAX_VEL_RAD, velRad));
      angleRad += velRad;
      angleRad = Math.atan2(Math.sin(angleRad), Math.cos(angleRad));

      const displayDeg = displayDegFromRad(angleRad);
      el.style.background = `linear-gradient(${displayDeg.toFixed(1)}deg, ${stopsForTheme()})`;

      if (gradientDebug) {
        const nowLog = performance.now();
        if (nowLog - debugLastLogMs >= GRADIENT_DEBUG_LOG_MS) {
          debugLastLogMs = nowLog;
          console.log("[gradient]", el.id || "?", {
            displayDeg: +displayDeg.toFixed(1),
            angleDeg: +((angleRad * 180) / Math.PI).toFixed(2),
            velRad: +velRad.toFixed(4),
            targetDeg: +((targetRad * 180) / Math.PI).toFixed(2),
            tensionMult: +tensionMult.toFixed(2),
          });
        }
      }

      const distHome = Math.abs(shortestAngleDeltaRad(angleRad, homeRad));
      const settled = !hovering && distHome < 0.018 && Math.abs(velRad) < 0.004;

      if (settled) {
        rafId = null;
        angleRad = homeRad;
        velRad = 0;
        el.style.background = "";
      } else {
        rafId = requestAnimationFrame(tick);
      }
    }

    function ensureLoop() {
      if (!rafId) rafId = requestAnimationFrame(tick);
    }

    el.addEventListener("mousemove", (e) => {
      if (lightOnly && !isLight()) return;
      hovering = true;

      const r = el.getBoundingClientRect();
      const dxPx = e.clientX - (r.left + r.width / 2);
      const dyPx = e.clientY - (r.top + r.height / 2);
      const halfWidthPx = Math.max(r.width / 2, 1e-6);
      const halfHeightPx = Math.max(r.height / 2, 1e-6);
      const nx = dxPx / halfWidthPx;
      const ny = dyPx / halfHeightPx;
      tensionMult = Math.min(1, Math.hypot(nx, ny));

      const len = Math.hypot(nx, ny);
      if (len > 1e-5) {
        targetRad = Math.atan2(nx, -ny);
      }

      ensureLoop();
    });

    el.addEventListener("mouseenter", () => {
      angleRad = homeRad;
      velRad = 0;
      targetRad = homeRad;
      tensionMult = 0;
    });

    el.addEventListener("mouseleave", () => {
      hovering = false;
      tensionMult = 0;
      ensureLoop();
    });
  }

  wire(q("#resetAccountBtn"), 332, {
    darkStops: GRADIENT_STOPS_DARK_RESET,
    lightStops: GRADIENT_STOPS_LIGHT_RESET,
  });
  wire(q("#marketPanel"), 152, { lightOnly: true });
}

window.addEventListener("DOMContentLoaded", () => {
  init().catch(console.error);
  initGradientMouseTrack();
});
