import os
import sys
from decimal import Decimal

import pytest

# Ensure the project root is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

os.environ.setdefault('FLASK_ENV', 'testing')

from app import create_app
from app.extensions import db as _db
from app.models import Product, ProductVariant, Category


@pytest.fixture(scope='function')
def app():
    app = create_app('testing')
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def category(db):
    cat = Category(name='Toys', slug='toys')
    db.session.add(cat)
    db.session.commit()
    return cat


@pytest.fixture
def simple_product(db):
    p = Product(name='Basic Tee', slug='basic-tee', price=Decimal('9.99'),
                stock_quantity=20, track_inventory=True, is_active=True)
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def variant_product(db):
    p = Product(name='Color Mug', slug='color-mug', price=Decimal('6.99'),
                stock_quantity=0, track_inventory=True, is_active=True)
    db.session.add(p)
    db.session.flush()
    red = ProductVariant(product_id=p.id, color_name='Red', color_hex='#FF0000',
                         sku='MUG-R', stock_quantity=5, is_active=True)
    blue = ProductVariant(product_id=p.id, color_name='Blue', color_hex='#0000FF',
                          sku='MUG-B', stock_quantity=0, is_active=True)
    green = ProductVariant(product_id=p.id, color_name='Green', color_hex='#00FF00',
                           sku='MUG-G', stock_quantity=10, is_active=True,
                           price_override=Decimal('7.99'))
    db.session.add_all([red, blue, green])
    db.session.commit()
    return p, red, blue, green
