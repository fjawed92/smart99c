"""Pull from IzyOps: API client (mocked HTTP), saving products, admin pages."""
import json
from decimal import Decimal
from unittest import mock

import pytest

from app.models import Category, Product
from app.services import izyops_client
from app.services.izyops_import import save_items


@pytest.fixture
def izyops_env(monkeypatch):
    monkeypatch.setenv('IZYOPS_URL', 'https://izyops.test')
    monkeypatch.setenv('IZYOPS_USERNAME', 'webstore')
    monkeypatch.setenv('IZYOPS_PASSWORD', 'secret')
    monkeypatch.setenv('IZYOPS_STORE_NAME', 'Smart 99c')


def _resp(status=200, body=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = body if body is not None else {}
    return r


ITEM = {'product_id': 7, 'product_name': 'POKEMON TCG SV BOOSTER PK', 'upc_code': '820650857017',
        'item_code': 'PKM-BP', 'unit_price': 5.99, 'unit_cost': 3.62, 'latest_cost': None,
        'quantity_on_hand': 12.0, 'image_url': '/static/img/7.png', 'vendor_name': 'Southern Hobby',
        'product_category': 'TOYS'}


def test_not_configured_gives_setup_message(monkeypatch):
    monkeypatch.delenv('IZYOPS_URL', raising=False)
    items, meta, err = izyops_client.search_products('keyword', 'pokemon')
    assert items == [] and 'not connected' in err


def test_search_sends_store_stock_and_auth(izyops_env):
    body = {'data': [dict(ITEM)], 'meta': {'total': 1, 'pages': 1}, 'error': None}
    with mock.patch('app.services.izyops_client.requests.get', return_value=_resp(200, body)) as get:
        items, meta, err = izyops_client.search_products('keyword', 'pokemon')
    assert err is None and meta['total'] == 1
    url, = get.call_args.args
    kwargs = get.call_args.kwargs
    assert url == 'https://izyops.test/api/v2/products'
    assert kwargs['auth'] == ('webstore', 'secret')
    assert kwargs['params']['search'] == 'pokemon'
    assert kwargs['params']['store_name'] == 'Smart 99c'
    assert kwargs['params']['include_stock'] == 'true'
    assert items[0]['image_url'] == 'https://izyops.test/static/img/7.png'


def test_vendor_and_upc_modes(izyops_env):
    body = {'data': [dict(ITEM), dict(ITEM, product_id=8, upc_code='999')], 'meta': {}, 'error': None}
    with mock.patch('app.services.izyops_client.requests.get', return_value=_resp(200, body)) as get:
        izyops_client.search_products('vendor', '3')
        assert get.call_args.kwargs['params']['vendor_id'] == '3'
        items, _, _ = izyops_client.search_products('upc', '820650857017')
    assert [i['product_id'] for i in items] == [7]


def test_bad_password_is_explained(izyops_env):
    with mock.patch('app.services.izyops_client.requests.get', return_value=_resp(401, {})):
        _, _, err = izyops_client.search_products('keyword', 'x')
    assert 'username or password' in err


def test_tidy_name():
    assert izyops_client.tidy_name('POKEMON TCG SV BOOSTER PK') == 'Pokémon TCG SV Booster PK'
    assert izyops_client.tidy_name('Already Nice') == 'Already Nice'


def _payload(**over):
    base = {'izyops_product_id': 7, 'upc': '820650857017', 'sku': 'PKM-BP', 'cost': 3.62,
            'name': 'Pokémon TCG Booster Pack', 'category': 'Pokémon Cards', 'price': '6.99',
            'compare_price': '', 'stock': '12', 'is_featured': True, 'is_active': True, 'image_url': ''}
    base.update(over)
    return base


def test_save_creates_then_updates_without_duplicates(app, db):
    r1 = save_items([_payload()])
    assert r1 == {'created': 1, 'updated': 0, 'errors': []}
    p = Product.query.one()
    assert (p.price, p.stock_quantity, p.upc, p.izyops_product_id) == (Decimal('6.99'), 12, '820650857017', 7)
    assert p.category.name == 'Pokémon Cards' and p.is_featured

    r2 = save_items([_payload(price='7.49', stock='3', name='Booster Pack (New Set)')])
    assert r2['updated'] == 1 and Product.query.count() == 1
    p = Product.query.one()
    assert (p.name, p.price, p.stock_quantity) == ('Booster Pack (New Set)', Decimal('7.49'), 3)
    assert Category.query.count() == 1


def test_save_reports_missing_price(app, db):
    r = save_items([_payload(price='', izyops_product_id=99, upc='1', sku='X', name='No Price')])
    assert r['created'] == 0 and 'price is required' in r['errors'][0]


def test_admin_page_without_connection_shows_setup(admin_client, monkeypatch):
    monkeypatch.delenv('IZYOPS_URL', raising=False)
    resp = admin_client.get('/admin/izyops')
    assert resp.status_code == 200 and b'Connect IzyOps' in resp.data


def test_admin_search_and_add(admin_client, db, izyops_env):
    facets = {'data': {'categories': [{'name': 'TOYS', 'product_count': 1}], 'vendors': []}, 'error': None}
    body = {'data': [dict(ITEM)], 'meta': {'total': 1, 'pages': 1}, 'error': None}
    with mock.patch('app.services.izyops_client.requests.get', side_effect=[_resp(200, facets), _resp(200, body)]):
        resp = admin_client.get('/admin/izyops?mode=keyword&q=pokemon')
    assert resp.status_code == 200
    assert b'Pok\xc3\xa9mon TCG SV Booster PK' in resp.data

    resp = admin_client.post('/admin/izyops/add', data={'items_json': json.dumps([_payload()]),
                                                        'back': '/admin/izyops?mode=keyword&q=pokemon'})
    assert resp.status_code == 302 and resp.headers['Location'].startswith('/admin/izyops')
    assert Product.query.count() == 1


def test_admin_add_rejects_offsite_redirect(admin_client, db):
    resp = admin_client.post('/admin/izyops/add', data={'items_json': json.dumps([_payload()]), 'back': 'https://evil.test'})
    assert resp.headers['Location'].endswith('/admin/izyops')


def test_admin_add_rejects_protocol_relative_redirect(admin_client, db):
    resp = admin_client.post('/admin/izyops/add', data={'items_json': json.dumps([_payload()]), 'back': '//evil.test/x'})
    assert resp.headers['Location'].endswith('/admin/izyops')
