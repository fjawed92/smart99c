"""Excel import: friendly headers, preview without saving, confirm step."""
import json
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.models import Category, Product
from app.services.product_import import apply_rows, parse_workbook, preview_rows


def _xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


SIMPLE = [
    ['Name', 'Category', 'Price', 'Stock', 'UPC'],
    ['Pokémon Booster Pack', 'Pokémon Cards', 5.99, 24, '820650857017'],
    ['Mystery Capsule', 'Plush', None, 3, None],
]


def test_friendly_headers_are_understood(app, db):
    rows, errors = parse_workbook(_xlsx(SIMPLE))
    assert errors == []
    assert rows[0]['product_name'] == 'Pokémon Booster Pack'
    assert rows[0]['price'] == '5.99'
    assert rows[0]['stock_quantity'] == '24'
    assert rows[0]['upc'] == '820650857017'


def test_preview_flags_rows_and_writes_nothing(app, db, simple_product):
    rows, _ = parse_workbook(_xlsx(SIMPLE + [['Basic Tee', None, 11, None, None]]))
    preview = preview_rows(rows)
    assert [r['_status'] for r in preview] == ['new', 'error', 'update']
    assert 'price is required' in preview[1]['_message']
    assert preview[0]['_new_category'] is True
    assert Product.query.count() == 1  # only the fixture product


def test_apply_creates_missing_category_and_sets_upc(app, db):
    rows, _ = parse_workbook(_xlsx(SIMPLE[:2]))
    result = apply_rows(rows)
    assert result['created'] == 1
    p = Product.query.filter_by(name='Pokémon Booster Pack').one()
    assert p.upc == '820650857017'
    assert p.price == Decimal('5.99')
    assert Category.query.filter_by(name='Pokémon Cards').one() == p.category


def test_upload_shows_preview_then_confirm_saves(admin_client, db):
    resp = admin_client.post('/admin/products/import',
                             data={'file': (BytesIO(_xlsx(SIMPLE)), 'restock.xlsx')},
                             content_type='multipart/form-data')
    assert resp.status_code == 200
    assert b'Review import' in resp.data
    assert Product.query.count() == 0

    edited = [{'_row': 2, 'product_name': 'Pokémon Booster Pack', 'category_name': 'Pokémon Cards',
               'price': '4.99', 'stock_quantity': '24'},
              {'_row': 3, 'product_name': 'Skipped', 'price': '1', '_skip': True}]
    resp = admin_client.post('/admin/products/import/confirm', data={'rows_json': json.dumps(edited)})
    assert resp.status_code == 200
    assert Product.query.count() == 1
    assert Product.query.one().price == Decimal('4.99')


def test_upload_rejects_non_xlsx(admin_client):
    resp = admin_client.post('/admin/products/import',
                             data={'file': (BytesIO(b'a,b'), 'list.csv')},
                             content_type='multipart/form-data')
    assert resp.status_code == 302
