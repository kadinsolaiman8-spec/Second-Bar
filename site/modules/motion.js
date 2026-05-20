/**
 * Motion preferences: animation speed scale persisted to localStorage.
 * OS prefers-reduced-motion always wins over user slider.
 */
(function attachTbMotion(global) {
  const STORAGE_KEY = "tb_motion_speed";
  const DEFAULT_SPEED = 100;
  const MIN_SPEED = 0;
  const MAX_SPEED = 200;

  const SPEED_LABELS = Object.freeze([
    { max: 0, label: "Off" },
    { max: 75, label: "Slow" },
    { max: 125, label: "Normal" },
    { max: 175, label: "Fast" },
    { max: Infinity, label: "Very fast" },
  ]);

  function normalizeSpeed(raw) {
    const parsed = Number.parseInt(String(raw ?? ""), 10);
    if (Number.isNaN(parsed)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, parsed));
  }

  function prefersReducedMotion() {
    try {
      return (
        typeof global.matchMedia === "function" &&
        global.matchMedia("(prefers-reduced-motion: reduce)").matches
      );
    } catch {
      return false;
    }
  }

  function getSpeed() {
    try {
      return normalizeSpeed(global.localStorage.getItem(STORAGE_KEY));
    } catch {
      return DEFAULT_SPEED;
    }
  }

  function setSpeed(nextRaw) {
    const next = normalizeSpeed(nextRaw);
    try {
      global.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      /* ignore quota / private mode */
    }
    apply();
    syncSliderUi();
    try {
      global.dispatchEvent(
        new CustomEvent("tb-motion-changed", { detail: { speed: next, scale: getScale() } }),
      );
    } catch {
      /* ignore */
    }
    return next;
  }

  function getScale() {
    if (prefersReducedMotion()) return 0;
    const speed = getSpeed();
    if (speed === 0) return 0;
    return speed / 100;
  }

  function durationMs(baseMs) {
    const scale = getScale();
    if (scale === 0) return 0;
    return Math.round(baseMs * scale);
  }

  function speedLabel(speed) {
    const normalized = normalizeSpeed(speed);
    for (const entry of SPEED_LABELS) {
      if (normalized <= entry.max) return entry.label;
    }
    return "Normal";
  }

  function apply() {
    const root = global.document?.documentElement;
    if (!root) return;
    const scale = getScale();
    root.style.setProperty("--motion-scale", String(scale));
    if (scale === 0) {
      root.setAttribute("data-motion", "reduced");
    } else {
      root.removeAttribute("data-motion");
    }
  }

  function previewMotionGroup() {
    const group = global.document?.querySelector?.("#motionSettingsGroup");
    if (!group || getScale() === 0) return;
    group.classList.remove("settings-group--preview");
    void group.offsetWidth;
    group.classList.add("settings-group--preview");
  }

  function syncSliderUi() {
    const slider = global.document?.querySelector?.("#motionSpeedSlider");
    const label = global.document?.querySelector?.("#motionSpeedLabel");
    const hint = global.document?.querySelector?.("#motionSpeedHint");
    const osReduced = prefersReducedMotion();
    const speed = getSpeed();

    if (slider) {
      slider.value = String(speed);
      slider.disabled = osReduced;
      slider.setAttribute("aria-valuetext", osReduced ? "Off (system)" : speedLabel(speed));
    }
    if (label) {
      label.textContent = osReduced ? "Off (system)" : speedLabel(speed);
    }
    if (hint) {
      hint.textContent = osReduced
        ? "Your system has reduced motion enabled. Animations stay off until that changes."
        : "Saved in this browser. System reduced-motion still applies.";
    }
  }

  function init() {
    apply();
    syncSliderUi();

    const slider = global.document?.querySelector?.("#motionSpeedSlider");
    if (slider && slider.dataset.wiredMotion !== "1") {
      slider.dataset.wiredMotion = "1";
      slider.addEventListener("input", () => {
        setSpeed(slider.value);
        previewMotionGroup();
      });
      slider.addEventListener("change", () => {
        setSpeed(slider.value);
        previewMotionGroup();
      });
    }

    try {
      const mql = global.matchMedia("(prefers-reduced-motion: reduce)");
      const onPrefChange = () => {
        apply();
        syncSliderUi();
      };
      if (typeof mql.addEventListener === "function") {
        mql.addEventListener("change", onPrefChange);
      } else if (typeof mql.addListener === "function") {
        mql.addListener(onPrefChange);
      }
    } catch {
      /* ignore */
    }

    global.addEventListener("storage", (event) => {
      if (event.key === STORAGE_KEY) {
        apply();
        syncSliderUi();
      }
    });
  }

  global.TbMotion = Object.freeze({
    STORAGE_KEY,
    DEFAULT_SPEED,
    MIN_SPEED,
    MAX_SPEED,
    getSpeed,
    setSpeed,
    getScale,
    durationMs,
    prefersReducedMotion,
    speedLabel,
    apply,
    init,
  });
})(typeof window !== "undefined" ? window : globalThis);
