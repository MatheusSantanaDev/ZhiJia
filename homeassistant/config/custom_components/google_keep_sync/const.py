"""Constants for the Google Keep Sync integration."""

from typing import Any

DOMAIN = "google_keep_sync"

# Sync interval configuration
DEFAULT_SYNC_INTERVAL_MINUTES = 15
MIN_SYNC_INTERVAL_MINUTES = 1
MAX_SYNC_INTERVAL_MINUTES = 1440  # 24 hours

# Titles used to auto-detect the Keep list that should act as the default
# shopping list when the user has not chosen one explicitly.
DEFAULT_SHOPPING_LIST_NAMES = frozenset(
    {
        "lista de compra",
        "lista de compras",
        "shopping list",
    }
)


def get_default_shopping_list_id(
    entry_data: dict[str, Any], lists: list[Any] | None
) -> str:
    """Return the configured default shopping list ID.

    An explicit empty value disables the default. If the option was never
    configured, a list whose title matches a common shopping-list name is used.
    """
    lists = lists or []
    valid_ids = {getattr(keep_list, "id", None) for keep_list in lists}

    if "default_shopping_list" in entry_data:
        configured = entry_data.get("default_shopping_list") or ""
        return configured if configured in valid_ids else ""

    for keep_list in lists:
        title = (getattr(keep_list, "title", "") or "").strip().casefold()
        if title in DEFAULT_SHOPPING_LIST_NAMES:
            return keep_list.id

    return ""
