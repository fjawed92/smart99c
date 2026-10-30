import cloudinary
import cloudinary.uploader
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, abort, current_app, send_file)
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DecimalField, IntegerField,
                     BooleanField, SelectField, FileField, PasswordField, validators)
from app.extensions import db
from app.models import (Product, ProductImage, ProductVariant, Category, Order,
                        OrderItem, User, ShippingRate, SiteSettings, PaymentLink,
                        ShippingAddress)
from app.helpers import (generate_slug, get_site_setting, set_site_setting,
                         encrypt_secret)
from app.timezones import (local_today, day_start_utc, day_end_utc, parse_day,
                           format_local, tz_label, zone_choices, valid_zone, forget_tz)
from app.services import izyops_mode

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return login_required(decorated)


# Pages that change product details. When IZY Ops manages the products
# (app/services/izyops_mode.py), saving any of them is refused (app/__init__.py).
CATALOG_ENDPOINTS = {
    'admin.new_product', 'admin.edit_product', 'admin.delete_product', 'admin.toggle_product',
    'admin.bulk_product_action', 'admin.delete_product_image', 'admin.create_variant',
    'admin.update_variant', 'admin.delete_variant', 'admin.products_import_upload',
    'admin.products_import_confirm', 'admin.izyops_add',
}


# ─── Dashboard ───────────────────────────────────────────────────────────────


@admin_bp.route('/')
@admin_required
def dashboard():
    # "Today" and "this month" are store-calendar days (Admin → Settings time zone).
    today = local_today()
    today_start, month_start = day_start_utc(today), day_start_utc(today.replace(day=1))

    total_orders = Order.visible().count()
    revenue_today = db.session.query(db.func.sum(Order.total))\
        .filter(Order.created_at >= today_start, Order.deleted_at.is_(None),
                Order.status.notin_(['cancelled', 'refunded'])).scalar() or 0
    revenue_month = db.session.query(db.func.sum(Order.total))\
        .filter(Order.created_at >= month_start, Order.deleted_at.is_(None),
                Order.status.notin_(['cancelled', 'refunded'])).scalar() or 0
    total_products = Product.query.filter_by(is_active=True).count()
    low_stock = Product.query.filter(
        Product.track_inventory == True,
        Product.stock_quantity <= 5,
        Product.is_active == True
    ).count()
    new_customers = User.query.filter(
        User.created_at >= month_start,
        User.is_admin == False
    ).count()
    recent_orders = Order.visible().order_by(Order.created_at.desc()).limit(10).all()

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

    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (20, 50, 100, 500):
        per_page = 20
    pagination = query.order_by(*Product.store_order()).paginate(page=page, per_page=per_page)
    categories = Category.query.order_by(Category.name).all()
    return render_template('admin/products.html',
                           pagination=pagination,
                           products=pagination.items,
                           categories=categories,
                           search=search,
                           category_id=category_id,
                           status=status,
                           per_page=per_page,
                           catalog_mode=izyops_mode.catalog_mode(),
                           izyops_connected=bool(get_site_setting(izyops_mode.SETTING_KEY, '')))


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


@admin_bp.route('/products/bulk-action', methods=['POST'])
@admin_required
def bulk_product_action(action=None):
    action = request.form.get('action', '')
    ids = request.form.getlist('product_ids')
    try:
        ids = [int(i) for i in ids if str(i).isdigit()]
    except ValueError:
        ids = []
    if not ids:
        flash('Select at least one product.', 'error')
        return redirect(request.referrer or url_for('admin.products'))

    products = Product.query.filter(Product.id.in_(ids)).all()
    if action == 'activate':
        for p in products:
            p.is_active = True
        db.session.commit()
        flash(f'Activated {len(products)} product(s).', 'success')
    elif action == 'deactivate':
        for p in products:
            p.is_active = False
        db.session.commit()
        flash(f'Deactivated {len(products)} product(s).', 'success')
    elif action == 'feature':
        for p in products:
            p.is_featured = True
        db.session.commit()
        flash(f'Featured {len(products)} product(s).', 'success')
    elif action == 'unfeature':
        for p in products:
            p.is_featured = False
        db.session.commit()
        flash(f'Unfeatured {len(products)} product(s).', 'success')
    elif action == 'delete':
        # Soft delete: same as the per-row Delete button (deactivate).
        for p in products:
            p.is_active = False
        db.session.commit()
        flash(f'Deactivated {len(products)} product(s).', 'success')
    else:
        flash('Unknown action.', 'error')

    return redirect(request.referrer or url_for('admin.products'))


# Store order is website-only, so it stays editable when IZY Ops runs the catalog.
def _renumber(ordered_ids):
    """Save the whole store order as 1, 2, 3… from a full list of product ids."""
    current = {pid: so for pid, so in db.session.query(Product.id, Product.sort_order)}
    for position, pid in enumerate(ordered_ids, start=1):
        if current.get(pid) != position:
            Product.query.filter_by(id=pid).update({'sort_order': position},
                                                   synchronize_session=False)


def _store_order_ids():
    return [pid for (pid,) in db.session.query(Product.id).order_by(*Product.store_order())]


@admin_bp.route('/products/reorder', methods=['POST'])
@admin_required
def reorder_products():
    """Drag and drop on the Products page: the rows on screen, in their new order.

    Only those products move, and only among the spots they already held,
    so products on other pages (or hidden by a filter) keep their place.
    """
    data = request.get_json(silent=True) or {}
    try:
        moved = [int(i) for i in data.get('ids', [])]
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Bad product list.'}), 400
    order = _store_order_ids()
    known = set(order)
    moved = [pid for pid in dict.fromkeys(moved) if pid in known]
    if not moved:
        return jsonify({'success': False, 'message': 'No products to move.'}), 400
    moved_set = set(moved)
    slots = iter(moved)
    order = [next(slots) if pid in moved_set else pid for pid in order]
    _renumber(order)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/products/move', methods=['POST'])
@admin_required
def move_products():
    """Send the ticked products to the very top or bottom of the store order."""
    where = request.form.get('where')
    ids = [int(i) for i in request.form.getlist('product_ids') if str(i).isdigit()]
    if not ids or where not in ('top', 'bottom'):
        flash('Select at least one product.', 'error')
        return redirect(request.referrer or url_for('admin.products'))
    order, ids = _store_order_ids(), set(ids)
    picked = [pid for pid in order if pid in ids]
    rest = [pid for pid in order if pid not in ids]
    _renumber(picked + rest if where == 'top' else rest + picked)
    db.session.commit()
    flash(f'Moved {len(picked)} product(s) to the {where} of the shop.', 'success')
    return redirect(request.referrer or url_for('admin.products'))


