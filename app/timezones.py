"""The store's time zone (Admin → Settings) and helpers for showing times in it.

Every timestamp in the database is stored in UTC without a time zone
(datetime.utcnow()). Use these helpers whenever a time is shown to someone,
or when "today" / a calendar day has to be worked out.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from flask import g, has_app_context

from app.store import STORE

UTC = ZoneInfo('UTC')
DEFAULT_TZ = STORE.get('timezone', 'America/New_York')

# Shown first in the Admin → Settings dropdown.
US_ZONES = [
    ('America/New_York', 'Eastern (New York)'),
    ('America/Chicago', 'Central (Chicago)'),
    ('America/Denver', 'Mountain (Denver)'),
    ('America/Phoenix', 'Arizona (no daylight saving)'),
    ('America/Los_Angeles', 'Pacific (Los Angeles)'),
    ('America/Anchorage', 'Alaska'),
    ('Pacific/Honolulu', 'Hawaii'),
]


def valid_zone(name):
    try:
        return bool(name) and ZoneInfo(name) is not None
    except (ZoneInfoNotFoundError, ValueError):
        return False


def zone_choices():
    """[(value, label)] — US zones first, then every other zone A–Z."""
    us = {z for z, _ in US_ZONES}
    others = sorted(z for z in available_timezones() if '/' in z and z not in us
                    and not z.startswith(('Etc/', 'SystemV/', 'posix/', 'right/')))
    return US_ZONES + [(z, z.replace('_', ' ')) for z in others]


def store_tz_name():
    """The saved zone name, looked up once per request."""
    if has_app_context() and '_store_tz' in g:
        return g._store_tz
    name = DEFAULT_TZ
    if has_app_context():
        from app.helpers import get_site_setting
        saved = (get_site_setting('store_timezone', '') or '').strip()
        if valid_zone(saved):
            name = saved
        g._store_tz = name
    return name


def forget_tz():
    """Call after saving a new zone so this request uses it."""
    if has_app_context():
        g.pop('_store_tz', None)


def store_tz():
    return ZoneInfo(store_tz_name())


def to_local(dt_utc):
    """Stored UTC time → aware time in the store's zone."""
    if dt_utc is None:
        return None
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(store_tz())


def to_utc(dt_local):
    """Aware time (any zone) → naive UTC, the way it's stored."""
    return dt_local.astimezone(UTC).replace(tzinfo=None)


def format_local(dt_utc, fmt='%a, %b %-d · %-I:%M %p'):
    local = to_local(dt_utc)
    return local.strftime(fmt) if local else ''


def local_now():
    return to_local(datetime.utcnow())


def local_today():
    return local_now().date()


def day_start_utc(day):
    """Midnight at the start of a store-calendar day, as stored (naive UTC)."""
    return to_utc(datetime.combine(day, time(0), tzinfo=store_tz()))


def day_end_utc(day):
    """Midnight at the end of a store-calendar day (the next day's start)."""
    return day_start_utc(day + timedelta(days=1))


def parse_day(value):
    """'2026-10-25' → date, or None."""
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def tz_label(dt_utc=None):
    """Short name for the zone right now (e.g. 'EDT'), for headers and footers."""
    local = to_local(dt_utc or datetime.utcnow())
    return local.tzname() or store_tz_name()
