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

        # ── Categories ────────────────────────────────────────────
        category_names = [
            'Cleaning Supplies',
            'Kitchen & Dining',
            'Party Supplies',
            'Personal Care',
            'Toys & Games',
            'Stationery',
            'Seasonal',
            'Food & Snacks',
        ]

        categories = {}
        for i, name in enumerate(category_names):
            slug = generate_slug(name)
            existing = Category.query.filter_by(slug=slug).first()
            if existing:
                categories[name] = existing
                print(f'  Category exists: {name}')
            else:
                cat = Category(
                    name=name,
                    slug=slug,
                    description=f'Browse our selection of {name.lower()}.',
                    is_active=True,
                    sort_order=i,
                )
                db.session.add(cat)
                db.session.flush()
                categories[name] = cat
                print(f'  Created category: {name}')

        # ── Sample Products ───────────────────────────────────────
        sample_products = [
            {
                'name': 'All-Purpose Cleaner Spray',
                'short_description': 'Powerful multi-surface cleaner for kitchen, bathroom, and more.',
                'description': '<p>Our all-purpose cleaner cuts through grease, grime, and tough stains on all surfaces. Safe for use in kitchens, bathrooms, and throughout the home.</p><ul><li>Ready-to-use formula</li><li>Fresh citrus scent</li><li>32 oz bottle</li></ul>',
                'price': Decimal('1.99'),
                'compare_price': Decimal('3.49'),
                'sku': 'CLN-001',
                'stock_quantity': 150,
                'weight': Decimal('2.5'),
                'category': 'Cleaning Supplies',
                'is_featured': True,
                'image_url': 'https://images.unsplash.com/photo-1585421514738-01798e348b17?w=600&q=80',
            },
            {
                'name': 'Colorful Party Balloons (Pack of 50)',
                'short_description': 'Bright, durable latex balloons perfect for any celebration.',
                'description': '<p>Make any party pop with our vibrant assorted balloon pack! Includes 50 premium latex balloons in a rainbow of colors. Perfect for birthdays, graduations, baby showers, and more.</p><ul><li>50 balloons per pack</li><li>Assorted bright colors</li><li>Premium latex, easy to inflate</li></ul>',
                'price': Decimal('2.99'),
                'compare_price': None,
                'sku': 'PTY-002',
                'stock_quantity': 200,
                'weight': Decimal('0.5'),
                'category': 'Party Supplies',
                'is_featured': True,
                'image_url': 'https://images.unsplash.com/photo-1530103862676-de8c9debad1d?w=600&q=80',
            },
            {
                'name': 'Kids Art Supply Set',
                'short_description': 'Complete drawing set with crayons, markers, colored pencils, and sketchpad.',
                'description': '<p>Give your little artist everything they need to create! This complete art set includes crayons, washable markers, colored pencils, and a sketchpad — all in one convenient carry case.</p><ul><li>24 crayons</li><li>12 washable markers</li><li>12 colored pencils</li><li>50-page sketchpad</li><li>Carry case included</li></ul>',
                'price': Decimal('4.99'),
                'compare_price': Decimal('9.99'),
                'sku': 'TOY-003',
                'stock_quantity': 75,
                'weight': Decimal('1.2'),
                'category': 'Stationery',
                'is_featured': True,
                'image_url': 'https://images.unsplash.com/photo-1513364776144-60967b0f800f?w=600&q=80',
            },
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

            if p_data.get('image_url'):
                img = ProductImage(
                    product_id=product.id,
                    image_url=p_data['image_url'],
                    is_primary=True,
                    sort_order=0,
                )
                db.session.add(img)

            print(f'  Created product: {p_data["name"]}')

        # ── Shipping Rates ────────────────────────────────────────
        if not ShippingRate.query.first():
            rates = [
                ShippingRate(name='Standard Shipping', price=Decimal('5.99'), estimated_days='3–5 business days', is_active=True, sort_order=0),
                ShippingRate(name='Express Shipping',  price=Decimal('12.99'), estimated_days='1–2 business days', is_active=True, sort_order=1),
                ShippingRate(name='Free Shipping',     price=Decimal('0.00'),  min_order_amount=Decimal('50.00'), estimated_days='5–7 business days', is_active=True, sort_order=2),
            ]
            for r in rates:
                db.session.add(r)
            print('Created shipping rates.')

        # ── Site Settings ─────────────────────────────────────────
        defaults = {
            'announcement': '🚚 Free shipping on orders over $50!',
            'tax_rate': '8.875',
            'free_shipping_threshold': '50',
            'store_hours': 'Mon–Sat: 9:00 AM – 8:00 PM\nSunday: 10:00 AM – 6:00 PM',
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
