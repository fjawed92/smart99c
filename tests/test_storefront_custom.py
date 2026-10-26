"""Admin → Banner & Filters, and drag-and-drop product order."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from app.helpers import get_site_setting, set_site_setting
from app.models import Product


def _csrf_off(app):
    app.config['WTF_CSRF_ENABLED'] = False


def _products(db, n):
    base = datetime(2026, 1, 1)
    items = []
    for i in range(n):
        p = Product(name=f'P{i}', slug=f'p{i}', price=Decimal('1.00') + i,
                    is_active=True, created_at=base + timedelta(days=i))
        db.session.add(p)
        items.append(p)
    db.session.commit()
    return items


def _order(db):
    return [p.name for p in Product.query.order_by(*Product.store_order())]


# ─── Scrolling banner ────────────────────────────────────────────────────────

def test_banner_starts_with_old_announcement_and_starter_lines(client, db):
    set_site_setting('announcement', 'Grand opening!')
    db.session.commit()
    html = client.get('/').get_data(as_text=True)
    assert 'Grand opening!' in html
    assert 'Same-day pickup in West Babylon' in html


def test_banner_add_edit_hide_delete(app, admin_client, db):
    _csrf_off(app)
    resp = admin_client.post('/admin/storefront', data={
        'part': 'ticker',
        'text': ['Eid Mubarak!', 'Hidden one', '   '],
        'active': ['1', '', '1'],
    })
    assert resp.status_code == 302
    saved = json.loads(get_site_setting('ticker_messages'))
    assert saved == [{'text': 'Eid Mubarak!', 'active': True},
                     {'text': 'Hidden one', 'active': False}]
    html = admin_client.get('/').get_data(as_text=True)
    assert 'Eid Mubarak!' in html
    assert 'Hidden one' not in html
    assert 'Same-day pickup in West Babylon' not in html  # deleted


def test_banner_hidden_when_everything_deleted(app, admin_client, db):
    _csrf_off(app)
    admin_client.post('/admin/storefront', data={'part': 'ticker'})
    html = admin_client.get('/').get_data(as_text=True)
    assert 'class="ticker"' not in html


def test_banner_reset_brings_back_starter_list(app, admin_client, db):
    _csrf_off(app)
    admin_client.post('/admin/storefront', data={'part': 'ticker', 'text': ['Only'], 'active': ['1']})
    admin_client.post('/admin/storefront', data={'part': 'reset_ticker'})
    assert 'Same-day pickup' in admin_client.get('/').get_data(as_text=True)


def test_settings_save_no_longer_touches_announcement(app, admin_client, db):
    _csrf_off(app)
    set_site_setting('announcement', 'Keep me')
    db.session.commit()
    admin_client.post('/admin/settings', data={'tax_rate': '8.875'})
    assert get_site_setting('announcement') == 'Keep me'


# ─── Shop-by-price buttons ───────────────────────────────────────────────────

def test_default_price_buttons(client, db):
    html = client.get('/shop').get_data(as_text=True)
    assert 'max_price=0.99' in html and 'max_price=9.99' in html
    assert 'deals=1' in html


def test_custom_price_buttons(app, admin_client, db):
    _csrf_off(app)
    admin_client.post('/admin/storefront', data={
        'part': 'filters',
        'kind': ['price', 'price', 'sale', 'price'],
        'label': ['$2.99', '$5 – $10', 'Deals', 'Off'],
        'note': ['& under', '', '', ''],
        'min': ['', '10', '', ''],
        'max': ['2.99', '$5', '', '1'],
        'active': ['1', '1', '1', ''],
    })
    saved = json.loads(get_site_setting('shop_filters'))
    assert saved[1]['min'] == '5.00' and saved[1]['max'] == '10.00'  # swapped into order
    html = admin_client.get('/shop').get_data(as_text=True)
    assert '<b>$2.99</b> &amp; under' in html
    assert 'min_price=5.0&amp;max_price=10.0' in html
    assert 'Deals' in html
    assert 'max_price=0.99' not in html
    assert '<b>Off</b>' not in html  # switched off


def test_price_button_filters_and_highlights(client, db):
    _products(db, 3)  # prices 1, 2, 3
    html = client.get('/shop?max_price=1.99').get_data(as_text=True)
    assert 'P0' in html and 'P1' not in html
    assert 'pchip is-active' in html


def test_price_button_without_price_is_dropped(app, admin_client, db):
    _csrf_off(app)
    resp = admin_client.post('/admin/storefront', data={
        'part': 'filters', 'kind': ['price'], 'label': ['Cheap'], 'note': [''],
        'min': [''], 'max': [''], 'active': ['1']}, follow_redirects=True)
    assert 'needs a price' in resp.get_data(as_text=True)
    assert json.loads(get_site_setting('shop_filters')) == []


# ─── Product order ───────────────────────────────────────────────────────────

def test_new_products_show_newest_first_by_default(db):
    _products(db, 3)
    assert _order(db) == ['P2', 'P1', 'P0']


def test_drag_reorder_page(app, admin_client, db):
    _products(db, 4)  # store order: P3 P2 P1 P0
    ps = {p.name: p.id for p in Product.query}
    _csrf_off(app)
    resp = admin_client.post('/admin/products/reorder',
                             json={'ids': [ps['P0'], ps['P3'], ps['P2'], ps['P1']]})
    assert resp.get_json()['success']
    assert _order(db) == ['P0', 'P3', 'P2', 'P1']
    # The shop's default sort follows it.
    html = admin_client.get('/shop').get_data(as_text=True)
    assert html.index('>P0<') < html.index('>P3<') < html.index('>P1<')


def test_drag_reorder_subset_keeps_others_in_place(app, admin_client, db):
    _products(db, 4)  # P3 P2 P1 P0
    ps = {p.name: p.id for p in Product.query}
    _csrf_off(app)
    # Only P3 and P1 on screen (e.g. filtered): swap them.
    admin_client.post('/admin/products/reorder', json={'ids': [ps['P1'], ps['P3']]})
    assert _order(db) == ['P1', 'P2', 'P3', 'P0']


def test_move_to_top_and_bottom(app, admin_client, db):
    _products(db, 4)  # P3 P2 P1 P0
    ps = {p.name: p.id for p in Product.query}
    _csrf_off(app)
    admin_client.post('/admin/products/move', data={'where': 'top', 'product_ids': [ps['P0']]})
    assert _order(db) == ['P0', 'P3', 'P2', 'P1']
    admin_client.post('/admin/products/move', data={'where': 'bottom', 'product_ids': [ps['P3']]})
    assert _order(db) == ['P0', 'P2', 'P1', 'P3']


def test_reorder_needs_admin(client, db):
    _products(db, 2)
    assert client.post('/admin/products/reorder', json={'ids': [1, 2]}).status_code in (302, 401, 403)


def test_admin_products_and_storefront_pages_render(admin_client, db):
    _products(db, 2)
    assert 'productRows' in admin_client.get('/admin/products').get_data(as_text=True)
    assert 'Scrolling banner' in admin_client.get('/admin/storefront').get_data(as_text=True)
