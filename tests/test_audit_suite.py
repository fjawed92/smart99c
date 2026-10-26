"""Suite audit: edge cases on the IZY Ops connection (smart99c side)."""
import base64
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe

from app.models import Order, OrderItem, Product, ShippingAddress


@pytest.fixture
def api(app):
    app.config.update(IZYOPS_API_USERNAME='izy', IZYOPS_API_PASSWORD='secret')
    token = base64.b64encode(b'izy:secret').decode()
    return SimpleNamespace(client=app.test_client(), auth={'Authorization': f'Basic {token}'})


def _order(db, total='20.00', status='processing', payment='card'):
    order = Order(order_number='S99-00001', status=status, payment_method=payment, subtotal=Decimal(total),
                  tax=0, shipping_cost=0, total=Decimal(total), shipping_method='Store pickup',
                  stripe_payment_intent_id='pi_123' if payment == 'card' else None)
    db.session.add(order)
    db.session.flush()
    db.session.add(ShippingAddress(order_id=order.id, first_name='Ann', last_name='Lee', email='ann@x.test',
                                   phone='5551234', address_line1='1 Main St', city='Bronx', state='NY',
                                   zip_code='10458'))
    db.session.commit()
    return order


def test_a_retried_refund_is_counted_once(api, db, monkeypatch):
    """IZY Ops resends the same request_id when it never got the first reply
    (e.g. a timeout). Stripe returns the same refund for the same idempotency
    key, so the order must not count the money twice."""
    monkeypatch.setattr(stripe.Refund, 'create', staticmethod(lambda **kw: {'id': 're_1'}))
    order = _order(db, total='20.00')
    url = f'/api/izyops/orders/{order.id}/refund'
    body = {'amount': '6.00', 'request_id': 'izyops-7-v3'}
    first = api.client.post(url, json=body, headers=api.auth)
    retry = api.client.post(url, json=body, headers=api.auth)
    assert first.status_code == 200 and retry.status_code == 200
    db.session.refresh(order)
    assert order.amount_refunded == Decimal('6.00')


def test_order_feed_has_every_key_izyops_reads(api, db):
    """The keys IZY Ops' webstore_sync / services.orders read from a row."""
    p = Product(name='Mug', slug='mug', sku='MUG', price=Decimal('10'), stock_quantity=5,
                track_inventory=True, is_active=True)
    db.session.add(p)
    order = _order(db)
    db.session.add(OrderItem(order_id=order.id, product_id=p.id, product_name='Mug', product_sku='MUG',
                             quantity=2, unit_price=Decimal('10.00'), total_price=Decimal('20.00')))
    db.session.commit()
    rows = api.client.get('/api/izyops/orders?scope=all', headers=api.auth).get_json()['data']
    assert rows['pages'] == 1
    row = rows['orders'][0]
    for key in ('orderid', 'order_type', 'status', 'pay_status', 'refunded', 'canceled', 'inventory_captured',
                'customer_name', 'customer_phone', 'customer_email', 'pickup_at', 'notes', 'subtotal', 'tax',
                'total', 'amount_refunded', 'created_at', 'updated_at', 'items'):
        assert key in row, key
    line = row['items'][0]
    for key in ('line_id', 'sku', 'name', 'quantity', 'unit_price', 'line_total'):
        assert key in line, key
    assert line['sku'] == 'MUG' and line['quantity'] == 2
