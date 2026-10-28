from datetime import datetime
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    force_password_change = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship('Order', back_populates='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f'<User {self.email}>'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    cloudinary_public_id = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    products = db.relationship('Product', back_populates='category', lazy='dynamic')

    @classmethod
    def shoppable(cls):
        """Active categories that have at least one active product — the ones shoppers see."""
        has_products = db.exists().where(Product.category_id == cls.id, Product.is_active.is_(True))
        return cls.query.filter(cls.is_active.is_(True), has_products)

    def __repr__(self):
        return f'<Category {self.name}>'


class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(280), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    short_description = db.Column(db.String(500))
    price = db.Column(db.Numeric(10, 2), nullable=False)
    compare_price = db.Column(db.Numeric(10, 2))
    cost_price = db.Column(db.Numeric(10, 2))
    sku = db.Column(db.String(100), unique=True)
    stock_quantity = db.Column(db.Integer, default=0)
    track_inventory = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    is_featured = db.Column(db.Boolean, default=False)
    weight = db.Column(db.Numeric(8, 2), default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    # Barcode and IzyOps catalog link — set when a product is pulled from IzyOps.
    upc = db.Column(db.String(120), index=True)
    izyops_product_id = db.Column(db.Integer, index=True)
    # Store order set by dragging rows in Admin → Products (1 = first).
    # New products start at 0, so they show first until moved.
    sort_order = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('Category', back_populates='products')
    images = db.relationship('ProductImage', back_populates='product',
                             cascade='all, delete-orphan', order_by='ProductImage.sort_order')
    variants = db.relationship('ProductVariant', back_populates='product',
                               cascade='all, delete-orphan',
                               order_by='ProductVariant.sort_order, ProductVariant.id')
    order_items = db.relationship('OrderItem', back_populates='product')

    @staticmethod
    def store_order():
        """ORDER BY for the owner's drag-and-drop order (newest first on ties)."""
        return (Product.sort_order.asc(), Product.created_at.desc(), Product.id.desc())

    @property
    def primary_image(self):
        gallery = [img for img in self.images if img.variant_id is None]
        primary = next((img for img in gallery if img.is_primary), None)
        if primary:
            return primary
        if gallery:
            return gallery[0]
        return self.images[0] if self.images else None

    @property
    def primary_image_url(self):
        img = self.primary_image
        return img.image_url if img else None

    @property
    def gallery_images(self):
        return [img for img in self.images if img.variant_id is None]

    @property
    def sale_percent(self):
        """% off from a running store-wide or category sale (Admin → Discounts)."""
        from app.services.pricing import sale_percent_for
        return sale_percent_for(self)

    def price_for(self, variant=None):
        """What the shopper pays for one, with any running sale taken off."""
        from app.services.pricing import apply_percent
        base = variant.effective_price if variant is not None else self.price
        return apply_percent(base, self.sale_percent)

    @property
    def current_price(self):
        return self.price_for()

    @property
    def was_price(self):
        """The crossed-out price, or None when nothing is on sale."""
        if self.compare_price and self.compare_price > self.current_price:
            return self.compare_price
        if self.sale_percent:
            return self.price
        return None

    @property
    def is_on_sale(self):
        return self.was_price is not None

    @property
    def active_variants(self):
        return [v for v in self.variants if v.is_active]

    @property
    def has_variants(self):
        return any(v.is_active for v in self.variants)

    @property
    def total_stock(self):
        if self.has_variants:
            return sum(v.stock_quantity for v in self.variants if v.is_active)
        return self.stock_quantity

    @property
    def in_stock(self):
        if not self.track_inventory:
            return True
        if self.has_variants:
            return any(v.stock_quantity > 0 for v in self.variants if v.is_active)
        return self.stock_quantity > 0

    @property
    def discount_percent(self):
        was = self.was_price
        if was:
            return int(((was - self.current_price) / was) * 100)
        return 0

    def __repr__(self):
        return f'<Product {self.name}>'


class ProductVariant(db.Model):
    __tablename__ = 'product_variants'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    color_name = db.Column(db.String(60), nullable=False)
    color_hex = db.Column(db.String(7))
    sku = db.Column(db.String(100), unique=True)
    price_override = db.Column(db.Numeric(10, 2))
    cost_price = db.Column(db.Numeric(10, 2))
    stock_quantity = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = db.relationship('Product', back_populates='variants')
    images = db.relationship('ProductImage', back_populates='variant',
                             foreign_keys='ProductImage.variant_id',
                             order_by='ProductImage.sort_order')

    @property
    def effective_price(self):
        return self.price_override if self.price_override is not None else self.product.price

    @property
    def sale_price(self):
        return self.product.price_for(self)

    @property
    def image_url(self):
        if self.images:
            return self.images[0].image_url
        return self.product.primary_image_url

    @property
    def in_stock(self):
        return self.is_active and self.stock_quantity > 0

    def __repr__(self):
        return f'<ProductVariant {self.product_id} {self.color_name}>'


class ProductImage(db.Model):
    __tablename__ = 'product_images'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    variant_id = db.Column(db.Integer,
                           db.ForeignKey('product_variants.id', ondelete='SET NULL'),
                           nullable=True, index=True)
    image_url = db.Column(db.String(500), nullable=False)
    cloudinary_public_id = db.Column(db.String(255))
    is_primary = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)

    product = db.relationship('Product', back_populates='images')
    variant = db.relationship('ProductVariant', back_populates='images', foreign_keys=[variant_id])

    def __repr__(self):
        return f'<ProductImage {self.product_id} variant={self.variant_id} primary={self.is_primary}>'


def is_pickup_option(name):
    return 'pickup' in (name or '').lower()


class Order(db.Model):
    __tablename__ = 'orders'

    STATUS_CHOICES = ['pending', 'processing', 'ready', 'picked_up', 'shipped', 'delivered',
                      'no_show', 'cancelled', 'refunded']
    STATUS_LABELS = {'processing': 'New', 'ready': 'Ready for pickup', 'picked_up': 'Picked up',
                     'no_show': 'No-show'}
    OPEN_STATUSES = ('pending', 'processing', 'ready')

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    status = db.Column(db.String(20), default='pending', nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    shipping_cost = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    stripe_payment_intent_id = db.Column(db.String(255))
    stripe_charge_id = db.Column(db.String(255))
    stripe_livemode = db.Column(db.Boolean)
    shipping_method = db.Column(db.String(100))
    service_fee = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    promo_code = db.Column(db.String(40))
    promo_discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    cash_discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    payment_method = db.Column(db.String(20), nullable=False, default='card')  # card | cash
    pickup_deadline = db.Column(db.DateTime)   # cash pickups only (UTC)
    ready_at = db.Column(db.DateTime)
    picked_up_at = db.Column(db.DateTime)
    # Cash orders: when the store's register took the money (IZY Ops says so).
    # Picked up also means paid; this covers paid-but-not-handed-over-yet.
    cash_paid_at = db.Column(db.DateTime)
    shipped_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    no_show_at = db.Column(db.DateTime)
    refunded_at = db.Column(db.DateTime)
    # Deleted by staff (here or in IZY Ops): cancelled, stock put back, and
    # left out of every list and total. Kept so IZY Ops and the register hear.
    deleted_at = db.Column(db.DateTime, index=True)
    reminder_sent_at = db.Column(db.DateTime)
    # Given back to the card so far (refunds from IZY Ops, partial or full).
    amount_refunded = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='orders')
    items = db.relationship('OrderItem', back_populates='order', cascade='all, delete-orphan')
    shipping_address = db.relationship('ShippingAddress', back_populates='order',
                                       uselist=False, cascade='all, delete-orphan')

    @classmethod
    def visible(cls):
        """Every order except deleted ones — what lists and totals count."""
        return cls.query.filter(cls.deleted_at.is_(None))

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def is_pickup(self):
        """True / False when the delivery option was saved, None for older orders."""
        if not self.shipping_method:
            # Older orders: a paid delivery fee means it was delivery.
            return False if self.shipping_cost and self.shipping_cost > 0 else None
        return is_pickup_option(self.shipping_method)

    # Which column records the time an order reached each status.
    STATUS_TIMES = {'ready': 'ready_at', 'picked_up': 'picked_up_at', 'shipped': 'shipped_at',
                    'delivered': 'delivered_at', 'cancelled': 'cancelled_at',
                    'no_show': 'no_show_at', 'refunded': 'refunded_at'}

    def set_status(self, status, now=None):
        """Change the status and record when it happened (first time only)."""
        self.status = status
        column = self.STATUS_TIMES.get(status)
        if column and getattr(self, column) is None:
            setattr(self, column, now or datetime.utcnow())

    @property
    def timeline(self):
        """[(label, time or None, tone)] for the order history, oldest first.

        Older orders may have a status without a saved time; those steps are
        listed last with no time rather than a made-up one.
        """
        placed = 'Reserved online (cash at pickup)' if self.is_cash else 'Ordered online'
        steps = [(placed, self.created_at, 'primary')]
        labels = [('ready_at', 'Packed · ready for pickup', 'primary'),
                  ('reminder_sent_at', 'Pickup reminder emailed', 'muted'),
                  ('cash_paid_at', 'Paid at register', 'success'),
                  ('picked_up_at', 'Picked up' + (' · cash paid' if self.is_cash else ''), 'success'),
                  ('shipped_at', 'Shipped / out for delivery', 'primary'),
                  ('delivered_at', 'Delivered', 'success'),
                  ('no_show_at', 'No-show · items put back in stock', 'danger'),
                  ('cancelled_at', 'Cancelled · items put back in stock', 'danger'),
                  ('refunded_at', 'Refunded', 'danger'),
                  ('deleted_at', 'Deleted · hidden from orders and totals', 'danger')]
        steps += [(label, getattr(self, col), tone) for col, label, tone in labels if getattr(self, col)]
        steps.sort(key=lambda s: s[1])
        column = self.STATUS_TIMES.get(self.status)
        if column and getattr(self, column) is None:
            steps.append((self.status_label, None, 'muted'))
        return steps

    @property
    def is_cash(self):
        return self.payment_method == 'cash'

    @property
    def cash_paid(self):
        """A cash order the customer has paid for at the register."""
        return self.is_cash and (self.cash_paid_at is not None or self.status in ('picked_up', 'delivered'))

    @property
    def discount_total(self):
        return (self.promo_discount or 0) + (self.cash_discount or 0)

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, (self.status or '').replace('_', ' ').title())

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self):
        return bool(self.is_cash and self.is_open and self.pickup_deadline
                    and datetime.utcnow() > self.pickup_deadline)

    @property
    def time_left_label(self):
        if not self.pickup_deadline:
            return ''
        secs = int((self.pickup_deadline - datetime.utcnow()).total_seconds())
        days, rem = divmod(abs(secs), 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        if days:
            span = f'{days}d {hours}h'
        elif hours:
            span = f'{hours}h {mins}m' if hours < 3 else f'{hours}h'
        else:
            span = f'{mins}m'
        return f'{span} left' if secs >= 0 else f'Overdue by {span}'

    @property
    def fulfillment_label(self):
        if self.shipping_method:
            return self.shipping_method + (' · pay cash' if self.is_cash else '')
        # Orders placed before the option was saved: only the price is known.
        if self.shipping_cost and self.shipping_cost > 0:
            return 'Delivery (option not recorded)'
        return 'Pickup or free delivery (not recorded)'

    def __repr__(self):
        return f'<Order {self.order_number}>'


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    variant_id = db.Column(db.Integer,
                           db.ForeignKey('product_variants.id', ondelete='SET NULL'),
                           nullable=True)
    product_name = db.Column(db.String(255), nullable=False)
    product_sku = db.Column(db.String(100))
    product_color = db.Column(db.String(60))
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship('Order', back_populates='items')
    product = db.relationship('Product', back_populates='order_items')
    variant = db.relationship('ProductVariant', foreign_keys=[variant_id])

    @property
    def display_name(self):
        if self.product_color:
            return f'{self.product_name} — {self.product_color}'
        return self.product_name

    def __repr__(self):
        return f'<OrderItem {self.product_name} x{self.quantity}>'


class ShippingAddress(db.Model):
    __tablename__ = 'shipping_addresses'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, unique=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(30))
    address_line1 = db.Column(db.String(255), nullable=False)
    address_line2 = db.Column(db.String(255))
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(50), nullable=False)
    zip_code = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(50), default='US', nullable=False)

    order = db.relationship('Order', back_populates='shipping_address')

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'

    @property
    def full_address(self):
        parts = [self.address_line1]
        if self.address_line2:
            parts.append(self.address_line2)
        parts.append(f'{self.city}, {self.state} {self.zip_code}')
        return ', '.join(parts)

    def __repr__(self):
        return f'<ShippingAddress {self.full_name}>'


