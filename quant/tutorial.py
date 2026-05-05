"""
Tutorial payload for web UI (Discord embed replaced with structured dict).
"""


def build_tutorial_payload() -> dict:
    """Structured guide mirroring former Discord tutorial content."""
    return {
        "title": "How the trading stack works",
        "sections": [
            {
                "name": "Web dashboard",
                "value": (
                    "Scan results and validation endpoints are served over HTTP. "
                    "Use /api/scan/* for scan snapshots and /api/quant/* for DTB validation helpers."
                ),
            },
            {
                "name": "Signal pipeline (DTB)",
                "value": (
                    "OHLCV → indicators → weighted scores → Buy/Sell/Hold consensus. "
                    "See docs/STRATEGY_AND_EDGE.md and config.yaml."
                ),
            },
            {
                "name": "Honest expectations",
                "value": (
                    "This repository archived rigorous tests finding no durable edge vs buy-and-hold "
                    "for the tested retail-style strategies; reuse the methodology, not hype."
                ),
            },
        ],
        "footer": "Tune thresholds and weights in config.yaml",
    }
