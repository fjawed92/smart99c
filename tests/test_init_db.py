from app.models import User
from init_db import ensure_admin, ensure_schema


def test_ensure_schema_is_idempotent(app):
    ensure_schema()
    ensure_schema()


def test_admin_created_from_env(app, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'Owner@Smart99c.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'S3cret-pass')
    ensure_admin()
    admin = User.query.filter_by(email='owner@smart99c.com').one()
    assert admin.is_admin and admin.check_password('S3cret-pass')
    # Second run does not create a duplicate.
    ensure_admin()
    assert User.query.filter_by(is_admin=True).count() == 1


def test_no_admin_without_env(app, monkeypatch):
    monkeypatch.delenv('ADMIN_EMAIL', raising=False)
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    ensure_admin()
    assert User.query.count() == 0


def test_pages_render_when_database_is_down(app, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.models import Category

    def boom(*a, **k):
        raise OperationalError('SELECT 1', {}, Exception('host not found'))

    monkeypatch.setattr(type(Category.query), 'all', boom)
    resp = app.test_client().get('/definitely-missing-page')
    assert resp.status_code == 404