class ShippingRate(db.Model):
    __tablename__ = 'shipping_rates'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    min_order_amount = db.Column(db.Numeric(10, 2), default=0)
    price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    estimated_days = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<ShippingRate {self.name} ${self.price}>'


class Sale(db.Model):
    """A % off sale on the whole store (category_id empty) or one category."""
    __tablename__ = 'sales'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    percent = db.Column(db.Numeric(5, 2), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id', ondelete='CASCADE'), nullable=True)
    starts_at = db.Column(db.DateTime)   # UTC; empty = right away
    ends_at = db.Column(db.DateTime)     # UTC; empty = until turned off
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    show_banner = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship('Category')

    def is_running(self, now=None):
        now = now or datetime.utcnow()
        return bool(self.is_active and (not self.starts_at or self.starts_at <= now)
                    and (not self.ends_at or now < self.ends_at))

    @property
    def scope_label(self):
        return self.category.name if self.category else 'Whole store'

    def __repr__(self):
        return f'<Sale {self.name} {self.percent}%>'


class PromoCode(db.Model):
    __tablename__ = 'promo_codes'

    KINDS = {'percent': '% off', 'amount': '$ off', 'free_shipping': 'Free delivery'}

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)  # stored uppercase
    kind = db.Column(db.String(20), nullable=False, default='percent')
    value = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    min_order = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    max_uses = db.Column(db.Integer)              # empty = no limit
    uses = db.Column(db.Integer, nullable=False, default=0)
    once_per_customer = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def label(self):
        if self.kind == 'percent':
            return f'{self.value.normalize():f}% off'
        if self.kind == 'amount':
            return f'${self.value:.2f} off'
        return 'Free delivery'

    def __repr__(self):
        return f'<PromoCode {self.code}>'


