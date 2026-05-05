"""
Stop/cancel mechanism for long-running operations.
Used by /stop command to interrupt backtest, recap, etc.
"""

_stop_requested = False


def request_stop() -> None:
    """Set the stop flag. Long-running tasks should check and exit when set."""
    global _stop_requested
    _stop_requested = True


def clear_stop() -> None:
    """Clear the stop flag. Call at the start of each new operation."""
    global _stop_requested
    _stop_requested = False


def is_stop_requested() -> bool:
    """Return True if /stop was called and the current operation should exit."""
    return _stop_requested


class StopRequested(Exception):
    """Raised when a task exits early due to /stop."""
