from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash
from app.models import Product, ProductVariant
from app.helpers import (get_cart_items, get_cart_subtotal, get_cart_count,
                         make_line_key, _normalize_cart)

cart_bp = Blueprint('cart', __name__)


def _save_cart(cart):
    session['cart'] = cart
    session.modified = True


def _resolve_product_and_variant(data):
    """Return (product, variant_or_None, error_message_or_None)."""
    product_id = data.get('product_id')
    variant_id = data.get('variant_id') or 0
    try:
        product_id = int(product_id)
        variant_id = int(variant_id or 0)
    except (TypeError, ValueError):
        return None, None, 'Invalid product.'

    product = Product.query.get(product_id)
    if not product or not product.is_active:
        return None, None, 'Product not found.'

    variant = None
    if variant_id:
        variant = ProductVariant.query.get(variant_id)
        if not variant or variant.product_id != product.id or not variant.is_active:
            return None, None, 'Color not available.'
    elif product.has_variants:
        return None, None, 'Please choose a color.'

    return product, variant, None


def _available_stock(product, variant):
    if not product.track_inventory:
        return None  # unlimited
    if variant is not None:
        return variant.stock_quantity
    return product.stock_quantity


@cart_bp.route('/cart')
def cart():
    items = get_cart_items()
    subtotal = get_cart_subtotal()
    return render_template('cart.html', items=items, subtotal=subtotal)


@cart_bp.route('/cart/add', methods=['POST'])
def add_to_cart():
    data = request.get_json() or request.form
    try:
        quantity = max(1, int(data.get('quantity', 1)))
    except (TypeError, ValueError):
        quantity = 1

    product, variant, err = _resolve_product_and_variant(data)
    if err:
        if request.is_json:
            return jsonify({'success': False, 'message': err}), 400
        flash(err, 'error')
        return redirect(request.referrer or url_for('cart.cart'))

    stock = _available_stock(product, variant)

    cart = _normalize_cart(session.get('cart', {}))
    key = make_line_key(product.id, variant.id if variant else 0)
    current_qty = cart.get(key, 0)
    new_qty = current_qty + quantity

    if stock is not None:
        if stock <= 0:
            msg = 'That color is out of stock.' if variant else 'Item is out of stock.'
            if request.is_json:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'error')
            return redirect(request.referrer or url_for('cart.cart'))
        if new_qty > stock:
            new_qty = stock

    cart[key] = new_qty
    _save_cart(cart)

    cart_count = sum(cart.values())
    name = f'{product.name} ({variant.color_name})' if variant else product.name
    if request.is_json:
        return jsonify({
            'success': True,
            'message': f'{name} added to cart!',
            'cart_count': cart_count,
        })
    flash(f'{name} added to cart!', 'success')
    return redirect(request.referrer or url_for('cart.cart'))


@cart_bp.route('/cart/update', methods=['POST'])
def update_cart():
    data = request.get_json() or request.form
    try:
        quantity = int(data.get('quantity', 0))
    except (TypeError, ValueError):
        quantity = 0

    # Accept either a line_key or product_id (+optional variant_id)
    line_key = data.get('line_key')
    if not line_key:
        product, variant, err = _resolve_product_and_variant(data)
        if err:
            if request.is_json:
                return jsonify({'success': False, 'message': err}), 400
            return redirect(url_for('cart.cart'))
        line_key = make_line_key(product.id, variant.id if variant else 0)

    cart = _normalize_cart(session.get('cart', {}))

    if quantity <= 0:
        cart.pop(line_key, None)
    else:
        # Validate stock if the line exists / can be resolved
        pid, vid = None, None
        if ':' in line_key:
            try:
                pid, vid = [int(x) for x in line_key.split(':', 1)]
            except ValueError:
                pass
        product = Product.query.get(pid) if pid else None
        variant = ProductVariant.query.get(vid) if vid else None
        if product:
            stock = _available_stock(product, variant)
            if stock is not None:
                quantity = min(quantity, max(0, stock))
                if quantity <= 0:
                    cart.pop(line_key, None)
                else:
                    cart[line_key] = quantity
            else:
                cart[line_key] = quantity
        else:
            cart.pop(line_key, None)

    _save_cart(cart)

    items = get_cart_items()
    subtotal = float(get_cart_subtotal())
    cart_count = sum(cart.values())

    item_total = 0.0
    for item in items:
        if item['line_key'] == line_key:
            item_total = float(item['total'])
            break

    if request.is_json:
        return jsonify({
            'success': True,
            'cart_count': cart_count,
            'subtotal': subtotal,
            'item_total': item_total,
        })
    return redirect(url_for('cart.cart'))


@cart_bp.route('/cart/remove', methods=['POST'])
def remove_from_cart():
    data = request.get_json() or request.form
    line_key = data.get('line_key')
    if not line_key:
        product, variant, err = _resolve_product_and_variant(data)
        if err:
            if request.is_json:
                return jsonify({'success': False, 'message': err}), 400
            return redirect(url_for('cart.cart'))
        line_key = make_line_key(product.id, variant.id if variant else 0)

    cart = _normalize_cart(session.get('cart', {}))
    cart.pop(line_key, None)
    _save_cart(cart)

    cart_count = sum(cart.values())
    subtotal = float(get_cart_subtotal())

    if request.is_json:
        return jsonify({'success': True, 'cart_count': cart_count, 'subtotal': subtotal})
    flash('Item removed from cart.', 'success')
    return redirect(url_for('cart.cart'))


@cart_bp.route('/cart/count')
def cart_count():
    return jsonify({'count': get_cart_count()})


@cart_bp.route('/cart/drawer')
def cart_drawer_json():
    items = get_cart_items()
    subtotal = float(get_cart_subtotal())
    return jsonify({
        'items': [{
            'product_id': item['product'].id,
            'variant_id': item['variant'].id if item['variant'] else 0,
            'line_key': item['line_key'],
            'name': item['product'].name,
            'color': item['variant'].color_name if item['variant'] else None,
            'color_hex': item['variant'].color_hex if item['variant'] else None,
            'slug': item['product'].slug,
            'image_url': (item['variant'].image_url if item['variant']
                          else item['product'].primary_image_url),
            'unit_price': float(item['unit_price']),
            'quantity': item['quantity'],
        } for item in items],
        'subtotal': subtotal,
        'cart_count': get_cart_count(),
    })
