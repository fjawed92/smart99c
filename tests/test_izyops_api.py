"""/api/izyops: IZY Ops manages this site's products, stock and orders through
the same API family99c offers."""
import base64
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe

from app.models import Category, Order, OrderItem, Product, ProductImage, ProductVariant, ShippingAddress


@pytest.fixture
def api(app):
    app.config.update(IZYOPS_API_USERNAME='izy', IZYOPS_API_PASSWORD='secret')
    token = base64.b64encode(b'izy:secret').decode()
    return SimpleNamespace(client=app.test_client(), auth={'Authorization': f'Basic {token}'})


def _order(db, number='S99-00001', status='processing', payment='card', total='20.00', product=None,
           variant=None, shipping='Store pickup', **extra):
    order = Order(order_number=number, status=status, payment_method=payment, subtotal=Decimal(total),
                  tax=0, shipping_cost=0, total=Decimal(total), shipping_method=shipping,
                  stripe_payment_intent_id='pi_123' if payment == 'card' else None, **extra)
    db.session.add(order)
    db.session.flush()
    db.session.add(ShippingAddress(order_id=order.id, first_name='Ann', last_name='Lee', email='ann@x.test',
                                   phone='5551234', address_line1='1 Main St', city='Bronx', state='NY',
                                   zip_code='10458'))
    if product is not None:
        db.session.add(OrderItem(order_id=order.id, product_id=product.id,
                                 variant_id=variant.id if variant else None, product_name=product.name,
                                 product_sku=(variant.sku if variant else product.sku) or '',
                                 product_color=variant.color_name if variant else None, quantity=2,
                                 unit_price=Decimal('10.00'), total_price=Decimal('20.00')))
    db.session.commit()
    return order


# ─── login ───────────────────────────────────────────────────────────────────

def test_health_is_open_and_everything_else_needs_the_login(app, api):
    assert api.client.get('/api/izyops/health').get_json()['data']['ok'] is True
    assert api.client.get('/api/izyops/orders').status_code == 401
    bad = {'Authorization': 'Basic ' + base64.b64encode(b'izy:nope').decode()}
    assert api.client.get('/api/izyops/orders', headers=bad).status_code == 401
    app.config['IZYOPS_API_PASSWORD'] = None
    assert api.client.get('/api/izyops/orders', headers=api.auth).status_code == 401


def test_api_calls_need_no_csrf_token(app, api, db, simple_product):
    app.config['WTF_CSRF_ENABLED'] = True
    simple_product.sku = 'TEE'
    db.session.commit()
    resp = api.client.post('/api/izyops/stock', json={'items': [{'sku': 'TEE', 'quantity': 3}]}, headers=api.auth)
    assert resp.status_code == 200


def test_the_stripe_webhook_needs_no_csrf_token(app, db, monkeypatch, simple_product):
    """It was refused with a 400 before, so paid card orders stayed pending."""
    app.config['WTF_CSRF_ENABLED'] = True
    order = _order(db, status='pending', product=simple_product)
    event = {'type': 'payment_intent.succeeded', 'data': {'object': {'id': 'pi_123', 'latest_charge': 'ch_1'}}}
    monkeypatch.setattr(stripe.Webhook, 'construct_event', lambda payload, sig, secret: event)
    resp = app.test_client().post('/stripe/webhook', data='{}', headers={'Stripe-Signature': 't=1'})
    assert resp.status_code == 200
    assert db.session.get(Order, order.id).status == 'processing'


# ─── products ────────────────────────────────────────────────────────────────

