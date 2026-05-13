"""Tests for the variant-aware cart, helpers, and checkout flow."""
import json
from decimal import Decimal

import pytest

from app.helpers import _parse_line_key, make_line_key


def _csrf():
    # WTF_CSRF_ENABLED is False in the testing config; no token needed.
    return {'Content-Type': 'application/json'}


def test_line_key_parses_legacy_and_composite():
    assert _parse_line_key('5') == (5, 0)
    assert _parse_line_key('5:12') == (5, 12)
    assert _parse_line_key('garbage') == (None, None)
    assert make_line_key(5) == '5:0'
    assert make_line_key(5, 12) == '5:12'


def test_legacy_cart_normalizes_on_read(client, app, simple_product):
    with client.session_transaction() as s:
        s['cart'] = {str(simple_product.id): 2}
    resp = client.get('/cart/count')
    assert resp.status_code == 200
    assert resp.get_json()['count'] == 2
    # On a subsequent read, the cart should have been upgraded to the composite shape
    with client.session_transaction() as s:
        cart = s.get('cart', {})
        assert f'{simple_product.id}:0' in cart


def test_add_simple_product_to_cart(client, simple_product):
    resp = client.post('/cart/add',
                       data=json.dumps({'product_id': simple_product.id, 'quantity': 3}),
                       headers=_csrf())
    body = resp.get_json()
    assert body['success'] is True
    assert body['cart_count'] == 3


def test_add_variant_product_requires_color(client, variant_product):
    product, *_ = variant_product
    resp = client.post('/cart/add',
                       data=json.dumps({'product_id': product.id, 'quantity': 1}),
                       headers=_csrf())
    assert resp.status_code == 400
    assert 'choose a color' in resp.get_json()['message'].lower()


def test_add_out_of_stock_variant_rejected(client, variant_product):
    product, red, blue, green = variant_product
    resp = client.post('/cart/add',
                       data=json.dumps({'product_id': product.id,
                                        'variant_id': blue.id, 'quantity': 1}),
                       headers=_csrf())
    assert resp.status_code == 400


def test_add_variant_caps_at_stock(client, variant_product):
    product, red, blue, green = variant_product  # red has 5 in stock
    resp = client.post('/cart/add',
                       data=json.dumps({'product_id': product.id,
                                        'variant_id': red.id, 'quantity': 99}),
                       headers=_csrf())
    body = resp.get_json()
    assert body['success'] is True
    # Cart should be capped at 5
    with client.session_transaction() as s:
        cart = s['cart']
        assert cart[f'{product.id}:{red.id}'] == 5


def test_two_variants_are_separate_lines(client, variant_product):
    product, red, blue, green = variant_product
    client.post('/cart/add', data=json.dumps({'product_id': product.id,
                                              'variant_id': red.id, 'quantity': 2}),
                headers=_csrf())
    client.post('/cart/add', data=json.dumps({'product_id': product.id,
                                              'variant_id': green.id, 'quantity': 1}),
                headers=_csrf())
    with client.session_transaction() as s:
        assert s['cart'][f'{product.id}:{red.id}'] == 2
        assert s['cart'][f'{product.id}:{green.id}'] == 1


def test_remove_by_line_key(client, variant_product):
    product, red, *_ = variant_product
    client.post('/cart/add', data=json.dumps({'product_id': product.id,
                                              'variant_id': red.id, 'quantity': 2}),
                headers=_csrf())
    resp = client.post('/cart/remove',
                       data=json.dumps({'line_key': f'{product.id}:{red.id}'}),
                       headers=_csrf())
    assert resp.get_json()['success'] is True
    with client.session_transaction() as s:
        assert s['cart'] == {}


def test_variant_effective_price(variant_product):
    _, red, _, green = variant_product
    assert red.effective_price == Decimal('6.99')   # falls back to product
    assert green.effective_price == Decimal('7.99')  # has override


def test_product_in_stock_reflects_variants(variant_product):
    product, red, blue, green = variant_product
    assert product.has_variants is True
    assert product.in_stock is True  # red + green have stock
    assert product.total_stock == 15  # 5 + 0 + 10
    # Deactivate red and green → only blue (out of stock) remains
    red.is_active = False
    green.is_active = False
    from app.extensions import db
    db.session.commit()
    assert product.in_stock is False
