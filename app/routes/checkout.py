import stripe
from decimal import Decimal
from flask import (Blueprint, render_template, request, session, jsonify,
                   redirect, url_for, flash, current_app)
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, validators
from app.extensions import db
from app.models import Order, OrderItem, ShippingAddress, ShippingRate, Product
from app.helpers import get_cart_items, get_cart_subtotal, generate_order_number

checkout_bp = Blueprint('checkout', __name__)


class CheckoutForm(FlaskForm):
    first_name = StringField('First Name', [validators.DataRequired(), validators.Length(max=100)])
    last_name = StringField('Last Name', [validators.DataRequired(), validators.Length(max=100)])
    email = StringField('Email', [validators.DataRequired(), validators.Email()])
    phone = StringField('Phone', [validators.Optional(), validators.Length(max=30)])
    address_line1 = StringField('Address', [validators.DataRequired(), validators.Length(max=255)])
    address_line2 = StringField('Apt/Suite', [validators.Optional(), validators.Length(max=255)])
    city = StringField('City', [validators.DataRequired(), validators.Length(max=100)])
    state = StringField('State', [validators.DataRequired(), validators.Length(max=50)])
    zip_code = StringField('ZIP Code', [validators.DataRequired(), validators.Length(max=20)])
    shipping_rate_id = SelectField('Shipping Method', coerce=int)


@checkout_bp.route('/checkout', methods=['GET'])
def checkout():
    items = get_cart_items()
    if not items:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart.cart'))

    form = CheckoutForm()
    shipping_rates = ShippingRate.query.filter_by(is_active=True)\
        .order_by(ShippingRate.sort_order, ShippingRate.price).all()
    form.shipping_rate_id.choices = [(r.id, f'{r.name} — ${r.price} ({r.estimated_days})')
                                     for r in shipping_rates]

    if current_user.is_authenticated:
        form.first_name.data = current_user.first_name
        form.last_name.data = current_user.last_name
        form.email.data = current_user.email

    subtotal = get_cart_subtotal()
    tax_rate = Decimal(str(current_app.config.get('TAX_RATE', 0.08875)))
    tax = subtotal * tax_rate

    return render_template('checkout.html',
                           form=form,
                           items=items,
                           subtotal=subtotal,
                           tax=tax,
                           shipping_rates=shipping_rates)


@checkout_bp.route('/checkout/create-payment-intent', methods=['POST'])
def create_payment_intent():
    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY')
    data = request.get_json() or {}
    shipping_rate_id = data.get('shipping_rate_id')

    items = get_cart_items()
    if not items:
        return jsonify({'error': 'Cart is empty'}), 400

    subtotal = get_cart_subtotal()
    tax_rate = Decimal(str(current_app.config.get('TAX_RATE', 0.08875)))
    tax = subtotal * tax_rate

    shipping_cost = Decimal('0')
    if shipping_rate_id:
        rate = ShippingRate.query.get(int(shipping_rate_id))
        if rate:
            shipping_cost = rate.price

    total = subtotal + tax + shipping_cost
    amount_cents = int(total * 100)

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency='usd',
            automatic_payment_methods={'enabled': True},
            metadata={
                'subtotal': str(subtotal),
                'tax': str(tax),
                'shipping': str(shipping_cost),
            }
        )
        session['pending_payment_intent'] = intent.id
        return jsonify({'client_secret': intent.client_secret,
                        'subtotal': float(subtotal),
                        'tax': float(tax),
                        'shipping': float(shipping_cost),
                        'total': float(total)})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e.user_message)}), 400


@checkout_bp.route('/checkout/confirm', methods=['POST'])
def confirm_order():
    data = request.get_json() or request.form
    payment_intent_id = data.get('payment_intent_id')
    shipping_rate_value = data.get('shipping_rate_id', 0)
    try:
        shipping_rate_id = int(shipping_rate_value or 0)
    except (TypeError, ValueError):
        shipping_rate_id = 0

    items = get_cart_items()
    if not items:
        return jsonify({'error': 'Cart is empty'}), 400

    subtotal = get_cart_subtotal()
    tax_rate = Decimal(str(current_app.config.get('TAX_RATE', 0.08875)))
    tax = subtotal * tax_rate
    shipping_cost = Decimal('0')

    rate = ShippingRate.query.get(shipping_rate_id) if shipping_rate_id else None
    if rate:
        shipping_cost = rate.price

    total = subtotal + tax + shipping_cost

    order = Order(
        order_number=generate_order_number(),
        user_id=current_user.id if current_user.is_authenticated else None,
        status='processing',
        subtotal=subtotal,
        tax=tax,
        shipping_cost=shipping_cost,
        total=total,
        stripe_payment_intent_id=payment_intent_id,
    )
    db.session.add(order)
    db.session.flush()

    addr_data = data if not request.is_json else data
    address = ShippingAddress(
        order_id=order.id,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        address_line1=data.get('address_line1', ''),
        address_line2=data.get('address_line2', ''),
        city=data.get('city', ''),
        state=data.get('state', ''),
        zip_code=data.get('zip_code', ''),
        country=data.get('country', 'US'),
    )
    db.session.add(address)

    for item in items:
        product = item['product']
        variant = item.get('variant')
        qty = item['quantity']
        unit_price = item['unit_price']
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id if variant else None,
            product_name=product.name,
            product_sku=(variant.sku if variant and variant.sku else product.sku) or '',
            product_color=variant.color_name if variant else None,
            quantity=qty,
            unit_price=unit_price,
            total_price=unit_price * qty,
        )
        db.session.add(order_item)

        if product.track_inventory:
            if variant is not None:
                variant.stock_quantity = max(0, variant.stock_quantity - qty)
            else:
                product.stock_quantity = max(0, product.stock_quantity - qty)

    db.session.commit()

    session.pop('cart', None)
    session.pop('pending_payment_intent', None)

    return jsonify({'success': True, 'order_number': order.order_number,
                    'redirect': url_for('checkout.order_confirmation',
                                        order_number=order.order_number)})


@checkout_bp.route('/order/confirmation/<order_number>')
def order_confirmation(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    return render_template('order_confirmation.html', order=order)


@checkout_bp.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY')
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = current_app.config.get('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return '', 400

    if event['type'] == 'payment_intent.succeeded':
        pi = event['data']['object']
        order = Order.query.filter_by(stripe_payment_intent_id=pi['id']).first()
        if order and order.status == 'pending':
            order.status = 'processing'
            order.stripe_charge_id = pi.get('latest_charge', '')
            db.session.commit()

    elif event['type'] == 'payment_intent.payment_failed':
        pi = event['data']['object']
        order = Order.query.filter_by(stripe_payment_intent_id=pi['id']).first()
        if order:
            order.status = 'cancelled'
            db.session.commit()

    return '', 200
