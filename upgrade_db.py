"""
Schema upgrade script — run once after pulling the color-variants release.

Adds:
    - product_variants table
    - product_images.variant_id column
    - order_items.variant_id column
    - order_items.product_color column

Safe to run multiple times: each step is checked first.

Usage:
    python upgrade_db.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import inspect, text
from app import create_app
from app.extensions import db


def column_exists(inspector, table, column):
    if table not in inspector.get_table_names():
        return False
    return column in {c['name'] for c in inspector.get_columns(table)}


def upgrade():
    app = create_app(os.environ.get('FLASK_ENV', 'development'))
    with app.app_context():
        inspector = inspect(db.engine)

        # 1. Create any missing tables (product_variants is the new one).
        print('Ensuring all tables exist…')
        db.create_all()

        # Refresh inspector after create_all
        inspector = inspect(db.engine)

        # 2. product_images.variant_id
        if not column_exists(inspector, 'product_images', 'variant_id'):
            print('Adding product_images.variant_id…')
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE product_images ADD COLUMN variant_id INTEGER '
                    'REFERENCES product_variants(id) ON DELETE SET NULL'
                ))
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS ix_product_images_variant_id '
                    'ON product_images (variant_id)'
                ))
        else:
            print('product_images.variant_id already present — skipping.')

        # 3. order_items.variant_id
        if not column_exists(inspector, 'order_items', 'variant_id'):
            print('Adding order_items.variant_id…')
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE order_items ADD COLUMN variant_id INTEGER '
                    'REFERENCES product_variants(id) ON DELETE SET NULL'
                ))
        else:
            print('order_items.variant_id already present — skipping.')

        # 4. order_items.product_color
        if not column_exists(inspector, 'order_items', 'product_color'):
            print('Adding order_items.product_color…')
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE order_items ADD COLUMN product_color VARCHAR(60)'
                ))
        else:
            print('order_items.product_color already present — skipping.')

        print('Done.')


if __name__ == '__main__':
    upgrade()
