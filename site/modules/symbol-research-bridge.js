/**
 * Scanner ↔ quant bridge: "Check history" CTA, backtest symbol prefill, in-app tutorial.
 * Product voice only — endpoint strings sanitized in JS before display.
 */
(function attachTbSymbolResearchBridge(global) {
  const TUTORIAL_PANEL_ID = "btTutorialPanel";

  const TUTORIAL_SECTION_COPY = Object.freeze({
    "Web dashboard": {
      title: "Your dashboard",
      body: "Run scans from Home, review alerts on your watchlist, and open Backtest when you want a longer history check on a symbol.",
    },
    "Signal pipeline (DTB)": {
      title: "How alerts are built",
      body: "Price history is scored against trend, momentum, volume, and risk rules. The scanner turns those scores into alerts with levels—not personalized advice.",
    },
    "Honest expectations": {
      title: "Research vs live alerts",
      body: "Backtests and walk-forward checks judge whether an idea held up over months or years. A strong live alert does not guarantee future results—use both views together.",
    },
  });

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normalizeSymbol(raw) {
    return String(raw ?? "")
      .trim()
      .toUpperCase()
      .replace(/[^A-Z0-9.-]/g, "")
      .slice(0, 12);
  }

  function parseBacktestSymbolFromHash() {
    const raw = global.location?.hash || "";
    const qIdx = raw.indexOf("?");
    if (qIdx === -1) return "";
    try {
      const params = new URLSearchParams(raw.slice(qIdx + 1));
      return normalizeSymbol(params.get("symbol") || params.get("ticker"));
    } catch {
      return "";
    }
  }

  function navigateToBacktestWithSymbol(symbol) {
    const sym = normalizeSymbol(symbol);
    if (!sym) return;
    global.location.hash = `#backtest?symbol=${encodeURIComponent(sym)}`;
  }

  function prefillBacktestTickerFromHash() {
    const sym = parseBacktestSymbolFromHash();
    const input = global.document?.querySelector?.("#btTicker");
    if (!sym || !input) return sym;
    input.value = sym;
    const minimal = global.document?.querySelector?.("#btRunMinimalTicker");
    if (minimal) minimal.textContent = sym;
    return sym;
  }

  function checkHistoryCtaHtml(symbol, extraClass) {
    const sym = normalizeSymbol(symbol);
    if (!sym) return "";
    const cls = extraClass ? ` ${extraClass}` : "";
    return `<button type="button" class="button secondary check-history-cta${cls}" data-check-history="${escapeHtml(sym)}">Check history</button>`;
  }

  function wireCheckHistoryButtons(root) {
    const scope = root && root.querySelectorAll ? root : global.document;
    if (!scope?.querySelectorAll) return;
    scope.querySelectorAll("[data-check-history]").forEach((btn) => {
      if (btn.dataset.wiredCheckHistory === "1") return;
      btn.dataset.wiredCheckHistory = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        navigateToBacktestWithSymbol(btn.getAttribute("data-check-history"));
      });
    });
  }

  function decorateFeedCard(card, symbol) {
    const sym = normalizeSymbol(symbol);
    if (!sym || !card || card.nodeType !== 1) return;
    if (card.querySelector(`[data-check-history="${sym}"]`)) return;
    const row = global.document.createElement("div");
    row.className = "symbol-research-cta-row";
    row.innerHTML = checkHistoryCtaHtml(sym, "symbol-research-cta");
    card.appendChild(row);
    wireCheckHistoryButtons(row);
  }

  function decorateScannerSurfaces(container) {
    if (!container) return;
    container.querySelectorAll(".scanner-feed-card[data-ticker]").forEach((card) => {
      decorateFeedCard(card, card.getAttribute("data-ticker"));
    });
    container.querySelectorAll(".suggested-trade-card").forEach((card) => {
      const btn = card.querySelector("[data-suggested-add]");
      const sym = btn
        ? btn.getAttribute("data-suggested-add")
        : card.querySelector(".suggested-trade-card-head b")?.textContent;
      decorateFeedCard(card, sym);
    });
  }

  function observeScannerSurfaces() {
    const doc = global.document;
    if (!doc) return;
    ["#homeScannerSignalFeed", "#homeSuggestedTradesBody"].forEach((sel) => {
      const el = doc.querySelector(sel);
      if (!el || el.dataset.symbolResearchObserved === "1") return;
      el.dataset.symbolResearchObserved = "1";
      decorateScannerSurfaces(el);
      const observer = new MutationObserver(() => decorateScannerSurfaces(el));
      observer.observe(el, { childList: true, subtree: true });
    });
  }

  function mountStockCta(symbol) {
    const slot = global.document?.querySelector?.("#stockSymbolResearchCta");
    if (!slot) return;
    const sym = normalizeSymbol(symbol);
    if (!sym) {
      slot.hidden = true;
      slot.innerHTML = "";
      return;
    }
    slot.hidden = false;
    slot.innerHTML = checkHistoryCtaHtml(sym, "symbol-research-cta");
    wireCheckHistoryButtons(slot);
  }

  function sanitizeTutorialSection(section) {
    const name = String(section?.name || "").trim();
    const mapped = TUTORIAL_SECTION_COPY[name];
    if (mapped) return mapped;
    const fallbackBody = String(section?.value || "")
      .replace(/\/api\/[^\s]+/gi, "the dashboard")
      .replace(/\bYahoo\b|\bPolygon\b|\bSSE\b/gi, "market data")
      .trim();
    return {
      title: name || "Guide",
      body: fallbackBody || "Tips for using scans and research together.",
    };
  }

  function wirePanelCollapsible(panel, triggerId, bodyId) {
    const trigger = panel.querySelector(`#${triggerId}`);
    const body = panel.querySelector(`#${bodyId}`);
    if (!trigger || !body || trigger.dataset.wiredCollapse === "1") return;
    trigger.dataset.wiredCollapse = "1";
    trigger.addEventListener("click", () => {
      const open = trigger.getAttribute("aria-expanded") !== "true";
      trigger.setAttribute("aria-expanded", open ? "true" : "false");
      panel.classList.toggle("dashboard-collapsible--expanded", open);
      if (open) body.removeAttribute("hidden");
      else body.setAttribute("hidden", "");
    });
  }

  function ensureTutorialPanelMounted() {
    const doc = global.document;
    if (!doc || doc.getElementById(TUTORIAL_PANEL_ID)) return;
    const mainColumn = doc.querySelector("#btMainColumn");
    if (!mainColumn) return;

    const panel = doc.createElement("article");
    panel.id = TUTORIAL_PANEL_ID;
    panel.className = "health-card bt-tutorial-panel dashboard-collapsible";
    panel.setAttribute("aria-label", "Research guide");
    panel.innerHTML = `
      <button
        type="button"
        class="dashboard-collapsible-trigger bt-panel-collapsible-trigger"
        id="btTutorialPanelTrigger"
        aria-expanded="false"
        aria-controls="btTutorialPanelBody"
      >
        <span class="dashboard-collapsible-chevron" aria-hidden="true"></span>
        <span class="dashboard-collapsible-title">Research guide</span>
      </button>
      <div id="btTutorialPanelBody" class="dashboard-collapsible-body bt-panel-collapsible-body" hidden>
        <h4 class="bt-tutorial-title">Using scans and backtests together</h4>
        <p class="backtest-muted bt-tutorial-lead">A short guide to how live alerts relate to longer history checks.</p>
        <div id="btTutorialBody" class="bt-tutorial-body" aria-live="polite">
          <p class="backtest-muted">Loading guide…</p>
        </div>
        <p class="backtest-muted bt-research-bridge-foot">
          <button type="button" class="button secondary" id="btOpenScannerSymbolBtn" hidden>Open symbol on scanner</button>
          <span id="btScannerCrossLinkHint" class="bt-scanner-cross-hint"></span>
        </p>
      </div>
    `;

    const workArea = mainColumn.querySelector("#btWorkArea");
    if (workArea) mainColumn.insertBefore(panel, workArea);
    else mainColumn.appendChild(panel);

    wirePanelCollapsible(panel, "btTutorialPanelTrigger", "btTutorialPanelBody");

    const openScannerBtn = panel.querySelector("#btOpenScannerSymbolBtn");
    if (openScannerBtn && openScannerBtn.dataset.wiredScannerCross !== "1") {
      openScannerBtn.dataset.wiredScannerCross = "1";
      openScannerBtn.addEventListener("click", () => {
        const sym = normalizeSymbol(openScannerBtn.getAttribute("data-scanner-symbol"));
        if (sym) global.location.hash = `#stock/${encodeURIComponent(sym)}`;
      });
    }
  }

  function renderTutorialSections(sections) {
    const list = Array.isArray(sections) ? sections : [];
    if (!list.length) {
      return `<p class="backtest-muted">Guide content is unavailable right now.</p>`;
    }
    return `<ul class="bt-tutorial-list">${list
      .map((sec) => {
        const copy = sanitizeTutorialSection(sec);
        return `<li class="bt-tutorial-item">
          <strong class="bt-tutorial-item-title">${escapeHtml(copy.title)}</strong>
          <p class="backtest-muted bt-tutorial-item-body">${escapeHtml(copy.body)}</p>
        </li>`;
      })
      .join("")}</ul>`;
  }

  async function refreshTutorialPanel(activeSymbol) {
    ensureTutorialPanelMounted();
    const bodyEl = global.document?.querySelector?.("#btTutorialBody");
    if (!bodyEl) return;

    try {
      const res = await fetch("/api/quant/tutorial");
      if (!res.ok) throw new Error("tutorial unavailable");
      const payload = await res.json().catch(() => ({}));
      bodyEl.innerHTML = renderTutorialSections(payload.sections);
    } catch {
      bodyEl.innerHTML = `<p class="backtest-muted">Could not load the guide. Start the server and reopen this tab.</p>`;
    }

    syncScannerCrossLink(activeSymbol);
  }

  function syncScannerCrossLink(activeSymbol) {
    const sym = normalizeSymbol(activeSymbol);
    const btn = global.document?.querySelector?.("#btOpenScannerSymbolBtn");
    const hint = global.document?.querySelector?.("#btScannerCrossLinkHint");
    if (!btn || !hint) return;
    if (!sym) {
      btn.hidden = true;
      hint.textContent = "";
      return;
    }
    btn.hidden = false;
    btn.setAttribute("data-scanner-symbol", sym);
    btn.textContent = `Open ${sym} on scanner`;
    hint.textContent = "Jump back to the live chart and playbook for this symbol.";
  }

  async function syncResearchBridgeScannerLink(activeSymbol) {
    const sym = normalizeSymbol(activeSymbol);
    try {
      const mod = await import("./research-bridge.js");
      mod.syncScannerCrossLink(global.document, { symbol: sym });
    } catch {
      /* panel may not be mounted */
    }
  }

  function mountStockCta(tickerSym) {
    const slot = global.document?.querySelector?.("#stockSymbolResearchCta");
    if (!slot) return;
    const sym = normalizeSymbol(tickerSym);
    if (!sym) {
      slot.hidden = true;
      slot.innerHTML = "";
      return;
    }
    slot.hidden = false;
    slot.innerHTML = checkHistoryCtaHtml(sym, "check-history-cta--stock");
    wireCheckHistoryButtons(slot);
  }

  function onBacktestPageOpened() {
    const sym =
      normalizeSymbol(global.document?.querySelector?.("#btTicker")?.value) ||
      parseBacktestSymbolFromHash();
    void refreshTutorialPanel(sym);
    void syncResearchBridgeScannerLink(sym);
  }

  function consumePrefillSymbol() {
    return parseBacktestSymbolFromHash();
  }

  function init() {
    wireCheckHistoryButtons(global.document);
    observeScannerSurfaces();
    global.addEventListener?.("hashchange", () => {
      const h = (global.location?.hash || "").replace(/^#\/?/, "");
      if (h === "backtest" || h.startsWith("backtest")) {
        prefillBacktestTickerFromHash();
      }
    });
  }

  global.TbSymbolResearchBridge = {
    normalizeSymbol,
    parseBacktestSymbolFromHash,
    consumePrefillSymbol,
    navigateToBacktestWithSymbol,
    prefillBacktestTickerFromHash,
    checkHistoryCtaHtml,
    wireCheckHistoryButtons,
    mountStockCta,
    ensureTutorialPanelMounted,
    refreshTutorialPanel,
    syncScannerCrossLink,
    mountStockCta,
    onBacktestPageOpened,
    init,
  };
})(typeof window !== "undefined" ? window : globalThis);
