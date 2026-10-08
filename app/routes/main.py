from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, validators
from app.models import Product, Category
from app.helpers import get_site_setting
from app.extensions import db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.store import STORE

main_bp = Blueprint('main', __name__)


class ContactForm(FlaskForm):
    name = StringField('Name', [validators.DataRequired(), validators.Length(max=100)])
    email = StringField('Email', [validators.DataRequired(), validators.Email()])
    subject = StringField('Subject', [validators.DataRequired(), validators.Length(max=200)])
    message = TextAreaField('Message', [validators.DataRequired(), validators.Length(min=10, max=2000)])


@main_bp.route('/')
def index():
    featured_products = Product.query.filter_by(is_active=True, is_featured=True)\
        .order_by(Product.updated_at.desc()).limit(8).all()
    new_products = Product.query.filter_by(is_active=True)\
        .order_by(Product.created_at.desc()).limit(8).all()
    categories = Category.shoppable()\
        .order_by(Category.sort_order, Category.name).limit(8).all()
    # Balloons always get a home-page tile, even when they sort past the first 8.
    if not any('balloon' in c.name.lower() for c in categories):
        balloons = Category.shoppable().filter(Category.name.ilike('%balloon%')).first()
        if balloons:
            categories = categories[:7] + [balloons]

    # Lead tile: the trading-card category if there is one, else the first category.
    lead = next((c for c in categories if any(k in c.name.lower() for k in ('pokémon', 'pokemon', 'tcg', 'card'))),
                categories[0] if categories else None)
    others = [c for c in categories if c is not lead]
    # Balloons get the pink "Party" tile right next to the lead tile.
    others.sort(key=lambda c: 'balloon' not in c.name.lower())

    from_price = None
    if lead:
        from_price = db.session.query(db.func.min(Product.price))\
            .filter(Product.is_active.is_(True), Product.category_id == lead.id).scalar()

    return render_template('index.html',
                           featured_products=featured_products,
                           new_products=new_products,
                           categories=categories,
                           lead_category=lead,
                           other_categories=others,
                           from_price=from_price,
                           next_restock=next_restock_iso())


def next_restock_iso(now=None):
    """Next weekly restock (store's local time, New York) as an ISO timestamp."""
    tz = ZoneInfo('America/New_York')
    now = now or datetime.now(tz)
    weekday, hour = STORE['restock_weekday'], STORE['restock_hour']
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=(weekday - now.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return target.isoformat()


@main_bp.route('/about')
def about():
    return render_template('about.html')


@main_bp.route('/contact', methods=['GET', 'POST'])
def contact():
    form = ContactForm()
    if form.validate_on_submit():
        # In production, send email via Flask-Mail
        flash('Thank you for your message! We will get back to you within 24 hours.', 'success')
        return redirect(url_for('main.contact'))
    return render_template('contact.html', form=form)