def test_products_get_permanent_skus_and_colours_are_rows(api, db, category, simple_product, variant_product):
    simple_product.category = category
    simple_product.sku = None
    db.session.add(ProductImage(product_id=simple_product.id, image_url='https://img.test/a.jpg', is_primary=True))
    db.session.add(ProductVariant(product_id=variant_product[0].id, color_name='Pink', stock_quantity=1))
    db.session.commit()
    data = api.client.get('/api/izyops/products', headers=api.auth).get_json()['data']
    by_sku = {p['sku']: p for p in data['products']}
    tee = by_sku[f'SC99-{simple_product.id:05d}']
    assert tee['id'] == simple_product.id and tee['category'] == 'Toys' and tee['in_stock'] == 20
    assert tee['image_url'] == 'https://img.test/a.jpg' and tee['price'] == 9.99
    assert {'MUG-R', 'MUG-B', 'MUG-G'} <= set(by_sku)
    green = by_sku['MUG-G']
    assert green['id'] is None and green['name'] == 'Color Mug — Green' and green['price'] == 7.99
    assert green['in_stock'] == 10 and green['variant_of'] == variant_product[0].sku
    pink = next(p for p in data['products'] if p['color'] == 'Pink')
    assert pink['sku'].startswith('SC99-') and '-V' in pink['sku']
    assert variant_product[0].sku not in by_sku or by_sku[variant_product[0].sku]['color']
    # Run again: nothing changes.
    again = api.client.get('/api/izyops/products', headers=api.auth).get_json()['data']
    assert {p['sku'] for p in again['products']} == set(by_sku)


def test_stock_is_set_per_product_and_per_colour(api, db, simple_product, variant_product):
    simple_product.sku = 'TEE'
    db.session.commit()
    mug = variant_product[0]
    mug.sku = 'MUG'
    db.session.commit()
    body = {'items': [{'sku': 'TEE', 'quantity': 4}, {'sku': 'MUG-R', 'quantity': 9},
                      {'sku': 'MUG', 'quantity': 50}, {'sku': 'NOPE', 'quantity': 1}]}
    data = api.client.post('/api/izyops/stock', json=body, headers=api.auth).get_json()['data']
    assert data == {'updated': ['TEE', 'MUG-R'], 'unmatched': ['MUG', 'NOPE']}
    assert db.session.get(Product, simple_product.id).stock_quantity == 4
    assert db.session.get(ProductVariant, variant_product[1].id).stock_quantity == 9


def test_izyops_creates_and_updates_a_product(api, db):
    body = {'name': 'Prismatic ETB', 'price': '59.99', 'category': 'Pokemon', 'barcode': '0820650',
            'description': 'Elite Trainer Box', 'images': ['https://izy.test/p/etb.jpg'], 'helium_required': None}
    resp = api.client.put('/api/izyops/products/SC99-00100', json=body, headers=api.auth)
    data = resp.get_json()['data']
    assert resp.status_code == 200 and data['created']
    product = db.session.get(Product, data['product_id'])
    assert product.sku == 'SC99-00100' and product.price == Decimal('59.99') and product.upc == '0820650'
    assert product.category.name == 'Pokemon' and product.stock_quantity == 0
    assert [i.image_url for i in product.images] == ['https://izy.test/p/etb.jpg']

    upd = {'external_id': product.id, 'price': '54.99', 'images': ['https://izy.test/p/etb.jpg'], 'is_active': False}
    data = api.client.put('/api/izyops/products/SC99-00100', json=upd, headers=api.auth).get_json()['data']
    assert not data['created'] and set(data['updated_fields']) == {'price', 'is_active'}
    assert len(db.session.get(Product, product.id).images) == 1


def test_a_colour_sku_updates_only_that_colour(api, db, variant_product):
    mug, red = variant_product[0], variant_product[1]
    body = {'name': 'ignored', 'price': '8.49', 'is_active': False, 'external_id': mug.id}
    data = api.client.put('/api/izyops/products/MUG-R', json=body, headers=api.auth).get_json()['data']
    assert data['product_id'] is None and set(data['updated_fields']) == {'price', 'is_active'}
    red = db.session.get(ProductVariant, red.id)
    assert red.price_override == Decimal('8.49') and not red.is_active
    assert db.session.get(Product, mug.id).name == 'Color Mug'


