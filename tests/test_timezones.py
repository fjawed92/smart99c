from datetime import datetime, timedelta
from decimal import Decimal

from app import timezones
from app.helpers import set_site_setting
from app.models import Order, ShippingAddress
from app.services import pickup, pricing


def _zone(db, name):
    set_site_setting('store_timezone', name)
    db.session.commit()
    timezones.forget_tz()


def _order(db, number='T1', created=None, **kw):
    o = Order(order_number=number, subtotal=Decimal('5'), tax=0, shipping_cost=0, total=Decimal('5'),
              status=kw.pop('status', 'processing'), created_at=created or datetime.utcnow(), **kw)
    db.session.add(o)
    db.session.flush()
    db.session.add(ShippingAddress(order_id=o.id, first_name='S', last_name='K', email='s@k.com',
                                   address_line1='', city='', state='', zip_code=''))
    db.session.commit()
    return o


# ─── The setting ─────────────────────────────────────────────────────────────

def test_default_and_saved_zone(db):
    assert timezones.store_tz_name() == 'America/New_York'
    _zone(db, 'America/Los_Angeles')
    assert timezones.store_tz_name() == 'America/Los_Angeles'


def test_bad_zone_falls_back(db):
    _zone(db, 'Mars/Olympus')
    assert timezones.store_tz_name() == 'America/New_York'


def test_admin_saves_zone(admin_client, db):
    page = admin_client.get('/admin/settings').get_data(as_text=True)
    assert 'Store Time Zone' in page and 'Pacific (Los Angeles)' in page
    admin_client.post('/admin/settings', data={'store_timezone': 'America/Chicago'})
    timezones.forget_tz()
    assert timezones.store_tz_name() == 'America/Chicago'
    admin_client.post('/admin/settings', data={'store_timezone': 'Nowhere/Land'})
    timezones.forget_tz()
    assert timezones.store_tz_name() == 'America/Chicago'


# ─── Dates shown in the store's zone ─────────────────────────────────────────

def test_late_evening_order_shows_store_date(admin_client, db):
    # 9:30 PM in New York on Oct 24 = 01:30 UTC on Oct 25.
    o = _order(db, created=datetime(2026, 10, 25, 1, 30))
    assert timezones.format_local(o.created_at, '%b %-d %-I:%M %p') == 'Oct 24 9:30 PM'
    page = admin_client.get(f'/admin/orders/{o.id}').get_data(as_text=True)
    assert 'Sat, Oct 24 · 9:30 PM' in page
    users = admin_client.get('/admin/').get_data(as_text=True)
    assert 'Oct 24, 2026' in users and 'Oct 25, 2026' not in users

    _zone(db, 'America/Los_Angeles')
    assert timezones.format_local(o.created_at, '%-I:%M %p') == '6:30 PM'


def test_orders_date_filter_uses_store_days(admin_client, db):
    _order(db, 'LATE', created=datetime(2026, 10, 25, 1, 30))     # Oct 24, 9:30 PM New York
    _order(db, 'NEXT', created=datetime(2026, 10, 25, 14, 0))     # Oct 25, 10 AM New York
    page = admin_client.get('/admin/orders?tab=all&date_from=2026-10-24&date_to=2026-10-24').get_data(as_text=True)
    assert 'LATE' in page and 'NEXT' not in page


def test_export_uses_store_time(admin_client, db):
    from io import BytesIO
    from openpyxl import load_workbook
    _order(db, created=datetime(2026, 10, 25, 1, 30), status='picked_up',
           picked_up_at=datetime(2026, 10, 25, 2, 0))
    resp = admin_client.get('/admin/orders/export')
    ws = load_workbook(BytesIO(resp.data)).active
    header = [c.value for c in ws[1]]
    row = dict(zip(header, [c.value for c in ws[2]]))
    assert row['Date (EDT)'] == '2026-10-24 21:30'
    assert row['Picked up'] == '2026-10-24 22:00'


def test_dashboard_today_is_store_today(admin_client, db, monkeypatch):
    # It's 11:30 PM Oct 24 in New York (03:30 UTC Oct 25). An order from 8 PM
    # New York counts as today; one from the morning before does not.
    fake_now = datetime(2026, 10, 25, 3, 30)
    monkeypatch.setattr(timezones, 'local_now', lambda: timezones.to_local(fake_now))
    _order(db, 'TODAY', created=datetime(2026, 10, 25, 0, 0))
    _order(db, 'YDAY', created=datetime(2026, 10, 24, 1, 0))
    html = admin_client.get('/admin/').get_data(as_text=True)
    assert '<div class="fs-4 fw-700">$5.00</div>\n      <div class="text-muted small">Revenue Today' in html


def test_sale_days_and_hold_deadlines_follow_zone(db):
    _zone(db, 'America/Los_Angeles')
    start = pricing.day_start_utc('2026-11-01')
    assert start == datetime(2026, 11, 1, 7, 0)               # midnight PDT
    assert pricing.local_day(pricing.day_end_utc('2026-11-03'), end=True) == '2026-11-03'
    # Fri 5 PM Pacific + 36h → Sunday 5 AM → moved to Sunday closing (7 PM Pacific).
    placed = timezones.to_utc(datetime(2026, 10, 23, 17, tzinfo=timezones.store_tz()))
    deadline = timezones.to_local(pickup.pickup_deadline(placed))
    assert (deadline.day, deadline.hour) == (25, 19)


# ─── Every order step gets a time ────────────────────────────────────────────

def test_status_changes_are_timestamped(admin_client, db):
    o = _order(db)
    admin_client.post(f'/admin/orders/{o.id}/status', data={'status': 'shipped'})
    admin_client.post(f'/admin/orders/{o.id}/pickup', data={'action': 'delivered'})
    assert o.shipped_at and o.delivered_at and o.status == 'delivered'
    labels = [label for label, _, _ in o.timeline]
    assert labels == ['Ordered online', 'Shipped / out for delivery', 'Delivered']
    page = admin_client.get(f'/admin/orders/{o.id}').get_data(as_text=True)
    assert 'Shipped / out for delivery' in page and 'Delivered' in page

    cancelled = _order(db, 'T2')
    admin_client.post(f'/admin/orders/{cancelled.id}/status', data={'status': 'refunded'})
    assert cancelled.refunded_at is not None


def test_first_time_is_kept(db):
    o = _order(db)
    first = datetime(2026, 1, 1, 12)
    o.set_status('ready', first)
    o.set_status('ready', first + timedelta(hours=2))
    assert o.ready_at == first


def test_old_orders_without_times_are_not_made_up(db):
    o = _order(db, status='delivered')
    assert o.timeline[-1] == ('Delivered', None, 'muted')


def test_auto_released_hold_is_timestamped(db, simple_product):
    set_site_setting('cash_expire_action', 'auto')
    db.session.commit()
    o = _order(db, payment_method='cash', pickup_deadline=datetime.utcnow() - timedelta(hours=1))
    pickup.process_holds()
    assert o.status == 'no_show' and o.no_show_at is not None
