from decimal import Decimal

from app.models import Category, Product


def _product(db, name, price, compare=None, category=None):
    p = Product(name=name, slug=name.lower().replace(' ', '-'), price=Decimal(price),
                compare_price=Decimal(compare) if compare else None,
                stock_quantity=5, is_active=True, category=category)
    db.session.add(p)
    db.session.commit()
    return p


def test_deals_filter_shows_only_sale_items(client, db):
    _product(db, 'Sale Box', '4.99', compare='9.99')
    _product(db, 'Regular Pack', '5.99')
    html = client.get('/shop?deals=1').get_data(as_text=True)
    assert 'Sale Box' in html
    assert 'Regular Pack' not in html


def test_price_chip_filters_by_max_price(client, db):
    _product(db, 'Cheap Bubbles', '0.99')
    _product(db, 'Big Puzzle', '12.99')
    html = client.get('/shop?max_price=0.99').get_data(as_text=True)
    assert 'Cheap Bubbles' in html
    assert 'Big Puzzle' not in html
    assert 'is-active' in html  # the 99¢ chip is highlighted


def test_home_always_shows_balloons_tile(client, db):
    for i in range(9):
        aisle = Category(name=f'Aisle {i}', slug=f'aisle-{i}', is_active=True, sort_order=i)
        db.session.add(aisle)
        _product(db, f'Aisle Item {i}', '1.99', category=aisle)
    balloons = Category(name='Party & Balloons', slug='party-balloons', is_active=True, sort_order=99)
    db.session.add(balloons)
    _product(db, 'Foil Balloon', '1.99', category=balloons)
    html = client.get('/').get_data(as_text=True)
    assert 'Party &amp; Balloons</h3>' in html
    assert 'Party time' in html


def test_empty_categories_are_hidden(client, db):
    stocked = Category(name='Stocked Aisle', slug='stocked-aisle', is_active=True)
    empty = Category(name='Empty Aisle', slug='empty-aisle', is_active=True)
    hidden_only = Category(name='Inactive Only Aisle', slug='inactive-only-aisle', is_active=True)
    db.session.add_all([stocked, empty, hidden_only])
    _product(db, 'Yo-Yo', '0.99', category=stocked)
    off = _product(db, 'Old Kite', '0.99', category=hidden_only)
    off.is_active = False
    db.session.commit()
    for url in ('/', '/shop'):
        html = client.get(url).get_data(as_text=True)
        assert 'Stocked Aisle' in html
        assert 'Empty Aisle' not in html
        assert 'Inactive Only Aisle' not in html


def test_layout_has_logo_and_tab_bar(client):
    html = client.get('/').get_data(as_text=True)
    assert 'img/logo.webp' in html
    assert 'class="tabbar"' in html
    assert 'id="cartBadgeMobile"' in html