@admin_bp.route('/orders/export', methods=['GET'])
@admin_required
def orders_export():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO
    from app.models import ShippingAddress

    wb = Workbook()
    ws = wb.active
    ws.title = 'Orders'

    zone = tz_label()
    header = ['Order #', f'Date ({zone})', 'Status', 'Customer', 'Email', 'Phone',
              'Items', 'Pickup / Delivery', 'Subtotal', 'Promo Code', 'Promo Discount', 'Cash Discount',
              'Tax', 'Shipping', 'Service Fee', 'Total', 'Payment Intent',
              'Ready', 'Picked up', 'Shipped', 'Delivered', 'No-show', 'Cancelled', 'Refunded']
    for i, h in enumerate(header, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='E8334A')
        c.alignment = Alignment(horizontal='left', vertical='center')

    widths = [14, 18, 12, 24, 28, 16, 50, 30, 10, 12, 12, 12, 10, 11, 12, 10, 32] + [17] * 7
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    orders = Order.visible().order_by(Order.created_at.desc()).all()
    for o in orders:
        items_str = '; '.join(
            f'{i.display_name} x{i.quantity} @ ${i.unit_price}' for i in o.items
        )
        addr = o.shipping_address
        ws.append([
            o.order_number,
            format_local(o.created_at, '%Y-%m-%d %H:%M'),
            o.status,
            addr.full_name if addr else (o.user.full_name if o.user else ''),
            addr.email if addr else (o.user.email if o.user else ''),
            addr.phone if addr else '',
            items_str,
            o.fulfillment_label,
            float(o.subtotal or 0),
            o.promo_code or '',
            float(o.promo_discount or 0),
            float(o.cash_discount or 0),
            float(o.tax or 0),
            float(o.shipping_cost or 0),
            float(o.service_fee or 0),
            float(o.total or 0),
            o.stripe_payment_intent_id or '',
            *(format_local(getattr(o, col), '%Y-%m-%d %H:%M')
              for col in ('ready_at', 'picked_up_at', 'shipped_at', 'delivered_at',
                          'no_show_at', 'cancelled_at', 'refunded_at')),
        ])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = local_today().strftime('%Y%m%d')
    return send_file(buf,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True,
                     download_name=f'smart99c-orders-{stamp}.xlsx')


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


def _handle_image_uploads(product, files, variant=None):
    """Upload Cloudinary images and attach them to a product (and optionally a variant)."""
    scope_images = variant.images if variant is not None else [
        i for i in product.images if i.variant_id is None
    ]
    is_first = not scope_images
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
                    variant_id=variant.id if variant else None,
                    image_url=result['secure_url'],
                    cloudinary_public_id=result['public_id'],
                    is_primary=is_first if variant is None else False,
                    sort_order=len(product.images),
                )
                db.session.add(img)
                is_first = False
            except Exception as e:
                flash(f'Image upload failed: {str(e)}', 'warning')


# ─── Product Variants ────────────────────────────────────────────────────────

def _parse_decimal(value):
    if value in (None, '', 'None'):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def _parse_int(value, default=0):
    if value in (None, '', 'None'):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@admin_bp.route('/products/<int:product_id>/variants', methods=['POST'])
@admin_required
def create_variant(product_id):
    product = Product.query.get_or_404(product_id)
    color_name = (request.form.get('color_name') or '').strip()
    if not color_name:
        flash('Color name is required.', 'error')
        return redirect(url_for('admin.edit_product', product_id=product_id))

    color_hex = (request.form.get('color_hex') or '').strip() or None
    sku = (request.form.get('sku') or '').strip() or None
    if sku and ProductVariant.query.filter_by(sku=sku).first():
        flash(f'SKU "{sku}" is already in use.', 'error')
        return redirect(url_for('admin.edit_product', product_id=product_id))

    variant = ProductVariant(
        product_id=product.id,
        color_name=color_name,
        color_hex=color_hex,
        sku=sku,
        price_override=_parse_decimal(request.form.get('price_override')),
        cost_price=_parse_decimal(request.form.get('cost_price')),
        stock_quantity=max(0, _parse_int(request.form.get('stock_quantity'), 0)),
        is_active=request.form.get('is_active') == 'on',
        sort_order=_parse_int(request.form.get('sort_order'), 0),
    )
    db.session.add(variant)
    db.session.flush()

    files = request.files.getlist('variant_images')
    _handle_image_uploads(product, files, variant=variant)

    db.session.commit()
    flash(f'Color "{variant.color_name}" added.', 'success')
    return redirect(url_for('admin.edit_product', product_id=product_id))


@admin_bp.route('/products/<int:product_id>/variants/<int:variant_id>', methods=['POST'])
@admin_required
def update_variant(product_id, variant_id):
    variant = ProductVariant.query.filter_by(id=variant_id, product_id=product_id).first_or_404()
    product = variant.product

    color_name = (request.form.get('color_name') or '').strip()
    if not color_name:
        flash('Color name is required.', 'error')
        return redirect(url_for('admin.edit_product', product_id=product_id))

    sku = (request.form.get('sku') or '').strip() or None
    if sku and sku != variant.sku:
        clash = ProductVariant.query.filter(ProductVariant.sku == sku,
                                            ProductVariant.id != variant.id).first()
        if clash:
            flash(f'SKU "{sku}" is already in use.', 'error')
            return redirect(url_for('admin.edit_product', product_id=product_id))

    variant.color_name = color_name
    variant.color_hex = (request.form.get('color_hex') or '').strip() or None
    variant.sku = sku
    variant.price_override = _parse_decimal(request.form.get('price_override'))
    variant.cost_price = _parse_decimal(request.form.get('cost_price'))
    variant.stock_quantity = max(0, _parse_int(request.form.get('stock_quantity'), 0))
    variant.is_active = request.form.get('is_active') == 'on'
    variant.sort_order = _parse_int(request.form.get('sort_order'), 0)

    files = request.files.getlist('variant_images')
    if files:
        _handle_image_uploads(product, files, variant=variant)

    db.session.commit()
    flash(f'Color "{variant.color_name}" updated.', 'success')
    return redirect(url_for('admin.edit_product', product_id=product_id))


# ─── Bulk Import / Export (Excel) ────────────────────────────────────────────

@admin_bp.route('/products/import', methods=['GET'])
@admin_required
def products_import():
    return render_template('admin/products_import.html')


@admin_bp.route('/products/import/template', methods=['GET'])
@admin_required
def products_import_template():
    from app.services.product_export import build_template_workbook, workbook_to_bytes
    wb = build_template_workbook()
    buf = workbook_to_bytes(wb)
    return send_file(buf,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True,
                     download_name='smart99c-products-template.xlsx')


@admin_bp.route('/products/export', methods=['GET'])
@admin_required
def products_export():
    from app.services.product_export import build_catalog_workbook, workbook_to_bytes
    wb = build_catalog_workbook()
    buf = workbook_to_bytes(wb)
    stamp = local_today().strftime('%Y%m%d')
    return send_file(buf,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True,
                     download_name=f'smart99c-products-{stamp}.xlsx')


@admin_bp.route('/products/backups', methods=['GET'])
@admin_required
def product_backups():
    """Copies of products saved before IZY Ops changed them, and the full
    copy made the first time IZY Ops connected."""
    from app.models import ProductBackup
    from app.services.backups import FIRST_BATCH, data_of

    search = (request.args.get('q') or '').strip()
    page = request.args.get('page', 1, type=int)
    q = ProductBackup.query.filter(ProductBackup.product_id.isnot(None))
    if search:
        like = f'%{search}%'
        q = q.filter(db.or_(ProductBackup.name.ilike(like), ProductBackup.sku.ilike(like)))
    pagination = q.order_by(ProductBackup.taken_at.desc(), ProductBackup.id.desc()).paginate(page=page, per_page=30)
    first = ProductBackup.query.filter_by(batch=FIRST_BATCH).order_by(ProductBackup.taken_at).first()
    first_count = ProductBackup.query.filter(ProductBackup.batch == FIRST_BATCH,
                                             ProductBackup.product_id.isnot(None)).count()
    return render_template('admin/product_backups.html', pagination=pagination, backups=pagination.items,
                           search=search, first=first, first_count=first_count, data_of=data_of,
                           locked=izyops_mode.locked())


