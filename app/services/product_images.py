"""Set a product's own photos from a list of links.

Used by IZY Ops' product updates, the Excel import's ``image_urls`` column and
Restore from a backup. A link is copied into Cloudinary once (named after the
link, so the same picture is never uploaded twice); without Cloudinary the
site links to it directly. Photos that are replaced are only unlinked from
the product, never deleted from Cloudinary, so a backup can bring them back.
"""
import hashlib

from flask import current_app

from app.extensions import db
from app.models import ProductImage


def split_urls(text):
    """'a, b' or one link per line → ['a', 'b'] (http(s) links only)."""
    parts = str(text or '').replace('\n', ',').replace(' ', ',').split(',')
    return list(dict.fromkeys(p.strip() for p in parts if p.strip().startswith(('http://', 'https://'))))


def own_image_urls(product):
    """The product's own photos (not a colour's), main photo first."""
    gallery = product.gallery_images
    primary = product.primary_image
    ordered = ([primary] if primary in gallery else []) + [img for img in gallery if img is not primary]
    return [img.image_url for img in ordered if img.image_url]


def stored_url(url):
    """(image_url, cloudinary_public_id) for a link, uploading it at most once."""
    key = 'izy_' + hashlib.sha1(url.encode()).hexdigest()[:24]
    known = ProductImage.query.filter(db.or_(ProductImage.cloudinary_public_id.like(f'%{key}'),
                                             ProductImage.image_url == url)).first()
    if known is not None:
        return known.image_url, known.cloudinary_public_id
    if current_app.config.get('CLOUDINARY_CLOUD_NAME'):
        try:
            import cloudinary.uploader
            up = cloudinary.uploader.upload(url, folder='smart99c/products', public_id=key,
                                            overwrite=True, resource_type='image')
            return up.get('secure_url') or url, up.get('public_id')
        except Exception:  # noqa: BLE001 — link to the original instead
            current_app.logger.warning('Cloudinary upload failed for %s; linking instead', url)
    return url, None


def set_product_images(product, urls):
    """Make the product's own photos these, in this order. True on a change."""
    current = [img for img in product.images if img.variant_id is None]
    if [img.image_url for img in current] == urls:
        return False
    stored = [stored_url(url) for url in urls]
    if [img.image_url for img in current] == [u for u, _ in stored]:
        return False
    for img in current:
        product.images.remove(img)
    for idx, (url, public_id) in enumerate(stored):
        product.images.append(ProductImage(image_url=url, cloudinary_public_id=public_id,
                                           is_primary=idx == 0, sort_order=idx))
    return True
