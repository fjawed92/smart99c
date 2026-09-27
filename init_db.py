"""
Start-up database check — safe to run on every deploy.

Render's start command runs this before gunicorn:
    python init_db.py && gunicorn run:app

It:
    1. creates any missing tables (a brand-new, empty database works),
    2. adds columns introduced by later releases (same checks as upgrade_db.py),
    3. adds starter categories and delivery options to an empty store,
    4. creates the first admin from ADMIN_EMAIL / ADMIN_PASSWORD when the
       database has no admin yet.

Nothing here deletes or overwrites existing data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import User, Category, ShippingRate
from app.defaults import ensure_defaults
from app.helpers import generate_slug

# (table, column, SQL type) added after the first release.
EXTRA_COLUMNS = [
    ('product_images', 'variant_id', 'INTEGER REFERENCES product_variants(id) ON DELETE SET NULL'),
    ('order_items', 'variant_id', 'INTEGER REFERENCES product_variants(id) ON DELETE SET NULL'),
    ('order_items', 'product_color', 'VARCHAR(60)'),
    ('products', 'upc', 'VARCHAR(120)'),
    ('products', 'izyops_product_id', 'INTEGER'),
]


def ensure_schema():
    db.create_all()
    inspector = inspect(db.engine)
    for table, column, sql_type in EXTRA_COLUMNS:
        if table not in inspector.get_table_names():
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        print(f'Adding {table}.{column}…')
        with db.engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type}'))


def ensure_admin():
    if User.query.filter_by(is_admin=True).first():
        return
    email = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
    password = os.environ.get('ADMIN_PASSWORD') or ''
    if not email or not password:
        print('No admin account yet — set ADMIN_EMAIL and ADMIN_PASSWORD to create one.')
        return
    admin = User(email=email, first_name='Store', last_name='Admin',
                 is_admin=True, is_active=True, force_password_change=False)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f'Created admin account {email}.')


def init_db(app=None):
    app = app or create_app(os.environ.get('FLASK_ENV', 'development'))
    with app.app_context():
        ensure_schema()
        for what in ensure_defaults(db, Category, ShippingRate, generate_slug):
            print(f'Created starter {what}.')
        ensure_admin()
    print('Database ready.')


if __name__ == '__main__':
    init_db()