def test_bad_product_pushes_change_nothing(api, db, simple_product):
    assert api.client.put('/api/izyops/products/X1', json={'name': 'New'}, headers=api.auth).status_code == 400
    bad_price = {'name': 'X', 'price': 'abc', 'category': 'Toys'}
    assert api.client.put('/api/izyops/products/X1', json=bad_price, headers=api.auth).status_code == 400
    simple_product.sku = 'TAKEN'
    other = Product(name='Other', slug='other', price=1, sku='OTHER')
    db.session.add(other)
    db.session.commit()
    clash = api.client.put('/api/izyops/products/TAKEN', json={'external_id': other.id}, headers=api.auth)
    assert clash.status_code == 409 and clash.get_json()['error'] == 'sku_conflict'


# ─── orders ──────────────────────────────────────────────────────────────────

def test_orders_speak_family99c_words(api, db, simple_product):
    simple_product.sku = 'TEE'
    card = _order(db, 'S99-00001', product=simple_product)
    cash = _order(db, 'S99-00002', payment='cash', total='18.00', product=simple_product,
                  pickup_deadline=datetime.utcnow() + timedelta(hours=24))
    _order(db, 'S99-00003', status='pending')
    gone = _order(db, 'S99-00004', status='cancelled')
    rows = api.client.get('/api/izyops/orders?scope=all', headers=api.auth).get_json()['data']['orders']
    assert [r['orderid'] for r in rows] == [card.id, cash.id, gone.id]
    c = rows[0]
    assert c['status'] == 'order_confirmed' and c['pay_status'] == 'paid_online' and c['inventory_captured']
    assert c['customer_name'] == 'Ann Lee' and c['customer_phone'] == '5551234' and c['pickup_at'] is None
    assert c['pickup_reference'] == 'S99-00001' and c['items'][0]['sku'] == 'TEE' and c['items'][0]['helium'] is None
    assert rows[1]['pay_status'] == 'not_paid' and 'Hold until' in rows[1]['notes']
    assert rows[2]['status'] == 'canceled' and rows[2]['canceled'] and not rows[2]['inventory_captured']
    stock = api.client.get('/api/izyops/orders', headers=api.auth).get_json()['data']['orders']
    assert [r['orderid'] for r in stock] == [card.id, cash.id]


def test_since_returns_only_newer_changes(api, db):
    old = _order(db, 'S99-00001')
    old.updated_at = datetime(2026, 1, 1)
    new = _order(db, 'S99-00002')
    db.session.commit()
    rows = api.client.get('/api/izyops/orders?scope=all&since=2026-06-01T00:00:00Z',
                          headers=api.auth).get_json()['data']['orders']
    assert [r['orderid'] for r in rows] == [new.id]


def test_board_statuses_move_the_order(api, db, monkeypatch, simple_product):
    sent = []
    monkeypatch.setattr('app.services.pickup.email_customer', lambda order, kind: sent.append(kind) or True)
    order = _order(db, product=simple_product)
    url = f'/api/izyops/orders/{order.id}'
    making = api.client.patch(url, json={'status': 'making', 'actor': 'IzyOps (sam)'}, headers=api.auth)
    assert making.status_code == 200 and db.session.get(Order, order.id).status == 'processing'
    assert 'Being packed' in db.session.get(Order, order.id).notes

    ready = api.client.patch(url, json={'status': 'ready'}, headers=api.auth).get_json()['data']
    assert ready['status'] == 'ready' and ready['customer_emailed'] and sent == ['ready']

    done = api.client.patch(url, json={'status': 'completed'}, headers=api.auth).get_json()['data']
    assert done['status'] == 'completed' and db.session.get(Order, order.id).status == 'picked_up'
    again = api.client.patch(url, json={'status': 'canceled'}, headers=api.auth)
    assert again.status_code == 409


def test_delivery_completes_as_delivered_and_cash_shows_paid(api, db):
    order = _order(db, payment='cash', shipping='Local delivery')
    data = api.client.patch(f'/api/izyops/orders/{order.id}', json={'status': 'completed'},
                            headers=api.auth).get_json()['data']
    assert db.session.get(Order, order.id).status == 'delivered' and data['pay_status'] == 'paid_instore'