class SiteSettings(db.Model):
    __tablename__ = 'site_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)

    def __repr__(self):
        return f'<SiteSettings {self.key}>'


class PaymentLink(db.Model):
    __tablename__ = 'payment_links'

    STATUS_CHOICES = ['active', 'paid', 'void']

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    customer_email = db.Column(db.String(255))
    customer_name = db.Column(db.String(255))

    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='SET NULL'), nullable=True)
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variants.id', ondelete='SET NULL'), nullable=True)
    deduct_stock_on_paid = db.Column(db.Boolean, default=False, nullable=False)

    stripe_product_id = db.Column(db.String(255))
    stripe_price_id = db.Column(db.String(255))
    stripe_payment_link_id = db.Column(db.String(255), index=True)
    stripe_checkout_session_id = db.Column(db.String(255))
    url = db.Column(db.String(500))

    status = db.Column(db.String(20), default='active', nullable=False)
    email_sent_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', foreign_keys=[product_id])
    variant = db.relationship('ProductVariant', foreign_keys=[variant_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def __repr__(self):
        return f'<PaymentLink {self.description} ${self.amount}>'


class SiteVisit(db.Model):
    """One page view or shopper action, for Admin → Visitors.

    `visitor_id` is a random token kept in the shopper's session cookie —
    no IP address or personal details are stored.
    """
    __tablename__ = 'site_visits'

    EVENTS = ['view', 'add_to_cart', 'order']

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    visitor_id = db.Column(db.String(32), nullable=False, index=True)
    event = db.Column(db.String(20), default='view', nullable=False)
    endpoint = db.Column(db.String(80))
    path = db.Column(db.String(300))
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='SET NULL'), index=True)
    referrer = db.Column(db.String(120))
    device = db.Column(db.String(10))
    search = db.Column(db.String(120))
    search_results = db.Column(db.Integer)
    order_total = db.Column(db.Numeric(10, 2))

    product = db.relationship('Product')


class ProductBackup(db.Model):
    """A copy of a product as it was, saved before IZY Ops changed it (and a
    full copy of every product the first time IZY Ops connected), so nothing
    on the website is ever lost. ``data`` is JSON (app/services/backups.py)."""
    __tablename__ = 'product_backups'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, index=True)   # no FK: a backup outlives its product
    sku = db.Column(db.String(100))
    name = db.Column(db.String(255))
    reason = db.Column(db.String(120), nullable=False)
    batch = db.Column(db.String(40), index=True)     # set on the rows of one full backup
    data = db.Column(db.Text, nullable=False)
    taken_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self):
        return f'<ProductBackup {self.product_id} {self.reason}>'
