"""Starter data for a brand-new database (used by init_db.py and seed.py)."""
from decimal import Decimal

DEFAULT_CATEGORIES = [
    ('Pokémon Cards', 'Sealed Pokémon TCG booster packs, Elite Trainer Boxes, tins and bundles.'),
    ('Trading Card Games', 'Magic: The Gathering, Yu-Gi-Oh! and more.'),
    ('Card Accessories', 'Sleeves, toploaders, binders and deck boxes.'),
    ('Board Games', 'Family favorites and party games.'),
    ('Action Figures', 'Heroes, villains and collectible figures.'),
    ('Plush', 'Soft, squishy friends for all ages.'),
    ('Puzzles', 'Jigsaw puzzles and brain teasers.'),
    ('Outdoor Toys', 'Bubbles, balls, RC cars and backyard fun.'),
    ('Party & Balloons', 'Balloons and party supplies from the store.'),
]

# (name, price, min_order_amount, estimated_days)
DEFAULT_SHIPPING = [
    ('Store pickup (West Babylon)', Decimal('0.00'), None, 'Ready in about 1 hour'),
    ('Local delivery', Decimal('4.99'), None, '1–2 days'),
    ('Free local delivery', Decimal('0.00'), Decimal('35.00'), '1–2 days'),
]


def ensure_defaults(db, Category, ShippingRate, slugify):
    """Create starter categories / delivery options only when none exist."""
    created = []
    if not Category.query.first():
        for i, (name, desc) in enumerate(DEFAULT_CATEGORIES):
            db.session.add(Category(name=name, slug=slugify(name), description=desc,
                                    is_active=True, sort_order=i))
        created.append('categories')
    if not ShippingRate.query.first():
        for i, (name, price, minimum, days) in enumerate(DEFAULT_SHIPPING):
            db.session.add(ShippingRate(name=name, price=price, min_order_amount=minimum,
                                        estimated_days=days, is_active=True, sort_order=i))
        created.append('delivery options')
    if created:
        db.session.commit()
    return created
