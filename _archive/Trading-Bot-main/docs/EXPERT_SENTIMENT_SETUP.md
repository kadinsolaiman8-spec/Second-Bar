# Expert Sentiment Setup

Expert sentiment adds a weighted "Expert Input" factor to the signal consensus. It acts as an indicator like news sentiment — read-only, config-driven.

## Config

Set per-ticker expert sentiment in `config.yaml` under `ticker_profiles`:

```yaml
ticker_profiles:
  SPY:
    expert_sentiment: 0.5   # -1 to +1 (Bullish/Neutral/Bearish)
  AAPL:
    expert_sentiment: -0.3  # Bearish
```

## Behavior

- **Source:** `config.yaml` only. No Discord commands, no Supabase.
- **Usage:** Same as news sentiment — used in `evaluate_signal`, `evaluate_all`, recap, and stock embeds.
- **Display:** Shows in recap as `| Expert: Bullish/Bearish/Neutral` per symbol when set.
- **Weight:** Controlled by `indicator_weights.expert` in config.

## Enable/Disable

```yaml
expert_input:
  enabled: true
```
