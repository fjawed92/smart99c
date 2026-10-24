from datetime import datetime, timedelta
from decimal import Decimal

import stripe

from app.helpers import set_site_setting
from app.models import Order, Product, PromoCode, Sale, ShippingAddress, ShippingRate
from app.services import pricing

CUSTOMER = {'first_name': 'Sara', 'last_name': 'Khan', 'email': 'sara@example.com',
            'phone': '(631) 555-0142', 'agree_deadline': True}


def _rates(db):
    pickup = ShippingRate(name='Store pickup (West Babylon)', price=Decimal('0'), is_active=True)
    delivery = ShippingRate(name='Local delivery', price=Decimal('4.99'), is_active=True, sort_order=1)
    free = ShippingRate(name='Free local delivery', price=Decimal('0'), is_active=True,
                        min_order_amount=Decimal('35'), sort_order=2)
    db.session.add_all([pickup, delivery, free])
    db.session.commit()
    return pickup, delivery, free


def _cart(client, product, qty=2):
    with client.session_transaction() as sess:
        sess['cart'] = {f'{product.id}:0': qty}


def _promo(db, code='EID10', kind='percent', value='10', **kw):
    promo = PromoCode(code=code, kind=kind, value=Decimal(value), **kw)
    db.session.add(promo)
    db.session.commit()
    return promo


def _items(product, qty=2):
    price = Decimal(str(product.price_for()))
    return [{'product': product, 'variant': None, 'quantity': qty,
             'unit_price': price, 'total': price * qty}]


# ─── The maths ───────────────────────────────────────────────────────────────

def test_cash_discount_comes_off_before_tax(db, simple_product):
    set_site_setting('cash_discount_percent', '10')
    db.session.commit()
    q = pricing.quote(_items(simple_product), cash=True)       # 2 × $9.99
    assert q['subtotal'] == Decimal('19.98')
    assert q['cash_discount'] == Decimal('2.00')
    assert q['tax'] == Decimal('1.60')                        # 8.875% of 17.98
    assert q['fee'] == 0
    assert q['total'] == Decimal('19.58')
    # Card payments don't get the cash discount.
    assert pricing.quote(_items(simple_product))['cash_discount'] == 0


def test_promo_and_cash_stack(db, simple_product):
    set_site_setting('cash_discount_percent', '10')
    db.session.commit()
    _promo(db)
    q = pricing.quote(_items(simple_product), cash=True, promo_code='eid10')
    assert q['promo_discount'] == Decimal('2.00')
    assert q['cash_discount'] == Decimal('1.80')              # 10% of what's left (17.98)
    assert q['promo_code'] == 'EID10'


def test_only_the_bigger_discount(db, simple_product):
    set_site_setting('cash_discount_percent', '10')
    set_site_setting('discount_stacking', 'best')
    db.session.commit()
    _promo(db, 'ONEOFF', 'amount', '1')
    q = pricing.quote(_items(simple_product), cash=True, promo_code='ONEOFF')
    assert q['promo_discount'] == 0 and q['cash_discount'] == Decimal('2.00')
    assert 'saves more' in q['promo_note'] and q['promo_code'] == ''

    _promo(db, 'HALF', 'percent', '50')
    q = pricing.quote(_items(simple_product), cash=True, promo_code='HALF')
    assert q['promo_discount'] == Decimal('9.99') and q['cash_discount'] == 0


def test_dollar_promo_never_goes_below_zero(db, simple_product):
    _promo(db, 'BIG', 'amount', '50')
    q = pricing.quote(_items(simple_product), promo_code='BIG')
    assert q['promo_discount'] == Decimal('19.98')
    assert q['tax'] == 0


def test_free_delivery_promo(db, simple_product):
    _, delivery, _ = _rates(db)
    _promo(db, 'SHIPFREE', 'free_shipping', '0')
    q = pricing.quote(_items(simple_product), delivery, promo_code='SHIPFREE')
    assert q['shipping'] == 0 and q['promo_discount'] == 0
    assert pricing.quote(_items(simple_product), delivery)['shipping'] == Decimal('4.99')


def test_promo_rules(db, simple_product):
    items = _items(simple_product)
    now = datetime.utcnow()
    _promo(db, 'MIN50', min_order=Decimal('50'))
    _promo(db, 'OLD', ends_at=now - timedelta(days=1))
    _promo(db, 'SOON', starts_at=now + timedelta(days=1))
    _promo(db, 'GONE', max_uses=3, uses=3)
    _promo(db, 'OFF', is_active=False)
    assert 'Add $30.02 more' in pricing.quote(items, promo_code='MIN50')['promo_error']
    assert 'expired' in pricing.quote(items, promo_code='OLD')['promo_error']
    assert "isn't active yet" in pricing.quote(items, promo_code='SOON')['promo_error']
    assert 'used up' in pricing.quote(items, promo_code='GONE')['promo_error']
    assert "isn't valid" in pricing.quote(items, promo_code='OFF')['promo_error']
    assert "isn't valid" in pricing.quote(items, promo_code='NOPE')['promo_error']
    # A failed code costs nothing.
    assert pricing.quote(items, promo_code='OLD')['promo_discount'] == 0


