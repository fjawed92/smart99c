from flask import Blueprint, render_template, request, abort, current_app
from app.models import Product, Category
from app.extensions import db

shop_bp = Blueprint('shop', __name__)


@shop_bp.route('/shop')
def shop():
    return _product_list(category_slug=None)


@shop_bp.route('/shop/<category_slug>')
def shop_category(category_slug):
    category = Category.query.filter_by(slug=category_slug, is_active=True).first_or_404()
    return _product_list(category_slug=category_slug, current_category=category)


def _product_list(category_slug=None, current_category=None):
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'newest')
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    per_page = current_app.config.get('PRODUCTS_PER_PAGE', 12)

    query = Product.query.filter_by(is_active=True)

    if category_slug:
        query = query.join(Category).filter(Category.slug == category_slug)

    if search:
        query = query.filter(
            db.or_(
                Product.name.ilike(f'%{search}%'),
                Product.short_description.ilike(f'%{search}%'),
                Product.description.ilike(f'%{search}%'),
            )
        )

    if min_price is not None:
        query = query.filter(Product.price >= min_price)
    if max_price is not None:
        query = query.filter(Product.price <= max_price)

    sort_map = {
        'newest': Product.created_at.desc(),
        'oldest': Product.created_at.asc(),
        'price_asc': Product.price.asc(),
        'price_desc': Product.price.desc(),
        'name_asc': Product.name.asc(),
    }
    query = query.order_by(sort_map.get(sort, Product.created_at.desc()))

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()

    return render_template('shop.html',
                           products=pagination.items,
                           pagination=pagination,
                           categories=categories,
                           current_category=current_category,
                           search=search,
                           sort=sort,
                           min_price=min_price,
                           max_price=max_price)


@shop_bp.route('/product/<slug>')
def product_detail(slug):
    product = Product.query.filter_by(slug=slug, is_active=True).first_or_404()
    related = []
    if product.category_id:
        related = Product.query.filter(
            Product.category_id == product.category_id,
            Product.id != product.id,
            Product.is_active == True
        ).limit(4).all()
    return render_template('product.html', product=product, related=related)
