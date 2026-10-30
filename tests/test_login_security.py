"""Login protection: password-guessing limits and safe ?next= redirects."""
import pytest

from app import create_app
from app.extensions import db as _db, limiter
from app.models import User
from app.routes.auth import _safe_next


@pytest.fixture
def customer(db):
    user = User(email='shopper@test.com', first_name='S', last_name='P', is_active=True)
    user.set_password('pw-123456')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def limited_client():
    """A separate app with rate limiting switched on.

    The limiter is shared by every app instance, so switch it back off
    afterwards to keep the rest of the suite (which logs in a lot) unaffected.
    """
    app = create_app('testing', {'RATELIMIT_ENABLED': True})
    limiter.reset()
    with app.app_context():
        _db.create_all()
        try:
            yield app.test_client()
        finally:
            limiter.reset()
            limiter.enabled = False
            _db.session.remove()
            _db.drop_all()


def _bad_login(client):
    return client.post('/login', data={'email': 'nobody@test.com', 'password': 'wrong-guess'})


def test_login_blocks_after_ten_tries_a_minute(limited_client):
    for _ in range(10):
        assert _bad_login(limited_client).status_code == 200
    resp = _bad_login(limited_client)
    assert resp.status_code == 429
    assert b'Too many tries' in resp.data


def test_viewing_the_login_page_is_not_limited(limited_client):
    for _ in range(15):
        assert limited_client.get('/login').status_code == 200


def test_sign_up_blocks_after_five_tries_a_minute(limited_client):
    for _ in range(5):
        limited_client.post('/register', data={'email': 'bad'})
    assert limited_client.post('/register', data={'email': 'bad'}).status_code == 429


@pytest.mark.parametrize('target', [
    'https://evil.example/login',
    '//evil.example',
    '/\\evil.example',
    'javascript:alert(1)',
    'evil.example',
    '',
    None,
])
def test_safe_next_refuses_other_sites(target):
    assert _safe_next(target) is None


@pytest.mark.parametrize('target', ['/', '/cart', '/shop?category=toys'])
def test_safe_next_keeps_pages_on_this_site(target):
    assert _safe_next(target) == target


def test_login_ignores_next_pointing_elsewhere(client, customer):
    resp = client.post('/login?next=https://evil.example',
                       data={'email': 'shopper@test.com', 'password': 'pw-123456'})
    assert resp.status_code == 302
    assert 'evil.example' not in resp.headers['Location']


def test_login_follows_next_on_this_site(client, customer):
    resp = client.post('/login?next=/cart',
                       data={'email': 'shopper@test.com', 'password': 'pw-123456'})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/cart')
