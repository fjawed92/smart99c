"""Product backups: what a product looked like before IZY Ops changed it.

- ``snapshot(product, reason)``      one product's name, prices, texts,
                                     category, photos and colours, as JSON
- ``before_izyops_change(product)``  a snapshot before an IZY Ops update, at
                                     most one per product per hour
- ``full_backup_once()``             every product, the first time IZY Ops
                                     connects (kept as one batch)
- ``restore(backup)``                put a snapshot's values back

Backups older than KEEP_DAYS are cleared, except the first full backup,
which is kept for good.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Product, ProductBackup, ProductVariant
from app.services.product_images import own_image_urls, set_product_images

KEEP_DAYS = 180
ONE_PER = timedelta(hours=1)
FIRST_BATCH = 'first-izyops'
FIRST_REASON = 'Before IZY Ops connected'
CHANGE_REASON = 'Before an IZY Ops change'


def _money(value):
    return None if value is None else str(value)


def product_data(product):
    return {
        'name': product.name,
        'slug': product.slug,
        'sku': product.sku,
        'upc': product.upc,
        'category': product.category.name if product.category else None,
        'short_description': product.short_description,
        'description': product.description,
        'price': _money(product.price),
        'compare_price': _money(product.compare_price),
        'cost_price': _money(product.cost_price),
        'is_active': bool(product.is_active),
        'is_featured': bool(product.is_featured),
        'stock_quantity': product.stock_quantity,
        'images': own_image_urls(product),
        'variants': [{
            'id': v.id,
            'color_name': v.color_name,
            'color_hex': v.color_hex,
            'sku': v.sku,
            'price_override': _money(v.price_override),
            'cost_price': _money(v.cost_price),
            'stock_quantity': v.stock_quantity,
            'is_active': bool(v.is_active),
        } for v in product.variants],
    }


def snapshot(product, reason, batch=None):
    row = ProductBackup(product_id=product.id, sku=product.sku, name=product.name, reason=reason,
                        batch=batch, data=json.dumps(product_data(product)))
    db.session.add(row)
    return row


def before_izyops_change(product, now=None):
    """Save the product as it is now, unless it was saved in the last hour.
    Returns the new backup (not yet committed), or None."""
    now = now or datetime.utcnow()
    recent = (ProductBackup.query
              .filter(ProductBackup.product_id == product.id, ProductBackup.batch.is_(None),
                      ProductBackup.taken_at > now - ONE_PER)
              .first())
    if recent is None:
        return snapshot(product, CHANGE_REASON)
    return None


def full_backup_once():
    """Every product as it is now, the first time IZY Ops connects. Commits.
    Returns how many products were saved (0 when it was done before)."""
    if ProductBackup.query.filter_by(batch=FIRST_BATCH).first() is not None:
        return 0
    products = Product.query.order_by(Product.id).all()
    for product in products:
        snapshot(product, FIRST_REASON, batch=FIRST_BATCH)
    if not products:
        # Remember it was done, even for an empty catalog.
        db.session.add(ProductBackup(reason=FIRST_REASON, batch=FIRST_BATCH, data='{}'))
    db.session.commit()
    prune()
    return len(products)


def prune(now=None):
    cutoff = (now or datetime.utcnow()) - timedelta(days=KEEP_DAYS)
    ProductBackup.query.filter(ProductBackup.taken_at < cutoff, ProductBackup.batch.is_(None)).delete(
        synchronize_session=False)
    db.session.commit()


def data_of(backup):
    try:
        return json.loads(backup.data or '{}')
    except ValueError:
        return {}


def _dec(value):
    return None if value in (None, '') else Decimal(str(value))


def restore(backup):
    """Put the backup's values back on its product. Stock is left alone (it
    comes from IZY Ops). Returns the product, or None if it no longer exists.
    Commits; the current values are saved as a backup first."""
    from app.services.product_import import _resolve_category

    data = data_of(backup)
    product = db.session.get(Product, backup.product_id) if backup.product_id else None
    if product is None or not data:
        return None
    snapshot(product, f'Before restoring the backup from {backup.taken_at:%b %d %H:%M} UTC')
    product.name = data.get('name') or product.name
    product.short_description = data.get('short_description')
    product.description = data.get('description')
    if data.get('price') is not None:
        product.price = _dec(data['price'])
    product.compare_price = _dec(data.get('compare_price'))
    product.cost_price = _dec(data.get('cost_price'))
    product.upc = data.get('upc')
    product.is_active = bool(data.get('is_active', product.is_active))
    product.is_featured = bool(data.get('is_featured', product.is_featured))
    if data.get('category'):
        product.category_id = _resolve_category(data['category'], {})[0].id
    elif 'category' in data:
        product.category_id = None
    set_product_images(product, list(data.get('images') or []))
    for row in data.get('variants') or []:
        variant = db.session.get(ProductVariant, row.get('id')) if row.get('id') else None
        if variant is None or variant.product_id != product.id:
            continue
        variant.color_name = row.get('color_name') or variant.color_name
        variant.color_hex = row.get('color_hex')
        variant.price_override = _dec(row.get('price_override'))
        variant.cost_price = _dec(row.get('cost_price'))
        variant.is_active = bool(row.get('is_active', variant.is_active))
    product.updated_at = datetime.utcnow()
    db.session.commit()
    return product


def first_backup_rows():
    return (ProductBackup.query.filter(ProductBackup.batch == FIRST_BATCH, ProductBackup.product_id.isnot(None))
            .order_by(ProductBackup.name).all())


