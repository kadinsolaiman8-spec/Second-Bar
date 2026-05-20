"""
Hybrid MR+TF signal system: runs both strategies independently and combines
via signal voting. Research shows even naive 50/50 combination raises Sharpe
from 0.65 to 0.91 due to negative correlation between MR and TF equity curves.

Signal voting:
  - Both agree (same direction) → full confidence
  - One signals, other Hold/None → half confidence (×0.7)
  - Conflict (one Buy, one Sell) → Hold (flat)
"""

from quant.indicators import precompute_indicators
from quant.signals import Signal, evaluate_signal_at
from quant.signals_trend import breakout_signal_at, precompute_breakout


def hybrid_signal_at(
    i: int,
    precomp_mr: dict | None,
    precomp_tf: dict | None,
    symbol: str,
    df,
    config: dict,
    ignore_volatility: bool = False,
    news_sentiment: float | None = None,
    expert_sentiment: float | None = None,
    timeframe: str | None = None,
    vix: float | None = None,
) -> Signal | None:
    """Run hybrid MR+TF voting at bar index i using precomputed arrays."""
    ind = config.get("indicators", {})

    mr_signal = (
        evaluate_signal_at(
            i,
            precomp_mr,
            symbol,
            rsi_oversold=ind.get("rsi_oversold", 35),
            rsi_overbought=ind.get("rsi_overbought", 65),
            ignore_volatility=ignore_volatility,
            config=config,
            news_sentiment=news_sentiment,
            expert_sentiment=expert_sentiment,
            timeframe=timeframe,
            vix=vix,
        )
        if precomp_mr is not None
        else None
    )

    tf_signal = (
        breakout_signal_at(
            i,
            precomp_tf,
            symbol,
            df,
            config=config,
        )
        if precomp_tf is not None
        else None
    )

    if mr_signal is None and tf_signal is None:
        return None

    mr_type = mr_signal.signal_type if mr_signal else "Hold"
    tf_type = tf_signal.signal_type if tf_signal else "Hold"

    hybrid_cfg = config.get("hybrid", {})
    conflict_action = hybrid_cfg.get("conflict_action", "flat")

    if mr_type == tf_type and mr_type != "Hold":
        signal_type = mr_type
        confidence = max(
            mr_signal.confidence if mr_signal else 0,
            tf_signal.confidence if tf_signal else 0,
        )
        strategy_label = f"Hybrid: MR+TF agree ({signal_type})"
    elif mr_type != "Hold" and tf_type != "Hold" and mr_type != tf_type:
        if conflict_action == "mr_priority":
            signal_type = mr_type
            confidence = int(mr_signal.confidence * 0.5) if mr_signal else 0
            strategy_label = f"Hybrid: conflict, MR priority ({mr_type})"
        elif conflict_action == "tf_priority":
            signal_type = tf_type
            confidence = int(tf_signal.confidence * 0.5) if tf_signal else 0
            strategy_label = f"Hybrid: conflict, TF priority ({tf_type})"
        else:
            signal_type = "Hold"
            confidence = 0
            strategy_label = "Hybrid: MR/TF conflict, flat"
    elif mr_type != "Hold":
        signal_type = mr_type
        confidence = int(mr_signal.confidence * 0.7) if mr_signal else 0
        strategy_label = f"Hybrid: MR only ({mr_type})"
    elif tf_type != "Hold":
        signal_type = tf_type
        confidence = int(tf_signal.confidence * 0.7) if tf_signal else 0
        strategy_label = f"Hybrid: TF only ({tf_type})"
    else:
        signal_type = "Hold"
        confidence = 0
        strategy_label = "Hybrid: both Hold"

    confidence = max(0, min(100, confidence))

    base = mr_signal if mr_signal else tf_signal

    regime_parts = []
    if base and base.regime:
        regime_parts.append(base.regime)
    regime_parts.append(strategy_label)
    regime_display = " | ".join(regime_parts)

    dominant = mr_signal if (signal_type == mr_type and mr_signal) else (tf_signal if (signal_type == tf_type and tf_signal) else base)
    stop_price = dominant.stop_price if dominant else None
    take_profit_price = dominant.take_profit_price if dominant else None
    stop_pct = dominant.stop_pct if dominant else None

    return Signal(
        symbol=symbol,
        signal_type=signal_type,
        confidence=confidence,
        rsi=base.rsi if base else 0.0,
        macd_hist=base.macd_hist if base else 0.0,
        price=base.price if base else 0.0,
        atr_pct=base.atr_pct if base else 0.0,
        net_score=mr_signal.net_score if mr_signal else None,
        weighted_scores=mr_signal.weighted_scores if mr_signal else None,
        regime=regime_display,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        stop_pct=stop_pct,
    )


def evaluate_hybrid(
    df,
    symbol: str,
    config: dict,
    ignore_volatility: bool = False,
    news_sentiment: float | None = None,
    expert_sentiment: float | None = None,
    timeframe: str | None = None,
    vix: float | None = None,
    indicator_params: dict | None = None,
) -> Signal | None:
    """
    Run both MR and TF on the latest bar, combine via signal voting.
    Uses the same precompute path as backtest for parity.
    """
    if df is None or df.empty:
        return None

    ind = config.get("indicators", {})
    tf_cfg = config.get("trend_following", {})

    precomp_mr = precompute_indicators(
        df,
        rsi_period=ind.get("rsi_period", 14),
        macd_fast=ind.get("macd_fast", 12),
        macd_slow=ind.get("macd_slow", 26),
        macd_signal=ind.get("macd_signal", 9),
        bb_period=ind.get("bb_period", 20),
        bb_std=ind.get("bb_std", 2),
        supertrend_period=ind.get("supertrend_period", 10),
        supertrend_multiplier=ind.get("supertrend_multiplier", 3),
        stoch_window=ind.get("stoch_window", 14),
        stoch_smooth=ind.get("stoch_smooth", 3),
        willr_period=ind.get("willr_period", 14),
        ema_fast=ind.get("ema_fast", 9),
        ema_slow=ind.get("ema_slow", 21),
        atr_period=ind.get("atr_period", 14),
        atr_avg_period=ind.get("atr_avg_period", 20),
    )
    precomp_tf = precompute_breakout(
        df,
        donchian_period=tf_cfg.get("donchian_period", 20),
        atr_period=tf_cfg.get("atr_period", 14),
        adx_period=tf_cfg.get("adx_period", 14),
        adx_threshold=tf_cfg.get("adx_threshold", 25),
        config=config,
    )

    if precomp_mr is None and precomp_tf is None:
        return None

    return hybrid_signal_at(
        len(df) - 1,
        precomp_mr,
        precomp_tf,
        symbol,
        df,
        config=config,
        ignore_volatility=ignore_volatility,
        news_sentiment=news_sentiment,
        expert_sentiment=expert_sentiment,
        timeframe=timeframe,
        vix=vix,
    )