def test_canceling_puts_the_stock_back_once(api, db, simple_product):
    order = _order(db, product=simple_product)
    simple_product.stock_quantity = 5
    db.session.commit()
    url = f'/api/izyops/orders/{order.id}'
    assert api.client.patch(url, json={'status': 'canceled'}, headers=api.auth).status_code == 200
    assert api.client.patch(url, json={'status': 'canceled'}, headers=api.auth).status_code == 200
    assert db.session.get(Product, simple_product.id).stock_quantity == 7
    assert db.session.get(Order, order.id).status == 'cancelled'


def test_edits_change_details_but_not_items(api, db):
    order = _order(db)
    url = f'/api/izyops/orders/{order.id}'
    resp = api.client.patch(url, json={'edit': {'customer_name': 'Ann Marie Lee', 'customer_phone': '5550000'}},
                            headers=api.auth)
    assert resp.status_code == 200 and resp.get_json()['data']['customer_name'] == 'Ann Marie Lee'
    items = api.client.patch(url, json={'edit': {'items': []}}, headers=api.auth)
    assert items.status_code == 400
    assert api.client.patch('/api/izyops/orders/999', json={'status': 'ready'}, headers=api.auth).status_code == 404


def test_refund_goes_back_to_the_card_once(api, db, monkeypatch):
    calls = []

    def fake_refund(**kwargs):
        calls.append(kwargs)
        return {'id': 're_1'}

    monkeypatch.setattr(stripe.Refund, 'create', staticmethod(fake_refund))
    order = _order(db, total='20.00')
    url = f'/api/izyops/orders/{order.id}/refund'
    data = api.client.post(url, json={'amount': '6.00', 'request_id': 'izyops-1-v2'}, headers=api.auth).get_json()['data']
    assert data['refund_id'] == 're_1' and data['order']['amount_refunded'] == 6.0
    assert calls[0]['amount'] == 600 and calls[0]['payment_intent'] == 'pi_123'
    assert calls[0]['idempotency_key'] == f'izyops_refund_{order.id}_600_izyops-1-v2'
    too_much = api.client.post(url, json={'amount': '15.00'}, headers=api.auth)
    assert too_much.status_code == 409
    rest = api.client.post(url, json={'amount': '14.00'}, headers=api.auth).get_json()['data']
    assert rest['order']['pay_status'] == 'refunded'
    cash = _order(db, 'S99-00009', payment='cash')
    assert api.client.post(f'/api/izyops/orders/{cash.id}/refund', json={'amount': '1.00'},
                           headers=api.auth).status_code == 409


# ─── order-changed ping and the catalog lock ─────────────────────────────────

def test_a_changed_order_pings_izyops(app, db, monkeypatch):
    from app.services import izyops_notify

    app.config.update(IZYOPS_NOTIFY_URL='https://izy.test/api/webstore/notify', IZYOPS_NOTIFY_KEY='k')
    sent = []
    monkeypatch.setattr(izyops_notify, 'send', lambda url, key, ids, logger: sent.append((url, key, ids)))

    class Now:
        def __init__(self, target, args, name, daemon):
            self.run = lambda: target(*args)

        def start(self):
            self.run()

    monkeypatch.setattr(izyops_notify, 'Thread', Now)
    order = _order(db)
    _order(db, 'S99-00002', status='pending')
    assert sent == [('https://izy.test/api/webstore/notify', 'k', [order.id])]


def test_catalog_lock_makes_product_pages_view_only(app, admin_client, db, simple_product):
    app.config['IZYOPS_OWNS_CATALOG'] = True
    resp = admin_client.post(f'/admin/products/{simple_product.id}/toggle')
    assert resp.status_code == 302 and '/admin/products' in resp.headers['Location']
    assert db.session.get(Product, simple_product.id).is_active
    page = admin_client.get('/admin/products').get_data(as_text=True)
    assert 'Products are managed in IZY Ops' in page
    app.config['IZYOPS_OWNS_CATALOG'] = False
    admin_client.post(f'/admin/products/{simple_product.id}/toggle')
    assert not db.session.get(Product, simple_product.id).is_active
