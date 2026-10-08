import re
import cloudinary
from datetime import datetime
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError
from config import config
from app.extensions import db, login_manager, migrate, csrf, mail


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Init extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    mail.init_app(app)

    # Login manager settings
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    # Cloudinary config
    if app.config.get('CLOUDINARY_CLOUD_NAME'):
        cloudinary.config(
            cloud_name=app.config['CLOUDINARY_CLOUD_NAME'],
            api_key=app.config['CLOUDINARY_API_KEY'],
            api_secret=app.config['CLOUDINARY_API_SECRET'],
        )

    # Register blueprints
    from app.routes.main import main_bp
    from app.routes.shop import shop_bp
    from app.routes.cart import cart_bp
    from app.routes.checkout import checkout_bp
    from app.routes.orders import orders_bp
    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Visitor analytics (Admin → Visitors)
    from app.services.visits import record_page_view
    app.after_request(record_page_view)

    # Custom error handlers
    from flask import render_template

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    # Jinja2 globals
    from app.models import SiteSettings, Category
    from app.helpers import get_cart_count, get_site_setting
    from app.art import product_art, category_art, art_svg, art_kind

    app.jinja_env.globals.update(product_art=product_art, category_art=category_art,
                                 art_svg=art_svg, art_kind=art_kind)

    from app.store import STORE

    @app.context_processor
    def inject_globals():
        cart_count = get_cart_count()
        try:
            categories = Category.shoppable().order_by(Category.sort_order, Category.name).all()
            db_ok = True
        except SQLAlchemyError:
            # Database unreachable — still render pages (incl. the error page)
            # instead of crashing inside the error handler.
            db.session.rollback()
            app.logger.exception('Database unavailable while building page globals')
            categories, db_ok = [], False

        def setting(key, default=''):
            return get_site_setting(key, default) if db_ok else default

        announcement = setting('announcement', '')

        # Admin → Settings can override hours and the free-delivery amount.
        store = dict(STORE)
        hours_text = setting('store_hours', '').strip()
        if hours_text:
            hours = []
            for line in hours_text.splitlines():
                day, sep, time = line.partition(':')
                if line.strip():
                    hours.append((day.strip(), time.strip()) if sep else (line.strip(), ''))
            store['hours'] = hours or STORE['hours']
        try:
            threshold = float(setting('free_shipping_threshold', '') or 0)
            if threshold > 0:
                store['free_delivery_over'] = int(threshold) if threshold.is_integer() else threshold
        except ValueError:
            pass
        ga_id = setting('ga_measurement_id', '').strip().upper()
        if not re.fullmatch(r'G-[A-Z0-9]{4,15}', ga_id):
            ga_id = ''
        footer_social = {
            'facebook': {
                'url': setting('facebook_url', ''),
                'active': setting('facebook_active', '1') == '1',
            },
            'instagram': {
                'url': setting('instagram_url', ''),
                'active': setting('instagram_active', '1') == '1',
            },
            'tiktok': {
                'url': setting('tiktok_url', ''),
                'active': setting('tiktok_active', '1') == '1',
            },
        }
        return dict(
            cart_count=cart_count,
            announcement=announcement,
            nav_categories=categories,
            footer_social=footer_social,
            stripe_public_key=app.config.get('STRIPE_PUBLIC_KEY', ''),
            store=store,
            ga_measurement_id=ga_id,
            current_year=datetime.utcnow().year,
        )

    return app