@admin_bp.route('/products/backups/<int:backup_id>/restore', methods=['POST'])
@admin_required
def restore_product_backup(backup_id):
    from app.models import ProductBackup
    from app.services import backups

    backup = ProductBackup.query.get_or_404(backup_id)
    if izyops_mode.locked():
        flash('IZY Ops manages products right now, so it would undo a restore here. Make the change in IZY Ops, '
              'or choose "Both" or "Website only" in IZY Ops → Settings → Websites → Catalog first.', 'warning')
        return redirect(url_for('admin.product_backups'))
    product = backups.restore(backup)
    if product is None:
        flash('That product no longer exists. Download the backup and add it back under Bulk Import.', 'error')
    else:
        flash(f'"{product.name}" is back the way it was on {backup.taken_at:%b %d, %Y %H:%M} UTC. '
              'Its values from just before the restore were saved as a backup too.', 'success')
    return redirect(url_for('admin.product_backups'))


@admin_bp.route('/products/backups/download', methods=['GET'])
@admin_required
def download_product_backups():
    """Excel of the first full backup (?which=first, default) or of one backup."""
    from app.models import ProductBackup
    from app.services.backups import first_backup_rows
    from app.services.product_export import build_backup_workbook, workbook_to_bytes

    backup_id = request.args.get('id', type=int)
    rows = [ProductBackup.query.get_or_404(backup_id)] if backup_id else first_backup_rows()
    buf = workbook_to_bytes(build_backup_workbook(rows))
    name = f'smart99c-backup-{backup_id}.xlsx' if backup_id else 'smart99c-products-before-izyops.xlsx'
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=name)


@admin_bp.route('/products/import', methods=['POST'])
@admin_required
def products_import_upload():
    """Step 1: read the file and show an editable preview. Nothing is saved yet."""
    from app.services.product_import import parse_workbook, preview_rows

    file = request.files.get('file')
    if not file or not file.filename:
        flash('Please choose a file to upload.', 'error')
        return redirect(url_for('admin.products_import'))

    if not file.filename.lower().endswith('.xlsx'):
        flash('Only .xlsx files are supported. In Excel use File → Save As → Excel Workbook (.xlsx).', 'error')
        return redirect(url_for('admin.products_import'))

    rows, errors = parse_workbook(file.read())
    if not rows:
        for _, msg in errors or [(0, 'No product rows found in that file.')]:
            flash(msg, 'error')
        return redirect(url_for('admin.products_import'))

    categories = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template('admin/products_import_preview.html',
                           rows=preview_rows(rows), file_errors=errors,
                           filename=file.filename, categories=categories)


@admin_bp.route('/products/import/confirm', methods=['POST'])
@admin_required
def products_import_confirm():
    """Step 2: save the (possibly edited) preview rows."""
    import json
    from app.services.product_import import apply_rows, ALL_COLUMNS

    try:
        raw = json.loads(request.form.get('rows_json') or '[]')
    except ValueError:
        raw = None
    if not isinstance(raw, list):
        flash('The preview could not be read. Please upload the file again.', 'error')
        return redirect(url_for('admin.products_import'))

    rows = []
    for item in raw:
        if not isinstance(item, dict) or item.get('_skip'):
            continue
        row = {'_row': item.get('_row')}
        for col in ALL_COLUMNS:
            val = item.get(col)
            row[col] = None if val is None or str(val).strip() == '' else str(val).strip()
        rows.append(row)

    result = apply_rows(rows)
    return render_template('admin/products_import_result.html', result=result)


# ─── Pull products from IzyOps ───────────────────────────────────────────────

@admin_bp.route('/izyops', methods=['GET'])
@admin_required
def izyops_pull():
    """Search the IzyOps catalog and preview results before adding them."""
    from app.services import izyops_client
    from app.services.izyops_import import on_site_map

    mode = request.args.get('mode', 'keyword')
    q = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    connected = izyops_client.is_configured()

    facets, facets_error = ({}, None)
    if connected:
        facets, facets_error = izyops_client.get_facets()
        facets = facets or {}

    items, meta, error = [], {}, None
    searched = 'q' in request.args
    if connected and searched:
        items, meta, error = izyops_client.search_products(mode, q, page)
        on_site = on_site_map(items)
        for it in items:
            site = on_site.get(it['product_id'])
            it['site_product'] = {'id': site.id, 'name': site.name, 'price': float(site.price),
                                  'category': site.category.name if site.category else ''} if site else None
            it['tidy_name'] = site.name if site else izyops_client.tidy_name(it.get('product_name'))

    categories = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template('admin/izyops_pull.html',
                           connected=connected, store=izyops_client.settings()['store'],
                           facets=facets, facets_error=facets_error,
                           mode=mode, q=q, page=page, items=items, meta=meta, error=error,
                           searched=searched, categories=categories)


@admin_bp.route('/izyops/add', methods=['POST'])
@admin_required
def izyops_add():
    import json
    from app.services.izyops_import import save_items

    try:
        items = json.loads(request.form.get('items_json') or '[]')
    except ValueError:
        items = None
    back = request.form.get('back') or url_for('admin.izyops_pull')
    if not back.startswith('/admin/izyops') or '\\' in back:
        back = url_for('admin.izyops_pull')
    if not isinstance(items, list) or not items:
        flash('Select at least one product to add.', 'warning')
        return redirect(back)

    result = save_items([i for i in items if isinstance(i, dict)])
    parts = []
    if result['created']:
        parts.append(f"{result['created']} added")
    if result['updated']:
        parts.append(f"{result['updated']} updated")
    if parts:
        flash('Products ' + ' and '.join(parts) + ' on Smart99c.com.', 'success')
    for msg in result['errors'][:10]:
        flash(msg, 'error')
    return redirect(back)


@admin_bp.route('/products/<int:product_id>/variants/<int:variant_id>/delete', methods=['POST'])
@admin_required
def delete_variant(product_id, variant_id):
    variant = ProductVariant.query.filter_by(id=variant_id, product_id=product_id).first_or_404()

    referenced = OrderItem.query.filter_by(variant_id=variant.id).first() is not None
    if referenced:
        variant.is_active = False
        db.session.commit()
        flash(f'Color "{variant.color_name}" has existing orders — deactivated instead of deleted.',
              'warning')
    else:
        for img in list(variant.images):
            if img.cloudinary_public_id:
                try:
                    cloudinary.uploader.destroy(img.cloudinary_public_id)
                except Exception:
                    pass
            db.session.delete(img)
        db.session.delete(variant)
        db.session.commit()
        flash('Color deleted.', 'success')
    return redirect(url_for('admin.edit_product', product_id=product_id))


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

ORDER_TABS = [
    ('pickup', 'Waiting for pickup'),
    ('late', 'Past deadline'),
    ('delivery', 'Delivery'),
    ('done', 'Completed'),
    ('noshow', 'No-shows'),
    ('all', 'All orders'),
]


def _tab_filter(query, tab, now):
    is_pickup = Order.shipping_method.ilike('%pickup%')
    overdue = db.and_(Order.payment_method == 'cash', Order.pickup_deadline.isnot(None),
                      Order.pickup_deadline < now)
    open_ = Order.status.in_(Order.OPEN_STATUSES)
    if tab == 'pickup':
        return query.filter(is_pickup, open_, db.not_(overdue))
    if tab == 'late':
        return query.filter(open_, overdue)
    if tab == 'delivery':
        return query.filter(db.or_(Order.shipping_method.is_(None), db.not_(is_pickup)),
                            Order.status.in_(('pending', 'processing', 'shipped')))
    if tab == 'done':
        return query.filter(Order.status.in_(('picked_up', 'delivered')))
    if tab == 'noshow':
        return query.filter(Order.status == 'no_show')
    return query


