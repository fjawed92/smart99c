from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, validators
from app.models import Product, Category
from app.helpers import get_site_setting

main_bp = Blueprint('main', __name__)


class ContactForm(FlaskForm):
    name = StringField('Name', [validators.DataRequired(), validators.Length(max=100)])
    email = StringField('Email', [validators.DataRequired(), validators.Email()])
    subject = StringField('Subject', [validators.DataRequired(), validators.Length(max=200)])
    message = TextAreaField('Message', [validators.DataRequired(), validators.Length(min=10, max=2000)])


@main_bp.route('/')
def index():
    featured_products = Product.query.filter_by(is_active=True, is_featured=True)\
        .limit(8).all()
    categories = Category.query.filter_by(is_active=True)\
        .order_by(Category.sort_order, Category.name).limit(6).all()
    free_shipping_threshold = get_site_setting('free_shipping_threshold', '50')
    return render_template('index.html',
                           featured_products=featured_products,
                           categories=categories,
                           free_shipping_threshold=free_shipping_threshold)


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
