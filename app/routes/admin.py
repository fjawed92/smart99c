import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, current_app)
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DecimalField, IntegerField,
                     BooleanField, SelectField, FileField, validators)
from app.extensions import db
from app.models import (Product, ProductImage, Category, Order, User,
                        ShippingRate, SiteSettings)
from app.helpers import generate_slug

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return login_required(decorated)


# ─── Dashboard ───────────────────────────────────────────────────────────────

@admin_bp.route('/')
@admin_required
def dashboard():
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    total_orders = Order.query.count()
    revenue_today = db.session.query(db.func.sum(Order.total))\
        .filter(db.func.date(Order.created_at) == today,
                Order.status.notin_(['cancelled', 'refunded'])).scalar() or 0
    revenue_month = db.session.query(db.func.sum(Order.total))\
        .filter(Order.created_at >= month_start,
                Order.status.notin_(['cancelled', 'refunded'])).scalar() or 0
    total_products = Product.query.filter_by(is_active=True).count()
    low_stock = Product.query.filter(
        Product.track_inventory == True,
        Product.stock_quantity <= 5,
        Product.is_active == True
    ).count()
    new_customers = User.query.filter(
        db.func.date(User.created_at) >= month_start,
        User.is_admin == False
    ).count()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()

    return render_template('admin/dashboard.html',
                           total_orders=total_orders,
                           revenue_today=revenue_today,
                           revenue_month=revenue_month,
                           total_products=total_products,
                           low_stock=low_stock,
                           new_customers=new_customers,
                           recent_orders=recent_orders)


# ─── Products ────────────────────────────────────────────────────────────────

class ProductForm(FlaskForm):
    name = StringField('Name', [validators.DataRequired(), validators.Length(max=255)])
    short_description = StringField('Short Description', [validators.Optional(), validators.Length(max=500)])
    description = TextAreaField('Description', [validators.Optional()])
    price = DecimalField('Price', [validators.DataRequired(), validators.NumberRange(min=0)], places=2)
    compare_price = DecimalField('Compare Price', [validators.Optional(), validators.NumberRange(min=0)], places=2)
    cost_price = DecimalField('Cost Price', [validators.Optional(), validators.NumberRange(min=0)], places=2)
    sku = StringField('SKU', [validators.Optional(), validators.Length(max=100)])
    stock_quantity = IntegerField('Stock', [validators.Optional(), validators.NumberRange(min=0)], default=0)
    track_inventory = BooleanField('Track Inventory', default=True)
    weight = DecimalField('Weight (lbs)', [validators.Optional(), validators.NumberRange(min=0)], places=2, default=0)
    category_id = SelectField('Category', coerce=int)
    is_active = BooleanField('Active', default=True)
    is_featured = BooleanField('Featured', default=False)


@admin_bp.route('/products')
@admin_required
def products():
    search = request.args.get('q', '')
    category_id = request.args.get('category_id', type=int)
    status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)

    query = Product.query
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    if category_id:
        query = query.filter_by(category_id=category_id)
    if status == 'active':
        query = query.filter_by(is_active=True)
    elif status == 'inactive':
        query = query.filter_by(is_active=False)

    pagination = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=20)
    categories = Category.query.order_by(Category.name).all()
    return render_template('admin/products.html',
                           pagination=pagination,
                           products=pagination.items,
                           categories=categories,
                           search=search,
                           category_id=category_id,
                           status=status)


@admin_bp.route('/products/new', methods=['GET', 'POST'])
@admin_required
def new_product():
    form = ProductForm()
    form.category_id.choices = [(0, '— No Category —')] + [
        (c.id, c.name) for c in Category.query.order_by(Category.name).all()
    ]
    if form.validate_on_submit():
        slug = generate_slug(form.name.data)
        existing = Product.query.filter_by(slug=slug).first()
        if existing:
            slug = f'{slug}-{int(datetime.utcnow().timestamp())}'

        product = Product(
            name=form.name.data,
            slug=slug,
            description=form.description.data,
            short_description=form.short_description.data,
            price=form.price.data,
            compare_price=form.compare_price.data or None,
            cost_price=form.cost_price.data or None,
            sku=form.sku.data or None,
            stock_quantity=form.stock_quantity.data or 0,
            track_inventory=form.track_inventory.data,
            weight=form.weight.data or 0,
            category_id=form.category_id.data or None,
            is_active=form.is_active.data,
            is_featured=form.is_featured.data,
        )
        db.session.add(product)
        db.session.flush()

        _handle_image_uploads(product, request.files.getlist('images'))

        db.session.commit()
        flash(f'Product "{product.name}" created!', 'success')
        return redirect(url_for('admin.products'))

    return render_template('admin/product_form.html', form=form, product=None)


