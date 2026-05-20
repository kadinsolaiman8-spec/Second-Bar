/**
 * Client-side backtest visuals — cumulative return sparkline from trade list.
 * Mirrors journal equity sparkline styling (no charting library).
 */

function escapeHtmlDefault(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function cumulativeReturnSeries(trades) {
  const list = Array.isArray(trades) ? trades : [];
  let cum = 0;
  const points = [];
  for (const trade of list) {
    const pnl =
      typeof trade?.pnl_pct === "number" && Number.isFinite(trade.pnl_pct) ? trade.pnl_pct : 0;
    cum += pnl;
    points.push(cum);
  }
  return points;
}

export function equitySparklineHtml(trades, escapeFn) {
  const escapeHtml = typeof escapeFn === "function" ? escapeFn : escapeHtmlDefault;
  const list = Array.isArray(trades) ? trades : [];
  const series = cumulativeReturnSeries(list);

  if (series.length < 2) {
    return `<p class="backtest-muted bt-sparkline-empty">Run needs at least two closed trades to draw a return stair.</p>`;
  }

  const slice = series.slice(-48);
  const mins = Math.min(...slice, 0);
  const maxs = Math.max(...slice, 0);
  const span = Math.max(1e-6, maxs - mins);

  const bars = slice
    .map((cumVal) => {
      const frac = (cumVal - mins) / span;
      const heightPct = Math.max(8, Math.round(frac * 100));
      const tone = cumVal < 0 ? "journal-equity-bar-loss" : "journal-equity-bar";
      const titleVal = `${cumVal >= 0 ? "+" : ""}${cumVal.toFixed(2)}%`;
      return `<span class="${tone}" style="height:${heightPct}px" title="${escapeHtml(titleVal)}"></span>`;
    })
    .join("");

  const last = slice[slice.length - 1];
  const foot =
    typeof last === "number" && Number.isFinite(last)
      ? `<p class="backtest-muted bt-sparkline-foot">Cumulative return across shown trades: ${escapeHtml(
          `${last >= 0 ? "+" : ""}${last.toFixed(1)}%`,
        )}</p>`
      : "";

  return `<div class="journal-equity-sparkline bt-equity-sparkline" aria-label="Cumulative return sparkline">${bars}</div>${foot}`;
}
