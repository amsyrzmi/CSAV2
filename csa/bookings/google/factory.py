"""Builds the Google Calendar objects a process needs from its settings."""

from __future__ import annotations

import httpx
from psycopg_pool import AsyncConnectionPool

from csa.bookings.google.client import CalendarClient
from csa.bookings.google.connections import CalendarConnections
from csa.bookings.google.push import CalendarPush
from csa.core.settings import Settings


def build_calendar_push(
    settings: Settings, pool: AsyncConnectionPool, http: httpx.AsyncClient
) -> CalendarPush | None:
    """The connection service and the push, sharing one client; none while the feature is off."""
    if not settings.BOOKINGS_GOOGLE_CALENDAR_ENABLED:
        return None
    redirect = settings.bookings_calendar_redirect_uri()
    assert settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and redirect
    client = CalendarClient(
        settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, redirect, http
    )
    connections = CalendarConnections(pool, client, enabled=True)
    return CalendarPush(pool, connections, client, meet=settings.BOOKINGS_GOOGLE_MEET_ENABLED)
