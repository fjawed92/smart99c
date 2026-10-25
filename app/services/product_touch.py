"""Mark a product changed when one of its colours or photos changes.

IZY Ops reads products changed since its last look (GET /api/izyops/products
?since=), keyed on ``Product.updated_at``. A colour's price or a new photo is
a change to the product too, so it bumps the product's time.
"""
from datetime import datetime

from sqlalchemy import event

from app.extensions import db
from app.models import Product, ProductImage, ProductVariant

_installed = False


def _before_flush(session, flush_context, instances):
    now = datetime.utcnow()
    touched = set()
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, (ProductVariant, ProductImage)):
            if obj in session.dirty and not session.is_modified(obj):
                continue
            product = obj.product if obj.product is not None else (
                session.get(Product, obj.product_id) if obj.product_id else None)
            if product is not None and product not in session.deleted and id(product) not in touched:
                touched.add(id(product))
                product.updated_at = now


def install():
    global _installed
    if _installed:
        return
    event.listen(db.session, 'before_flush', _before_flush)
    _installed = True
