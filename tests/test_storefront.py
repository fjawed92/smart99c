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
        db.session.add(Category(name=f'Aisle {i}', slug=f'aisle-{i}', is_active=True, sort_order=i))
    db.session.add(Category(name='Party & Balloons', slug='party-balloons', is_active=True, sort_order=99))
    db.session.commit()
    html = client.get('/').get_data(as_text=True)
    assert 'Party &amp; Balloons</h3>' in html
    assert 'Party time' in html


def test_layout_has_logo_and_tab_bar(client):
    html = client.get('/').get_data(as_text=True)
    assert 'img/logo.webp' in html
    assert 'class="tabbar"' in html
    assert 'id="cartBadgeMobile"' in html