@admin_bp.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    form.category_id.choices = [(0, '— No Category —')] + [
        (c.id, c.name) for c in Category.query.order_by(Category.name).all()
    ]

    if form.validate_on_submit():
        product.name = form.name.data
        product.description = form.description.data
        product.short_description = form.short_description.data
        product.price = form.price.data
        product.compare_price = form.compare_price.data or None
        product.cost_price = form.cost_price.data or None
        product.sku = form.sku.data or None
        product.stock_quantity = form.stock_quantity.data or 0
        product.track_inventory = form.track_inventory.data
        product.weight = form.weight.data or 0
        product.category_id = form.category_id.data or None
        product.is_active = form.is_active.data
        product.is_featured = form.is_featured.data

        _handle_image_uploads(product, request.files.getlist('images'))
        db.session.commit()
        flash(f'Product "{product.name}" updated!', 'success')
        return redirect(url_for('admin.products'))

    form.category_id.data = product.category_id or 0
    return render_template('admin/product_form.html', form=form, product=product)


@admin_bp.route('/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.is_active = False
    db.session.commit()
    flash(f'Product "{product.name}" deactivated.', 'success')
    return redirect(url_for('admin.products'))


@admin_bp.route('/products/<int:product_id>/toggle', methods=['POST'])
@admin_required
def toggle_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.is_active = not product.is_active
    db.session.commit()
    state = 'activated' if product.is_active else 'deactivated'
    return jsonify({'success': True, 'is_active': product.is_active,
                    'message': f'Product {state}.'})


@admin_bp.route('/products/<int:product_id>/images/<int:image_id>/delete', methods=['POST'])
@admin_required
def delete_product_image(product_id, image_id):
    image = ProductImage.query.filter_by(id=image_id, product_id=product_id).first_or_404()
    if image.cloudinary_public_id:
        try:
            cloudinary.uploader.destroy(image.cloudinary_public_id)
        except Exception:
            pass
    db.session.delete(image)
    db.session.commit()
    return jsonify({'success': True})


def _handle_image_uploads(product, files):
    is_first = not product.images
    for f in files:
        if f and f.filename:
            try:
                result = cloudinary.uploader.upload(
                    f,
                    folder='smart99c/products',
                    transformation=[{'width': 800, 'height': 800, 'crop': 'limit', 'quality': 'auto'}]
                )
                img = ProductImage(
                    product_id=product.id,
                    image_url=result['secure_url'],
                    cloudinary_public_id=result['public_id'],
                    is_primary=is_first,
                    sort_order=len(product.images),
                )
                db.session.add(img)
                is_first = False
            except Exception as e:
                flash(f'Image upload failed: {str(e)}', 'warning')


# ─── Categories ──────────────────────────────────────────────────────────────

class CategoryForm(FlaskForm):
    name = StringField('Name', [validators.DataRequired(), validators.Length(max=100)])
    description = TextAreaField('Description', [validators.Optional()])
    is_active = BooleanField('Active', default=True)
    sort_order = IntegerField('Sort Order', [validators.Optional()], default=0)


@admin_bp.route('/categories')
@admin_required
def categories():
    cats = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template('admin/categories.html', categories=cats)


@admin_bp.route('/categories/new', methods=['GET', 'POST'])
@admin_required
def new_category():
    form = CategoryForm()
    if form.validate_on_submit():
        slug = generate_slug(form.name.data)
        cat = Category(
            name=form.name.data,
            slug=slug,
            description=form.description.data,
            is_active=form.is_active.data,
            sort_order=form.sort_order.data or 0,
        )
        if 'image' in request.files and request.files['image'].filename:
            try:
                result = cloudinary.uploader.upload(
                    request.files['image'],
                    folder='smart99c/categories',
                    transformation=[{'width': 600, 'height': 400, 'crop': 'fill', 'quality': 'auto'}]
                )
                cat.image_url = result['secure_url']
                cat.cloudinary_public_id = result['public_id']
            except Exception as e:
                flash(f'Image upload failed: {str(e)}', 'warning')
        db.session.add(cat)
        db.session.commit()
        flash(f'Category "{cat.name}" created!', 'success')
        return redirect(url_for('admin.categories'))
    return render_template('admin/category_form.html', form=form, category=None)


@admin_bp.route('/categories/<int:cat_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    form = CategoryForm(obj=cat)
    if form.validate_on_submit():
        cat.name = form.name.data
        cat.description = form.description.data
        cat.is_active = form.is_active.data
        cat.sort_order = form.sort_order.data or 0
        if 'image' in request.files and request.files['image'].filename:
            try:
                result = cloudinary.uploader.upload(
                    request.files['image'],
                    folder='smart99c/categories',
                    transformation=[{'width': 600, 'height': 400, 'crop': 'fill', 'quality': 'auto'}]
                )
                cat.image_url = result['secure_url']
                cat.cloudinary_public_id = result['public_id']
            except Exception as e:
                flash(f'Image upload failed: {str(e)}', 'warning')
        db.session.commit()
        flash(f'Category "{cat.name}" updated!', 'success')
        return redirect(url_for('admin.categories'))
    return render_template('admin/category_form.html', form=form, category=cat)


# ─── Orders ──────────────────────────────────────────────────────────────────

@admin_bp.route('/orders')
@admin_required
def orders():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '')
    search = request.args.get('q', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Order.query
    if status:
        query = query.filter_by(status=status)
    if search:
        query = query.join(ShippingAddress, isouter=True).filter(
            db.or_(
                Order.order_number.ilike(f'%{search}%'),
                ShippingAddress.email.ilike(f'%{search}%'),
                ShippingAddress.last_name.ilike(f'%{search}%'),
            )
        )
    if date_from:
        query = query.filter(Order.created_at >= date_from)
    if date_to:
        query = query.filter(Order.created_at <= date_to + ' 23:59:59')

    from app.models import ShippingAddress
    pagination = query.order_by(Order.created_at.desc()).paginate(page=page, per_page=25)
    return render_template('admin/orders.html',
                           pagination=pagination,
                           orders=pagination.items,
                           status=status,
                           search=search)


@admin_bp.route('/orders/<int:order_id>')
@admin_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template('admin/order_detail.html', order=order,
                           status_choices=Order.STATUS_CHOICES)


@admin_bp.route('/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status not in Order.STATUS_CHOICES:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin.order_detail', order_id=order_id))

    if new_status == 'cancelled' and order.status not in ['cancelled', 'refunded']:
        for item in order.items:
            if item.product and item.product.track_inventory:
                item.product.stock_quantity += item.quantity

    order.status = new_status
    db.session.commit()
    flash(f'Order {order.order_number} status updated to {new_status}.', 'success')
    return redirect(url_for('admin.order_detail', order_id=order_id))


# ─── Users ───────────────────────────────────────────────────────────────────

@admin_bp.route('/users')
@admin_required
def users():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '')
    query = User.query
    if search:
        query = query.filter(
            db.or_(
                User.email.ilike(f'%{search}%'),
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
            )
        )
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=25)
    return render_template('admin/users.html',
                           pagination=pagination,
                           users=pagination.items,
                           search=search)