def test_once_per_customer(db, simple_product):
    _promo(db, 'WELCOME', once_per_customer=True)
    o = Order(order_number='P1', subtotal=1, tax=0, shipping_cost=0, total=1,
              promo_code='WELCOME', status='picked_up')
    db.session.add(o)
    db.session.flush()
    db.session.add(ShippingAddress(order_id=o.id, first_name='S', last_name='K', email='Sara@Example.com',
                                   address_line1='', city='', state='', zip_code=''))
    db.session.commit()
    items = _items(simple_product)
    assert 'already used' in pricing.quote(items, promo_code='WELCOME', email='sara@example.com')['promo_error']
    assert pricing.quote(items, promo_code='WELCOME', email='new@example.com')['promo_error'] is None


def test_admin_tax_rate_is_used(db, simple_product):
    set_site_setting('tax_rate', '5')
    db.session.commit()
    assert pricing.quote(_items(simple_product))['tax'] == Decimal('1.00')   # 5% of 19.98


# ─── Delivery options ────────────────────────────────────────────────────────

def test_free_delivery_needs_its_minimum(client, db, simple_product):
    _, _, free = _rates(db)
    _cart(client, simple_product)                              # $19.98, minimum is $35
    resp = client.post('/checkout/create-payment-intent', json={'shipping_rate_id': free.id})
    assert resp.status_code == 400 and 'Add $15.02 more' in resp.get_json()['error']
    page = client.get('/checkout').get_data(as_text=True)
    assert 'Add $15.02 more to get Free local delivery' in page


def test_switched_off_rate_is_refused(client, db, simple_product):
    _, delivery, _ = _rates(db)
    delivery.is_active = False
    db.session.commit()
    _cart(client, simple_product)
    resp = client.post('/checkout/create-payment-intent', json={'shipping_rate_id': delivery.id})
    assert resp.status_code == 400


# ─── Sales ───────────────────────────────────────────────────────────────────

def test_bigger_sale_wins_and_cart_uses_it(client, db, simple_product, category):
    plush = Product(name='Plush', slug='plush', price=Decimal('10.00'), stock_quantity=5,
                    category_id=category.id, is_active=True)
    db.session.add(plush)
    db.session.add(Sale(name='Eid sale', percent=Decimal('10')))
    db.session.add(Sale(name='Toy week', percent=Decimal('25'), category_id=category.id))
    db.session.add(Sale(name='Ended', percent=Decimal('90'), ends_at=datetime.utcnow() - timedelta(hours=1)))
    db.session.commit()
    assert simple_product.current_price == Decimal('8.99')     # 10% off 9.99
    assert plush.current_price == Decimal('7.50')               # 25% beats 10%
    assert plush.was_price == Decimal('10.00') and plush.discount_percent == 25

    with client.session_transaction() as sess:
        sess['cart'] = {f'{plush.id}:0': 2}
    page = client.get('/cart').get_data(as_text=True)
    assert '$7.50 each' in page and '$15.00' in page
    assert 'Toy week: 25% off Toys' in page                     # site banner
    deals = client.get('/shop?deals=1').get_data(as_text=True)
    assert 'Plush' in deals and 'Basic Tee' in deals             # whole-store sale = all deals


def test_scheduled_sale_waits(db, simple_product):
    db.session.add(Sale(name='Later', percent=Decimal('50'), starts_at=datetime.utcnow() + timedelta(days=2)))
    db.session.commit()
    assert simple_product.current_price == Decimal('9.99') and not simple_product.is_on_sale


# ─── Checkout flow ───────────────────────────────────────────────────────────

def test_cash_order_with_promo_saves_discounts(client, db, simple_product):
    _rates(db)
    set_site_setting('cash_discount_percent', '10')
    db.session.commit()
    promo = _promo(db)
    _cart(client, simple_product)
    resp = client.post('/checkout/promo', json={'code': ' eid10 '})
    assert resp.get_json()['success']
    page = client.get('/checkout').get_data(as_text=True)
    assert 'Save 10%' in page and 'EID10' in page

    resp = client.post('/checkout/reserve-cash', json=CUSTOMER)
    assert resp.get_json()['success']
    order = Order.query.one()
    assert order.promo_code == 'EID10'
    assert order.promo_discount == Decimal('2.00') and order.cash_discount == Decimal('1.80')
    assert order.total == Decimal('17.62')                      # 16.18 + 8.875% tax (1.44)
    assert promo.uses == 1
    with client.session_transaction() as sess:
        assert 'promo_code' not in sess
    confirmation = client.get(f'/order/confirmation/{order.order_number}').get_data(as_text=True)
    assert 'Promo EID10' in confirmation and 'Cash discount' in confirmation


def test_bad_promo_is_rejected(client, db, simple_product):
    _rates(db)
    _promo(db, 'MIN50', min_order=Decimal('50'))
    _cart(client, simple_product)
    resp = client.post('/checkout/promo', json={'code': 'MIN50'})
    assert resp.status_code == 400 and 'Add $30.02 more' in resp.get_json()['error']