@admin_bp.route('/orders')
@admin_required
def orders():
    from app.models import ShippingAddress
    from app.services.pickup import process_holds, to_local, to_utc
    try:
        process_holds()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Pickup hold check failed')

    page = request.args.get('page', 1, type=int)
    tab = request.args.get('tab', 'pickup')
    if tab not in dict(ORDER_TABS):
        tab = 'pickup'
    status = request.args.get('status', '')
    search = request.args.get('q', '').strip()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    now = datetime.utcnow()

    query = Order.visible()
    if search:
        digits = ''.join(c for c in search if c.isdigit())
        conds = [Order.order_number.ilike(f'%{search}%'),
                 ShippingAddress.email.ilike(f'%{search}%'),
                 ShippingAddress.first_name.ilike(f'%{search}%'),
                 ShippingAddress.last_name.ilike(f'%{search}%')]
        if len(digits) >= 4:
            conds.append(ShippingAddress.phone.ilike(f'%{digits[-4:]}%'))
        query = query.join(ShippingAddress, isouter=True).filter(db.or_(*conds))
        tab = 'all' if 'tab' not in request.args else tab
    day_from, day_to = parse_day(date_from), parse_day(date_to)
    if day_from:
        query = query.filter(Order.created_at >= day_start_utc(day_from))
    if day_to:
        query = query.filter(Order.created_at < day_end_utc(day_to))
    if status and tab == 'all':
        query = query.filter_by(status=status)

    tab_counts = {key: _tab_filter(Order.visible(), key, now).count() for key, _ in ORDER_TABS}
    pagination = _tab_filter(query, tab, now).order_by(Order.created_at.desc())\
        .paginate(page=page, per_page=25)

    # Summary boxes
    open_cash = Order.visible().filter(Order.payment_method == 'cash',
                                       Order.status.in_(Order.OPEN_STATUSES)).all()
    today_start = to_utc(to_local(now).replace(hour=0, minute=0, second=0, microsecond=0))
    paid_today = Order.visible().filter(Order.payment_method == 'card',
                                        Order.created_at >= today_start,
                                        Order.status.notin_(('pending', 'cancelled', 'refunded'))).all()
    summary = {
        'cash_due': sum((o.total for o in open_cash), 0),
        'cash_count': len(open_cash),
        'to_pack': Order.query.filter(Order.status == 'processing').count(),
        'late': tab_counts['late'],
        'paid_today': sum((o.total for o in paid_today), 0),
        'paid_today_count': len(paid_today),
    }
    return render_template('admin/orders.html',
                           pagination=pagination,
                           orders=pagination.items,
                           tabs=ORDER_TABS,
                           tab=tab,
                           tab_counts=tab_counts,
                           summary=summary,
                           status=status,
                           search=search)


@admin_bp.route('/orders/<int:order_id>')
@admin_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    livemode = order.stripe_livemode
    if livemode is None:
        # Older orders didn't save this; go by the key the site is using now.
        livemode = not (current_app.config.get('STRIPE_SECRET_KEY') or '').startswith('sk_test')
    stripe_url = None
    if order.stripe_payment_intent_id:
        stripe_url = ('https://dashboard.stripe.com/' + ('' if livemode else 'test/')
                      + 'payments/' + order.stripe_payment_intent_id)
    from app.services.pickup import no_show_count, to_local
    addr = order.shipping_address
    past_orders = 0
    if addr and addr.email:
        past_orders = Order.visible().join(ShippingAddress)\
            .filter(db.func.lower(ShippingAddress.email) == addr.email.lower(),
                    Order.id != order.id).count()
    hold_pct = 0
    if order.is_cash and order.pickup_deadline and order.created_at:
        span = (order.pickup_deadline - order.created_at).total_seconds() or 1
        left = (order.pickup_deadline - datetime.utcnow()).total_seconds()
        hold_pct = max(0, min(100, round(100 * left / span)))
    return render_template('admin/order_detail.html', order=order,
                           status_choices=Order.STATUS_CHOICES,
                           stripe_livemode=livemode, stripe_url=stripe_url,
                           past_orders=past_orders,
                           no_shows=no_show_count(addr.email, addr.phone) if addr else 0,
                           hold_pct=hold_pct,
                           today=to_local(datetime.utcnow()).date().isoformat())


@admin_bp.route('/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    if new_status not in Order.STATUS_CHOICES:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin.order_detail', order_id=order_id))
    if order.is_deleted:
        flash(f'Order {order.order_number} was deleted and can\'t be changed.', 'warning')
        return redirect(url_for('admin.order_detail', order_id=order_id))

    if new_status in ('cancelled', 'no_show') and order.status not in ('cancelled', 'refunded', 'no_show'):
        from app.services.pickup import restock
        restock(order)

    order.set_status(new_status)
    db.session.commit()
    flash(f'Order {order.order_number} status updated to {new_status}.', 'success')
    return redirect(url_for('admin.order_detail', order_id=order_id))


@admin_bp.route('/orders/<int:order_id>/pickup', methods=['POST'])
@admin_required
def order_pickup_action(order_id):
    """One-tap actions from the orders list and order page."""
    from app.services.pickup import restock, email_customer, to_utc
    from app.timezones import store_tz
    order = Order.query.get_or_404(order_id)
    action = request.form.get('action', '')
    back = request.form.get('next') or url_for('admin.order_detail', order_id=order.id)
    if not back.startswith('/'):
        back = url_for('admin.order_detail', order_id=order.id)
    now = datetime.utcnow()
    closed = ('picked_up', 'delivered', 'no_show', 'cancelled', 'refunded')
    msg = None

    if order.is_deleted and action != 'note':
        flash(f'Order {order.order_number} was deleted and can\'t be changed.', 'warning')
        return redirect(back)
    if action == 'note':
        order.notes = (request.form.get('notes') or '').strip()
        msg = 'Note saved.'
    elif action in ('ready', 'picked_up', 'delivered', 'no_show', 'cancel',
                    'extend_12', 'extend_24', 'extend_date') and order.status in closed:
        flash(f'Order {order.order_number} is already {order.status_label.lower()}.', 'warning')
        return redirect(back)
    elif action == 'ready':
        order.set_status('ready', now)
        db.session.commit()
        sent = email_customer(order, 'ready')
        flash(f'{order.order_number} marked ready.'
              + (' Customer emailed.' if sent else ' (Email not sent — check Admin → Email.)'),
              'success')
        return redirect(back)
    elif action == 'picked_up':
        order.set_status('picked_up', now)
        msg = f'{order.order_number} picked up' + (' and paid in cash.' if order.is_cash else '.')
    elif action == 'delivered':
        order.set_status('delivered', now)
        msg = f'{order.order_number} marked delivered.'
    elif action in ('no_show', 'cancel'):
        restock(order)
        order.set_status('no_show' if action == 'no_show' else 'cancelled', now)
        msg = (f'{order.order_number} marked as no-show. Items are back in stock.'
               if action == 'no_show' else f'{order.order_number} cancelled. Items are back in stock.')
    elif action in ('extend_12', 'extend_24', 'extend_date'):
        base = max(order.pickup_deadline or now, now)
        if action == 'extend_date':
            try:
                picked = datetime.strptime(request.form.get('date', ''), '%Y-%m-%d')
            except ValueError:
                flash('Please pick a date.', 'error')
                return redirect(back)
            from app.store import STORE
            hours = STORE['open_hours'].get(picked.weekday()) or (9, 21)
            new_deadline = to_utc(picked.replace(hour=hours[1], tzinfo=store_tz()))
        else:
            new_deadline = base + timedelta(hours=12 if action == 'extend_12' else 24)
        order.pickup_deadline = new_deadline
        order.reminder_sent_at = None
        msg = f'Hold for {order.order_number} extended.'
    else:
        flash('Unknown action.', 'error')
        return redirect(back)

    db.session.commit()
    if msg:
        flash(msg, 'success')
    return redirect(back)


