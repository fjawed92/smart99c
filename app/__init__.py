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

    # IZY Ops (the store's back office) manages products, stock and orders
    # through /api/izyops. It and Stripe sign their own requests, so neither
    # carries a CSRF token: without the exemption every Stripe webhook was
    # refused with a 400 and paid card orders could stay "pending".
    from app.routes.izyops_api import izyops_api_bp
    from app.routes.checkout import stripe_webhook
    app.register_blueprint(izyops_api_bp)
    csrf.exempt(izyops_api_bp)
    csrf.exempt(stripe_webhook)

    from app.services import izyops_notify
    izyops_notify.install()

    # When IZY Ops manages the products (IZY Ops → Websites → Catalog, or
    # IZYOPS_OWNS_CATALOG=1), the admin's product forms stop saving; orders,
    # stock counts from IZY Ops and everything else keep working.
    from app.routes.admin import CATALOG_ENDPOINTS
    from app.services import izyops_mode, product_touch
    product_touch.install()

    @app.before_request
    def catalog_lock():
        from flask import flash, redirect, request as req, url_for
        if req.method == 'POST' and req.endpoint in CATALOG_ENDPOINTS and izyops_mode.locked():
            flash('Products are managed in IZY Ops — change them there and this site follows within a minute.',
                  'warning')
            return redirect(url_for('admin.products'))

    # Visitor analytics (Admin → Visitors)
    from app.services.visits import record_page_view
    app.after_request(record_page_view)

    # Cash-pickup reminders and expired holds: checked at most every 5 minutes
    # while the site is in use (there's no separate background worker).
    from app.services.pickup import process_holds
    from app.timezones import format_local
    app.jinja_env.filters['local_time'] = format_local

    def pct(value, places=2):
        """10 → '10%', 8.875 → '8.875%', 12.50 → '12.5%'."""
        from decimal import Decimal
        text = f'{Decimal(str(value or 0)):.{places}f}'.rstrip('0').rstrip('.')
        return f'{text}%'
    app.jinja_env.filters['pct'] = pct
    last_hold_check = {'at': None}

    @app.before_request
    def check_pickup_holds():
        from flask import request as req
        if app.config.get('TESTING') or req.path.startswith('/static'):
            return
        now = datetime.utcnow()
        if last_hold_check['at'] and (now - last_hold_check['at']).total_seconds() < 300:
            return
        last_hold_check['at'] = now
        try:
            process_holds(now)
        except Exception:
            db.session.rollback()
            app.logger.exception('Pickup hold check failed')

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
        # "Free delivery over $X" comes from the free delivery option's minimum
        # order (Admin → Shipping), so the banner always matches checkout.
        store['free_delivery_over'] = None
        cash_pct = 0
        sale_banners = []
        if db_ok:
            try:
                from app.models import ShippingRate, is_pickup_option
                from app.services import pricing
                free = [r for r in ShippingRate.query.filter_by(is_active=True).all()
                        if r.price == 0 and r.min_order_amount and not is_pickup_option(r.name)]
                if free:
                    over = min(r.min_order_amount for r in free)
                    store['free_delivery_over'] = f'{over:.2f}'.removesuffix('.00')
                if setting('cash_pickup_active', '1') == '1':
                    cash_pct = pricing.cash_discount_percent()
                for sale in pricing.running_sales():
                    if sale.show_banner:
                        where = f'{sale.category.name} ' if sale.category else 'everything '
                        sale_banners.append(f'{sale.name}: {sale.percent.normalize():f}% off {where}'.strip())
            except SQLAlchemyError:
                db.session.rollback()
        cash_banner = ''
        if cash_pct and setting('cash_discount_banner_active', '1') == '1':
            cash_banner = f'Pay cash at pickup & save {cash_pct.normalize():f}%'
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
            sale_banners=sale_banners,
            cash_banner=cash_banner,
            cash_discount_pct=cash_pct,
            nav_categories=categories,
            footer_social=footer_social,
            stripe_public_key=app.config.get('STRIPE_PUBLIC_KEY', ''),
            store=store,
            ga_measurement_id=ga_id,
            current_year=datetime.utcnow().year,
        )

    return app
