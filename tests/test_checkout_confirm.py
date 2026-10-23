from decimal import Decimal

import stripe

from app.models import Order, ShippingRate


class FakeIntent(dict):
    def __init__(self, status, livemode=True):
        super().__init__(id='pi_123', latest_charge='ch_123', livemode=livemode)
        self.status = status


def _setup(client, db, product, monkeypatch, status='succeeded', livemode=True):
    pickup = ShippingRate(name='Store pickup (West Babylon)', price=Decimal('0'), is_active=True)
    db.session.add(pickup)
    db.session.commit()
    with client.session_transaction() as sess:
        sess['cart'] = {str(product.id): 1}
    monkeypatch.setattr(stripe.PaymentIntent, 'retrieve',
                        lambda pid: FakeIntent(status, livemode))
    return pickup


def _confirm(client, rate_id, pi='pi_123'):
    return client.post('/checkout/confirm', json={
        'payment_intent_id': pi, 'shipping_rate_id': rate_id,
        'first_name': 'A', 'last_name': 'B', 'email': 'a@b.com',
        'address_line1': '1 Main', 'city': 'X', 'state': 'NY', 'zip_code': '11704'})


def test_paid_pickup_order_records_option(client, db, simple_product, monkeypatch):
    pickup = _setup(client, db, simple_product, monkeypatch)
    resp = _confirm(client, pickup.id)
    assert resp.get_json()['success']
    order = Order.query.one()
    assert order.shipping_method == 'Store pickup (West Babylon)'
    assert order.is_pickup is True
    assert order.status == 'processing'
    assert order.stripe_charge_id == 'ch_123'
    assert order.stripe_livemode is True


def test_unpaid_intent_does_not_create_order(client, db, simple_product, monkeypatch):
    pickup = _setup(client, db, simple_product, monkeypatch, status='requires_payment_method')
    resp = _confirm(client, pickup.id)
    assert resp.status_code == 400
    assert Order.query.count() == 0


def test_unknown_intent_does_not_create_order(client, db, simple_product, monkeypatch):
    pickup = _setup(client, db, simple_product, monkeypatch)

    def missing(pid):
        raise stripe.error.InvalidRequestError('No such payment_intent', 'id')
    monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', missing)
    resp = _confirm(client, pickup.id, pi='pi_fake')
    assert resp.status_code == 400
    assert Order.query.count() == 0


def test_admin_order_page_shows_pickup_and_test_mode(admin_client, db):
    order = Order(order_number='SM1', subtotal=5, tax=0, shipping_cost=0, total=5,
                  status='processing', stripe_payment_intent_id='pi_123',
                  stripe_livemode=False, shipping_method='Store pickup (West Babylon)')
    db.session.add(order)
    db.session.commit()
    html = admin_client.get(f'/admin/orders/{order.id}').get_data(as_text=True)
    assert 'STORE PICKUP' in html
    assert 'Test mode payment' in html
    assert 'https://dashboard.stripe.com/test/payments/pi_123' in html
    listing = admin_client.get('/admin/orders').get_data(as_text=True)
    assert 'Pickup</span>' in listing


def test_old_order_without_option_is_flagged(db):
    old = Order(order_number='X1', subtotal=5, total=5, shipping_cost=Decimal('0'))
    assert old.is_pickup is None
    paid_delivery = Order(order_number='X2', subtotal=5, total=10, shipping_cost=Decimal('4.99'))
    assert paid_delivery.is_pickup is False