@admin_bp.route('/orders/<int:order_id>/delete', methods=['POST'])
@admin_required
def delete_order(order_id):
    """Delete an order: it's cancelled, its items go back in stock, and it no
    longer shows in any list or total — here, in IZY Ops or on the register."""
    from app.services.pickup import delete_order as delete
    order = Order.query.get_or_404(order_id)
    if delete(order):
        db.session.commit()
        flash(f'Order {order.order_number} deleted. It no longer counts in any totals.', 'success')
    else:
        flash(f'Order {order.order_number} was already deleted.', 'info')
    return redirect(url_for('admin.orders'))


# ─── Users ───────────────────────────────────────────────────────────────────

class UserCreateForm(FlaskForm):
    first_name = StringField('First Name', [validators.DataRequired(), validators.Length(max=100)])
    last_name = StringField('Last Name', [validators.DataRequired(), validators.Length(max=100)])
    email = StringField('Email', [validators.DataRequired(), validators.Email(), validators.Length(max=255)])
    password = PasswordField('Password', [validators.DataRequired(), validators.Length(min=8)])
    is_admin = BooleanField('Admin', default=False)
    is_active = BooleanField('Active', default=True)
    force_password_change = BooleanField('Require Password Change on Next Login', default=True)


class UserEditForm(FlaskForm):
    first_name = StringField('First Name', [validators.DataRequired(), validators.Length(max=100)])
    last_name = StringField('Last Name', [validators.DataRequired(), validators.Length(max=100)])
    email = StringField('Email', [validators.DataRequired(), validators.Email(), validators.Length(max=255)])
    new_password = PasswordField('New Password', [validators.Optional(), validators.Length(min=8)])
    is_admin = BooleanField('Admin', default=False)
    is_active = BooleanField('Active', default=True)
    force_password_change = BooleanField('Require Password Change on Next Login', default=False)


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


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@admin_required
def new_user():
    form = UserCreateForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return render_template('admin/user_form.html', form=form, user=None)

        user = User(
            email=email,
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            is_admin=form.is_admin.data,
            is_active=form.is_active.data,
            force_password_change=form.force_password_change.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User "{user.full_name}" created!', 'success')
        return redirect(url_for('admin.user_detail', user_id=user.id))

    return render_template('admin/user_form.html', form=form, user=None)


@admin_bp.route('/users/<int:user_id>')
@admin_required
def user_detail(user_id):
    user = User.query.get_or_404(user_id)
    orders = Order.visible().filter_by(user_id=user_id).order_by(Order.created_at.desc()).all()
    return render_template('admin/user_detail.html', user=user, orders=orders)


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    form = UserEditForm(obj=user)

    if form.validate_on_submit():
        new_email = form.email.data.lower().strip()
        if new_email != user.email:
            existing = User.query.filter_by(email=new_email).first()
            if existing and existing.id != user.id:
                flash('An account with that email already exists.', 'error')
                return render_template('admin/user_form.html', form=form, user=user)
            user.email = new_email

        user.first_name = form.first_name.data.strip()
        user.last_name = form.last_name.data.strip()

        if user.id != current_user.id:
            user.is_admin = form.is_admin.data
            user.is_active = form.is_active.data

        user.force_password_change = form.force_password_change.data

        if form.new_password.data:
            user.set_password(form.new_password.data)

        db.session.commit()
        flash(f'User "{user.full_name}" updated!', 'success')
        return redirect(url_for('admin.user_detail', user_id=user.id))

    return render_template('admin/user_form.html', form=form, user=user)


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin.user_detail', user_id=user.id))

    has_orders = Order.query.filter_by(user_id=user.id).first() is not None
    if has_orders:
        user.is_active = False
        db.session.commit()
        flash(f'User "{user.full_name}" has existing orders and was deactivated instead of deleted.', 'warning')
        return redirect(url_for('admin.users'))

    name = user.full_name
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{name}" deleted.', 'success')
    return redirect(url_for('admin.users'))


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
    price = DecimalField('Price', [validators.InputRequired(), validators.NumberRange(min=0)], places=2)
    min_order_amount = DecimalField('Only for orders over', [validators.Optional(), validators.NumberRange(min=0)], places=2, default=0)
    estimated_days = StringField('Estimated Days', [validators.Optional(), validators.Length(max=50)])
    is_active = BooleanField('Active', default=True)
    sort_order = IntegerField('Sort Order', [validators.Optional()], default=0)


@admin_bp.route('/shipping')
@admin_required
def shipping():
    rates = ShippingRate.query.order_by(ShippingRate.sort_order, ShippingRate.price).all()
    editing = ShippingRate.query.get(request.args.get('edit', type=int) or 0)
    form = ShippingRateForm(obj=editing) if editing else ShippingRateForm()
    return render_template('admin/shipping.html', rates=rates, form=form, editing=editing)


def _fill_rate(rate, form):
    rate.name = form.name.data.strip()
    rate.price = form.price.data
    rate.min_order_amount = form.min_order_amount.data or 0
    rate.estimated_days = form.estimated_days.data
    rate.is_active = form.is_active.data
    rate.sort_order = form.sort_order.data or 0


def _form_errors(form):
    for field, errors in form.errors.items():
        label = getattr(form, field).label.text if hasattr(form, field) else field
        flash(f'{label}: {errors[0]}', 'error')


@admin_bp.route('/shipping/new', methods=['POST'])
@admin_required
def new_shipping_rate():
    form = ShippingRateForm()
    if form.validate_on_submit():
        rate = ShippingRate()
        _fill_rate(rate, form)
        db.session.add(rate)
        db.session.commit()
        flash(f'Shipping rate "{rate.name}" added!', 'success')
    else:
        _form_errors(form)
    return redirect(url_for('admin.shipping'))


@admin_bp.route('/shipping/<int:rate_id>/edit', methods=['POST'])
@admin_required
def edit_shipping_rate(rate_id):
    rate = ShippingRate.query.get_or_404(rate_id)
    form = ShippingRateForm()
    if form.validate_on_submit():
        _fill_rate(rate, form)
        db.session.commit()
        flash(f'"{rate.name}" saved.', 'success')
        return redirect(url_for('admin.shipping'))
    _form_errors(form)
    return redirect(url_for('admin.shipping', edit=rate.id))


@admin_bp.route('/shipping/<int:rate_id>/toggle', methods=['POST'])
@admin_required
def toggle_shipping_rate(rate_id):
    rate = ShippingRate.query.get_or_404(rate_id)
    rate.is_active = not rate.is_active
    db.session.commit()
    flash(f'"{rate.name}" is now {"on" if rate.is_active else "off"}.', 'success')
    return redirect(url_for('admin.shipping'))


@admin_bp.route('/shipping/<int:rate_id>/delete', methods=['POST'])
@admin_required
def delete_shipping_rate(rate_id):
    rate = ShippingRate.query.get_or_404(rate_id)
    db.session.delete(rate)
    db.session.commit()
    flash('Shipping rate deleted.', 'success')
    return redirect(url_for('admin.shipping'))


# ─── Discounts ───────────────────────────────────────────────────────────────

def _form_decimal(name, default=None):
    raw = (request.form.get(name) or '').strip().replace('$', '').replace('%', '')
    if not raw:
        return default
    try:
        return Decimal(raw)
    except Exception:
        return None


def _form_dates():
    from app.services.pricing import day_start_utc, day_end_utc
    starts = day_start_utc(request.form.get('starts_on'))
    ends = day_end_utc(request.form.get('ends_on'))
    if starts and ends and ends <= starts:
        return starts, ends, 'The end date must be on or after the start date.'
    return starts, ends, None


@admin_bp.route('/discounts')
@admin_required
def discounts():
    from app.models import Sale, PromoCode
    from app.services import pricing
    now = datetime.utcnow()
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    promos = PromoCode.query.order_by(PromoCode.created_at.desc()).all()
    edit_sale = Sale.query.get(request.args.get('edit_sale', type=int) or 0)
    edit_promo = PromoCode.query.get(request.args.get('edit_promo', type=int) or 0)
    categories = Category.query.order_by(Category.name).all()
    stats = db.session.query(db.func.count(Order.id),
                             db.func.coalesce(db.func.sum(Order.promo_discount), 0),
                             db.func.coalesce(db.func.sum(Order.cash_discount), 0))\
        .filter(Order.status.notin_(('cancelled', 'refunded', 'no_show')),
                db.or_(Order.promo_discount > 0, Order.cash_discount > 0)).first()
    return render_template('admin/discounts.html', sales=sales, promos=promos,
                           edit_sale=edit_sale, edit_promo=edit_promo, categories=categories,
                           now=now, local_day=pricing.local_day,
                           running_count=sum(1 for x in sales if x.is_running(now)),
                           cash_percent=pricing.cash_discount_percent(),
                           cash_pickup_on=get_site_setting('cash_pickup_active', '1') == '1',
                           banner_on=get_site_setting('cash_discount_banner_active', '1') == '1',
                           stacking=pricing.stacking(), stats=stats)


@admin_bp.route('/discounts/settings', methods=['POST'])
@admin_required
def discount_settings():
    pct = _form_decimal('cash_discount_percent', Decimal('0'))
    if pct is None or pct < 0 or pct >= 100:
        flash('Cash discount must be a number from 0 to 99.', 'error')
        return redirect(url_for('admin.discounts'))
    set_site_setting('cash_discount_percent', f'{pct.normalize():f}')
    set_site_setting('cash_discount_banner_active',
                     '1' if request.form.get('cash_discount_banner_active') == 'on' else '')
    set_site_setting('discount_stacking',
                     'best' if request.form.get('discount_stacking') == 'best' else 'stack')
    db.session.commit()
    flash('Discount settings saved.', 'success')
    return redirect(url_for('admin.discounts'))


@admin_bp.route('/discounts/sales', methods=['POST'])
@admin_bp.route('/discounts/sales/<int:sale_id>', methods=['POST'])
@admin_required
def save_sale(sale_id=None):
    from app.models import Sale
    sale = Sale.query.get_or_404(sale_id) if sale_id else Sale()
    back = url_for('admin.discounts', edit_sale=sale_id) if sale_id else url_for('admin.discounts')
    name = (request.form.get('name') or '').strip()[:100]
    pct = _form_decimal('percent')
    starts, ends, date_error = _form_dates()
    if not name:
        flash('Give the sale a name, e.g. "Eid sale".', 'error')
        return redirect(back + '#sales')
    if pct is None or pct <= 0 or pct >= 100:
        flash('Sale % must be between 1 and 99.', 'error')
        return redirect(back + '#sales')
    if date_error:
        flash(date_error, 'error')
        return redirect(back + '#sales')
    category_id = request.form.get('category_id', type=int) or None
    sale.name, sale.percent, sale.category_id = name, pct, category_id
    sale.starts_at, sale.ends_at = starts, ends
    sale.is_active = request.form.get('is_active') == 'on'
    sale.show_banner = request.form.get('show_banner') == 'on'
    if not sale_id:
        db.session.add(sale)
    db.session.commit()
    flash(f'Sale "{sale.name}" saved.', 'success')
    return redirect(url_for('admin.discounts') + '#sales')


@admin_bp.route('/discounts/sales/<int:sale_id>/toggle', methods=['POST'])
@admin_required
def toggle_sale(sale_id):
    from app.models import Sale
    sale = Sale.query.get_or_404(sale_id)
    sale.is_active = not sale.is_active
    db.session.commit()
    flash(f'Sale "{sale.name}" is now {"on" if sale.is_active else "off"}.', 'success')
    return redirect(url_for('admin.discounts') + '#sales')


@admin_bp.route('/discounts/sales/<int:sale_id>/delete', methods=['POST'])
@admin_required
def delete_sale(sale_id):
    from app.models import Sale
    sale = Sale.query.get_or_404(sale_id)
    db.session.delete(sale)
    db.session.commit()
    flash('Sale deleted.', 'success')
    return redirect(url_for('admin.discounts') + '#sales')


@admin_bp.route('/discounts/promos', methods=['POST'])
@admin_bp.route('/discounts/promos/<int:promo_id>', methods=['POST'])
@admin_required
def save_promo(promo_id=None):
    from app.models import PromoCode
    from app.services.pricing import normalize_code
    promo = PromoCode.query.get_or_404(promo_id) if promo_id else PromoCode()
    back = (url_for('admin.discounts', edit_promo=promo_id) if promo_id else url_for('admin.discounts')) + '#promos'
    code = normalize_code(request.form.get('code'))
    kind = request.form.get('kind')
    value = _form_decimal('value', Decimal('0'))
    min_order = _form_decimal('min_order', Decimal('0'))
    max_uses = request.form.get('max_uses', type=int)
    starts, ends, date_error = _form_dates()
    if not code or not code.replace('-', '').replace('_', '').isalnum():
        flash('Codes can use letters, numbers, - and _ (e.g. EID10).', 'error')
        return redirect(back)
    clash = PromoCode.query.filter_by(code=code).first()
    if clash and clash.id != promo.id:
        flash(f'There is already a code called {code}.', 'error')
        return redirect(back)
    if kind not in PromoCode.KINDS:
        flash('Choose what the code gives.', 'error')
        return redirect(back)
    if kind == 'percent' and (value is None or value <= 0 or value > 100):
        flash('% off must be between 1 and 100.', 'error')
        return redirect(back)
    if kind == 'amount' and (value is None or value <= 0):
        flash('$ off must be more than $0.', 'error')
        return redirect(back)
    if min_order is None or min_order < 0:
        flash('Minimum order must be $0 or more.', 'error')
        return redirect(back)
    if max_uses is not None and max_uses < 1:
        max_uses = None
    if date_error:
        flash(date_error, 'error')
        return redirect(back)
    promo.code, promo.kind = code, kind
    promo.value = value if kind != 'free_shipping' else Decimal('0')
    promo.min_order, promo.max_uses = min_order, max_uses
    promo.starts_at, promo.ends_at = starts, ends
    promo.once_per_customer = request.form.get('once_per_customer') == 'on'
    promo.is_active = request.form.get('is_active') == 'on'
    if not promo_id:
        promo.uses = 0
        db.session.add(promo)
    db.session.commit()
    flash(f'Promo code {promo.code} saved.', 'success')
    return redirect(url_for('admin.discounts') + '#promos')


@admin_bp.route('/discounts/promos/<int:promo_id>/toggle', methods=['POST'])
@admin_required
def toggle_promo(promo_id):
    from app.models import PromoCode
    promo = PromoCode.query.get_or_404(promo_id)
    promo.is_active = not promo.is_active
    db.session.commit()
    flash(f'{promo.code} is now {"on" if promo.is_active else "off"}.', 'success')
    return redirect(url_for('admin.discounts') + '#promos')


@admin_bp.route('/discounts/promos/<int:promo_id>/delete', methods=['POST'])
@admin_required
def delete_promo(promo_id):
    from app.models import PromoCode
    promo = PromoCode.query.get_or_404(promo_id)
    db.session.delete(promo)
    db.session.commit()
    flash(f'{promo.code} deleted. Past orders keep the code on them.', 'success')
    return redirect(url_for('admin.discounts') + '#promos')


# ─── Settings ────────────────────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        keys = [
            'store_hours',
            'tax_rate',
            'service_fee_active',
            'service_fee_percent',
            'service_fee_fixed',
            'cash_pickup_active',
            'cash_hold_hours',
            'cash_max_order',
            'cash_reminder_hours',
            'cash_noshow_limit',
            'cash_hold_closing_active',
            'cash_expire_action',
            'facebook_url',
            'instagram_url',
            'tiktok_url',
            'facebook_active',
            'instagram_active',
            'tiktok_active',
            'ga_measurement_id',
        ]
        for key in keys:
            value = '1' if key.endswith('_active') and request.form.get(key) == 'on' else request.form.get(key, '')
            setting = SiteSettings.query.filter_by(key=key).first()
            if setting:
                setting.value = value
            else:
                db.session.add(SiteSettings(key=key, value=value))
        from app.services.storefront import parse_drop_time
        set_site_setting('drop_countdown_active',
                         '1' if request.form.get('drop_countdown_active') == 'on' else '0')
        drop_day = request.form.get('drop_weekday', '')
        if drop_day in [str(d) for d in range(7)]:
            set_site_setting('drop_weekday', drop_day)
        drop_time = parse_drop_time(request.form.get('drop_time'))
        if drop_time:
            set_site_setting('drop_time', '%02d:%02d' % drop_time)
        elif request.form.get('drop_time'):
            flash('That drop time didn\'t look right, so the drop time was not changed.', 'error')
        zone = (request.form.get('store_timezone') or '').strip()
        if zone and valid_zone(zone):
            set_site_setting('store_timezone', zone)
        elif zone:
            flash(f'"{zone}" is not a time zone I know, so the time zone was not changed.', 'error')
        db.session.commit()
        forget_tz()
        flash('Settings saved!', 'success')
        return redirect(url_for('admin.settings'))

    settings_dict = {s.key: s.value for s in SiteSettings.query.all()}
    from app.timezones import store_tz_name, local_now
    from app.services.storefront import drop_schedule, DAY_NAMES
    return render_template('admin/settings.html', settings=settings_dict,
                           drop=drop_schedule(), day_names=DAY_NAMES,
                           zone_choices=zone_choices(), current_zone=store_tz_name(),
                           zone_now=local_now(), zone_label=tz_label())


