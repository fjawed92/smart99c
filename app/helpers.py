from flask import session
from slugify import slugify as _slugify


def get_cart_count():
    cart = session.get('cart', {})
    return sum(cart.values())


def get_cart_items():
    from app.models import Product
    cart = session.get('cart', {})
    items = []
    for product_id, quantity in cart.items():
        product = Product.query.get(int(product_id))
        if product and product.is_active:
            items.append({'product': product, 'quantity': quantity,
                          'total': product.price * quantity})
    return items


def get_cart_subtotal():
    items = get_cart_items()
    return sum(item['total'] for item in items)


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
