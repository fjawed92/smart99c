"""Deleting an order: cancelled, stock back, left out of every list and total,
and IZY Ops hears about it (and can delete one too)."""
import base64
from types import SimpleNamespace

import pytest

from app.models import Order
from tests.test_izyops_api import _order


@pytest.fixture
def api(app):
    app.config.update(IZYOPS_API_USERNAME='izy', IZYOPS_API_PASSWORD='secret')
    token = base64.b64encode(b'izy:secret').decode()
    return SimpleNamespace(client=app.test_client(), auth={'Authorization': f'Basic {token}'})


def test_admin_delete_restocks_hides_and_drops_from_totals(admin_client, db, simple_product):
    keep = _order(db, number='S99-00001', product=simple_product)
    gone = _order(db, number='S99-00002', product=simple_product)
    stock = simple_product.stock_quantity

    resp = admin_client.post(f'/admin/orders/{gone.id}/delete')
    assert resp.status_code == 302

    gone = db.session.get(Order, gone.id)
    assert gone.is_deleted and gone.status == 'cancelled'
    assert db.session.get(type(simple_product), simple_product.id).stock_quantity == stock + 2
    assert [o.id for o in Order.visible()] == [keep.id]

    admin_client.get('/admin/orders')  # shows (and clears) the "deleted" message
    listing = admin_client.get('/admin/orders?tab=all').get_data(as_text=True)
    assert 'S99-00001' in listing and 'S99-00002' not in listing
    dashboard = admin_client.get('/admin/').get_data(as_text=True)
    assert 'S99-00002' not in dashboard

    # The order page still opens, says it was deleted, and can't be changed.
    detail = admin_client.get(f'/admin/orders/{gone.id}').get_data(as_text=True)
    assert 'This order was deleted' in detail and 'Delete order' not in detail
    admin_client.post(f'/admin/orders/{gone.id}/pickup', data={'action': 'picked_up'})
    admin_client.post(f'/admin/orders/{gone.id}/status', data={'status': 'processing'})
    assert db.session.get(Order, gone.id).status == 'cancelled'

    # Deleting again doesn't put the stock back twice.
    admin_client.post(f'/admin/orders/{gone.id}/delete')
    assert db.session.get(type(simple_product), simple_product.id).stock_quantity == stock + 2


def test_deleting_a_cancelled_order_does_not_restock_again(admin_client, db, simple_product):
    order = _order(db, status='cancelled', product=simple_product)
    stock = simple_product.stock_quantity
    admin_client.post(f'/admin/orders/{order.id}/delete')
    assert db.session.get(Order, order.id).is_deleted
    assert db.session.get(type(simple_product), simple_product.id).stock_quantity == stock


def test_izyops_sees_the_deleted_flag(api, db, simple_product):
    order = _order(db, product=simple_product)
    from app.services.pickup import delete_order
    delete_order(order)
    db.session.commit()
    rows = api.client.get('/api/izyops/orders?scope=all', headers=api.auth).get_json()['data']['orders']
    row = next(r for r in rows if r['orderid'] == order.id)
    assert row['deleted'] is True and row['deleted_at']
    assert row['status'] == 'canceled' and row['canceled'] is True and row['inventory_captured'] is False


def test_izyops_can_delete_even_a_finished_order(api, db, simple_product):
    order = _order(db, status='picked_up', product=simple_product)
    stock = simple_product.stock_quantity
    resp = api.client.patch(f'/api/izyops/orders/{order.id}', json={'deleted': True, 'actor': 'Sam'},
                            headers=api.auth)
    data = resp.get_json()['data']
    assert resp.status_code == 200 and data['deleted'] is True and data['status'] == 'canceled'
    order = db.session.get(Order, order.id)
    assert order.is_deleted and 'Deleted in IZY Ops by Sam' in order.notes
    assert db.session.get(type(simple_product), simple_product.id).stock_quantity == stock + 2

    # A retry is harmless.
    again = api.client.patch(f'/api/izyops/orders/{order.id}', json={'deleted': True}, headers=api.auth)
    assert again.status_code == 200
    assert db.session.get(type(simple_product), simple_product.id).stock_quantity == stock + 2
