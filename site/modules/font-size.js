/**
 * Font size preference: small | medium | large (default medium).
 */
(function attachTbFontSize(global) {
  const STORAGE_KEY = "tb_font_size";
  const SIZES = Object.freeze(["small", "medium", "large"]);
  const DEFAULT_SIZE = "medium";

  const SLIDER_TO_SIZE = Object.freeze({
    0: "small",
    50: "medium",
    100: "large",
  });

  const SIZE_TO_SLIDER = Object.freeze({
    small: 0,
    medium: 50,
    large: 100,
  });

  const SIZE_LABELS = Object.freeze({
    small: "Small",
    medium: "Medium",
    large: "Big",
  });

  function normalizeSize(raw) {
    const size = String(raw ?? "")
      .trim()
      .toLowerCase();
    return SIZES.includes(size) ? size : DEFAULT_SIZE;
  }

  function getSize() {
    try {
      return normalizeSize(global.localStorage.getItem(STORAGE_KEY));
    } catch {
      return DEFAULT_SIZE;
    }
  }

  function sizeFromSlider(raw) {
    const parsed = Number.parseInt(String(raw ?? ""), 10);
    if (Number.isNaN(parsed)) return DEFAULT_SIZE;
    if (parsed <= 25) return "small";
    if (parsed <= 75) return "medium";
    return "large";
  }

  function sliderFromSize(size) {
    return SIZE_TO_SLIDER[normalizeSize(size)] ?? SIZE_TO_SLIDER[DEFAULT_SIZE];
  }

  function setSize(nextRaw) {
    const next = normalizeSize(nextRaw);
    try {
      global.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    apply();
    syncSliderUi();
    try {
      global.dispatchEvent(new CustomEvent("tb-font-size-changed", { detail: { size: next } }));
    } catch {
      /* ignore */
    }
    return next;
  }

  function apply() {
    const root = global.document?.documentElement;
    if (!root) return;
    root.setAttribute("data-font-size", getSize());
  }

  function previewTypographyGroup() {
    const group = global.document?.querySelector?.("#fontSizeSettingsGroup");
    if (!group || global.TbMotion?.getScale?.() === 0) return;
    group.classList.remove("settings-group--preview");
    void group.offsetWidth;
    group.classList.add("settings-group--preview");
  }

  function syncSliderUi() {
    const slider = global.document?.querySelector?.("#fontSizeSlider");
    const label = global.document?.querySelector?.("#fontSizeLabel");
    const size = getSize();
    const sliderValue = sliderFromSize(size);

    if (slider) {
      slider.value = String(sliderValue);
      slider.setAttribute("aria-valuetext", SIZE_LABELS[size] ?? SIZE_LABELS.medium);
    }
    if (label) {
      label.textContent = SIZE_LABELS[size] ?? SIZE_LABELS.medium;
    }
  }

  function init() {
    apply();
    syncSliderUi();

    const slider = global.document?.querySelector?.("#fontSizeSlider");
    if (slider && slider.dataset.wiredFontSize !== "1") {
      slider.dataset.wiredFontSize = "1";
      const onInput = () => {
        setSize(sizeFromSlider(slider.value));
        previewTypographyGroup();
      };
      slider.addEventListener("input", onInput);
      slider.addEventListener("change", onInput);
    }

    global.addEventListener("storage", (event) => {
      if (event.key === STORAGE_KEY) {
        apply();
        syncSliderUi();
      }
    });
  }

  global.TbFontSize = Object.freeze({
    STORAGE_KEY,
    DEFAULT_SIZE,
    SIZES,
    getSize,
    setSize,
    sizeLabel: (size) => SIZE_LABELS[normalizeSize(size)] ?? SIZE_LABELS.medium,
    apply,
    init,
  });
})(typeof window !== "undefined" ? window : globalThis);