@admin_bp.route('/users/<int:user_id>')
@admin_required
def user_detail(user_id):
    user = User.query.get_or_404(user_id)
    orders = Order.query.filter_by(user_id=user_id).order_by(Order.created_at.desc()).all()
    return render_template('admin/user_detail.html', user=user, orders=orders)


@admin_bp.route('/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot change your own admin status'}), 400
    user.is_admin = not user.is_admin
    db.session.commit()
    return jsonify({'success': True, 'is_admin': user.is_admin})


@admin_bp.route('/users/<int:user_id>/toggle-active', methods=['POST'])
@admin_required
def toggle_user_active(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot deactivate yourself'}), 400
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'success': True, 'is_active': user.is_active})


# ─── Shipping ─────────────────────────────────────────────────────────────────

class ShippingRateForm(FlaskForm):
    name = StringField('Name', [validators.DataRequired(), validators.Length(max=100)])
    price = DecimalField('Price', [validators.DataRequired(), validators.NumberRange(min=0)], places=2)
    min_order_amount = DecimalField('Free Shipping Threshold', [validators.Optional(), validators.NumberRange(min=0)], places=2, default=0)
    estimated_days = StringField('Estimated Days', [validators.Optional(), validators.Length(max=50)])
    is_active = BooleanField('Active', default=True)
    sort_order = IntegerField('Sort Order', [validators.Optional()], default=0)


@admin_bp.route('/shipping')
@admin_required
def shipping():
    rates = ShippingRate.query.order_by(ShippingRate.sort_order, ShippingRate.price).all()
    form = ShippingRateForm()
    return render_template('admin/shipping.html', rates=rates, form=form)


@admin_bp.route('/shipping/new', methods=['POST'])
@admin_required
def new_shipping_rate():
    form = ShippingRateForm()
    if form.validate_on_submit():
        rate = ShippingRate(
            name=form.name.data,
            price=form.price.data,
            min_order_amount=form.min_order_amount.data or 0,
            estimated_days=form.estimated_days.data,
            is_active=form.is_active.data,
            sort_order=form.sort_order.data or 0,
        )
        db.session.add(rate)
        db.session.commit()
        flash(f'Shipping rate "{rate.name}" added!', 'success')
    return redirect(url_for('admin.shipping'))


@admin_bp.route('/shipping/<int:rate_id>/delete', methods=['POST'])
@admin_required
def delete_shipping_rate(rate_id):
    rate = ShippingRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    flash('Shipping rate deleted.', 'success')
    return redirect(url_for('admin.shipping'))


# ─── Settings ────────────────────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        keys = ['announcement', 'store_hours', 'tax_rate', 'free_shipping_threshold']
        for key in keys:
            value = request.form.get(key, '')
            setting = SiteSettings.query.filter_by(key=key).first()
            if setting:
                setting.value = value
            else:
                db.session.add(SiteSettings(key=key, value=value))
        db.session.commit()
        flash('Settings saved!', 'success')
        return redirect(url_for('admin.settings'))

    settings_dict = {s.key: s.value for s in SiteSettings.query.all()}
    return render_template('admin/settings.html', settings=settings_dict)
