"""Permanent item numbers (SKUs) for IZY Ops.

IZY Ops links this site's products to its own by SKU, for stock and for
product details. A product or colour without one gets ``SC99-00042`` (the
product id) or ``SC99-00042-V7`` (the colour's id), once; a SKU that is
already set is never changed. ``SC99-`` keeps them apart from order numbers
(``S99-00001``).
"""
from app.extensions import db
from app.models import Product, ProductVariant

PREFIX = 'SC99-'


def _taken(sku):
    return (Product.query.filter_by(sku=sku).first() is not None
            or ProductVariant.query.filter_by(sku=sku).first() is not None)


def _free(base):
    sku, n = base, 1
    while _taken(sku):
        n += 1
        sku = f'{base}-{n}'
    return sku


def ensure_skus():
    """Give every product and colour without a SKU its own. Returns how many
    were filled in (and commits when there were any)."""
    filled = 0
    for product in Product.query.filter(db.or_(Product.sku.is_(None), Product.sku == '')).all():
        product.sku = _free(f'{PREFIX}{product.id:05d}')
        db.session.flush()
        filled += 1
    blank = db.or_(ProductVariant.sku.is_(None), ProductVariant.sku == '')
    for variant in ProductVariant.query.filter(blank).all():
        variant.sku = _free(f'{PREFIX}{variant.product_id:05d}-V{variant.id}')
        db.session.flush()
        filled += 1
    if filled:
        db.session.commit()
    return filled
