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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('Category', back_populates='products')
    images = db.relationship('ProductImage', back_populates='product',
                             cascade='all, delete-orphan', order_by='ProductImage.sort_order')
    variants = db.relationship('ProductVariant', back_populates='product',
                               cascade='all, delete-orphan',
                               order_by='ProductVariant.sort_order, ProductVariant.id')
    order_items = db.relationship('OrderItem', back_populates='product')

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
    def is_on_sale(self):
        return bool(self.compare_price and self.compare_price > self.price)

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
        if self.is_on_sale:
            return int(((self.compare_price - self.price) / self.compare_price) * 100)
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


class Order(db.Model):
    __tablename__ = 'orders'

    STATUS_CHOICES = ['pending', 'processing', 'shipped', 'delivered', 'cancelled', 'refunded']

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
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='orders')
    items = db.relationship('OrderItem', back_populates='order', cascade='all, delete-orphan')
    shipping_address = db.relationship('ShippingAddress', back_populates='order',
                                       uselist=False, cascade='all, delete-orphan')

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


class SiteSettings(db.Model):
    __tablename__ = 'site_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)

    def __repr__(self):
        return f'<SiteSettings {self.key}>'
