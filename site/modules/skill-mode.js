/**
 * Skill-tier UI density: beginner | standard | advanced (default standard).
 * Persists to localStorage key tb_skill_mode.
 */
(function attachTbSkillMode(global) {
  const SKILL_STORAGE_KEY = "tb_skill_mode";
  const MODES = Object.freeze(["beginner", "standard", "advanced"]);
  const DEFAULT_MODE = "standard";

  const MODE_LABELS = Object.freeze({
    beginner: "Beginner",
    standard: "Standard",
    advanced: "Advanced",
  });

  const MODE_BAR_COUNT = Object.freeze({
    beginner: 1,
    standard: 2,
    advanced: 3,
  });

  const CONTEXT_COLLAPSIBLE_SHELLS = Object.freeze([
    { shellId: "homeScanContextSqueezeShell", bodyId: "homeScanContextSqueeze", triggerId: "homeScanContextSqueezeTrigger" },
    { shellId: "homeScanContextSectorsShell", bodyId: "homeScanContextSectors", triggerId: "homeScanContextSectorsTrigger" },
  ]);

  function normalizeMode(raw) {
    const mode = String(raw ?? "")
      .trim()
      .toLowerCase();
    return MODES.includes(mode) ? mode : DEFAULT_MODE;
  }

  function getMode() {
    try {
      return normalizeMode(global.localStorage.getItem(SKILL_STORAGE_KEY));
    } catch {
      return DEFAULT_MODE;
    }
  }

  function isBeginnerMode() {
    return getMode() === "beginner";
  }

  function isAdvancedMode() {
    return getMode() === "advanced";
  }

  function cycleMode(current) {
    const mode = normalizeMode(current);
    const idx = MODES.indexOf(mode);
    return MODES[(idx + 1) % MODES.length];
  }

  function syncDocumentMode(mode) {
    const next = normalizeMode(mode);
    try {
      global.document?.documentElement?.setAttribute("data-skill-mode", next);
    } catch {
      /* ignore */
    }
    return next;
  }

  function syncControl(mode) {
    const next = normalizeMode(mode);
    const toggle = global.document?.querySelector?.("#skillModeToggle");
    const labelEl = global.document?.querySelector?.("#skillModeLevelLabel");
    const barCount = MODE_BAR_COUNT[next] ?? 2;
    const labelText = MODE_LABELS[next] ?? MODE_LABELS.standard;

    if (labelEl) labelEl.textContent = labelText;

    if (toggle) {
      toggle.setAttribute("aria-label", `Experience level: ${labelText}. Tap to change.`);
      toggle.querySelectorAll(".skill-mode-bar").forEach((bar, index) => {
        bar.classList.toggle("skill-mode-bar--filled", index < barCount);
      });
    }
  }

  function setCollapsibleExpanded(shell, trigger, body, expanded) {
    if (!shell || !trigger || !body) return;
    const isOpen = Boolean(expanded);
    trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    shell.classList.toggle("dashboard-collapsible--expanded", isOpen);
    if (isOpen) body.removeAttribute("hidden");
    else body.setAttribute("hidden", "");
  }

  function applyContextPanelCollapse(level) {
    const mode = normalizeMode(level != null ? level : getMode());
    const expandedByDefault = mode === "advanced";

    for (const spec of CONTEXT_COLLAPSIBLE_SHELLS) {
      const shell = global.document?.getElementById?.(spec.shellId);
      const body = global.document?.getElementById?.(spec.bodyId);
      const trigger = global.document?.getElementById?.(spec.triggerId);
      if (!shell || !body || !trigger) continue;
      delete shell.dataset.userExpanded;
      setCollapsibleExpanded(shell, trigger, body, expandedByDefault);
    }
  }

  function toggleContextPanel(shellId, bodyId, triggerId) {
    const shell = global.document?.getElementById?.(shellId);
    const body = global.document?.getElementById?.(bodyId);
    const trigger = global.document?.getElementById?.(triggerId);
    if (!shell || !body || !trigger) return;
    const nextOpen = trigger.getAttribute("aria-expanded") !== "true";
    shell.dataset.userExpanded = nextOpen ? "true" : "false";
    setCollapsibleExpanded(shell, trigger, body, nextOpen);
  }

  function dispatchSkillModeChanged(mode) {
    const detail = { mode: normalizeMode(mode) };
    try {
      global.dispatchEvent(new CustomEvent("tb-skill-mode-changed", { detail }));
    } catch {
      /* ignore */
    }
  }

  /**
   * Apply density, jargon hints, and raw JSON visibility for a narrative subtree.
   * Stream 2 narrative.js should call this after rendering `.narrative-root` blocks.
   *
   * @param {ParentNode | Document | null | undefined} root
   * @param {"beginner"|"standard"|"advanced"|string} [level]
   */
  function applySkillMode(root, level) {
    const mode = normalizeMode(level != null ? level : getMode());
    syncDocumentMode(mode);

    const scopes = [];
    if (!root || root === global.document) {
      scopes.push(global.document);
    } else if (root instanceof Element) {
      if (root.classList?.contains("narrative-root")) scopes.push(root);
      else scopes.push(root);
    } else if (root.querySelector) {
      scopes.push(root);
    }

    for (const scope of scopes) {
      if (!scope || !scope.querySelectorAll) continue;

      scope.querySelectorAll(".narrative-hint-beginner").forEach((el) => {
        el.hidden = mode !== "beginner";
      });
      scope.querySelectorAll(".narrative-hint-advanced").forEach((el) => {
        el.hidden = mode !== "advanced";
      });

      const hideRaw = mode !== "advanced";
      scope.querySelectorAll(".narrative-raw, details.narrative-disclosure").forEach((el) => {
        if (hideRaw) el.setAttribute("hidden", "");
        else el.removeAttribute("hidden");
      });
      scope.querySelectorAll("[data-narrative-advanced]").forEach((el) => {
        if (hideRaw) el.setAttribute("hidden", "");
        else el.removeAttribute("hidden");
      });
      scope.querySelectorAll("details[data-narrative-details]").forEach((node) => {
        if (mode === "advanced") node.open = true;
        else if (node.open && mode === "beginner") node.open = false;
      });
      scope.querySelectorAll("details.stock-detail-raw").forEach((el) => {
        if (hideRaw) el.setAttribute("hidden", "");
        else el.removeAttribute("hidden");
      });

      scope.querySelectorAll(".narrative-root, .scanner-feed-card, .suggested-trade-card").forEach((el) => {
        el.classList.toggle("skill-density-compact", mode === "advanced");
        el.classList.toggle("skill-density-expanded", mode === "beginner");
      });

      scope.querySelectorAll(".scanner-feed-explanation").forEach((el) => {
        el.classList.toggle("skill-hide-feed-explanation", mode === "advanced");
      });

      scope.querySelectorAll(".narrative-checklist, .narrative-why").forEach((el) => {
        const items = el.querySelectorAll("li");
        const maxVisible = mode === "advanced" ? 1 : mode === "standard" ? 3 : items.length;
        items.forEach((li, index) => {
          if (index >= maxVisible) li.setAttribute("hidden", "");
          else li.removeAttribute("hidden");
        });
      });
    }
  }

  function applyAll() {
    applySkillMode(global.document, getMode());
    const stockBody = global.document?.querySelector?.("#stockDetailBody");
    if (stockBody) applySkillMode(stockBody, getMode());
    const feed = global.document?.querySelector?.("#homeScannerSignalFeed");
    if (feed) applySkillMode(feed, getMode());
    const suggested = global.document?.querySelector?.("#homeSuggestedTradesBody");
    if (suggested) applySkillMode(suggested, getMode());
    global.document?.querySelectorAll?.(".narrative-root").forEach((node) => {
      applySkillMode(node, getMode());
    });
    applyContextPanelCollapse(getMode());
  }

  function setMode(mode, options) {
    const opts = options && typeof options === "object" ? options : {};
    const next = normalizeMode(mode);
    try {
      global.localStorage.setItem(SKILL_STORAGE_KEY, next);
    } catch {
      /* ignore quota / private mode */
    }
    syncDocumentMode(next);
    syncControl(next);
    applyAll();
    if (!opts.silent) {
      dispatchSkillModeChanged(next);
      global.TbNarrative?.onSkillModeChanged?.();
    }
    return next;
  }

  function resetToDefault() {
    return setMode(DEFAULT_MODE);
  }

  function initContextCollapsibleTriggers() {
    for (const spec of CONTEXT_COLLAPSIBLE_SHELLS) {
      const trigger = global.document?.getElementById?.(spec.triggerId);
      if (!trigger || trigger.dataset.wiredContextCollapse === "1") continue;
      trigger.dataset.wiredContextCollapse = "1";
      trigger.addEventListener("click", () => {
        toggleContextPanel(spec.shellId, spec.bodyId, spec.triggerId);
      });
    }
  }

  function init() {
    const mode = getMode();
    try {
      global.localStorage.setItem(SKILL_STORAGE_KEY, mode);
    } catch {
      /* ignore */
    }
    syncDocumentMode(mode);
    syncControl(mode);
    initContextCollapsibleTriggers();

    const toggle = global.document?.querySelector?.("#skillModeToggle");
    if (toggle && toggle.dataset.wiredSkillMode !== "1") {
      toggle.dataset.wiredSkillMode = "1";
      toggle.addEventListener("click", () => {
        setMode(cycleMode(getMode()));
      });
    }

    applyAll();
  }

  try {
    global.addEventListener("storage", (ev) => {
      if (ev.key === SKILL_STORAGE_KEY) {
        const mode = normalizeMode(ev.newValue);
        syncDocumentMode(mode);
        syncControl(mode);
        applyAll();
        global.TbNarrative?.onSkillModeChanged?.();
      }
    });
  } catch {
    /* ignore */
  }

  global.TbSkillMode = {
    SKILL_STORAGE_KEY,
    MODES,
    DEFAULT_MODE,
    MODE_LABELS,
    normalizeMode,
    getMode,
    setMode,
    resetToDefault,
    cycleMode,
    isBeginnerMode,
    isAdvancedMode,
    applySkillMode,
    applyAll,
    applyContextPanelCollapse,
    init,
  };
})(typeof window !== "undefined" ? window : globalThis);
