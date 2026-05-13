"""Tests for the Excel product import service."""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.models import Product, ProductVariant
from app.services.product_import import COLUMNS, import_workbook


class _FakeFileStorage:
    """Mimic the minimal FileStorage interface (read) used by the import service."""

    def __init__(self, wb):
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        self._buf = buf

    def read(self):
        return self._buf.read()


def _new_workbook_with(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for r in rows:
        ws.append(r)
    return wb


def _row(**kwargs):
    return [kwargs.get(col) for col in COLUMNS]


def test_import_creates_simple_product(app, db, category):
    wb = _new_workbook_with([
        _row(product_name='New Widget', category_name='Toys',
             price=1.99, stock_quantity=15, is_active='TRUE'),
    ])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['created'] == 1
    assert result['errors'] == []
    p = Product.query.filter_by(name='New Widget').first()
    assert p is not None
    assert p.price == Decimal('1.99')
    assert p.stock_quantity == 15
    assert p.category_id == category.id


def test_import_updates_existing_by_slug_and_preserves_blanks(app, db, simple_product):
    # Change price but leave name + description blank → those should not be cleared
    wb = _new_workbook_with([
        _row(product_slug='basic-tee', price=12.50),
    ])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['updated'] == 1
    assert result['created'] == 0
    db.session.refresh(simple_product)
    assert simple_product.price == Decimal('12.50')
    assert simple_product.name == 'Basic Tee'  # unchanged


def test_import_creates_variants(app, db, simple_product):
    wb = _new_workbook_with([
        _row(product_slug='basic-tee', color_name='Red', color_hex='#FF0000',
             variant_sku='TEE-R', variant_stock_quantity=8),
        _row(product_slug='basic-tee', color_name='Blue', color_hex='#0000FF',
             variant_sku='TEE-B', variant_stock_quantity=0),
    ])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['variants_created'] == 2
    db.session.refresh(simple_product)
    variants = {v.color_name: v for v in simple_product.variants}
    assert variants['Red'].stock_quantity == 8
    assert variants['Blue'].stock_quantity == 0
    assert variants['Red'].sku == 'TEE-R'


def test_import_updates_existing_variant_by_sku(app, db, variant_product):
    product, red, *_ = variant_product
    wb = _new_workbook_with([
        _row(product_slug='color-mug', color_name='Red', variant_sku='MUG-R',
             variant_stock_quantity=99, variant_price_override=8.50),
    ])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['variants_updated'] == 1
    db.session.refresh(red)
    assert red.stock_quantity == 99
    assert red.price_override == Decimal('8.50')


def test_import_row_with_invalid_price_is_skipped_others_succeed(app, db):
    wb = _new_workbook_with([
        _row(product_name='Good Item', price=2.99, stock_quantity=10),
        _row(product_name='Bad Item', price='not-a-number'),
        _row(product_name='Another Good Item', price=3.99, stock_quantity=5),
    ])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['created'] == 2
    assert result['skipped'] == 1
    assert len(result['errors']) == 1
    assert Product.query.filter_by(name='Good Item').first() is not None
    assert Product.query.filter_by(name='Bad Item').first() is None
    assert Product.query.filter_by(name='Another Good Item').first() is not None


def test_import_rejects_missing_columns(app, db):
    wb = Workbook()
    ws = wb.active
    ws.append(['only_one_column'])
    ws.append(['something'])
    result = import_workbook(_FakeFileStorage(wb))
    assert result['created'] == 0
    assert any('Missing columns' in msg for _, msg in result['errors'])