def test_quote_endpoint_for_cash(client, db, simple_product):
    _rates(db)
    set_site_setting('cash_discount_percent', '10')
    db.session.commit()
    _cart(client, simple_product)
    data = client.post('/checkout/quote', json={'cash': True}).get_json()
    assert data['cash_discount'] == 2.0 and data['total'] == 19.58 and data['service_fee'] == 0


class FakeIntent(dict):
    def __init__(self, amount):
        super().__init__(id='pi_9', latest_charge='ch_9', livemode=False, amount=amount)
        self.status = 'succeeded'


def test_charged_amount_mismatch_is_flagged_not_refused(client, db, simple_product, monkeypatch):
    pickup, _, _ = _rates(db)
    _cart(client, simple_product, qty=1)
    monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', lambda pid: FakeIntent(500))
    resp = client.post('/checkout/confirm', json={
        'payment_intent_id': 'pi_9', 'shipping_rate_id': pickup.id,
        'first_name': 'A', 'last_name': 'B', 'email': 'a@b.com'})
    assert resp.get_json()['success']
    assert 'Stripe charged $5.00' in Order.query.one().notes


# ─── Admin ───────────────────────────────────────────────────────────────────

def test_admin_discounts_page(admin_client, db, category):
    assert 'Cash pickup discount' in admin_client.get('/admin/discounts').get_data(as_text=True)
    admin_client.post('/admin/discounts/settings', data={'cash_discount_percent': '5',
                                                         'discount_stacking': 'best'})
    assert pricing.cash_discount_percent() == Decimal('5')
    assert pricing.stacking() == 'best'

    admin_client.post('/admin/discounts/promos', data={
        'code': 'eid-10', 'kind': 'percent', 'value': '10', 'max_uses': '50',
        'ends_on': '2099-12-31', 'is_active': 'on'})
    promo = PromoCode.query.one()
    assert promo.code == 'EID-10' and promo.max_uses == 50 and promo.ends_at.year == 2100
    assert pricing.local_day(promo.ends_at, end=True) == '2099-12-31'

    admin_client.post('/admin/discounts/sales', data={
        'name': 'Toy week', 'percent': '20', 'category_id': str(category.id),
        'is_active': 'on', 'show_banner': 'on'})
    sale = Sale.query.one()
    assert sale.category_id == category.id and sale.is_running()
    page = admin_client.get('/admin/discounts').get_data(as_text=True)
    assert 'EID-10' in page and 'Toy week' in page and 'Running' in page

    # Bad input is refused with a message, nothing saved.
    admin_client.post('/admin/discounts/promos', data={'code': 'X', 'kind': 'percent', 'value': '150'})
    assert PromoCode.query.count() == 1


def test_admin_can_edit_and_switch_off_rates(admin_client, db):
    _, delivery, _ = _rates(db)
    admin_client.post(f'/admin/shipping/{delivery.id}/edit', data={
        'name': 'Local delivery', 'price': '5.99', 'min_order_amount': '10',
        'estimated_days': 'Same day', 'is_active': 'y', 'sort_order': '1'})
    assert delivery.price == Decimal('5.99') and delivery.min_order_amount == Decimal('10')
    admin_client.post(f'/admin/shipping/{delivery.id}/toggle')
    assert delivery.is_active is False
    # A free option can be added (price 0 used to be rejected).
    admin_client.post('/admin/shipping/new', data={'name': 'Curbside pickup', 'price': '0',
                                                   'is_active': 'y', 'sort_order': '3'})
    assert ShippingRate.query.filter_by(name='Curbside pickup').count() == 1


def test_once_per_customer_code_needs_email(client, db, simple_product):
    _rates(db)
    _promo(db, 'WELCOME', once_per_customer=True)
    _cart(client, simple_product)
    resp = client.post('/checkout/promo', json={'code': 'WELCOME'})
    assert resp.status_code == 400 and 'enter your email' in resp.get_json()['error']
    assert client.post('/checkout/promo', json={'code': 'WELCOME', 'email': 'a@b.com'}).get_json()['success']


def test_paid_order_keeps_code_that_ran_out(client, db, simple_product, monkeypatch):
    pickup, _, _ = _rates(db)
    promo = _promo(db, 'LAST1', max_uses=1)
    _cart(client, simple_product, qty=1)
    with client.session_transaction() as sess:
        sess['promo_code'] = 'LAST1'
    promo.uses = 1          # someone else used the last one while this customer paid
    db.session.commit()
    monkeypatch.setattr(stripe.PaymentIntent, 'retrieve', lambda pid: FakeIntent(1040))   # $8.99 + tax + card fee
    resp = client.post('/checkout/confirm', json={
        'payment_intent_id': 'pi_9', 'shipping_rate_id': pickup.id,
        'first_name': 'A', 'last_name': 'B', 'email': 'a@b.com'})
    assert resp.get_json()['success']
    order = Order.query.one()
    assert order.promo_discount == Decimal('1.00') and order.notes is None
