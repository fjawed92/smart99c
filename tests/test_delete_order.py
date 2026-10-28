from decimal import Decimal

from app.models import Order, OrderItem, ShippingAddress


def _order(db, product, status, qty=2):
    o = Order(order_number=f'S99-{status}', status=status, payment_method='cash',
              subtotal=Decimal('3.98'), tax=Decimal('0.35'), shipping_cost=0, total=Decimal('4.33'))
    db.session.add(o)
    db.session.flush()
    db.session.add(OrderItem(order_id=o.id, product_id=product.id, product_name=product.name,
                             quantity=qty, unit_price=Decimal('1.99'), total_price=Decimal('3.98')))
    db.session.add(ShippingAddress(order_id=o.id, first_name='Test', last_name='Order', email='t@x.com',
                                   address_line1='', city='', state='', zip_code=''))
    db.session.commit()
    return o


def test_delete_open_order_restocks_and_removes_everything(admin_client, db, simple_product):
    stock = simple_product.stock_quantity
    order = _order(db, simple_product, 'processing')
    page = admin_client.get(f'/admin/orders/{order.id}').get_data(as_text=True)
    assert 'Delete order' in page
    resp = admin_client.post(f'/admin/orders/{order.id}/delete')
    assert resp.status_code == 302
    assert Order.query.count() == 0
    assert OrderItem.query.count() == 0 and ShippingAddress.query.count() == 0
    assert simple_product.stock_quantity == stock + 2


def test_delete_cancelled_order_does_not_restock_twice(admin_client, db, simple_product):
    stock = simple_product.stock_quantity
    order = _order(db, simple_product, 'cancelled')
    admin_client.post(f'/admin/orders/{order.id}/delete')
    assert Order.query.count() == 0
    assert simple_product.stock_quantity == stock


def test_delete_needs_admin(client, db, simple_product):
    order = _order(db, simple_product, 'processing')
    client.post(f'/admin/orders/{order.id}/delete')
    assert Order.query.count() == 1


def test_dashboard_order_count_skips_cancelled(admin_client, db, simple_product):
    _order(db, simple_product, 'processing')
    _order(db, simple_product, 'cancelled')
    page = admin_client.get('/admin/').get_data(as_text=True)
    assert '<div class="fs-4 fw-700">1</div>' in page
