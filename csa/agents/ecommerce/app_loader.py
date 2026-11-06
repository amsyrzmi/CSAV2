"""Server-side TikTok app registry loader: secrets from settings, revision from the row."""

from collections.abc import Awaitable, Callable

from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.core import db
from csa.core.settings import Settings

AppLoader = Callable[[str], Awaitable[TikTokAppMaterial | None]]


def build_app_loader(settings: Settings, pool: AsyncConnectionPool) -> AppLoader:
    """Build the resolver's ``load_app``.

    Returns ``None`` (never a fallback) when TikTok is off, the requested id is not
    ``TIKTOK_APP_ID``, or the ``ecommerce_apps`` row is missing or ``disabled``. The
    revision always comes from the row, so a disable/enable bump fences every grant.
    """

    async def load_app(app_id: str) -> TikTokAppMaterial | None:
        key, secret = settings.TIKTOK_APP_KEY, settings.TIKTOK_APP_SECRET
        if not settings.tiktok_enabled or key is None or secret is None:
            return None
        if app_id != settings.TIKTOK_APP_ID:
            return None
        async with pool.connection() as conn:
            row = await db.q.fetch_one("ecommerce/get_app.sql", (app_id,), conn=conn)
        if row is None or row["state"] != "active":
            return None
        return TikTokAppMaterial(
            app_id=row["app_id"],
            revision=row["revision"],
            app_key=SecretStr(key),
            app_secret=secret,
            enabled=True,
        )

    return load_app
