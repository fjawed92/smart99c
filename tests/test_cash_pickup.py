from datetime import datetime, timedelta
from decimal import Decimal

from app.helpers import set_site_setting
from app.models import Order, ShippingAddress, ShippingRate
from app.services import pickup


def _local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=pickup.store_tz())


def _deadline_local(placed_local):
    return pickup.to_local(pickup.pickup_deadline(pickup.to_utc(placed_local)))


# ─── Deadline math (store hours: Mon–Sat 9–9, Sun 10–7) ─────────────────────

def test_overnight_deadline_moves_to_closing_time(app):
    # Fri Oct 24 2025 5 PM + 36h = Sun 5 AM → Sunday closing, 7 PM.
    assert _deadline_local(_local(2025, 10, 24, 17)) == _local(2025, 10, 26, 19)


def test_after_closing_deadline_moves_to_next_day(app):
    # Sat 10 AM + 36h = Sun 10 PM (closed at 7) → Monday closing, 9 PM.
    assert _deadline_local(_local(2025, 10, 25, 10)) == _local(2025, 10, 27, 21)


def test_deadline_inside_open_hours_is_kept(app):
    # Mon 9 AM + 36h = Tue 9 PM, right at closing.
    assert _deadline_local(_local(2025, 10, 27, 9)) == _local(2025, 10, 28, 21)


def test_snap_can_be_turned_off(app, db):
    set_site_setting('cash_hold_closing_active', '')
    set_site_setting('cash_hold_hours', '12')
    db.session.commit()
    assert _deadline_local(_local(2025, 10, 24, 17)) == _local(2025, 10, 25, 5)


# ─── Checkout ────────────────────────────────────────────────────────────────

CUSTOMER = {'first_name': 'Sara', 'last_name': 'Khan', 'email': 'sara@example.com',
            'phone': '(631) 555-0142', 'agree_deadline': True}


def _cart(client, db, product, qty=1):
    db.session.add(ShippingRate(name='Store pickup (West Babylon)', price=Decimal('0'), is_active=True))
    db.session.add(ShippingRate(name='Local delivery', price=Decimal('4.99'), is_active=True))
    db.session.commit()
    with client.session_transaction() as sess:
        sess['cart'] = {str(product.id): qty}


def test_reserve_cash_order(client, db, simple_product):
    _cart(client, db, simple_product, qty=2)
    resp = client.post('/checkout/reserve-cash', json=CUSTOMER)
    assert resp.get_json()['success']
    order = Order.query.one()
    assert order.payment_method == 'cash'
    assert order.is_pickup and order.status == 'processing'
    assert order.service_fee == 0
    assert order.pickup_deadline > datetime.utcnow()
    assert order.stripe_payment_intent_id is None
    assert simple_product.stock_quantity == 18
    with client.session_transaction() as sess:
        assert 'cart' not in sess
    page = client.get(f'/order/confirmation/{order.order_number}').get_data(as_text=True)
    assert 'Your order is reserved!' in page and 'Bring cash' in page


def test_reserve_cash_needs_phone_and_agreement(client, db, simple_product):
    _cart(client, db, simple_product)
    assert client.post('/checkout/reserve-cash', json={**CUSTOMER, 'phone': ''}).status_code == 400
    assert client.post('/checkout/reserve-cash', json={**CUSTOMER, 'agree_deadline': False}).status_code == 400
    assert Order.query.count() == 0


def test_cash_blocked_over_max_order(client, db, simple_product):
    set_site_setting('cash_max_order', '15')
    _cart(client, db, simple_product, qty=2)   # ~$21.75 with tax
    resp = client.post('/checkout/reserve-cash', json=CUSTOMER)
    assert resp.status_code == 400 and 'paid online' in resp.get_json()['error']


def test_cash_blocked_after_repeat_no_shows(client, db, simple_product):
    for n in range(2):
        o = Order(order_number=f'OLD{n}', status='no_show', payment_method='cash',
                  subtotal=1, tax=0, shipping_cost=0, total=1)
        db.session.add(o)
        db.session.flush()
        db.session.add(ShippingAddress(order_id=o.id, first_name='S', last_name='K',
                                       email='SARA@example.com', address_line1='', city='',
                                       state='', zip_code=''))
    _cart(client, db, simple_product)
    resp = client.post('/checkout/reserve-cash', json=CUSTOMER)
    assert resp.status_code == 400 and 'not picked up' in resp.get_json()['error']