# ─── Banner & shop filters ───────────────────────────────────────────────────

@admin_bp.route('/storefront', methods=['GET', 'POST'])
@admin_required
def storefront():
    from app.services import storefront as sf
    if request.method == 'POST':
        part = request.form.get('part')
        if part == 'ticker':
            items = sf.save_ticker_messages(request.form.getlist('text'),
                                            request.form.getlist('active'))
            set_site_setting('ticker_free_delivery_active',
                             '1' if request.form.get('ticker_free_delivery_active') == 'on' else '')
            db.session.commit()
            shown = sum(1 for m in items if m['active'])
            flash(f'Banner saved: {shown} message(s) showing.', 'success')
        elif part == 'filters':
            f = request.form
            items, problems = sf.save_shop_filters(
                f.getlist('kind'), f.getlist('label'), f.getlist('note'),
                f.getlist('min'), f.getlist('max'), f.getlist('active'))
            db.session.commit()
            for problem in problems:
                flash(problem, 'warning')
            flash(f'Shop buttons saved: {sum(1 for i in items if i["active"])} showing.', 'success')
        elif part in ('reset_ticker', 'reset_filters'):
            sf.reset(sf.TICKER_KEY if part == 'reset_ticker' else sf.FILTERS_KEY)
            db.session.commit()
            flash('Put back the starter list.', 'success')
        return redirect(url_for('admin.storefront') + ('#filters' if 'filters' in (part or '') else ''))

    return render_template('admin/storefront.html',
                           messages=sf.ticker_messages(),
                           filters=sf.shop_filters(include_hidden=True),
                           free_delivery_on=get_site_setting('ticker_free_delivery_active', '1') == '1')


