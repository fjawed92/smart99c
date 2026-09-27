"""
Seed script — run once to populate initial data.

Usage:
    python seed.py
"""
import os
import sys

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from app.extensions import db
from app.models import User, Category, Product, ProductImage, ShippingRate, SiteSettings
from app.helpers import generate_slug
from app.defaults import ensure_defaults
from decimal import Decimal


def seed():
    app = create_app(os.environ.get('FLASK_ENV', 'development'))

    with app.app_context():
        print('Creating tables…')
        db.create_all()

        # ── Admin User ────────────────────────────────────────────
        if not User.query.filter_by(email='admin@smart99c.com').first():
            admin = User(
                email='admin@smart99c.com',
                first_name='Admin',
                last_name='Smart99c',
                is_admin=True,
                is_active=True,
                force_password_change=True,
            )
            admin.set_password('Admin123!')
            db.session.add(admin)
            print('Created admin user: admin@smart99c.com / Admin123!')
        else:
            print('Admin user already exists.')

        # ── Categories & delivery options ─────────────────────────
        ensure_defaults(db, Category, ShippingRate, generate_slug)
        categories = {c.name: c for c in Category.query.all()}

        # ── Sample Products (no photos — the site draws an illustration) ──
        def sample(name, short, price, compare, sku, stock, category, featured=False):
            return {'name': name, 'short_description': short, 'description': f'<p>{short}</p>',
                    'price': Decimal(price), 'compare_price': Decimal(compare) if compare else None,
                    'sku': sku, 'stock_quantity': stock, 'weight': Decimal('0.5'),
                    'category': category, 'is_featured': featured}

        sample_products = [
            sample('Pokémon TCG Booster Pack', 'One sealed booster pack of 10 cards.', '5.99', None, 'PKM-BP', 48, 'Pokémon Cards', True),
            sample('Pokémon TCG Elite Trainer Box', '9 booster packs, 65 card sleeves, dice and a player guide.', '49.99', '59.99', 'PKM-ETB', 6, 'Pokémon Cards', True),
            sample('Card Sleeves · 100 Count', 'Standard-size sleeves to protect your pulls.', '3.99', None, 'AC-SL100', 60, 'Card Accessories'),
            sample('UNO Classic Card Game', 'The classic family card game for 2–10 players.', '6.99', None, 'GM-UNO', 22, 'Board Games', True),
            sample('Squish Buddy Plush 12"', 'Extra-soft plush friend.', '8.99', None, 'PL-SQ12', 30, 'Plush', True),
        ]

        for p_data in sample_products:
            slug = generate_slug(p_data['name'])
            if Product.query.filter_by(slug=slug).first():
                print(f'  Product exists: {p_data["name"]}')
                continue

            cat = categories.get(p_data['category'])
            product = Product(
                name=p_data['name'],
                slug=slug,
                short_description=p_data['short_description'],
                description=p_data['description'],
                price=p_data['price'],
                compare_price=p_data.get('compare_price'),
                sku=p_data['sku'],
                stock_quantity=p_data['stock_quantity'],
                track_inventory=True,
                weight=p_data['weight'],
                category_id=cat.id if cat else None,
                is_active=True,
                is_featured=p_data.get('is_featured', False),
            )
            db.session.add(product)
            db.session.flush()

            print(f'  Created product: {p_data["name"]}')

        # ── Site Settings ─────────────────────────────────────────
        defaults = {
            'announcement': '',
            'tax_rate': '8.875',
            'free_shipping_threshold': '35',
        }
        for key, value in defaults.items():
            if not SiteSettings.query.filter_by(key=key).first():
                db.session.add(SiteSettings(key=key, value=value))

        db.session.commit()
        print('\n✓ Seed complete!')
        print('  Admin login: admin@smart99c.com / Admin123!')
        print('  (You will be prompted to change your password on first login.)')


if __name__ == '__main__':
    seed()
