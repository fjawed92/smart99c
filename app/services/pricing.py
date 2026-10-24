"""Order pricing: sales, promo codes, the cash-pickup discount, tax, delivery and fees.

Everything that puts a price on an order goes through `quote()` so the cart,
checkout, Stripe and the saved order always agree. The order of steps:

    1. item prices already have any running sale taken off (store-wide or
       category — the bigger one wins, they never add up),
    2. a promo code comes off the subtotal,
    3. the cash-pickup discount comes off what is left (cash orders only),
       or — when Admin → Discounts says "only the bigger one" — whichever of
       the promo and the cash discount saves more is used,
    4. tax on the discounted amount,
    5. delivery (checked against the option's minimum order),
    6. the online service fee on card payments.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import current_app, g, has_app_context

from app.extensions import db
from app.helpers import get_site_setting, service_fee_for

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def _decimal_setting(key, default):
    try:
        return Decimal(get_site_setting(key, '') or default)
    except InvalidOperation:
        return Decimal(default)


# ─── Sales ───────────────────────────────────────────────────────────────────

def running_sales():
    """Sales running right now, looked up once per request."""
    from app.models import Sale
    if has_app_context() and '_running_sales' in g:
        return g._running_sales
    try:
        sales = [s for s in Sale.query.filter_by(is_active=True).all() if s.is_running()]
    except Exception:
        db.session.rollback()
        sales = []
    if has_app_context():
        g._running_sales = sales
    return sales


def forget_sales():
    """Call after changing sales so this request sees the new ones."""
    if has_app_context():
        g.pop('_running_sales', None)


def sale_percent_for(product):
    best = ZERO
    for sale in running_sales():
        if sale.category_id is None or sale.category_id == product.category_id:
            best = max(best, Decimal(sale.percent))
    return best


def apply_percent(amount, percent):
    amount = Decimal(amount or 0)
    if not percent:
        return amount
    return money(amount * (Decimal(100) - Decimal(percent)) / Decimal(100))


def sale_category_ids():
    """(whole_store_sale_running, {category ids on sale}) — for the Deals filter."""
    sales = running_sales()
    return (any(s.category_id is None for s in sales),
            {s.category_id for s in sales if s.category_id is not None})


# ─── Settings ────────────────────────────────────────────────────────────────

def tax_rate():
    """Admin → Settings tax rate as a fraction; falls back to config TAX_RATE."""
    raw = (get_site_setting('tax_rate', '') or '').strip()
    try:
        if raw:
            pct = Decimal(raw)
            if 0 <= pct < 100:
                return pct / 100
    except InvalidOperation:
        pass
    return Decimal(str(current_app.config.get('TAX_RATE', 0.08875)))


def cash_discount_percent():
    pct = _decimal_setting('cash_discount_percent', '0')
    return pct if 0 < pct < 100 else ZERO


def stacking():
    """'stack' = promo and cash discount both apply; 'best' = only the bigger one."""
    return 'best' if get_site_setting('discount_stacking', 'stack') == 'best' else 'stack'


# ─── Promo codes ─────────────────────────────────────────────────────────────

def normalize_code(code):
    return ''.join((code or '').split()).upper()[:40]


def find_promo(code):
    from app.models import PromoCode
    code = normalize_code(code)
    return PromoCode.query.filter_by(code=code).first() if code else None


def times_used_by(promo, email):
    from app.models import Order, ShippingAddress
    email = (email or '').strip().lower()
    if not email:
        return 0
    return (Order.query.join(ShippingAddress)
            .filter(Order.promo_code == promo.code,
                    db.func.lower(ShippingAddress.email) == email,
                    Order.status.notin_(('cancelled', 'refunded')))
            .count())


def promo_problem(promo, subtotal, email='', now=None):
    """None when the code can be used on this order, else a message for the shopper."""
    now = now or datetime.utcnow()
    if promo is None or not promo.is_active:
        return "That code isn't valid."
    if promo.starts_at and now < promo.starts_at:
        return "That code isn't active yet."
    if promo.ends_at and now >= promo.ends_at:
        return 'That code has expired.'
    if promo.max_uses is not None and promo.uses >= promo.max_uses:
        return 'That code has been used up.'
    if promo.min_order and subtotal < promo.min_order:
        short = money(promo.min_order - subtotal)
        return f'Add ${short:.2f} more to use this code (orders over ${promo.min_order:.2f}).'
    if promo.once_per_customer and times_used_by(promo, email):
        return "You've already used this code."
    return None


def promo_amount(promo, subtotal):
    if promo.kind == 'percent':
        pct = min(Decimal(promo.value), Decimal(100))
        return money(subtotal * pct / 100)
    if promo.kind == 'amount':
        return min(money(promo.value), money(subtotal))
    return ZERO  # free_shipping is applied to delivery


# ─── Delivery options ────────────────────────────────────────────────────────

def rate_problem(rate, goods_total):
    """None when this delivery option can be used, else a message."""
    if rate is None:
        return None
    if not rate.is_active:
        return 'That delivery option is not available right now.'
    minimum = Decimal(rate.min_order_amount or 0)
    if minimum and goods_total < minimum:
        return f'Add ${money(minimum - goods_total):.2f} more to get {rate.name}.'
    return None


# ─── The quote ───────────────────────────────────────────────────────────────

def quote(items, rate=None, *, cash=False, promo_code='', email='', enforce_promo=True):
    """Price an order. `items` is the list from helpers.get_cart_items().

    enforce_promo=False still applies an existing code that has since expired or
    run out — used after a card payment already went through at that price.
    """
    from app.models import is_pickup_option
    subtotal = money(sum((i['total'] for i in items), ZERO))
    q = {'subtotal': subtotal, 'promo': None, 'promo_code': '', 'promo_error': None,
         'promo_note': None, 'promo_discount': ZERO, 'cash_percent': ZERO,
         'cash_discount': ZERO, 'free_shipping': False, 'rate': rate, 'rate_error': None}

    # 2. promo code
    promo = find_promo(promo_code) if promo_code else None
    if promo_code:
        problem = promo_problem(promo, subtotal, email)
        if problem and not enforce_promo and promo is not None:
            problem = None
        if problem:
            q['promo_error'] = problem
        else:
            q['promo'], q['promo_code'] = promo, promo.code
            q['promo_discount'] = promo_amount(promo, subtotal)
            q['free_shipping'] = promo.kind == 'free_shipping'
            if q['free_shipping'] and (rate is None or is_pickup_option(rate.name)):
                q['promo_note'] = 'Free delivery applies when you choose delivery.'

    # 3. cash discount
    if cash:
        pct = cash_discount_percent()
        q['cash_percent'] = pct
        if pct:
            cash_off = money((subtotal - q['promo_discount']) * pct / 100)
            if q['promo'] and stacking() == 'best':
                promo_only = q['promo_discount']
                cash_only = money(subtotal * pct / 100)
                if cash_only > promo_only:
                    q['promo_note'] = (f'Your {pct.normalize():f}% cash discount saves more, '
                                       'so the code was not used.')
                    q['promo'], q['promo_code'], q['promo_discount'] = None, '', ZERO
                    q['free_shipping'] = False
                    cash_off = cash_only
                else:
                    q['promo_note'] = 'Your code saves more than the cash discount, so the code was used.'
                    cash_off = ZERO
            q['cash_discount'] = cash_off

    goods = subtotal - q['promo_discount'] - q['cash_discount']

    # 4–6. tax, delivery, fee
    q['tax'] = money(goods * tax_rate())
    q['rate_error'] = rate_problem(rate, goods)
    shipping = money(rate.price) if rate else ZERO
    if q['free_shipping'] and rate is not None and not is_pickup_option(rate.name):
        shipping = ZERO
    q['shipping'] = shipping
    q['discount'] = q['promo_discount'] + q['cash_discount']
    before_fee = goods + q['tax'] + shipping
    q['fee'] = service_fee_for(before_fee, paid_by_card=not cash)
    q['total'] = money(before_fee + q['fee'])
    return q


def rate_states(rates, items, promo_code='', email=''):
    """{rate id: message or None} — which delivery options this cart can use."""
    base = quote(items, None, promo_code=promo_code, email=email)
    goods = base['subtotal'] - base['promo_discount']
    return {r.id: rate_problem(r, goods) for r in rates}


def quote_json(q):
    return {
        'subtotal': float(q['subtotal']),
        'promo_code': q['promo_code'],
        'promo_label': q['promo'].label if q['promo'] else '',
        'promo_discount': float(q['promo_discount']),
        'promo_error': q['promo_error'],
        'promo_note': q['promo_note'],
        'cash_percent': float(q['cash_percent']),
        'cash_discount': float(q['cash_discount']),
        'tax': float(q['tax']),
        'shipping': float(q['shipping']),
        'free_shipping': q['free_shipping'],
        'service_fee': float(q['fee']),
        'total': float(q['total']),
        'rate_error': q['rate_error'],
    }


# ─── Dates from the admin forms (store-local days) ───────────────────────────

def day_start_utc(value):
    """'2026-11-01' → that day's midnight in store time, as naive UTC."""
    from app.services.pickup import STORE_TZ, to_utc
    try:
        d = datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None
    return to_utc(datetime.combine(d, time(0), tzinfo=STORE_TZ))


def day_end_utc(value):
    """'2026-11-03' → the end of that day in store time (next midnight), naive UTC."""
    start = day_start_utc(value)
    if start is None:
        return None
    from app.services.pickup import STORE_TZ, to_local, to_utc
    local = to_local(start)
    nxt = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=STORE_TZ)
    return to_utc(nxt)


def local_day(dt_utc, end=False):
    """Inverse of day_start_utc / day_end_utc, for filling the form back in."""
    if dt_utc is None:
        return ''
    from app.services.pickup import to_local
    local = to_local(dt_utc - timedelta(seconds=1) if end else dt_utc)
    return local.strftime('%Y-%m-%d')
