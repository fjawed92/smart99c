from decimal import Decimal
from flask import session


def _parse_line_key(key):
    """Return (product_id, variant_id) where variant_id may be 0 (= no variant).

    Accepts both legacy keys ('5') and composite keys ('5:12').
    """
    s = str(key)
    if ':' in s:
        pid, vid = s.split(':', 1)
        try:
            return int(pid), int(vid)
        except (TypeError, ValueError):
            return None, None
    try:
        return int(s), 0
    except (TypeError, ValueError):
        return None, None


def make_line_key(product_id, variant_id=None):
    """Build a cart key. variant_id None or 0 means 'no variant'."""
    return f'{int(product_id)}:{int(variant_id or 0)}'


def _normalize_cart(cart):
    """Upgrade legacy '<product_id>' keys to '<product_id>:0' shape in-place."""
    if not cart:
        return cart
    fixed = {}
    changed = False
    for key, qty in cart.items():
        if ':' not in str(key):
            try:
                new_key = f'{int(key)}:0'
                fixed[new_key] = qty
                changed = True
                continue
            except (TypeError, ValueError):
                continue
        fixed[str(key)] = qty
    if changed:
        session['cart'] = fixed
        session.modified = True
        return fixed
    return cart


def get_cart_count():
    cart = _normalize_cart(session.get('cart', {}))
    return sum(cart.values())


def get_cart_items():
    from app.models import Product, ProductVariant
    cart = _normalize_cart(session.get('cart', {}))
    items = []
    for key, quantity in cart.items():
        product_id, variant_id = _parse_line_key(key)
        if not product_id:
            continue
        product = Product.query.get(product_id)
        if not product or not product.is_active:
            continue
        variant = None
        if variant_id:
            variant = ProductVariant.query.get(variant_id)
            if not variant or variant.product_id != product.id or not variant.is_active:
                continue
        unit_price = variant.effective_price if variant else product.price
        unit_price = Decimal(str(unit_price))
        items.append({
            'product': product,
            'variant': variant,
            'quantity': quantity,
            'unit_price': unit_price,
            'total': unit_price * quantity,
            'line_key': key,
        })
    return items


def get_cart_subtotal():
    items = get_cart_items()
    return sum((item['total'] for item in items), Decimal('0'))


def get_site_setting(key, default=''):
    from app.models import SiteSettings
    try:
        setting = SiteSettings.query.filter_by(key=key).first()
        return setting.value if setting else default
    except Exception:
        return default


def generate_slug(text):
    try:
        from slugify import slugify
        return slugify(text)
    except ImportError:
        import re
        text = text.lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[\s_-]+', '-', text)
        return text.strip('-')


def generate_order_number():
    from app.models import Order
    last = Order.query.order_by(Order.id.desc()).first()
    next_id = (last.id + 1) if last else 1
    return f'S99-{next_id:05d}'
