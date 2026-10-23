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


# ─── Online service fee ─────────────────────────────────────────────────────

def test_fee_covers_stripe_cut(app):
    from app.helpers import service_fee_for
    delivery = ShippingRate(name='Local delivery', price=Decimal('4.99'))
    amount = Decimal('20.00')
    fee = service_fee_for(amount, delivery)
    charged = amount + fee
    stripe_cut = charged * Decimal('0.029') + Decimal('0.30')
    # Store keeps at least the order amount, and never more than a cent over.
    assert amount <= charged - stripe_cut < amount + Decimal('0.01')


def test_no_fee_for_pickup_or_when_turned_off(app, db):
    from app.helpers import service_fee_for, set_site_setting
    pickup = ShippingRate(name='Store pickup (West Babylon)', price=Decimal('0'))
    delivery = ShippingRate(name='Local delivery', price=Decimal('4.99'))
    assert service_fee_for(Decimal('20'), pickup) == 0
    assert service_fee_for(Decimal('20'), delivery) > 0
    set_site_setting('service_fee_active', '')
    db.session.commit()
    assert service_fee_for(Decimal('20'), delivery) == 0


def test_delivery_order_charges_and_saves_fee(client, db, simple_product, monkeypatch):
    _setup(client, db, simple_product, monkeypatch)
    delivery = ShippingRate(name='Local delivery', price=Decimal('4.99'), is_active=True)
    db.session.add(delivery)
    db.session.commit()

    created = {}

    def fake_create(**kw):
        created.update(kw)
        return type('PI', (), {'id': 'pi_123', 'client_secret': 'sec'})()
    monkeypatch.setattr(stripe.PaymentIntent, 'create', fake_create)

    data = client.post('/checkout/create-payment-intent',
                       json={'shipping_rate_id': delivery.id}).get_json()
    assert data['service_fee'] > 0
    assert created['amount'] == int(Decimal(str(data['total'])) * 100)

    _confirm(client, delivery.id)
    order = Order.query.one()
    assert order.service_fee > 0
    assert order.total == order.subtotal + order.tax + order.shipping_cost + order.service_fee


def test_pickup_order_has_no_fee(client, db, simple_product, monkeypatch):
    pickup = _setup(client, db, simple_product, monkeypatch)
    _confirm(client, pickup.id)
    assert Order.query.one().service_fee == 0