# ─── Visitors (site analytics) ───────────────────────────────────────────────

@admin_bp.route('/visitors')
@admin_required
def visitors():
    from app.services.visits import build_report
    days = request.args.get('days', 30, type=int)
    if days not in (1, 7, 30, 90, 365):
        days = 30
    return render_template('admin/visitors.html', r=build_report(days), days=days)


# ─── Email / SMTP Settings ───────────────────────────────────────────────────

@admin_bp.route('/email-settings', methods=['GET', 'POST'])
@admin_required
def email_settings():
    from app.services.mailer import send_email, is_mail_configured, render_email

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        mail_server = (request.form.get('mail_server') or '').strip() or 'smtp.gmail.com'
        mail_port = (request.form.get('mail_port') or '').strip() or '587'
        mail_use_tls = '1' if request.form.get('mail_use_tls') == 'on' else '0'
        mail_username = (request.form.get('mail_username') or '').strip()
        mail_from_name = (request.form.get('mail_from_name') or '').strip()
        mail_from_email = (request.form.get('mail_from_email') or '').strip() or mail_username
        new_password = request.form.get('mail_password') or ''

        set_site_setting('mail_server', mail_server)
        set_site_setting('mail_port', mail_port)
        set_site_setting('mail_use_tls', mail_use_tls)
        set_site_setting('mail_username', mail_username)
        set_site_setting('mail_from_name', mail_from_name)
        set_site_setting('mail_from_email', mail_from_email)
        set_site_setting('order_alert_enabled', '1' if request.form.get('order_alert_enabled') == 'on' else '0')
        set_site_setting('order_alert_emails', (request.form.get('order_alert_emails') or '').strip())
        if new_password:
            set_site_setting('mail_password_encrypted', encrypt_secret(new_password))
        db.session.commit()

        if action == 'test':
            test_to = (request.form.get('test_email') or '').strip() or mail_from_email
            if not test_to:
                flash('Enter a recipient address to send a test email.', 'error')
                return redirect(url_for('admin.email_settings'))
            try:
                subject = 'Smart 99¢ Plus — Test Email'
                send_email(
                    test_to,
                    subject,
                    render_email('test.html', subject, to_email=test_to,
                                 from_email=mail_from_email,
                                 preheader='Your SMTP settings are working.'),
                    text_body=f'Test email from Smart 99¢ Plus admin panel. Sent to {test_to}.',
                )
                flash(f'Test email sent to {test_to}.', 'success')
            except Exception as e:
                flash(f'Test email failed: {e}', 'error')
        else:
            flash('Email settings saved.', 'success')
        return redirect(url_for('admin.email_settings'))

    password_is_set = bool(get_site_setting('mail_password_encrypted'))
    settings_dict = {
        'mail_server': get_site_setting('mail_server') or current_app.config.get('MAIL_SERVER', 'smtp.gmail.com'),
        'mail_port': get_site_setting('mail_port') or str(current_app.config.get('MAIL_PORT', 587)),
        'mail_use_tls': get_site_setting('mail_use_tls', '1'),
        'mail_username': get_site_setting('mail_username') or current_app.config.get('MAIL_USERNAME', ''),
        'mail_from_name': get_site_setting('mail_from_name', ''),
        'mail_from_email': get_site_setting('mail_from_email') or current_app.config.get('MAIL_USERNAME', ''),
        'order_alert_enabled': get_site_setting('order_alert_enabled', '1'),
        'order_alert_emails': get_site_setting('order_alert_emails', ''),
    }
    return render_template('admin/email_settings.html',
                           settings=settings_dict,
                           password_is_set=password_is_set,
                           is_configured=is_mail_configured())


# ─── Payment Links ───────────────────────────────────────────────────────────

