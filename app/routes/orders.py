from flask import Blueprint, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.models import Order

orders_bp = Blueprint('orders', __name__)


@orders_bp.route('/orders')
@login_required
def order_list():
    orders = Order.query.filter_by(user_id=current_user.id)\
        .order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders)


@orders_bp.route('/orders/<order_number>')
@login_required
def order_detail(order_number):
    order = Order.query.filter_by(order_number=order_number).first_or_404()
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template('order_detail.html', order=order)


@orders_bp.route('/account')
@login_required
def account():
    orders = Order.query.filter_by(user_id=current_user.id)\
        .order_by(Order.created_at.desc()).limit(5).all()
    return render_template('account.html', orders=orders)
