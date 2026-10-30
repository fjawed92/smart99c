from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.helpers import get_site_setting
from app.models import Category, Product
from app.routes.main import next_restock_iso
from app.services.storefront import drop_schedule, parse_drop_time

NY = ZoneInfo('America/New_York')


def _pokemon_aisle(db):
    cat = Category(name='Pokémon Cards', slug='pokemon-cards', is_active=True)
    db.session.add(cat)
    db.session.add(Product(name='Booster', slug='booster', price=Decimal('4.99'),
                           stock_quantity=5, is_active=True, category=cat))
    db.session.commit()


def test_defaults_to_friday_4pm(db):
    drop = drop_schedule()
    assert (drop['weekday'], drop['hour'], drop['minute']) == (4, 16, 0)
    assert drop['label'] == 'Fridays at 4 PM'
    assert drop['countdown'] is True


def test_parse_drop_time():
    assert parse_drop_time('16:00') == (16, 0)
    assert parse_drop_time('9:30') == (9, 30)
    assert parse_drop_time('25:00') is None
    assert parse_drop_time('noon') is None


def test_admin_saves_drop_and_home_page_follows(admin_client, client, db):
    _pokemon_aisle(db)
    resp = admin_client.post('/admin/settings', data={
        'drop_weekday': '5', 'drop_time': '14:30', 'drop_countdown_active': 'on'})
    assert resp.status_code == 302
    assert get_site_setting('drop_weekday') == '5'
    assert get_site_setting('drop_time') == '14:30'

    html = client.get('/').get_data(as_text=True)
    assert 'Saturday drop' in html
    assert 'New stock lands every Saturday at 2:30 PM.' in html
    assert 'Saturdays' not in html.split('Next restock in')[0].rsplit('<p>', 1)[-1]
    assert 'Saturdays at 2:30 PM' in client.get('/about').get_data(as_text=True)


def test_countdown_targets_saved_day_and_time(admin_client, db):
    admin_client.post('/admin/settings', data={'drop_weekday': '5', 'drop_time': '14:30'})
    wed = datetime(2026, 10, 28, 12, 0, tzinfo=NY)
    assert next_restock_iso(wed).startswith('2026-10-31T14:30:00')
    after = datetime(2026, 10, 31, 15, 0, tzinfo=NY)
    assert next_restock_iso(after).startswith('2026-11-07T14:30:00')


def test_turning_countdown_off_hides_it(admin_client, client, db):
    _pokemon_aisle(db)
    admin_client.post('/admin/settings', data={'drop_weekday': '4', 'drop_time': '16:00'})
    html = client.get('/').get_data(as_text=True)
    assert 'In stock now' in html and 'id="restockCountdown"' not in html


def test_bad_time_keeps_old_time(admin_client, db):
    admin_client.post('/admin/settings', data={'drop_weekday': '4', 'drop_time': '18:00',
                                               'drop_countdown_active': 'on'})
    admin_client.post('/admin/settings', data={'drop_weekday': '9', 'drop_time': 'oops',
                                               'drop_countdown_active': 'on'})
    drop = drop_schedule()
    assert (drop['weekday'], drop['time']) == (4, '18:00')
