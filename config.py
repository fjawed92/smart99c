import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Normalise Render's postgres:// and route through psycopg3 driver
    _db_url = os.environ.get('DATABASE_URL', 'sqlite:///smart99c.db')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif _db_url.startswith('postgresql://'):
        _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cloudinary
    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
    CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
    CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

    # Stripe
    STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

    # Mail
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_USERNAME')

    # IZY Ops connection (the back office manages this site's products, stock
    # and orders through /api/izyops). Its own login, separate from the
    # IZYOPS_USERNAME / IZYOPS_PASSWORD this site uses to read IZY Ops.
    IZYOPS_API_USERNAME = os.environ.get('IZYOPS_API_USERNAME')
    IZYOPS_API_PASSWORD = os.environ.get('IZYOPS_API_PASSWORD')
    # "An order just changed" ping to IZY Ops (Settings → Websites shows both).
    IZYOPS_NOTIFY_URL = os.environ.get('IZYOPS_NOTIFY_URL')
    IZYOPS_NOTIFY_KEY = os.environ.get('IZYOPS_NOTIFY_KEY')
    # 1 = product details are edited in IZY Ops only; the admin product pages
    # become read-only. Off until the catalog has been imported into IZY Ops.
    IZYOPS_OWNS_CATALOG = os.environ.get('IZYOPS_OWNS_CATALOG', '').strip().lower() in ('1', 'true', 'yes', 'on')

    # App
    TAX_RATE = float(os.environ.get('TAX_RATE', '0.08875'))  # NY tax rate
    PRODUCTS_PER_PAGE = 12
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

    # Flask-Limiter (login / sign-up guessing protection)
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    RATELIMIT_ENABLED = True


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    SECRET_KEY = 'test-secret'
    RATELIMIT_ENABLED = False  # tests log in many times from one address


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
