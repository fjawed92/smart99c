"""Save products picked on Admin → Pull from IzyOps.

Each item is what the admin reviewed and edited on screen:
    izyops_product_id, name, price, compare_price, cost, category, stock,
    short_description, is_featured, is_active, upc, sku, image_url

Existing site products are matched by IzyOps id, then UPC, then SKU, then
name, and updated in place, so pulling the same product twice never makes a
duplicate.
"""
from decimal import Decimal, InvalidOperation

from flask import current_app

from app.extensions import db
from app.helpers import generate_slug
from app.models import Product, ProductImage
from app.services.product_import import _resolve_category, _unique_slug


def _money(value):
    if value in (None, ''):
        return None
    try:
        d = Decimal(str(value).strip().lstrip('$'))
    except (InvalidOperation, ValueError):
        raise ValueError(f'"{value}" is not a valid price')
    if d < 0:
        raise ValueError('prices cannot be negative')
    return d.quantize(Decimal('0.01'))


def _int(value):
    if value in (None, ''):
        return None
    try:
        return max(0, int(float(str(value).strip())))
    except ValueError:
        raise ValueError(f'"{value}" is not a whole number')


def find_existing(izyops_id=None, upc=None, sku=None, name=None):
    q = Product.query
    if izyops_id:
        p = q.filter_by(izyops_product_id=int(izyops_id)).first()
        if p:
            return p
    if upc:
        p = q.filter_by(upc=upc).first()
        if p:
            return p
    if sku:
        p = q.filter_by(sku=sku).first()
        if p:
            return p
    if name:
        return q.filter(db.func.lower(Product.name) == name.strip().lower()).first()
    return None


def on_site_map(items):
    """{izyops product_id: site Product} for search results already on the site."""
    ids = [i['product_id'] for i in items if i.get('product_id')]
    upcs = [i['upc_code'] for i in items if i.get('upc_code')]
    found = {}
    if ids:
        for p in Product.query.filter(Product.izyops_product_id.in_(ids)).all():
            found[p.izyops_product_id] = p
    if upcs:
        by_upc = {p.upc: p for p in Product.query.filter(Product.upc.in_(upcs)).all()}
        for i in items:
            if i['product_id'] not in found and i.get('upc_code') in by_upc:
                found[i['product_id']] = by_upc[i['upc_code']]
    return found


def _store_image(url, name):
    """Copy the picture into Cloudinary when it's set up; otherwise link to it."""
    if current_app.config.get('CLOUDINARY_CLOUD_NAME'):
        try:
            import cloudinary.uploader
            up = cloudinary.uploader.upload(url, folder='smart99c/products', public_id=generate_slug(name)[:80] or None,
                                            overwrite=True, resource_type='image')
            return up.get('secure_url') or url, up.get('public_id')
        except Exception:  # noqa: BLE001 — fall back to the original link
            current_app.logger.warning('Cloudinary upload failed for %s; linking instead', url)
    return url, None


def save_items(items):
    result = {'created': 0, 'updated': 0, 'errors': []}
    cat_cache = {}
    for item in items:
        name = (item.get('name') or '').strip()
        label = name or f"IzyOps #{item.get('izyops_product_id')}"
        savepoint = db.session.begin_nested()
        try:
            price = _money(item.get('price'))
            if not name:
                raise ValueError('a name is required')
            product = find_existing(item.get('izyops_product_id'), item.get('upc'), item.get('sku'), name)
            created = product is None
            if created:
                if price is None or price <= 0:
                    raise ValueError('a price is required')
                product = Product(name=name, slug=_unique_slug(generate_slug(name) or 'product'),
                                  price=price, stock_quantity=0, track_inventory=True, is_active=True)
                db.session.add(product)
            else:
                product.name = name
                if price is not None and price > 0:
                    product.price = price

            product.compare_price = _money(item.get('compare_price'))
            cost = _money(item.get('cost'))
            if cost is not None:
                product.cost_price = cost
            stock = _int(item.get('stock'))
            if stock is not None:
                product.stock_quantity = stock
            if item.get('short_description') is not None:
                product.short_description = (item.get('short_description') or '').strip()[:500] or None
            product.is_featured = bool(item.get('is_featured'))
            product.is_active = bool(item.get('is_active', True))
            if item.get('izyops_product_id'):
                product.izyops_product_id = int(item['izyops_product_id'])
            if item.get('upc'):
                product.upc = str(item['upc']).strip()
            sku = (item.get('sku') or '').strip()
            if sku and (product.sku is None or product.sku == sku):
                clash = Product.query.filter(Product.sku == sku, Product.id != product.id).first() if product.id else \
                    Product.query.filter_by(sku=sku).first()
                if not clash:
                    product.sku = sku
            cat_name = (item.get('category') or '').strip()
            if cat_name:
                cat, _ = _resolve_category(cat_name, cat_cache)
                product.category_id = cat.id
            db.session.flush()

            url = (item.get('image_url') or '').strip()
            if url and not product.images:
                stored, public_id = _store_image(url, name)
                db.session.add(ProductImage(product_id=product.id, image_url=stored,
                                            cloudinary_public_id=public_id, is_primary=True, sort_order=0))

            savepoint.commit()
            result['created' if created else 'updated'] += 1
        except ValueError as e:
            savepoint.rollback()
            result['errors'].append(f'{label}: {e}')
        except Exception as e:  # noqa: BLE001
            savepoint.rollback()
            current_app.logger.exception('IzyOps import failed for %s', label)
            result['errors'].append(f'{label}: could not be saved ({e.__class__.__name__})')
    db.session.commit()
    return result
