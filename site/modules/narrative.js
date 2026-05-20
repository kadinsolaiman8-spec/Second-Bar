/**
 * Scanner assistant playbook renderer — Headline → Why → Checklist → Levels.
 * Consumes API `assistant` objects; builds client playbooks from row fields when missing.
 */
(function attachTbNarrative(global) {
  const SKILL_STORAGE_KEY = "tb_skill_mode";
  const TABLE_HEADLINE_MAX = 72;
  const TABLE_HEADLINE_MAX_ADVANCED = 48;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function getSkillMode() {
    if (global.TbSkillMode?.getMode) return global.TbSkillMode.getMode();
    try {
      const raw = global.localStorage.getItem(SKILL_STORAGE_KEY);
      if (raw === "beginner" || raw === "advanced" || raw === "standard") return raw;
    } catch {
      /* ignore */
    }
    return "standard";
  }

  function normalizeTicker(raw) {
    return String(raw ?? "")
      .trim()
      .toUpperCase()
      .replace(/[^A-Z0-9.-]/g, "")
      .slice(0, 12);
  }

  function formatLevelPrice(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
    return value.toFixed(2);
  }

  function asStringList(value) {
    if (!value) return [];
    if (typeof value === "string") {
      const t = value.trim();
      return t ? [t] : [];
    }
    if (!Array.isArray(value)) return [];
    return value.map((x) => String(x).trim()).filter(Boolean);
  }

  function pickAssistant(source) {
    if (!source || typeof source !== "object") return null;
    if (source.assistant && typeof source.assistant === "object") return source.assistant;
    if (source.state && typeof source.state === "object" && source.state.assistant) {
      return source.state.assistant;
    }
    return null;
  }

  function findAssistantInScanPayload(ticker, scanPayload) {
    if (!scanPayload || typeof scanPayload !== "object") return null;
    const tk = normalizeTicker(ticker);
    if (!tk) return null;
    const pools = [
      ...(Array.isArray(scanPayload.best) ? scanPayload.best : []),
      ...(Array.isArray(scanPayload.suggested_watchlist) ? scanPayload.suggested_watchlist : []),
    ];
    for (const row of pools) {
      if (!row || normalizeTicker(row.ticker) !== tk) continue;
      const assistant = pickAssistant(row);
      if (assistant) return assistant;
    }
    return null;
  }

  function buildClientAssistantFromRow(row) {
    const r = row && typeof row === "object" ? row : {};
    const sd = r.score_data && typeof r.score_data === "object" ? r.score_data : {};
    const ticker = normalizeTicker(r.ticker) || "Symbol";
    const scoreHeadline = typeof sd.headline === "string" ? sd.headline.trim() : "";
    const explanation = typeof sd.explanation === "string" ? sd.explanation.trim() : "";
    const why = asStringList(r.conditions_met);
    if (explanation && explanation !== scoreHeadline && !why.includes(explanation)) {
      why.unshift(explanation);
    }
    if (!why.length) why.push("Scan is still building context for this symbol.");
    const dir = String(r.direction || "").trim();
    const headline =
      scoreHeadline ||
      (dir ? `${ticker}: ${dir.toLowerCase()} setup on watch` : `${ticker}: on watch`);
    const checklist = ["Wait for a clear setup before sizing a trade."];
    return {
      headline,
      why_bullets: why.slice(0, 4),
      action_checklist: checklist,
      levels: {
        entry: r.entry_price ?? null,
        stop: r.stop ?? null,
        target: r.tp1 ?? null,
        risk_reward: r.rr ?? sd.rr_ratio ?? null,
      },
      caution: "",
      skill_hints: { beginner: "", advanced: "" },
    };
  }

  function resolveAssistantForRow(row, scanPayload) {
    const direct = pickAssistant(row);
    if (direct) return direct;
    const fromScan = findAssistantInScanPayload(row?.ticker, scanPayload);
    if (fromScan) return fromScan;
    return buildClientAssistantFromRow(row);
  }

  function resolveAssistant(source, scanPayload) {
    if (scanPayload != null) {
      const row = source?.state && typeof source.state === "object" ? source.state : source;
      return resolveAssistantForRow(row, scanPayload);
    }
    return pickAssistant(source) || buildClientAssistantFromRow(source?.state || source);
  }

  function confidenceLabelFromFlag(flagRaw) {
    const flag = String(flagRaw || "").trim();
    if (flag === "🔥") return "High conviction";
    if (flag === "✅") return "Strong";
    if (flag === "⚠️") return "Caution";
    if (flag === "⚪") return "Below bar";
    return "Confidence";
  }

  function confidenceToneFromFlag(flagRaw) {
    const flag = String(flagRaw || "").trim();
    if (flag === "🔥") return "high";
    if (flag === "✅") return "strong";
    if (flag === "⚠️") return "caution";
    return "neutral";
  }

  function confidenceToneClass(flagRaw) {
    const flag = String(flagRaw || "").trim();
    if (flag === "🔥") return "scanner-confidence-band--high-conviction";
    if (flag === "✅") return "scanner-confidence-band--strong";
    if (flag === "⚠️") return "scanner-confidence-band--caution";
    return "";
  }

  function truncateHeadline(text, maxLen) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const cap = typeof maxLen === "number" ? maxLen : TABLE_HEADLINE_MAX;
    if (raw.length <= cap) return raw;
    return `${raw.slice(0, Math.max(0, cap - 1)).trim()}…`;
  }

  function confidenceBandHtml(row) {
    const r = row && typeof row === "object" ? row : {};
    const sd = r.score_data && typeof r.score_data === "object" ? r.score_data : {};
    const flag = String(sd.flag || "").trim();
    const label = confidenceLabelFromFlag(flag);
    const toneClass = confidenceToneClass(flag);
    const toneAttr = toneClass ? ` ${toneClass}` : "";
    return `<span class="scanner-confidence-band${toneAttr}">${escapeHtml(label)}</span>`;
  }

  function watchlistSignalCellHtml(row, scanPayload) {
    const assistant = resolveAssistantForRow(row, scanPayload);
    const mode = getSkillMode();
    const maxLen = mode === "advanced" ? TABLE_HEADLINE_MAX_ADVANCED : TABLE_HEADLINE_MAX;
    const headline = truncateHeadline(assistant.headline, maxLen);
    const band = confidenceBandHtml(row);
    const dir = String(row?.direction || "").trim();
    const headlineHtml = headline
      ? `<span class="scanner-alert-headline">${escapeHtml(headline)}</span>`
      : "";
    const dirHtml = dir ? `<span class="scanner-alert-dir">${escapeHtml(dir)}</span>` : "";
    return `<div class="scanner-alert-cell">${band}${headlineHtml}${dirHtml}</div>`;
  }

  function watchlistAlertSignalHtml(row, scanPayload) {
    return watchlistSignalCellHtml(row, scanPayload);
  }

  function levelsLineHtml(levels, compact) {
    const lv = levels && typeof levels === "object" ? levels : {};
    const bits = [];
    const ent = formatLevelPrice(lv.entry);
    const stp = formatLevelPrice(lv.stop);
    const tgt = formatLevelPrice(lv.target);
    const rr = typeof lv.risk_reward === "number" && Number.isFinite(lv.risk_reward) ? lv.risk_reward : null;
    if (ent != null) bits.push(`Entry ${ent}`);
    if (stp != null) bits.push(`Stop ${stp}`);
    if (tgt != null) bits.push(compact ? `T1 ${tgt}` : `Target ${tgt}`);
    if (rr != null && !compact) bits.push(`R:R ${rr.toFixed(2)}`);
    if (!bits.length) {
      return `<p class="narrative-levels narrative-levels--pending">Levels fill in on the next pass.</p>`;
    }
    return `<p class="narrative-levels">${escapeHtml(bits.join(" · "))}</p>`;
  }

  function listHtml(items, className) {
    const rows = asStringList(items);
    if (!rows.length) return "";
    return `<ul class="${className}">${rows.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`;
  }

  function renderChecklistHtml(items, maxItems) {
    const rows = asStringList(items);
    const slice = typeof maxItems === "number" ? rows.slice(0, maxItems) : rows;
    if (!slice.length) return "";
    return listHtml(slice, "narrative-checklist");
  }

  function renderWhyHtml(items, maxItems) {
    const rows = asStringList(items);
    const slice = typeof maxItems === "number" ? rows.slice(0, maxItems) : rows;
    if (!slice.length) return "";
    return listHtml(slice, "narrative-why");
  }

  function skillHintFooter(assistant) {
    const hints = assistant?.skill_hints;
    if (!hints || typeof hints !== "object") return "";
    const beginner = String(hints.beginner || "").trim();
    const advanced = String(hints.advanced || "").trim();
    const parts = [];
    if (beginner) {
      parts.push(
        `<p class="narrative-skill-hint narrative-hint-beginner">${escapeHtml(beginner)}</p>`,
      );
    }
    if (advanced) {
      parts.push(
        `<p class="narrative-skill-hint narrative-hint-advanced">${escapeHtml(advanced)}</p>`,
      );
    }
    return parts.join("");
  }

  function cautionHtml(assistant) {
    const text = String(assistant?.caution || "").trim();
    if (!text) return "";
    return `<p class="narrative-caution" role="note">${escapeHtml(text)}</p>`;
  }

  /**
   * Compact block for home feed cards: headline + one checklist line.
   */
  function feedCardInnerHtml(row, scanPayload) {
    const assistant = resolveAssistantForRow(row, scanPayload);
    const headline = String(assistant.headline || "").trim();
    const firstAction = asStringList(assistant.action_checklist)[0] || "";
    const headlineHtml = headline
      ? `<span class="narrative-feed-headline">${escapeHtml(headline)}</span>`
      : "";
    const actionHtml = firstAction
      ? `<span class="narrative-feed-action">${escapeHtml(firstAction)}</span>`
      : "";
    if (!headlineHtml && !actionHtml) return "";
    return `<span class="narrative-feed-block narrative-root">${headlineHtml}${actionHtml}</span>`;
  }

  /**
   * Suggested-trade card: headline, one why, one action, levels.
   */
  function suggestedCardInnerHtml(row, scanPayload) {
    const assistant = resolveAssistantForRow(row, scanPayload);
    const headline = String(assistant.headline || "").trim();
    const whyOne = asStringList(assistant.why_bullets)[0] || "";
    const actionOne = asStringList(assistant.action_checklist)[0] || "";
    return `<div class="narrative-suggested narrative-root">
      ${headline ? `<p class="narrative-headline">${escapeHtml(headline)}</p>` : ""}
      ${whyOne ? `<p class="narrative-why-one">${escapeHtml(whyOne)}</p>` : ""}
      ${actionOne ? `<p class="narrative-action-one">${escapeHtml(actionOne)}</p>` : ""}
      ${levelsLineHtml(assistant.levels, true)}
    </div>`;
  }

  function suggestedCardInnerHtmlWide(row, scanPayload) {
    const assistant = resolveAssistantForRow(row, scanPayload);
    const headline = String(assistant.headline || "").trim();
    const whyItems = asStringList(assistant.why_bullets).slice(0, 3);
    const actionItems = asStringList(assistant.action_checklist).slice(0, 2);
    const whyHtml = whyItems.length
      ? `<ul class="narrative-why narrative-why-list">${whyItems
          .map((item) => `<li>${escapeHtml(item)}</li>`)
          .join("")}</ul>`
      : "";
    const actionHtml = actionItems.length
      ? `<ul class="narrative-checklist narrative-action-list">${actionItems
          .map((item) => `<li>${escapeHtml(item)}</li>`)
          .join("")}</ul>`
      : "";
    return `<div class="narrative-suggested narrative-suggested--wide narrative-root">
      ${headline ? `<p class="narrative-headline">${escapeHtml(headline)}</p>` : ""}
      ${whyHtml}
      ${actionHtml}
      ${levelsLineHtml(assistant.levels, true)}
    </div>`;
  }

  function scoreBreakdownInnerHtml(scoreData) {
    const sd = scoreData && typeof scoreData === "object" ? scoreData : {};
    const bd = sd.breakdown && typeof sd.breakdown === "object" ? sd.breakdown : null;
    const rows = [];
    if (typeof sd.total === "number" && Number.isFinite(sd.total)) {
      rows.push(
        `<div class="narrative-kv"><dt>Total</dt><dd>${escapeHtml(String(Math.round(sd.total)))} / 100</dd></div>`,
      );
    }
    if (bd) {
      for (const [key, val] of Object.entries(bd)) {
        if (typeof val === "number" && Number.isFinite(val)) {
          const label = key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
          rows.push(
            `<div class="narrative-kv"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(String(Math.round(val)))}</dd></div>`,
          );
        }
      }
    }
    if (!rows.length) return "";
    return `<dl class="narrative-dl">${rows.join("")}</dl>`;
  }

  function rawJsonDetailsHtml(label, payload, maxLen) {
    if (payload == null || payload === "") return "";
    const raw =
      typeof payload === "object" ? JSON.stringify(payload, null, 2) : String(payload);
    const cap = typeof maxLen === "number" ? maxLen : 8000;
    return `<details class="narrative-raw" data-narrative-advanced>
      <summary>${escapeHtml(label)}</summary>
      <pre class="scanner-json-snippet">${escapeHtml(raw.slice(0, cap))}</pre>
    </details>`;
  }

  /**
   * Full playbook for stock detail — placed above indicator sections.
   */
  function playbookSectionHtml(detailJson, scanPayload) {
    const snapState = detailJson?.state && typeof detailJson.state === "object" ? detailJson.state : {};
    const assistant = resolveAssistant(detailJson, scanPayload);
    const mode = getSkillMode();
    const sd = snapState.score_data && typeof snapState.score_data === "object" ? snapState.score_data : {};
    const breakdownHtml = scoreBreakdownInnerHtml(sd);
    const showDetailsDefault = mode === "advanced";
    const openAttr = showDetailsDefault ? " open" : "";

    const extras = [];
    if (snapState.ale_details != null && snapState.ale_details !== "") {
      extras.push(rawJsonDetailsHtml("Scan extras", snapState.ale_details, 6000));
    }
    if (snapState.flow_result != null && snapState.flow_result !== "") {
      extras.push(rawJsonDetailsHtml("Order flow", snapState.flow_result, 6000));
    }
    extras.push(rawJsonDetailsHtml("All indicator fields", snapState.indicators ?? {}, 12000));
    extras.push(rawJsonDetailsHtml("Full score payload", snapState.score_data ?? {}, 8000));

    const detailsInner = [
      breakdownHtml ? `<div class="narrative-details-section"><p class="narrative-details-label">Score breakdown</p>${breakdownHtml}</div>` : "",
      extras.length ? extras.join("") : "",
    ]
      .filter(Boolean)
      .join("");

    const detailsBlock = detailsInner
      ? `<details class="narrative-details"${openAttr} data-narrative-details>
          <summary>Show details</summary>
          <div class="narrative-details-body">${detailsInner}</div>
        </details>`
      : "";

    return `<section class="narrative-playbook narrative-root" aria-label="Trade playbook">
      <p class="narrative-section-label">Playbook</p>
      <h3 class="narrative-headline">${escapeHtml(String(assistant.headline || "Setup"))}</h3>
      ${renderWhyHtml(assistant.why_bullets)}
      ${renderChecklistHtml(assistant.action_checklist)}
      ${levelsLineHtml(assistant.levels, false)}
      ${cautionHtml(assistant)}
      ${skillHintFooter(assistant)}
      ${detailsBlock}
    </section>`;
  }

  function findScanRowForTicker(ticker, scanPayload) {
    if (!scanPayload || typeof scanPayload !== "object") return null;
    const tk = normalizeTicker(ticker);
    if (!tk) return null;
    const pools = [
      ...(Array.isArray(scanPayload.signals) ? scanPayload.signals : []),
      ...(Array.isArray(scanPayload.best) ? scanPayload.best : []),
      ...(Array.isArray(scanPayload.suggested_watchlist) ? scanPayload.suggested_watchlist : []),
    ];
    for (const row of pools) {
      if (row && normalizeTicker(row.ticker) === tk) return row;
    }
    return null;
  }

  function scanHeadlineLine(ticker, scanPayload) {
    const row = findScanRowForTicker(ticker, scanPayload);
    if (!row) return "";
    const assistant = resolveAssistantForRow(row, scanPayload);
    const headline = String(assistant?.headline || "").trim();
    if (!headline) return "";
    return `From your latest scan: ${headline}`;
  }

  function wireDisclosure(root) {
    if (global.TbSkillMode?.applySkillMode) {
      global.TbSkillMode.applySkillMode(root || global.document);
      return;
    }
    const el = root && root.querySelector ? root : global.document;
    if (!el) return;
    const mode = getSkillMode();
    el.querySelectorAll("[data-narrative-advanced]").forEach((node) => {
      if (mode !== "advanced") node.setAttribute("hidden", "");
      else node.removeAttribute("hidden");
    });
    el.querySelectorAll("details[data-narrative-details]").forEach((node) => {
      if (mode === "advanced") node.open = true;
    });
  }

  function onSkillModeChanged() {
    wireDisclosure(global.document);
    global.TbSkillMode?.applyAll?.();
  }

  global.TbNarrative = {
    getSkillMode,
    resolveAssistant,
    resolveAssistantForRow,
    buildClientAssistantFromRow,
    confidenceBandHtml,
    watchlistSignalCellHtml,
    watchlistAlertSignalHtml,
    feedCardInnerHtml,
    suggestedCardInnerHtml,
    suggestedCardInnerHtmlWide,
    playbookSectionHtml,
    scanHeadlineLine,
    findScanRowForTicker,
    wireDisclosure,
    onSkillModeChanged,
  };

  try {
    global.addEventListener("storage", (ev) => {
      if (ev.key === SKILL_STORAGE_KEY) onSkillModeChanged();
    });
  } catch {
    /* ignore */
  }
})(typeof window !== "undefined" ? window : globalThis);
