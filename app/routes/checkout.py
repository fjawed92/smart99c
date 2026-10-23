import stripe
from decimal import Decimal
from flask import (Blueprint, render_template, request, session, jsonify,
                   redirect, url_for, flash, current_app)
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, validators
from datetime import datetime
from app.extensions import db
from app.models import (Order, OrderItem, ShippingAddress, ShippingRate, Product,
                        ProductVariant, PaymentLink, is_pickup_option)
from app.helpers import (get_cart_items, get_cart_subtotal, generate_order_number,
                         service_fee_for, get_site_setting)
from app.services.visits import track_event
from app.services import pickup

checkout_bp = Blueprint('checkout', __name__)


def _order_totals(rate, paid_by_card=True):
    subtotal = get_cart_subtotal()
    tax_rate = Decimal(str(current_app.config.get('TAX_RATE', 0.08875)))
    tax = subtotal * tax_rate
    shipping_cost = rate.price if rate else Decimal('0')
    fee = service_fee_for(subtotal + tax + shipping_cost, paid_by_card)
    total = subtotal + tax + shipping_cost + fee
    return subtotal, tax, shipping_cost, fee, total


def _save_order(data, items, rate, totals, **fields):
    """Save the order, its address and items, take the stock and empty the cart."""
    subtotal, tax, shipping_cost, fee, total = totals
    order = Order(
        order_number=generate_order_number(),
        user_id=current_user.id if current_user.is_authenticated else None,
        subtotal=subtotal,
        tax=tax,
        shipping_cost=shipping_cost,
        service_fee=fee,
        total=total,
        shipping_method=rate.name if rate else None,
        **fields,
    )
    db.session.add(order)
    db.session.flush()

    address = ShippingAddress(
        order_id=order.id,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        address_line1=data.get('address_line1', '') or '',
        address_line2=data.get('address_line2', '') or '',
        city=data.get('city', '') or '',
        state=data.get('state', '') or '',
        zip_code=data.get('zip_code', '') or '',
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
    track_event('order', order_total=order.total)

    session.pop('cart', None)
    session.pop('pending_payment_intent', None)
    return order


def _pickup_rate():
    for rate in ShippingRate.query.filter_by(is_active=True)\
            .order_by(ShippingRate.sort_order, ShippingRate.price).all():
        if is_pickup_option(rate.name):
            return rate
    return None


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

    # Pay-cash-at-pickup option (Admin → Settings).
    cash = pickup.cash_settings()
    pickup_rate = _pickup_rate()
    cash_total = subtotal + tax + (pickup_rate.price if pickup_rate else 0)
    cash_option = None
    if cash['active'] and pickup_rate:
        cash_option = {
            'rate': pickup_rate,
            'total': cash_total,
            'hold_hours': cash['hold_hours'],
            'deadline': pickup.format_local(pickup.pickup_deadline(settings=cash)),
            'blocked': pickup.cash_block_reason(cash_total, settings=cash),
        }

    return render_template('checkout.html',
                           form=form,
                           items=items,
                           subtotal=subtotal,
                           tax=tax,
                           shipping_rates=shipping_rates,
                           cash_option=cash_option,
                           fee_active=get_site_setting('service_fee_active', '1') == '1')


@checkout_bp.route('/checkout/create-payment-intent', methods=['POST'])
def create_payment_intent():
    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY')
    data = request.get_json() or {}
    shipping_rate_id = data.get('shipping_rate_id')

    items = get_cart_items()
    if not items:
        return jsonify({'error': 'Cart is empty'}), 400

    rate = ShippingRate.query.get(int(shipping_rate_id)) if shipping_rate_id else None
    subtotal, tax, shipping_cost, fee, total = _order_totals(rate)
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
                'service_fee': str(fee),
                'delivery_option': rate.name if rate else '',
            }
        )
        session['pending_payment_intent'] = intent.id
        return jsonify({'client_secret': intent.client_secret,
                        'subtotal': float(subtotal),
                        'tax': float(tax),
                        'shipping': float(shipping_cost),
                        'service_fee': float(fee),
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

    rate = ShippingRate.query.get(shipping_rate_id) if shipping_rate_id else None
    subtotal, tax, shipping_cost, fee, total = _order_totals(rate)

    # Ask Stripe whether this payment really went through before saving an order.
    if not payment_intent_id:
        return jsonify({'error': 'Missing payment. Please try again.'}), 400
    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY')
    status = 'processing'
    charge_id = None
    livemode = None
    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.error.InvalidRequestError:
        return jsonify({'error': 'We could not find your payment. Please try again.'}), 400
    except stripe.error.StripeError:
        # Stripe unreachable: keep the order as pending; the webhook marks it paid.
        intent = None
        status = 'pending'
    if intent is not None:
        if intent.status not in ('succeeded', 'processing', 'requires_capture'):
            return jsonify({'error': 'Your payment was not completed. Please try again.'}), 400
        if intent.status != 'succeeded':
            status = 'pending'
        charge_id = intent.get('latest_charge')
        livemode = bool(intent.get('livemode'))

    order = _save_order(data, items, rate, (subtotal, tax, shipping_cost, fee, total),
                        status=status,
                        stripe_payment_intent_id=payment_intent_id,
                        stripe_charge_id=charge_id,
                        stripe_livemode=livemode)

    return jsonify({'success': True, 'order_number': order.order_number,
                    'redirect': url_for('checkout.order_confirmation',
                                        order_number=order.order_number)})


@checkout_bp.route('/checkout/reserve-cash', methods=['POST'])
def reserve_cash():
    """Pickup order paid in cash at the counter: no card, held until a deadline."""
    data = request.get_json() or {}
    items = get_cart_items()
    if not items:
        return jsonify({'error': 'Your cart is empty.'}), 400

    for field, label in (('first_name', 'first name'), ('last_name', 'last name'),
                         ('email', 'email'), ('phone', 'phone number')):
        if not (data.get(field) or '').strip():
            return jsonify({'error': f'Please enter your {label}.'}), 400
    if not data.get('agree_deadline'):
        return jsonify({'error': 'Please confirm you can pick up before the deadline.'}), 400

    rate = _pickup_rate()
    if rate is None:
        return jsonify({'error': 'Store pickup is not available right now.'}), 400
    totals = _order_totals(rate, paid_by_card=False)
    settings = pickup.cash_settings()
    blocked = pickup.cash_block_reason(totals[-1], data.get('email', ''), data.get('phone', ''),
                                       settings=settings)
    if blocked:
        return jsonify({'error': blocked}), 400

    order = _save_order(data, items, rate, totals,
                        status='processing',
                        payment_method='cash',
                        pickup_deadline=pickup.pickup_deadline(settings=settings))
    pickup.email_customer(order, 'reserved')
    return jsonify({'success': True, 'order_number': order.order_number,
                    'redirect': url_for('checkout.order_confirmation',
                                        order_number=order.order_number)})


@checkout_bp.route('/order/confirmation/<order_number>')
def order_confirmation(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    return render_template('order_confirmation.html', order=order,
                           deadline=pickup.format_local(order.pickup_deadline))


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

    elif event['type'] == 'checkout.session.completed':
        sess = event['data']['object']
        payment_link_id = sess.get('payment_link')
        if not payment_link_id:
            return '', 200
        link = PaymentLink.query.filter_by(stripe_payment_link_id=payment_link_id).first()
        if not link or link.status == 'paid':
            return '', 200

        link.status = 'paid'
        link.paid_at = datetime.utcnow()
        link.stripe_checkout_session_id = sess.get('id')

        if link.deduct_stock_on_paid and link.product_id:
            product = Product.query.get(link.product_id)
            if product and product.track_inventory:
                qty = link.quantity or 1
                if link.variant_id:
                    variant = ProductVariant.query.get(link.variant_id)
                    if variant:
                        variant.stock_quantity = max(0, variant.stock_quantity - qty)
                else:
                    product.stock_quantity = max(0, product.stock_quantity - qty)

        db.session.commit()

    return '', 200
