from datetime import datetime, timedelta

from app.models import SiteVisit
from app.services.visits import build_report, purge_old_visits, source_name

PHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148 Safari/604.1'
DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'


def test_page_views_are_recorded_per_visitor(client, db, simple_product):
    client.get('/', headers={'User-Agent': PHONE, 'Referer': 'https://www.google.com/'})
    client.get('/product/basic-tee', headers={'User-Agent': PHONE})
    visits = SiteVisit.query.order_by(SiteVisit.id).all()
    assert [v.endpoint for v in visits] == ['main.index', 'shop.product_detail']
    assert visits[0].visitor_id == visits[1].visitor_id
    assert visits[0].referrer == 'google.com'
    assert visits[0].device == 'Mobile'
    assert visits[1].product_id == simple_product.id


def test_bots_and_json_are_not_counted(client, db):
    client.get('/', headers={'User-Agent': 'Googlebot/2.1'})
    client.get('/cart/count', headers={'User-Agent': DESKTOP})
    assert SiteVisit.query.count() == 0


def test_admin_visits_are_not_counted(admin_client, db):
    admin_client.get('/', headers={'User-Agent': DESKTOP})
    assert SiteVisit.query.count() == 0


def test_search_records_term_and_empty_results(client, db, simple_product):
    client.get('/shop?q=Pokemon', headers={'User-Agent': DESKTOP})
    client.get('/shop?q=tee', headers={'User-Agent': DESKTOP})
    by_term = {v.search: v.search_results for v in SiteVisit.query.all()}
    assert by_term == {'pokemon': 0, 'tee': 1}


def test_add_to_cart_is_tracked(client, db, simple_product):
    client.post('/cart/add', json={'product_id': simple_product.id, 'quantity': 1},
                headers={'User-Agent': DESKTOP})
    visit = SiteVisit.query.filter_by(event='add_to_cart').one()
    assert visit.product_id == simple_product.id


def test_report_funnel_sources_and_searches(db, simple_product):
    now = datetime.utcnow()
    rows = [
        SiteVisit(visitor_id='a', event='view', endpoint='main.index', path='/',
                  referrer='facebook.com', device='Mobile', created_at=now),
        SiteVisit(visitor_id='a', event='view', endpoint='shop.product_detail',
                  path='/product/basic-tee', product_id=simple_product.id,
                  device='Mobile', created_at=now),
        SiteVisit(visitor_id='a', event='add_to_cart', product_id=simple_product.id,
                  created_at=now),
        SiteVisit(visitor_id='a', event='order', order_total=9.99, created_at=now),
        SiteVisit(visitor_id='b', event='view', endpoint='shop.shop', path='/shop',
                  search='balloons', search_results=0, device='Desktop', created_at=now),
    ]
    db.session.add_all(rows)
    db.session.commit()

    r = build_report(30)
    assert r['visitors'] == 2
    assert r['orders'] == 1
    assert r['conversion'] == 50.0
    assert [s['visitors'] for s in r['funnel']] == [2, 1, 1, 0, 1]
    assert {s['name']: s['orders'] for s in r['sources']} == {'Facebook': 1, 'Direct / typed in': 0}
    assert r['missed_searches'][0]['term'] == 'balloons'
    assert r['top_products'][0]['carted'] == 1


def test_visitors_page_renders(admin_client, db):
    db.session.add(SiteVisit(visitor_id='x', event='view', endpoint='main.index', path='/',
                             device='Desktop', created_at=datetime.utcnow()))
    db.session.commit()
    for days in (1, 7, 30):
        resp = admin_client.get(f'/admin/visitors?days={days}')
        assert resp.status_code == 200
        assert 'Visitors per day' in resp.get_data(as_text=True) or days == 1


def test_visitors_page_empty_state(admin_client, db):
    html = admin_client.get('/admin/visitors').get_data(as_text=True)
    assert 'No visitors recorded yet' in html


def test_purge_removes_only_old_rows(db):
    db.session.add_all([
        SiteVisit(visitor_id='old', created_at=datetime.utcnow() - timedelta(days=500)),
        SiteVisit(visitor_id='new', created_at=datetime.utcnow()),
    ])
    db.session.commit()
    assert purge_old_visits() == 1
    assert [v.visitor_id for v in SiteVisit.query.all()] == ['new']


def test_source_names():
    assert source_name(None) == 'Direct / typed in'
    assert source_name('l.instagram.com') == 'Instagram'
    assert source_name('m.facebook.com') == 'Facebook'
    assert source_name('flyer') == 'flyer'


def test_google_analytics_only_with_valid_id(client, db):
    from app.helpers import set_site_setting
    assert 'googletagmanager' not in client.get('/').get_data(as_text=True)
    set_site_setting('ga_measurement_id', '"><script>bad')
    db.session.commit()
    assert 'googletagmanager' not in client.get('/').get_data(as_text=True)
    set_site_setting('ga_measurement_id', 'G-ABC123XYZ')
    db.session.commit()
    assert 'gtag/js?id=G-ABC123XYZ' in client.get('/').get_data(as_text=True)
