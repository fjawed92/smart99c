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
