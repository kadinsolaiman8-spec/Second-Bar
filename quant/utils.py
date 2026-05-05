"""
Shared utilities for safe display of user-supplied strings.
"""


def sanitize_for_display(text: str, max_len: int = 100) -> str:
    """
    Sanitize user-supplied text before echoing in UI or logs.
    Breaks @everyone/@here-style tokens and trims length.
    """
    if not text:
        return ""
    # Truncate first to limit processing
    text = str(text)[:max_len]
    # Break mentions: @everyone, @here, <@user_id>, <@&role_id>
    text = text.replace("@", "@\u200b")  # Zero-width space breaks mentions
    return text
