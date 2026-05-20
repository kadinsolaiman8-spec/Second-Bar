/**
 * Longer-horizon research copy for the Backtest / walk-forward panel.
 * Product voice only — no vendor or library names in user-facing strings.
 */

export const EXAMPLE_WFO_SLUG = "example-summary";
const PRIMARY_PVALUE_PASS = 0.05;

function ensureResearchBridgeStyles(root = document) {
  if (root.getElementById("btResearchBridgeStyles")) return;
  const style = root.createElement("style");
  style.id = "btResearchBridgeStyles";
  style.textContent = `
    .bt-wfo-panel { margin-top: 0; }
    .bt-wfo-panel-title { margin: 4px 0 8px; font-size: 1.05rem; }
    .bt-wfo-panel-lead { margin-bottom: 12px; }
    .bt-research-bridge-heading { margin: 0 0 10px; font-size: 0.95rem; }
    .bt-research-list { list-style: none; margin: 0 0 14px; padding: 0; display: flex; flex-direction: column; gap: 10px; }
    .bt-research-item { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--panel); }
    .bt-research-item--pass { border-color: color-mix(in srgb, var(--green, #2d8a4e) 55%, var(--line)); }
    .bt-research-item--fail { border-color: color-mix(in srgb, var(--red, #c44) 45%, var(--line)); }
    .bt-research-item-title { display: block; font-size: 0.82rem; margin-bottom: 4px; }
    .bt-research-item-body { margin: 0; font-size: 0.86rem; line-height: 1.45; }
    .bt-wfo-summary-dl { display: grid; grid-template-columns: minmax(9rem, 38%) 1fr; gap: 6px 12px; margin: 0 0 12px; font-size: 0.86rem; }
    .bt-wfo-dt { color: var(--muted); font-weight: 600; }
    .bt-wfo-json-pre { max-height: 220px; overflow: auto; font-size: 0.75rem; margin-top: 8px; }
    .bt-research-bridge-foot { margin-top: 10px; font-size: 0.8rem; }
    .bt-doc-ref { font-size: 0.78rem; }
  `;
  root.head.appendChild(style);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatPct(val) {
  if (typeof val !== "number" || !Number.isFinite(val)) return null;
  return `${val >= 0 ? "+" : ""}${val.toFixed(1)}%`;
}

function formatPvalue(val) {
  if (typeof val !== "number" || !Number.isFinite(val)) return null;
  if (val < 0.001) return "< 0.001";
  return val.toFixed(3);
}

function normalizeTickerSymbol(value) {
  return String(value || "")
    .toUpperCase()
    .trim();
}

function findScanRowForTicker(ticker, scanPayload) {
  const sym = normalizeTickerSymbol(ticker);
  if (!sym || !scanPayload || typeof scanPayload !== "object") return null;
  const pools = [
    ...(Array.isArray(scanPayload.best) ? scanPayload.best : []),
    ...(Array.isArray(scanPayload.suggested_watchlist) ? scanPayload.suggested_watchlist : []),
    ...(Array.isArray(scanPayload.signals) ? scanPayload.signals : []),
  ];
  return pools.find((row) => normalizeTickerSymbol(row?.ticker) === sym) ?? null;
}

function resolveAssistantForScanRow(row, scanPayload) {
  const narrative = typeof globalThis !== "undefined" ? globalThis.TbNarrative : null;
  if (typeof narrative?.resolveAssistantForRow === "function") {
    return narrative.resolveAssistantForRow(row, scanPayload);
  }
  if (row?.assistant && typeof row.assistant === "object") return row.assistant;
  const sym = normalizeTickerSymbol(row?.ticker);
  if (sym && scanPayload && typeof scanPayload === "object") {
    for (const pool of [scanPayload.best, scanPayload.suggested_watchlist]) {
      if (!Array.isArray(pool)) continue;
      const match = pool.find((entry) => normalizeTickerSymbol(entry?.ticker) === sym);
      if (match?.assistant && typeof match.assistant === "object") return match.assistant;
    }
  }
  if (typeof narrative?.resolveAssistant === "function") return narrative.resolveAssistant(row);
  return null;
}

function scanContextBullet(ticker, scanPayload) {
  const sym = normalizeTickerSymbol(ticker);
  if (!sym || !scanPayload) return null;
  const row = findScanRowForTicker(sym, scanPayload);
  if (!row) return null;
  const assistant = resolveAssistantForScanRow(row, scanPayload);
  const headline =
    assistant && typeof assistant.headline === "string" ? assistant.headline.trim() : "";
  if (!headline) return null;
  return {
    title: "Scanner context",
    body: `From your latest scan: ${headline}`,
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

/**
 * Mount walk-forward panel DOM under the backtest main column if missing.
 */
export function ensureWfoPanelMounted(root = document) {
  ensureResearchBridgeStyles(root);
  const mainColumn = root.querySelector("#btMainColumn");
  if (!mainColumn || root.querySelector("#btWfoPanel")) return;

  const panel = document.createElement("article");
  panel.id = "btWfoPanel";
  panel.className = "health-card bt-wfo-panel dashboard-collapsible";
  panel.setAttribute("aria-label", "Walk-forward validation");
  panel.innerHTML = `
    <button
      type="button"
      class="dashboard-collapsible-trigger bt-panel-collapsible-trigger"
      id="btWfoPanelTrigger"
      aria-expanded="false"
      aria-controls="btWfoPanelBody"
    >
      <span class="dashboard-collapsible-chevron" aria-hidden="true"></span>
      <span class="dashboard-collapsible-title">Longer horizons</span>
    </button>
    <div id="btWfoPanelBody" class="dashboard-collapsible-body bt-panel-collapsible-body" hidden>
      <h4 class="bt-wfo-panel-title">Walk-forward check</h4>
      <p class="backtest-muted bt-wfo-panel-lead">
        Saved validation runs help you judge robustness and drawdowns over months to years — separate from live scanner alerts.
      </p>
      <div id="btResearchBridge" class="bt-research-bridge" aria-live="polite"></div>
      <p id="btWfoEmpty" class="backtest-muted bt-wfo-empty" hidden></p>
      <dl id="btWfoSummaryDl" class="bt-wfo-summary-dl" hidden></dl>
      <details id="btWfoRawWrap" class="bt-wfo-raw-wrap" hidden>
        <summary class="backtest-muted">Technical export</summary>
        <pre id="btWfoJsonPre" class="bt-wfo-json-pre"></pre>
      </details>
      <p class="backtest-muted bt-research-bridge-foot">
        How we interpret edge and validation limits is documented in
        <code class="bt-doc-ref">docs/STRATEGY_AND_EDGE.md</code>
        in your project copy (not shown inside this dashboard).
        <span id="btResearchBridgeScannerWrap" class="bt-research-bridge-scanner-wrap" hidden>
          ·
          <button type="button" class="backtest-inline-link" id="btResearchBridgeScannerBtn" hidden>
            View symbol on scanner
          </button>
        </span>
      </p>
    </div>
  `;

  const workArea = mainColumn.querySelector("#btWorkArea");
  if (workArea) {
    mainColumn.insertBefore(panel, workArea);
  } else {
    mainColumn.appendChild(panel);
  }

  wirePanelCollapsible(panel, "btWfoPanelTrigger", "btWfoPanelBody");
}

function oosPrimaryVerdict(pvalue) {
  if (typeof pvalue !== "number" || !Number.isFinite(pvalue)) {
    return {
      label: "Out-of-sample primary check",
      detail:
        "No primary p-value on this export — treat the run as incomplete for go/no-go decisions.",
      tone: "neutral",
    };
  }
  if (pvalue <= PRIMARY_PVALUE_PASS) {
    return {
      label: "Out-of-sample primary check",
      detail: `Passed at the pre-registered ${PRIMARY_PVALUE_PASS * 100}% bar (p = ${formatPvalue(pvalue)}). Still not a promise of future results.`,
      tone: "pass",
    };
  }
  return {
    label: "Out-of-sample primary check",
    detail: `Did not pass at the ${PRIMARY_PVALUE_PASS * 100}% bar (p = ${formatPvalue(pvalue)}). Favor skepticism before sizing risk on this idea.`,
    tone: "fail",
  };
}

/**
 * Build plain-language bullets for the "What this means for you" block.
 */
function resolveScanHeadlineLine(meta = {}) {
  const symbol = meta.symbol || meta.ticker || meta.activeSymbol;
  const scanPayload = meta.scanPayload;
  if (!symbol || !scanPayload) return null;
  const resolver =
    typeof globalThis !== "undefined" && globalThis.TbNarrative?.scanHeadlineLine
      ? globalThis.TbNarrative.scanHeadlineLine.bind(globalThis.TbNarrative)
      : null;
  if (!resolver) return null;
  const line = resolver(String(symbol), scanPayload);
  return line && String(line).trim() ? String(line).trim() : null;
}

export function buildResearchNarrative(data, meta = {}) {
  const d = data && typeof data === "object" ? data : {};
  const isExample = Boolean(meta.isExample || d.is_example);
  const pvalue = d.stationary_bootstrap_pvalue;
  const verdict = oosPrimaryVerdict(pvalue);
  const scanLine = resolveScanHeadlineLine(meta);
  const dd = formatPct(d.max_drawdown_pct);
  const sharpe =
    typeof d.oos_sharpe_headline === "number" && Number.isFinite(d.oos_sharpe_headline)
      ? d.oos_sharpe_headline.toFixed(2)
      : null;

  const holding =
    typeof d.holding_period_note === "string" && d.holding_period_note.trim()
      ? d.holding_period_note.trim()
      : "These runs use longer bar sizes (daily or weekly) over multi-month to multi-year windows. They answer whether an idea held up historically — not what to trade in the next few minutes on the scanner.";

  const bullets = [];

  if (scanLine) {
    bullets.push({
      title: "Live scanner context",
      body: scanLine,
    });
  }

  if (isExample) {
    bullets.push({
      title: "Sample summary",
      body: "This is an illustrative export so the panel is useful before you import your own validation batch.",
    });
  }

  bullets.push({
    title: verdict.label,
    body: verdict.detail,
    tone: verdict.tone,
  });

  if (dd) {
    bullets.push({
      title: "Drawdown context",
      body: `Peak-to-trough loss in the combined out-of-sample window was about ${dd}. Compare that to the headline return story before you trust the idea at size.`,
    });
  } else {
    bullets.push({
      title: "Drawdown context",
      body: "If your export includes max drawdown, use it to see whether returns came with pain you could actually hold through.",
    });
  }

  if (sharpe != null) {
    bullets.push({
      title: "Risk-adjusted headline",
      body: `Combined out-of-sample Sharpe (headline) was ${sharpe}. It summarizes smoothness of returns, not whether live signals will match.`,
    });
  }

  bullets.push({
    title: "Holding period",
    body: holding,
  });

  const ticker = d.ticker ? String(d.ticker).toUpperCase() : meta.ticker || null;
  const folds = d.n_folds;
  if (ticker && typeof folds === "number") {
    bullets.push({
      title: "Scope",
      body: `${ticker} · ${folds} walk-forward fold${folds === 1 ? "" : "s"}${d.period ? ` · ${d.period} history` : ""}.`,
    });
  }

  return { bullets, isExample, verdict };
}

/**
 * Render summary definition list rows (labels + values).
 */
export function renderWfoSummaryRows(data, escapeFn) {
  const d = data && typeof data === "object" ? data : {};
  const esc = typeof escapeFn === "function" ? escapeFn : escapeHtml;

  function row(label, val) {
    let vDisp = "—";
    if (val != null && val !== "") {
      if (typeof val === "number" && Number.isFinite(val)) {
        vDisp = String(val);
      } else if (typeof val !== "number") {
        vDisp = esc(String(val));
      }
    }
    return `<div class="bt-wfo-dt">${esc(label)}</div><div class="bt-wfo-dd">${vDisp}</div>`;
  }

  return [
    row("Symbol", d.ticker),
    row("Engine preset", d.strategy),
    row("Optimized for", d.optimize_metric),
    row("Fold count", d.n_folds),
    row("Max drawdown (%)", d.max_drawdown_pct),
    row("Sharpe headline (combined OOS bars)", d.oos_sharpe_headline),
    row("Bootstrap p-value", d.stationary_bootstrap_pvalue),
    row("Bar permutation p-value", d.bar_permutation_pvalue),
    row("Exported at", d.timestamp_iso),
  ].join("");
}

export function renderResearchBridgeHtml(data, meta = {}) {
  const { bullets } = buildResearchNarrative(data, meta);
  const items = bullets
    .map((b) => {
      const toneClass =
        b.tone === "pass" ? " bt-research-item--pass" : b.tone === "fail" ? " bt-research-item--fail" : "";
      return `<li class="bt-research-item${toneClass}">
        <strong class="bt-research-item-title">${escapeHtml(b.title)}</strong>
        <p class="backtest-muted bt-research-item-body">${escapeHtml(b.body)}</p>
      </li>`;
    })
    .join("");

  return `
    <section class="bt-research-bridge-inner" aria-labelledby="btResearchBridgeHeading">
      <h5 id="btResearchBridgeHeading" class="bt-research-bridge-heading">What this means for you</h5>
      <ul class="bt-research-list">${items}</ul>
    </section>
  `;
}

function normalizeScannerSymbol(raw) {
  return String(raw ?? "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9._-]/g, "")
    .slice(0, 12);
}

export function syncScannerCrossLink(root, meta = {}) {
  ensureWfoPanelMounted(root);
  const wrap = root.querySelector("#btResearchBridgeScannerWrap");
  const btn = root.querySelector("#btResearchBridgeScannerBtn");
  if (!wrap || !btn) return;
  const sym = normalizeScannerSymbol(meta.symbol || meta.ticker);
  if (!sym) {
    wrap.hidden = true;
    btn.hidden = true;
    btn.onclick = null;
    return;
  }
  wrap.hidden = false;
  btn.hidden = false;
  btn.textContent = `Open ${sym} on the scanner`;
  btn.onclick = () => {
    if (typeof globalThis !== "undefined") {
      globalThis.location.hash = `#stock/${encodeURIComponent(sym)}`;
    }
  };
}

export function applyResearchBridge(root, data, meta = {}) {
  ensureWfoPanelMounted(root);
  const bridgeEl = root.querySelector("#btResearchBridge");
  if (!bridgeEl) return;
  bridgeEl.innerHTML = renderResearchBridgeHtml(data, meta);
  syncScannerCrossLink(root, meta);
}

export function setWfoPanelEmpty(root, message) {
  ensureWfoPanelMounted(root);
  const emptyEl = root.querySelector("#btWfoEmpty");
  const dlEl = root.querySelector("#btWfoSummaryDl");
  const rawWrap = root.querySelector("#btWfoRawWrap");
  const bridgeEl = root.querySelector("#btResearchBridge");
  if (emptyEl) {
    emptyEl.hidden = false;
    emptyEl.textContent = message;
  }
  if (dlEl) {
    dlEl.hidden = true;
    dlEl.innerHTML = "";
  }
  if (rawWrap) rawWrap.hidden = true;
  if (bridgeEl) bridgeEl.innerHTML = "";
  syncScannerCrossLink(root, {});
}

export function populateWfoPanel(root, wrapped, meta = {}) {
  ensureWfoPanelMounted(root);
  const d = wrapped && typeof wrapped.data === "object" ? wrapped.data : {};
  const slug = meta.slug ? String(meta.slug) : "";
  const isExample = slug === EXAMPLE_WFO_SLUG || Boolean(d.is_example);

  const emptyEl = root.querySelector("#btWfoEmpty");
  const dlEl = root.querySelector("#btWfoSummaryDl");
  const rawEl = root.querySelector("#btWfoJsonPre");
  const rawWrap = root.querySelector("#btWfoRawWrap");

  if (emptyEl) emptyEl.hidden = true;

  applyResearchBridge(root, d, { ...meta, isExample, slug });

  if (dlEl) {
    let html = renderWfoSummaryRows(d, escapeHtml);
    if (slug) {
      html += `<div class="bt-wfo-dt">Saved id</div><div class="bt-wfo-dd">${escapeHtml(slug)}</div>`;
    }
    dlEl.innerHTML = html;
    dlEl.hidden = false;
  }

  if (rawEl && rawWrap) {
    try {
      rawEl.textContent = JSON.stringify(d, null, 2);
      rawWrap.hidden = false;
    } catch {
      rawWrap.hidden = true;
    }
  }
}