def test_checkout_page_offers_cash(client, db, simple_product):
    _cart(client, db, simple_product)
    html = client.get('/checkout').get_data(as_text=True)
    assert 'Pick up · Pay cash in store' in html
    assert "We'll hold your order for 36 hours" in html
    assert 'Pick up · Pay now by card' in html


def test_cash_option_hidden_when_turned_off(client, db, simple_product):
    set_site_setting('cash_pickup_active', '')
    _cart(client, db, simple_product)
    assert 'Pay cash in store' not in client.get('/checkout').get_data(as_text=True)
    assert client.post('/checkout/reserve-cash', json=CUSTOMER).status_code == 400


# ─── Reminders and expired holds ─────────────────────────────────────────────

def _cash_order(db, product, deadline_in_hours, qty=1):
    now = datetime.utcnow()
    o = Order(order_number=f'C{deadline_in_hours}'.replace('-', 'm'), status='processing',
              payment_method='cash', shipping_method='Store pickup (West Babylon)',
              subtotal=1, tax=0, shipping_cost=0, total=1,
              created_at=now - timedelta(hours=30),
              pickup_deadline=now + timedelta(hours=deadline_in_hours))
    db.session.add(o)
    db.session.flush()
    from app.models import OrderItem
    db.session.add(OrderItem(order_id=o.id, product_id=product.id, product_name=product.name,
                             quantity=qty, unit_price=1, total_price=qty))
    db.session.add(ShippingAddress(order_id=o.id, first_name='S', last_name='K', email='s@k.com',
                                   address_line1='', city='', state='', zip_code=''))
    db.session.commit()
    return o


def test_expired_hold_waits_for_owner_by_default(db, simple_product):
    o = _cash_order(db, simple_product, -2)
    pickup.process_holds()
    assert o.status == 'processing' and o.is_overdue


def test_expired_hold_released_automatically(db, simple_product):
    set_site_setting('cash_expire_action', 'auto')
    o = _cash_order(db, simple_product, -2, qty=3)
    assert pickup.process_holds() == (0, 1)
    assert o.status == 'no_show'
    assert simple_product.stock_quantity == 23


def test_reminder_marked_once(db, simple_product):
    o = _cash_order(db, simple_product, 3)
    assert pickup.process_holds()[0] == 1
    assert o.reminder_sent_at is not None
    assert pickup.process_holds()[0] == 0


# ─── Admin ───────────────────────────────────────────────────────────────────

def test_admin_actions(admin_client, db, simple_product):
    o = _cash_order(db, simple_product, 5, qty=2)
    url = f'/admin/orders/{o.id}/pickup'
    admin_client.post(url, data={'action': 'ready'})
    assert o.status == 'ready' and o.ready_at
    before = o.pickup_deadline
    admin_client.post(url, data={'action': 'extend_24'})
    assert o.pickup_deadline == before + timedelta(hours=24)
    admin_client.post(url, data={'action': 'no_show'})
    assert o.status == 'no_show' and simple_product.stock_quantity == 22
    # Closed orders can't be changed by a second tap.
    admin_client.post(url, data={'action': 'picked_up'})
    assert o.status == 'no_show'


def test_admin_pages_render(admin_client, db, simple_product):
    late = _cash_order(db, simple_product, -2)
    _cash_order(db, simple_product, 10)
    html = admin_client.get('/admin/orders').get_data(as_text=True)
    assert 'Waiting for pickup (1)' in html and 'Past deadline (1)' in html
    assert 'Cash to collect' in html
    late_tab = admin_client.get('/admin/orders?tab=late').get_data(as_text=True)
    assert late.order_number in late_tab and 'Overdue by' in late_tab
    detail = admin_client.get(f'/admin/orders/{late.id}').get_data(as_text=True)
    assert 'STORE PICKUP · PAY CASH' in detail and 'No-show · put items back on shelf' in detail
    settings = admin_client.get('/admin/settings').get_data(as_text=True)
    assert 'Pay Cash at Pickup' in settings
