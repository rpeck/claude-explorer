"""Config router."""

from fastapi import APIRouter, Depends

from mcp_server import __version__

from ..config import get_settings
from ..deps import get_store
from ..models import AppConfig, AppConfigStats
from ..store import ConversationStore

router = APIRouter(tags=["config"])


@router.get(
    "/config",
    response_model=AppConfig,
    summary="Get fast application configuration (no disk-walk; safe to poll)",
)
def get_config() -> AppConfig:
    """Get application configuration.

    Bug A 2026-05-03: this endpoint must be fast — both
    ``ConnectionStatus.checkConnection`` (with a 5s ``AbortSignal``
    timeout) and ``useConfig`` poll it on every page load, and React
    StrictMode in dev fires both effects twice → up to four parallel
    cold calls. Previously this returned ``conversation_count`` from
    ``ConversationStore.count_conversations()``, which walks every
    JSON file on disk (~2.5s for ~600 conversations). Four serialized
    walks blow past the 5s timeout and trigger the connection retry
    loop, which manifested as the "Connecting to Backend" modal
    cycling through retries.

    The conversation count moved to a separate endpoint
    (``/config/stats``) and the field was REMOVED from this response
    entirely (2026-05-06): keeping a hardcoded 0 was misleading to
    anyone curling the endpoint directly.

    Layer 3 of PLANS/2026.05.18-config-corruption-safe-mode.md adds
    two coupled behaviours:

    * The response now carries ``config_corrupt_reason`` so the
      frontend can render a persistent corruption banner.
    * The route clears the ``get_settings`` lru_cache on every
      request so the user's "fix the file, refresh the UI" recovery
      flow works without a server restart. The construction cost
      is ~µs-scale (file stat + optional JSON parse) and runs in
      Starlette's threadpool (route defined as plain ``def``, not
      ``async def``, per the Python Expert Council 2026-05-19
      blocking-I/O-on-event-loop check). Without this clear, a user
      who repairs their config.json never sees the banner clear
      because the lru_cache still holds the original corrupt
      Settings.
    """
    # Clear FIRST so the immediately-following ``get_settings()`` re-
    # reads on-disk state. The L1 test slab pins that ``Settings.load``
    # picks up the new state when called fresh.
    get_settings.cache_clear()
    settings = get_settings()
    return AppConfig(
        data_dir=str(settings.data_dir),
        config_corrupt_reason=settings.config_corrupt_reason,
        version=__version__,
    )


@router.get(
    "/config/stats",
    response_model=AppConfigStats,
    summary="Get application configuration plus on-disk conversation count (slow)",
)
async def get_config_stats(
    store: ConversationStore = Depends(get_store),
) -> AppConfigStats:
    """`/config` plus the populated conversation count.

    Slow on cold cache; intended for the Settings page where the user
    is willing to wait.
    """
    settings = get_settings()
    return AppConfigStats(
        data_dir=str(settings.data_dir),
        conversation_count=store.count_conversations(),
        version=__version__,
    )
