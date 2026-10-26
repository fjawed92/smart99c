"""Tell IZY Ops the moment an order changes.

IZY Ops pulls ``GET /api/izyops/orders?since=`` every few minutes. With
``IZYOPS_NOTIFY_URL`` and ``IZYOPS_NOTIFY_KEY`` set (Settings → Websites in
IZY Ops makes the key), every commit that added or changed an ``Order`` or
one of its ``OrderItem`` lines also sends ``POST IZYOPS_NOTIFY_URL`` with
``{"order_ids": [...]}`` so IZY Ops pulls them straight away.

The ping is a hint, not the record: it is sent from a background thread
after the commit, and any failure is logged and forgotten — the regular pull
picks the change up anyway. A card order still waiting for its payment is
left out (IZY Ops doesn't see it until it's paid). A copy of family99c's.
"""
from __future__ import annotations

from threading import Thread

import requests
from flask import current_app, has_app_context
from sqlalchemy import event

from app.models import Order, OrderItem, db

NOTIFY_TIMEOUT = 5  # seconds
_PENDING = 'izyops_notify_order_ids'


def _settings():
    if not has_app_context():
        return None
    url = (current_app.config.get('IZYOPS_NOTIFY_URL') or '').strip()
    key = (current_app.config.get('IZYOPS_NOTIFY_KEY') or '').strip()
    return (url, key) if url and key else None


def _changed_orders(session):
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Order) and (obj in session.new or session.is_modified(obj)):
            yield obj
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, OrderItem) and obj.order_id is not None:
            if obj in session.dirty and not session.is_modified(obj):
                continue
            order = session.get(Order, obj.order_id)
            if order is not None:
                yield order


def _after_flush(session, flush_context):
    if _settings() is None:
        return
    with session.no_autoflush:
        ids = {o.id for o in _changed_orders(session) if o.id is not None and o.status != 'pending'}
    if ids:
        session.info.setdefault(_PENDING, set()).update(ids)


def _after_commit(session):
    if session.in_nested_transaction():
        return
    ids = session.info.pop(_PENDING, None)
    if not ids:
        return
    settings = _settings()
    if settings is None:
        return
    url, key = settings
    Thread(target=send, args=(url, key, sorted(ids), current_app.logger),
           name='izyops-notify', daemon=True).start()


def _after_soft_rollback(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(_PENDING, None)


def send(url, key, order_ids, logger) -> bool:
    """POST the changed ids to IZY Ops. Never raises; returns success."""
    try:
        resp = requests.post(url, json={'order_ids': order_ids},
                             headers={'X-IzyOps-Key': key}, timeout=NOTIFY_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning('IZY Ops notify failed for orders %s: %s', order_ids, exc.__class__.__name__)
        return False
    if resp.status_code >= 300:
        logger.warning('IZY Ops notify for orders %s got HTTP %s', order_ids, resp.status_code)
        return False
    return True


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    event.listen(db.session, 'after_flush', _after_flush)
    event.listen(db.session, 'after_commit', _after_commit)
    event.listen(db.session, 'after_soft_rollback', _after_soft_rollback)
    _installed = True
