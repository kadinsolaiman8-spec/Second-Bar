"""
Shared utilities for the Discord Trading Bot.
"""


def sanitize_for_discord(text: str, max_len: int = 100) -> str:
    """
    Sanitize user-supplied text before echoing to Discord.
    Prevents @everyone/@here/role pings and markdown injection.
    """
    if not text:
        return ""
    # Truncate first to limit processing
    text = str(text)[:max_len]
    # Break mentions: @everyone, @here, <@user_id>, <@&role_id>
    text = text.replace("@", "@\u200b")  # Zero-width space breaks mentions
    return text
