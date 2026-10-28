from app.helpers import set_site_setting, encrypt_secret
from app.services import mailer


class FakeSMTP:
    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg, from_addr=None, to_addrs=None):
        # Real smtplib serializes the message to bytes; this raised on '¢' before.
        FakeSMTP.sent.append(msg.as_bytes())


def test_send_email_handles_cent_sign(app, db, monkeypatch):
    set_site_setting('mail_username', 'store@example.com')
    set_site_setting('mail_password_encrypted', encrypt_secret('app-pass'))
    set_site_setting('mail_from_name', 'Smart 99¢ Plus')
    db.session.commit()
    FakeSMTP.sent.clear()
    monkeypatch.setattr(mailer.smtplib, 'SMTP', FakeSMTP)

    mailer.send_email('buyer@example.com', 'Smart 99¢ Plus — Test Email',
                      '<p>Balloons from 99¢</p>', text_body='Balloons from 99¢')

    assert len(FakeSMTP.sent) == 1
    raw = FakeSMTP.sent[0]
    assert b'99=C2=A2' in raw  # ¢ safely encoded in the subject
    assert b'multipart/alternative' in raw


# ─── Branded templates ──────────────────────────────────────────────────────

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Order, OrderItem, ShippingAddress, PaymentLink
from app.services import pickup


def _order(db, product, payment='cash'):
    o = Order(order_number='S99-00418', status='ready', payment_method=payment,
              shipping_method='Store pickup (West Babylon)',
              subtotal=Decimal('7.95'), tax=Decimal('0.69'), shipping_cost=0,
              cash_discount=Decimal('0.50'), total=Decimal('8.14'),
              pickup_deadline=datetime.utcnow() + timedelta(hours=20) if payment == 'cash' else None)
    db.session.add(o)
    db.session.flush()
    db.session.add(OrderItem(order_id=o.id, product_id=product.id, product_name='Foil balloon <Happy>',
                             quantity=2, unit_price=Decimal('1.99'), total_price=Decimal('3.98')))
    db.session.add(ShippingAddress(order_id=o.id, first_name='Aisha', last_name='K', email='a@k.com',
                                   address_line1='', city='', state='', zip_code=''))
    db.session.commit()
    return o


@pytest.mark.parametrize('kind', ['reserved', 'ready', 'reminder', 'released'])
def test_pickup_emails_render_branded_html(app, db, simple_product, kind):
    order = _order(db, simple_product)
    with app.test_request_context():
        subject, html, text = pickup._email_body(order, kind)
    assert subject.endswith('S99-00418')
    assert 'email-logo.png' in html
    assert 'Aisha' in html and '$8.14' in html
    assert 'Cash discount' in html
    assert 'Foil balloon &lt;Happy&gt;' in html   # customer/product text is escaped
    assert 'S99-00418' in text


def test_card_ready_email_says_paid(app, db, simple_product):
    order = _order(db, simple_product, payment='card')
    with app.test_request_context():
        _, html, _ = pickup._email_body(order, 'ready')
    assert 'Paid by card' in html
    assert 'Pick up by' not in html


def test_payment_link_email_renders(app, db, monkeypatch):
    from app.routes import admin
    sent = []
    monkeypatch.setattr(mailer, 'send_email', lambda to, subj, html, text_body=None: sent.append(html))
    link = PaymentLink(description='Balloon arch deposit', amount=Decimal('25.00'),
                       customer_email='c@x.com', customer_name='Omar Ali', url='https://pay.example/x')
    db.session.add(link)
    db.session.commit()
    with app.test_request_context():
        ok, err = admin._send_payment_link_email(link)
    assert ok, err
    assert 'Hi Omar' in sent[0] and 'Pay $25.00' in sent[0] and 'https://pay.example/x' in sent[0]
