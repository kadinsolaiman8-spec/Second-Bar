/**
 * Home scan context panels: opening-range breakouts, squeezes, sector rotation.
 * Reads from cached scan bundle (backendHealth.scan) — no extra network calls.
 */
(function attachTbScanContext(global) {
  const TOP_N = 6;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function getSkillMode() {
    if (global.TbSkillMode?.getMode) return global.TbSkillMode.getMode();
    return "standard";
  }

  function formatPct(val) {
    if (typeof val !== "number" || !Number.isFinite(val)) return "—";
    const sign = val >= 0 ? "+" : "";
    return `${sign}${val.toFixed(1)}%`;
  }

  function normalizeTicker(raw) {
    return String(raw ?? "")
      .trim()
      .toUpperCase()
      .replace(/[^A-Z0-9.-]/g, "")
      .slice(0, 12);
  }

  function tickerChipHtml(sym) {
    const safe = escapeHtml(sym);
    return `<span class="ticker-chip ticker-chip--clickable" data-ticker="${safe}" tabindex="0" role="button"><span class="ticker-chip-dollar" aria-hidden="true">$</span><b>${safe}</b></span>`;
  }

  function emptyState(message) {
    return `<p class="scanner-feed-empty scan-context-empty">${escapeHtml(message)}</p>`;
  }

  function dirClass(dirRaw) {
    const dir = String(dirRaw || "").toUpperCase();
    if (dir.includes("BULL") || dir === "LONG" || dir === "BUY") return "scan-context-dir scan-context-dir--bull";
    if (dir.includes("BEAR") || dir === "SHORT" || dir === "SELL") return "scan-context-dir scan-context-dir--bear";
    return "scan-context-dir";
  }

  function renderOrbPanel(orbList, mode) {
    const items = Array.isArray(orbList) ? orbList.slice(0, TOP_N) : [];
    if (!items.length) {
      return emptyState("No opening-range breakouts in this snapshot.");
    }
    const leadSym = normalizeTicker(items[0].ticker);
    const beginnerLead =
      mode === "beginner"
        ? `<p class="scan-context-summary">Opening range breakouts cleared the first session range. ${tickerChipHtml(leadSym)} is leading right now.</p>`
        : "";
    const rows = items
      .map((row) => {
        const sym = normalizeTicker(row.ticker);
        const dir = String(row.direction || "—");
        const px =
          typeof row.price === "number" && Number.isFinite(row.price) ? row.price.toFixed(2) : "—";
        return `<li class="scan-context-item"><span class="scan-context-item-main">${tickerChipHtml(sym)}<span class="${dirClass(dir)}">${escapeHtml(dir)}</span></span><span class="scan-context-item-side">${escapeHtml(px)}</span></li>`;
      })
      .join("");
    return `${beginnerLead}<ul class="dash-list scan-context-list">${rows}</ul>`;
  }

  function renderSqueezePanel(squeezeList, mode) {
    const items = Array.isArray(squeezeList) ? squeezeList.slice(0, TOP_N) : [];
    if (!items.length) {
      return emptyState("No tight-range symbols flagged right now.");
    }
    const leadSym = normalizeTicker(items[0].ticker);
    const beginnerLead =
      mode === "beginner"
        ? `<p class="scan-context-summary">Quiet ranges can precede larger moves. ${tickerChipHtml(leadSym)} is coiling—watch for a clean break.</p>`
        : "";
    const rows = items
      .map((row) => {
        const sym = normalizeTicker(row.ticker);
        const width =
          typeof row.bb_width === "number" && Number.isFinite(row.bb_width)
            ? `${row.bb_width.toFixed(1)}% width`
            : "tight range";
        return `<li class="scan-context-item"><span class="scan-context-item-main">${tickerChipHtml(sym)}</span><span class="scan-context-item-side">${escapeHtml(width)}</span></li>`;
      })
      .join("");
    return `${beginnerLead}<ul class="dash-list scan-context-list">${rows}</ul>`;
  }

  function renderSectorsPanel(sectorsObj, mode) {
    const sectors =
      sectorsObj && typeof sectorsObj === "object" && !Array.isArray(sectorsObj) ? sectorsObj : {};
    const entries = Object.entries(sectors)
      .map(([name, stats]) => {
        const st = stats && typeof stats === "object" ? stats : {};
        return {
          name,
          avgChange:
            typeof st.avg_change === "number" && Number.isFinite(st.avg_change) ? st.avg_change : null,
          avgScore:
            typeof st.avg_score === "number" && Number.isFinite(st.avg_score) ? st.avg_score : null,
        };
      })
      .sort((a, b) => (b.avgChange ?? -Infinity) - (a.avgChange ?? -Infinity))
      .slice(0, TOP_N);

    if (!entries.length) {
      return emptyState("Sector rotation fills in after a scan with enough symbols.");
    }

    const beginnerLead =
      mode === "beginner"
        ? `<p class="scan-context-summary"><strong>${escapeHtml(entries[0].name)}</strong> is leading (${escapeHtml(formatPct(entries[0].avgChange))} avg move). Align ideas with stronger groups when the scanner agrees.</p>`
        : "";

    const rows = entries
      .map((row) => {
        const sc = row.avgScore != null ? Math.round(row.avgScore) : "—";
        return `<li class="scan-context-item"><span class="scan-context-item-main"><b>${escapeHtml(row.name)}</b></span><span class="scan-context-item-side">${escapeHtml(formatPct(row.avgChange))} · score ${escapeHtml(String(sc))}</span></li>`;
      })
      .join("");
    return `${beginnerLead}<ul class="dash-list scan-context-list">${rows}</ul>`;
  }

  let lastScanPayload = null;

  function render(scanPayload) {
    const orbEl = global.document?.querySelector?.("#homeScanContextOrb");
    const squeezeEl = global.document?.querySelector?.("#homeScanContextSqueeze");
    const sectorsEl = global.document?.querySelector?.("#homeScanContextSectors");
    if (!orbEl && !squeezeEl && !sectorsEl) return;

    const mode = getSkillMode();
    const sp = scanPayload && typeof scanPayload === "object" ? scanPayload : null;
    lastScanPayload = sp;

    if (!sp) {
      const offline = emptyState("Market context appears after the server connects and you run a scan.");
      if (orbEl) orbEl.innerHTML = offline;
      if (squeezeEl) squeezeEl.innerHTML = offline;
      if (sectorsEl) sectorsEl.innerHTML = offline;
      return;
    }

    if (orbEl) orbEl.innerHTML = renderOrbPanel(sp.orb_breakouts, mode);
    if (squeezeEl) squeezeEl.innerHTML = renderSqueezePanel(sp.squeeze, mode);
    if (sectorsEl) sectorsEl.innerHTML = renderSectorsPanel(sp.sectors, mode);
  }

  function renderPanels(scanPayload, options) {
    const opts = options && typeof options === "object" ? options : {};
    if (opts.loaded === false || !scanPayload) {
      render(null);
      return;
    }
    render(scanPayload);
    global.TbSkillMode?.applyContextPanelCollapse?.();
  }

  function onSkillModeChanged() {
    render(lastScanPayload);
  }

  try {
    global.addEventListener("tb-skill-mode-changed", () => {
      onSkillModeChanged();
    });
  } catch {
    /* ignore */
  }

  global.TbScanContext = { render, renderPanels, onSkillModeChanged };
})(typeof window !== "undefined" ? window : globalThis);
