from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for, flash
from app.models import Product
from app.helpers import get_cart_items, get_cart_subtotal, get_cart_count

cart_bp = Blueprint('cart', __name__)


def _save_cart(cart):
    session['cart'] = cart
    session.modified = True


@cart_bp.route('/cart')
def cart():
    items = get_cart_items()
    subtotal = get_cart_subtotal()
    return render_template('cart.html', items=items, subtotal=subtotal)


@cart_bp.route('/cart/add', methods=['POST'])
def add_to_cart():
    data = request.get_json() or request.form
    product_id = str(data.get('product_id', ''))
    quantity = int(data.get('quantity', 1))

    product = Product.query.get(int(product_id)) if product_id else None
    if not product or not product.is_active:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Product not found'}), 404
        flash('Product not found.', 'error')
        return redirect(url_for('cart.cart'))

    if product.track_inventory and quantity > product.stock_quantity:
        if request.is_json:
            return jsonify({'success': False, 'message': 'Not enough stock'}), 400
        flash('Not enough stock available.', 'error')
        return redirect(request.referrer or url_for('cart.cart'))

    cart = session.get('cart', {})
    current_qty = cart.get(product_id, 0)
    new_qty = current_qty + quantity

    if product.track_inventory and new_qty > product.stock_quantity:
        new_qty = product.stock_quantity

    cart[product_id] = new_qty
    _save_cart(cart)

    cart_count = sum(cart.values())
    if request.is_json:
        return jsonify({
            'success': True,
            'message': f'{product.name} added to cart!',
            'cart_count': cart_count,
        })
    flash(f'{product.name} added to cart!', 'success')
    return redirect(request.referrer or url_for('cart.cart'))


@cart_bp.route('/cart/update', methods=['POST'])
def update_cart():
    data = request.get_json() or request.form
    product_id = str(data.get('product_id', ''))
    quantity = int(data.get('quantity', 0))

    cart = session.get('cart', {})

    if quantity <= 0:
        cart.pop(product_id, None)
    else:
        product = Product.query.get(int(product_id)) if product_id else None
        if product and product.track_inventory:
            quantity = min(quantity, product.stock_quantity)
        cart[product_id] = quantity

    _save_cart(cart)

    from app.helpers import get_cart_items, get_cart_subtotal
    items = get_cart_items()
    subtotal = float(get_cart_subtotal())
    cart_count = sum(cart.values())

    item_total = 0.0
    for item in items:
        if str(item['product'].id) == product_id:
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
    product_id = str(data.get('product_id', ''))

    cart = session.get('cart', {})
    cart.pop(product_id, None)
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
            'name': item['product'].name,
            'slug': item['product'].slug,
            'image_url': item['product'].primary_image_url,
            'unit_price': float(item['product'].price),
            'quantity': item['quantity'],
        } for item in items],
        'subtotal': subtotal,
        'cart_count': get_cart_count(),
    })
