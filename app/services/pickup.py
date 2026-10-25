"""Pay-cash-at-pickup: hold deadlines, limits, reminders and releasing expired holds.

All settings live in Admin → Settings (SiteSettings rows); the defaults below
apply until the owner changes them. Times are stored in UTC like every other
timestamp in the app and shown in the store's local time.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app, url_for

from app.extensions import db
from app.helpers import get_site_setting
from app.store import STORE

from app.timezones import store_tz, to_local, to_utc, format_local  # noqa: F401 (re-exported)

DEFAULTS = {
    'cash_pickup_active': '1',
    'cash_hold_hours': '36',
    'cash_max_order': '75',
    'cash_reminder_hours': '6',
    'cash_noshow_limit': '2',
    'cash_hold_closing_active': '1',
    'cash_expire_action': 'ask',       # ask | auto
}


def _num(key, cast=float):
    raw = get_site_setting(key, '') or DEFAULTS[key]
    try:
        return cast(raw)
    except (TypeError, ValueError, InvalidOperation):
        return cast(DEFAULTS[key])


def cash_settings():
    return {
        'active': get_site_setting('cash_pickup_active', DEFAULTS['cash_pickup_active']) == '1',
        'hold_hours': max(1, _num('cash_hold_hours', int)),
        'max_order': _num('cash_max_order', Decimal),
        'reminder_hours': max(0, _num('cash_reminder_hours', int)),
        'noshow_limit': max(0, _num('cash_noshow_limit', int)),
        'snap_to_closing': get_site_setting('cash_hold_closing_active',
                                            DEFAULTS['cash_hold_closing_active']) == '1',
        'expire_action': get_site_setting('cash_expire_action', '') or DEFAULTS['cash_expire_action'],
    }


# ─── Time helpers ────────────────────────────────────────────────────────────

def _closing_on(day):
    hours = STORE['open_hours'].get(day.weekday())
    if not hours:
        return None, None
    open_h, close_h = hours
    start = datetime(day.year, day.month, day.day, open_h, tzinfo=store_tz())
    end = datetime(day.year, day.month, day.day, close_h, tzinfo=store_tz())
    return start, end


def pickup_deadline(placed_utc=None, settings=None):
    """When a cash order placed at `placed_utc` must be picked up (UTC)."""
    settings = settings or cash_settings()
    placed_utc = placed_utc or datetime.utcnow()
    deadline = to_local(placed_utc) + timedelta(hours=settings['hold_hours'])
    if not settings['snap_to_closing']:
        return to_utc(deadline)
    # Never end a hold while the store is closed: before opening → that day's
    # closing time; after closing → the next open day's closing time.
    day = deadline.date()
    for _ in range(8):
        start, end = _closing_on(day)
        if start is not None:
            if deadline <= end:
                return to_utc(end if deadline < start else deadline)
        day += timedelta(days=1)
        deadline = datetime(day.year, day.month, day.day, tzinfo=store_tz())
    return to_utc(deadline)


# ─── Who may pay cash ────────────────────────────────────────────────────────

def no_show_count(email='', phone=''):
    from app.models import Order, ShippingAddress
    conds = []
    if email:
        conds.append(db.func.lower(ShippingAddress.email) == email.strip().lower())
    digits = ''.join(c for c in (phone or '') if c.isdigit())[-10:]
    if len(digits) == 10:
        conds.append(ShippingAddress.phone.like(f'%{digits[-4:]}'))
    if not conds:
        return 0
    rows = (Order.query.join(ShippingAddress)
            .filter(Order.status == 'no_show', db.or_(*conds)).all())
    # The phone match above is loose (last 4 digits); confirm the full number.
    count = 0
    for o in rows:
        addr = o.shipping_address
        same_email = email and addr.email and addr.email.lower() == email.strip().lower()
        same_phone = len(digits) == 10 and ''.join(c for c in (addr.phone or '') if c.isdigit())[-10:] == digits
        if same_email or same_phone:
            count += 1
    return count


def cash_block_reason(total, email='', phone='', settings=None):
    """None when this order may pay cash at pickup, else a message for the customer."""
    settings = settings or cash_settings()
    if not settings['active']:
        return 'Paying cash at pickup is not available right now.'
    if settings['max_order'] and Decimal(total) > settings['max_order']:
        return (f'Orders over ${settings["max_order"]:.2f} need to be paid online. '
                'You can still pick up for free.')
    limit = settings['noshow_limit']
    if limit and no_show_count(email, phone) >= limit:
        return ('Pay-at-pickup is not available for this account because of earlier orders '
                'that were not picked up. Please pay online — pickup is still free.')
    return None


# ─── Stock ───────────────────────────────────────────────────────────────────

def restock(order):
    for item in order.items:
        if item.product and item.product.track_inventory:
            if item.variant is not None:
                item.variant.stock_quantity += item.quantity
            else:
                item.product.stock_quantity += item.quantity


# ─── Customer emails ─────────────────────────────────────────────────────────

def _email_body(order, kind):
    deadline = format_local(order.pickup_deadline)
    total = f'${order.total:.2f}'
    name = order.shipping_address.first_name if order.shipping_address else 'there'
    lines = {
        'reserved': ('Your order is reserved',
                     f'Thanks, {name}! Order {order.order_number} is reserved. We\'ll email you when it\'s ready. '
                     f'Bring {total} cash and pick up by {deadline}.'),
        'ready': ('Your order is ready for pickup',
                  f'Hi {name}, order {order.order_number} is packed and waiting at the counter. '
                  + (f'Bring {total} cash and pick up by {deadline}.' if order.is_cash else 'See you soon!')),
        'reminder': ('Reminder: pick up your order',
                     f'Hi {name}, just a reminder that order {order.order_number} is waiting for you. '
                     f'Please pick up and pay {total} cash by {deadline}, or call us if you need more time.'),
        'released': ('Your order was released',
                     f'Hi {name}, order {order.order_number} wasn\'t picked up by {deadline}, so the items '
                     'went back on the shelf. You\'re welcome to place a new order any time.'),
    }
    subject, text = lines[kind]
    footer = f'{STORE["name"]} · {STORE["address"]} · {STORE["phone"]}'
    try:
        link = url_for('checkout.order_confirmation', order_number=order.order_number, _external=True)
    except RuntimeError:
        link = ''
    html = (f'<p>{text}</p>'
            + (f'<p><a href="{link}">View your order</a></p>' if link else '')
            + f'<p style="color:#666">{footer}</p>')
    return f'{subject} — {order.order_number}', html, f'{text}\n\n{link}\n\n{footer}'


def email_customer(order, kind):
    """Best-effort customer email. Returns True when sent."""
    from app.services.mailer import send_email, is_mail_configured
    addr = order.shipping_address
    if not addr or not addr.email:
        return False
    try:
        if not is_mail_configured():
            return False
        subject, html, text = _email_body(order, kind)
        send_email(addr.email, subject, html, text)
        return True
    except Exception:
        current_app.logger.exception('Could not send %s email for %s', kind, order.order_number)
        return False


# ─── Reminders and expired holds ─────────────────────────────────────────────

def process_holds(now=None):
    """Send due reminders and, when set to auto, release expired holds."""
    from app.models import Order
    now = now or datetime.utcnow()
    settings = cash_settings()
    reminded = released = 0
    open_cash = Order.query.filter(Order.payment_method == 'cash',
                                   Order.status.in_(Order.OPEN_STATUSES),
                                   Order.pickup_deadline.isnot(None)).all()
    for order in open_cash:
        if order.pickup_deadline <= now:
            if settings['expire_action'] == 'auto':
                restock(order)
                order.set_status('no_show', now)
                db.session.commit()
                email_customer(order, 'released')
                released += 1
            continue
        due = order.pickup_deadline - timedelta(hours=settings['reminder_hours'])
        placed_recently = order.created_at and now - order.created_at < timedelta(hours=1)
        if (settings['reminder_hours'] and not order.reminder_sent_at and now >= due
                and not placed_recently):
            # Mark first so a failed send isn't retried every few minutes.
            order.reminder_sent_at = now
            db.session.commit()
            email_customer(order, 'reminder')
            reminded += 1
    return reminded, released