class PaymentLinkForm(FlaskForm):
    description = StringField('Description', [validators.DataRequired(), validators.Length(max=255)])
    amount = DecimalField('Amount (USD)', [validators.DataRequired(), validators.NumberRange(min=0.5)], places=2)
    quantity = IntegerField('Quantity', [validators.Optional(), validators.NumberRange(min=1)], default=1)
    customer_name = StringField('Customer Name (optional)', [validators.Optional(), validators.Length(max=255)])
    customer_email = StringField('Customer Email (optional)', [validators.Optional(), validators.Email(), validators.Length(max=255)])
    product_id = SelectField('Attach Product (optional)', coerce=int, default=0)
    variant_id = SelectField('Variant / Color (optional)', coerce=int, default=0)
    deduct_stock_on_paid = BooleanField('Deduct stock when paid', default=False)
    send_email = BooleanField('Email payment link to customer on create', default=False)


def _product_choices():
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()
    return [(0, '— No product —')] + [(p.id, f'{p.name} (${p.price})') for p in products]


def _variant_choices(product_id=None):
    choices = [(0, '— No variant —')]
    if product_id:
        variants = ProductVariant.query.filter_by(product_id=product_id, is_active=True)\
            .order_by(ProductVariant.sort_order, ProductVariant.id).all()
        choices += [(v.id, v.color_name) for v in variants]
    return choices


@admin_bp.route('/payment-links')
@admin_required
def payment_links():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '')
    query = PaymentLink.query
    if status:
        query = query.filter_by(status=status)
    pagination = query.order_by(PaymentLink.created_at.desc()).paginate(page=page, per_page=25)
    return render_template('admin/payment_links.html',
                           pagination=pagination,
                           links=pagination.items,
                           status=status)


@admin_bp.route('/payment-links/new', methods=['GET', 'POST'])
@admin_required
def new_payment_link():
    import stripe

    form = PaymentLinkForm()
    form.product_id.choices = _product_choices()
    # Variant choices depend on selected product. Populate based on submitted value
    # for POST validation; otherwise leave empty.
    selected_product_id = request.form.get('product_id', type=int) or request.args.get('product_id', type=int) or 0
    form.variant_id.choices = _variant_choices(selected_product_id)

    if form.validate_on_submit():
        if not current_app.config.get('STRIPE_SECRET_KEY'):
            flash('Stripe secret key is not configured. Set STRIPE_SECRET_KEY in your environment.', 'error')
            return render_template('admin/payment_link_form.html', form=form, link=None)

        product_id = form.product_id.data or None
        variant_id = form.variant_id.data or None
        if variant_id:
            variant = ProductVariant.query.get(variant_id)
            if not variant or variant.product_id != product_id:
                variant_id = None
        quantity = max(1, form.quantity.data or 1)
        amount_cents = int(Decimal(str(form.amount.data)) * 100)

        stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
        try:
            stripe_product = stripe.Product.create(name=form.description.data)
            stripe_price = stripe.Price.create(
                product=stripe_product.id,
                unit_amount=amount_cents,
                currency='usd',
            )
            metadata = {'description': form.description.data}
            if product_id:
                metadata['product_id'] = str(product_id)
            if variant_id:
                metadata['variant_id'] = str(variant_id)
            metadata['quantity'] = str(quantity)
            metadata['deduct_stock'] = '1' if (form.deduct_stock_on_paid.data and product_id) else '0'
            if form.customer_email.data:
                metadata['customer_email'] = form.customer_email.data.strip()

            stripe_link = stripe.PaymentLink.create(
                line_items=[{'price': stripe_price.id, 'quantity': quantity}],
                metadata=metadata,
            )
        except Exception as e:
            flash(f'Stripe error: {e}', 'error')
            return render_template('admin/payment_link_form.html', form=form, link=None)

        link = PaymentLink(
            description=form.description.data,
            amount=form.amount.data,
            quantity=quantity,
            customer_name=(form.customer_name.data or '').strip() or None,
            customer_email=(form.customer_email.data or '').strip().lower() or None,
            product_id=product_id,
            variant_id=variant_id,
            deduct_stock_on_paid=bool(form.deduct_stock_on_paid.data and product_id),
            stripe_product_id=stripe_product.id,
            stripe_price_id=stripe_price.id,
            stripe_payment_link_id=stripe_link.id,
            url=stripe_link.url,
            status='active',
            created_by_id=current_user.id,
        )
        db.session.add(link)
        db.session.commit()

        if form.send_email.data and link.customer_email:
            _send_payment_link_email(link)

        flash(f'Payment link created: {link.url}', 'success')
        return redirect(url_for('admin.payment_link_detail', link_id=link.id))

    return render_template('admin/payment_link_form.html', form=form, link=None)


@admin_bp.route('/payment-links/variants/<int:product_id>')
@admin_required
def payment_link_variants(product_id):
    variants = ProductVariant.query.filter_by(product_id=product_id, is_active=True)\
        .order_by(ProductVariant.sort_order, ProductVariant.id).all()
    return jsonify({'variants': [{'id': v.id, 'name': v.color_name,
                                  'stock': v.stock_quantity} for v in variants]})


@admin_bp.route('/payment-links/<int:link_id>')
@admin_required
def payment_link_detail(link_id):
    link = PaymentLink.query.get_or_404(link_id)
    return render_template('admin/payment_link_detail.html', link=link)


@admin_bp.route('/payment-links/<int:link_id>/resend', methods=['POST'])
@admin_required
def resend_payment_link(link_id):
    link = PaymentLink.query.get_or_404(link_id)
    override_email = (request.form.get('customer_email') or '').strip().lower()
    if override_email:
        link.customer_email = override_email
    if not link.customer_email:
        flash('Add a customer email first.', 'error')
        return redirect(url_for('admin.payment_link_detail', link_id=link.id))
    ok, err = _send_payment_link_email(link)
    if ok:
        flash(f'Payment link emailed to {link.customer_email}.', 'success')
    else:
        flash(f'Could not send email: {err}', 'error')
    return redirect(url_for('admin.payment_link_detail', link_id=link.id))


@admin_bp.route('/payment-links/<int:link_id>/void', methods=['POST'])
@admin_required
def void_payment_link(link_id):
    import stripe
    link = PaymentLink.query.get_or_404(link_id)
    if link.status == 'paid':
        flash('Cannot void a link that has already been paid.', 'error')
        return redirect(url_for('admin.payment_link_detail', link_id=link.id))
    if current_app.config.get('STRIPE_SECRET_KEY') and link.stripe_payment_link_id:
        stripe.api_key = current_app.config['STRIPE_SECRET_KEY']
        try:
            stripe.PaymentLink.modify(link.stripe_payment_link_id, active=False)
        except Exception as e:
            flash(f'Stripe deactivation warning: {e}', 'warning')
    link.status = 'void'
    db.session.commit()
    flash('Payment link voided.', 'success')
    return redirect(url_for('admin.payment_links'))


def _send_payment_link_email(link):
    """Best-effort email send. Returns (ok, error_message)."""
    from app.services.mailer import send_email, render_email
    greeting = f'Hi {link.customer_name},' if link.customer_name else 'Hi there,'
    subject = f'Payment link: {link.description}'
    first = (link.customer_name or '').split(' ')[0]
    html = render_email('payment_link.html', subject, link=link,
                        greeting_title=f'Hi {first}, here\'s your payment link.' if first
                        else 'Here\'s your payment link.',
                        preheader=f'${link.amount:.2f} for {link.description}. Pay securely by card.',
                        footer_note='If you did not request this, you can ignore this email.')
    text = (f'{greeting}\n\nYour payment link from Smart 99¢ Plus:\n'
            f'{link.description}\nAmount: ${link.amount}\n\n{link.url}\n')
    try:
        send_email(link.customer_email, subject, html, text_body=text)
        link.email_sent_at = datetime.utcnow()
        db.session.commit()
        return True, None
    except Exception as e:
        return False, str(e)
