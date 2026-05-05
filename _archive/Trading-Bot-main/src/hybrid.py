"""
Hybrid MR+TF signal system: runs both strategies independently and combines
via signal voting. Research shows even naive 50/50 combination raises Sharpe
from 0.65 to 0.91 due to negative correlation between MR and TF equity curves.

Signal voting:
  - Both agree (same direction) → full confidence
  - One signals, other Hold/None → half confidence (×0.7)
  - Conflict (one Buy, one Sell) → Hold (flat)
"""

from src.signals import Signal, evaluate_signal
from src.signals_trend import evaluate_breakout_signal


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
    Run both MR and TF, combine via signal voting.
    Returns a Signal with hybrid metadata in the regime field.
    """
    indicator_params = indicator_params or {}

    # Get indicator config for MR
    ind = config.get("indicators", {})

    # Run MR signal
    mr_signal = evaluate_signal(
        df,
        symbol,
        rsi_oversold=ind.get("rsi_oversold", 35),
        rsi_overbought=ind.get("rsi_overbought", 65),
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
        stoch_oversold=ind.get("stoch_oversold", 20),
        stoch_overbought=ind.get("stoch_overbought", 80),
        willr_period=ind.get("willr_period", 14),
        willr_oversold=ind.get("willr_oversold", -80),
        willr_overbought=ind.get("willr_overbought", -20),
        ema_fast=ind.get("ema_fast", 9),
        ema_slow=ind.get("ema_slow", 21),
        atr_period=ind.get("atr_period", 14),
        atr_avg_period=ind.get("atr_avg_period", 20),
        ignore_volatility=ignore_volatility,
        config=config,
        news_sentiment=news_sentiment,
        expert_sentiment=expert_sentiment,
        timeframe=timeframe,
        vix=vix,
    )

    # Run TF signal
    tf_cfg = config.get("trend_following", {})
    tf_signal = evaluate_breakout_signal(
        df,
        symbol,
        donchian_period=tf_cfg.get("donchian_period", 20),
        atr_period=tf_cfg.get("atr_period", 14),
        adx_period=tf_cfg.get("adx_period", 14),
        adx_threshold=tf_cfg.get("adx_threshold", None),
        config=config,
    )

    # Neither strategy produced a signal
    if mr_signal is None and tf_signal is None:
        return None

    # Determine types
    mr_type = mr_signal.signal_type if mr_signal else "Hold"
    tf_type = tf_signal.signal_type if tf_signal else "Hold"

    # Signal voting
    hybrid_cfg = config.get("hybrid", {})
    conflict_action = hybrid_cfg.get("conflict_action", "flat")

    if mr_type == tf_type and mr_type != "Hold":
        # Both agree → full position
        signal_type = mr_type
        confidence = max(
            mr_signal.confidence if mr_signal else 0,
            tf_signal.confidence if tf_signal else 0,
        )
        strategy_label = f"Hybrid: MR+TF agree ({signal_type})"
    elif mr_type != "Hold" and tf_type != "Hold" and mr_type != tf_type:
        # Conflict → flat
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
        # Only MR signals
        signal_type = mr_type
        confidence = int(mr_signal.confidence * 0.7) if mr_signal else 0
        strategy_label = f"Hybrid: MR only ({mr_type})"
    elif tf_type != "Hold":
        # Only TF signals
        signal_type = tf_type
        confidence = int(tf_signal.confidence * 0.7) if tf_signal else 0
        strategy_label = f"Hybrid: TF only ({tf_type})"
    else:
        # Both Hold
        signal_type = "Hold"
        confidence = 0
        strategy_label = "Hybrid: both Hold"

    confidence = max(0, min(100, confidence))

    # Use MR signal's detailed data when available, otherwise TF's
    base = mr_signal if mr_signal else tf_signal

    # Build regime display combining regime + strategy label
    regime_parts = []
    if base and base.regime:
        regime_parts.append(base.regime)
    regime_parts.append(strategy_label)
    regime_display = " | ".join(regime_parts)

    # Propagate stop/TP from the dominant sub-signal (base)
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
