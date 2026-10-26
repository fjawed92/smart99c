"""IZY Ops API (the store's back office manages this site through it).

The same contract family99c's ``/api/izyops`` offers, so IZY Ops connects to
this site with no code of its own: Settings → Websites in IZY Ops, pointed at
``https://<this site>/api/izyops``.

  GET   /health               connectivity probe (no login)
  GET   /products             catalog export (IZY Ops' "Import from website")
  PUT   /products/<sku>       IZY Ops' version of a product's details
  POST  /stock                absolute stock levels by SKU
  GET   /orders               orders changed since a cursor
  PATCH /orders/<id>          a status (and simple details) set in IZY Ops
  POST  /orders/<id>/refund   give part or all of a card payment back

Login: HTTP Basic with ``IZYOPS_API_USERNAME`` / ``IZYOPS_API_PASSWORD``;
with either unset every call is refused. Replies are ``{"success": true,
"data": ...}`` or ``{"success": false, "error": ...}``.

This site's words are translated to the ones IZY Ops knows from family99c:
order statuses (processing → order_confirmed, picked_up → completed …) and a
``pay_status`` worked out from how the order is paid (card online, or cash at
pickup). A product with colours is exported one row per colour, each with its
own SKU, so stock is kept per colour.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

import stripe
from flask import Blueprint, current_app, jsonify, request

from app.extensions import db
from app.models import Category, Order, Product, ProductVariant
from app.services.product_images import own_image_urls, set_product_images

izyops_api_bp = Blueprint('izyops_api', __name__, url_prefix='/api/izyops')

_MAX_PER_PAGE = 500
_CENT = Decimal('0.01')
_MAX_MONEY = Decimal('99999999.99')


# ─── Replies and login ───────────────────────────────────────────────────────

def _ok(data, status=200):
    response = jsonify({'success': True, 'data': data})
    response.status_code = status
    return response


def _err(error, http_status=400, **extra):
    response = jsonify({'success': False, 'error': error, **extra})
    response.status_code = http_status
    return response


def _check_basic_auth() -> bool:
    user = current_app.config.get('IZYOPS_API_USERNAME')
    pwd = current_app.config.get('IZYOPS_API_PASSWORD')
    if not user or not pwd:
        return False
    auth = request.authorization
    if not auth or not auth.username or auth.password is None:
        return False
    return (hmac.compare_digest(auth.username.encode(), user.encode())
            and hmac.compare_digest(auth.password.encode(), pwd.encode()))


def require_izyops_auth(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _check_basic_auth():
            response = _err('Unauthorized', 401)
            response.headers['WWW-Authenticate'] = 'Basic realm="Smart 99c IZY Ops API"'
            return response
        return view(*args, **kwargs)
    return wrapper


# ─── Small helpers ───────────────────────────────────────────────────────────

def _iso(dt):
    """Naive UTC (how the database stores times) → ISO with a Z."""
    return dt.replace(microsecond=0).isoformat() + 'Z' if dt else None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _money(value):
    return float(value) if value is not None else None


def _page_args(default=200):
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', default))
    except (TypeError, ValueError):
        per_page = default
    return page, max(1, min(per_page, _MAX_PER_PAGE))


def _since_arg():
    raw = (request.args.get('since') or '').strip()
    if not raw:
        return None
    try:
        since = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)
    return since


def _paged(query, order_by, key, serialize, default=200):
    page, per_page = _page_args(default)
    total = query.count()
    rows = query.order_by(*order_by).offset((page - 1) * per_page).limit(per_page).all()
    return _ok({
        key: [serialize(r) for r in rows],
        'page': page,
        'per_page': per_page,
        'pages': max(1, -(-total // per_page)),
        'total': total,
        'server_time': _iso(_now()),
    })


# ─── Products ────────────────────────────────────────────────────────────────

def _product_row(product, variant=None):
    images = own_image_urls(product)
    if variant is not None and variant.images:
        images = [variant.images[0].image_url] + [u for u in images if u != variant.images[0].image_url]
    name = product.name if variant is None else f'{product.name} — {variant.color_name}'
    return {
        # A colour has no product id of its own here: IZY Ops finds it by SKU.
        'id': product.id if variant is None else None,
        'name': name,
        'slug': product.slug,
        'sku': variant.sku if variant is not None else product.sku,
        'barcode': product.upc if variant is None else None,
        'brand': None,
        'category': product.category.name if product.category else None,
        'subcategory': None,
        'product_type': None,
        'price': _money(variant.effective_price if variant is not None else product.price),
        'price_without_helium': None,
        'compare_at_price': _money(product.compare_price),
        'cost': _money(variant.cost_price if variant is not None and variant.cost_price is not None
                       else product.cost_price),
        'taxable': True,
        'in_stock': int((variant.stock_quantity if variant is not None else product.stock_quantity) or 0),
        'track_inventory': bool(product.track_inventory),
        'color': variant.color_name if variant is not None else None,
        'size': None,
        'number_figure': None,
        'number_of_balloons': None,
        'helium_required': None,
        'image_url': images[0] if images else None,
        'gallery_urls': images[1:],
        'short_description': product.short_description,
        'description': product.description,
        'sort_order': 0,
        'is_featured': bool(product.is_featured),
        'is_active': bool(product.is_active and (variant is None or variant.is_active)),
        'deleted': False,
        'variant_of': product.sku if variant is not None else None,
        'created_at': _iso(product.created_at),
        'updated_at': _iso(max(filter(None, [product.updated_at, variant.updated_at if variant else None]),
                               default=None)),
    }


@izyops_api_bp.route('/health', methods=['GET'])
def health():
    return _ok({'ok': True, 'server_time': _iso(_now())})


@izyops_api_bp.route('/settings', methods=['POST'])
@require_izyops_auth
def save_settings():
    """``{"catalog_managed_by": "izyops"|"website"|"both"}`` — who manages
    this website's products (IZY Ops → Settings → Websites → Catalog). The
    first time IZY Ops connects, every product is backed up first."""
    from app.helpers import set_site_setting
    from app.services import backups, izyops_mode

    body = request.get_json(silent=True) or {}
    mode = body.get('catalog_managed_by')
    if mode not in izyops_mode.MODES:
        return _err(f"'catalog_managed_by' must be one of {list(izyops_mode.MODES)}")
    backups.full_backup_once()
    set_site_setting(izyops_mode.SETTING_KEY, mode)
    db.session.commit()
    return _ok({'catalog_managed_by': izyops_mode.catalog_mode(),
                'forced_by_server': bool(current_app.config.get('IZYOPS_OWNS_CATALOG'))})


@izyops_api_bp.route('/products', methods=['GET'])
@require_izyops_auth
def list_products():
    """Every product; one row per colour for a product with colours."""
    from app.services.izyops_skus import ensure_skus

    ensure_skus()
    rows = []
    q = Product.query
    since = _since_arg()
    if since is not None:
        q = q.filter(Product.updated_at > since)
    category = (request.args.get('category') or '').strip()
    if category:
        q = q.join(Category).filter(db.func.lower(Category.name) == category.lower())
    for product in q.order_by(Product.id).all():
        if product.variants:
            rows.extend(_product_row(product, v) for v in product.variants)
        else:
            rows.append(_product_row(product))
    page, per_page = _page_args()
    return _ok({
        'products': rows[(page - 1) * per_page: page * per_page],
        'page': page,
        'per_page': per_page,
        'pages': max(1, -(-len(rows) // per_page)),
        'total': len(rows),
        'server_time': _iso(_now()),
    })


@izyops_api_bp.route('/stock', methods=['POST'])
@require_izyops_auth
def set_stock():
    """``{"items": [{"sku", "quantity"}]}`` — IZY Ops' on-hand counts. A SKU
    can be a product's or a colour's. Products that don't track stock are
    reported unmatched and left alone."""
    body = request.get_json(silent=True) or {}
    raw = body.get('items')
    if not isinstance(raw, list):
        return _err("'items' must be a list")
    wanted = {}
    for idx, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get('sku') or '').strip():
            return _err(f"items[{idx}] needs a 'sku'")
        try:
            wanted[str(item['sku']).strip()] = max(0, int(item.get('quantity')))
        except (TypeError, ValueError):
            return _err(f'items[{idx}].quantity must be an integer')

    updated, unmatched = [], []
    if wanted:
        products = {p.sku: p for p in Product.query.filter(Product.sku.in_(list(wanted))).all()}
        variants = {v.sku: v for v in ProductVariant.query.filter(ProductVariant.sku.in_(list(wanted))).all()}
        for sku, qty in wanted.items():
            variant = variants.get(sku)
            product = variant.product if variant is not None else products.get(sku)
            # A product with colours keeps its stock on the colours.
            if (product is None or not product.track_inventory
                    or (variant is None and product.has_variants)):
                unmatched.append(sku)
                continue
            target = variant if variant is not None else product
            if (target.stock_quantity or 0) != qty:
                target.stock_quantity = qty
            updated.append(sku)
        db.session.commit()
    return _ok({'updated': updated, 'unmatched': unmatched})


class _EditError(ValueError):
    """A request that can't be applied; becomes a 400 with nothing changed."""


def _parse_money(key, value, nullable=False):
    if value is None:
        if nullable:
            return None
        raise _EditError(f"'{key}' can't be empty")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _EditError(f"'{key}' must be a dollar amount like \"4.99\"")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        amount = None
    if amount is None or not amount.is_finite() or amount < 0 or amount > _MAX_MONEY:
        raise _EditError(f"'{key}' must be a dollar amount like \"4.99\"; got {value!r}")
    return amount.quantize(_CENT)


def _parse_text(body, key, max_len, nullable=True):
    value = body.get(key)
    if value is not None and not isinstance(value, str):
        raise _EditError(f"'{key}' must be text")
    value = (value or '').strip()
    if not value and not nullable:
        raise _EditError(f"'{key}' can't be empty")
    if max_len and len(value) > max_len:
        raise _EditError(f"'{key}' is longer than {max_len} characters")
    return value or None


def _drop(backup):
    """Nothing changed after all: no backup needed."""
    if backup is None:
        return
    if backup in db.session.new:
        db.session.expunge(backup)
    else:
        db.session.delete(backup)


def _category(name):
    from app.services.product_import import _resolve_category
    return _resolve_category(name, {})[0] if name else None


@izyops_api_bp.route('/products/<path:sku>', methods=['PUT'])
@require_izyops_auth
def upsert_product(sku):
    """Create or update a product from IZY Ops. Keys (all optional except
    ``name``, ``price`` and ``category`` on create): ``external_id``,
    ``name``, ``short_description``, ``description``, ``price``,
    ``compare_at_price``, ``category``, ``barcode``, ``is_active``,
    ``images`` (ordered links, first = main photo). Balloon-only keys are
    accepted and ignored. Stock is never changed here (that's ``/stock``).

    A colour's SKU updates that colour only: its price and whether it's on.
    """
    from app.services.product_import import _unique_slug
    from app.helpers import generate_slug

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err('Send a JSON object')
    sku = sku.strip()
    if not sku or len(sku) > 100:
        return _err('The SKU must be 1-100 characters')
    try:
        fields = {}
        if 'name' in body:
            fields['name'] = _parse_text(body, 'name', 255, nullable=False)
        # An empty description from IZY Ops means "IZY Ops has none", not
        # "erase the website's": text is only ever replaced, never blanked.
        short = _parse_text(body, 'short_description', None) if 'short_description' in body else None
        if short:
            fields['short_description'] = short[:500]
        description = _parse_text(body, 'description', None) if 'description' in body else None
        if description:
            fields['description'] = description
        if 'barcode' in body:
            fields['upc'] = _parse_text(body, 'barcode', 120)
        if 'price' in body:
            fields['price'] = _parse_money('price', body['price'])
        if 'compare_at_price' in body:
            fields['compare_price'] = _parse_money('compare_at_price', body['compare_at_price'], nullable=True)
        if 'is_active' in body:
            if not isinstance(body['is_active'], bool):
                raise _EditError("'is_active' must be true or false")
            fields['is_active'] = body['is_active']
        category = _parse_text(body, 'category', 100) if 'category' in body else None
        urls = None
        if 'images' in body:
            raw = body['images'] or []
            if not isinstance(raw, list) or not all(isinstance(u, str) and u.strip().startswith(('http://', 'https://'))
                                                     for u in raw):
                raise _EditError("'images' must be a list of http(s) links")
            urls = list(dict.fromkeys(u.strip() for u in raw))
    except _EditError as exc:
        return _err(str(exc))

    from app.services import backups, izyops_mode

    if izyops_mode.catalog_mode() == 'website':
        return _err('managed_on_website', 409,
                    message='Products on this website are managed on the website (IZY Ops → Websites → Catalog)')
    # The first time IZY Ops changes anything, every product is saved first.
    backups.full_backup_once()

    variant = ProductVariant.query.filter_by(sku=sku).first()
    if variant is not None:
        backup = backups.before_izyops_change(variant.product)
        updated = []
        if 'price' in fields:
            override = None if fields['price'] == variant.product.price else fields['price']
            if variant.price_override != override:
                variant.price_override = override
                updated.append('price')
        if 'is_active' in fields and variant.is_active != fields['is_active']:
            variant.is_active = fields['is_active']
            updated.append('is_active')
        if not updated:
            _drop(backup)
        db.session.commit()
        return _ok({'product_id': None, 'sku': sku, 'created': False, 'updated_fields': updated,
                    'image_errors': []})

    external_id = body.get('external_id')
    if external_id is not None and (isinstance(external_id, bool) or not isinstance(external_id, int)):
        return _err("'external_id' must be a number or null")
    product = db.session.get(Product, external_id) if external_id is not None else None
    if product is None:
        product = Product.query.filter_by(sku=sku).first()
    elif product.sku != sku:
        clash = Product.query.filter(Product.sku == sku, Product.id != product.id).first()
        if clash is not None:
            return _err('sku_conflict', 409, message=f'SKU {sku!r} already belongs to product #{clash.id}',
                        product_id=clash.id)

    created = product is None
    if created:
        missing = [k for k in ('name', 'price') if fields.get(k) is None] + ([] if category else ['category'])
        if missing:
            return _err(f"A new product needs {', '.join(repr(k) for k in missing)}")
        product = Product(name=fields['name'], slug=_unique_slug(generate_slug(fields['name']) or 'product'),
                          sku=sku, price=fields['price'], stock_quantity=0, track_inventory=True)
        db.session.add(product)
    updated = []
    backup = None if created else backups.before_izyops_change(product)
    if not created and product.sku != sku:
        product.sku = sku
        updated.append('sku')
    for attr, value in fields.items():
        if created or getattr(product, attr) != value:
            setattr(product, attr, value)
            updated.append(attr)
    if category is not None:
        cat = _category(category)
        if product.category_id != cat.id:
            product.category = cat
            updated.append('category')
    if urls is not None and set_product_images(product, urls):
        updated.append('images')
    if created or updated:
        product.updated_at = datetime.utcnow()
    if not updated:
        _drop(backup)
    db.session.commit()
    return _ok({'product_id': product.id, 'sku': product.sku, 'created': created, 'updated_fields': updated,
                'image_errors': []})


# ─── Orders ──────────────────────────────────────────────────────────────────

# This site's order status → the words IZY Ops reads (family99c's).
_STATUS_OUT = {
    'processing': 'order_confirmed',
    'ready': 'ready',
    'shipped': 'ready',
    'picked_up': 'completed',
    'delivered': 'completed',
    'cancelled': 'canceled',
    'no_show': 'canceled',
    'refunded': 'refunded',
}
_FINAL = ('picked_up', 'delivered', 'cancelled', 'no_show', 'refunded')
# Stock went back on the shelf here.
_RESTOCKED = ('cancelled', 'no_show')
MAKING_NOTE = 'Being packed (IZY Ops)'


def pay_status(order):
    """How the order stands for money, in family99c's words."""
    if order.status == 'pending':
        return 'not_paid'
    if order.is_cash:
        return 'paid_instore' if order.status in ('picked_up', 'delivered') else 'not_paid'
    if order.amount_refunded and order.amount_refunded >= order.total:
        return 'refunded'
    return 'paid_online'


def _customer(order):
    addr = order.shipping_address
    if addr is None:
        return order.user.full_name if order.user else None, order.user.email if order.user else None, None
    return addr.full_name.strip() or None, addr.email, addr.phone


def _order_notes(order):
    parts = [order.fulfillment_label]
    if order.is_pickup is False and order.shipping_address is not None:
        parts.append('Deliver to ' + order.shipping_address.full_address)
    if order.is_cash and order.pickup_deadline:
        parts.append('Hold until ' + _iso(order.pickup_deadline))
    if order.notes:
        parts.append(order.notes)
    return ' · '.join(p for p in parts if p)


def serialize_order(order):
    name, email, phone = _customer(order)
    status = _STATUS_OUT.get(order.status, 'order_confirmed')
    return {
        'orderid': order.id,
        'order_number': order.order_number,
        'order_type': 'online',
        'status': status,
        'pay_status': pay_status(order),
        'payment_method': order.payment_method,
        'refunded': order.status == 'refunded',
        'canceled': order.status in _RESTOCKED,
        # Stock comes off the shelf when the order is placed; a cancel or
        # no-show puts it back.
        'inventory_captured': order.status not in _RESTOCKED,
        'customer_name': name,
        'customer_phone': phone,
        'customer_email': email,
        'pickup_option': 'pickup' if order.is_pickup else ('delivery' if order.is_pickup is False else None),
        # Shoppers don't pick a pickup time here; the board shows it as today.
        'pickup_at': None,
        'pickup_reference': order.order_number,
        'notes': _order_notes(order),
        'subtotal': _money(order.subtotal),
        'tax': _money(order.tax),
        'processing_fee': _money(order.service_fee),
        'shipping_cost': _money(order.shipping_cost),
        'discount': _money(order.discount_total),
        'total': _money(order.total),
        'amount_refunded': _money(order.amount_refunded or 0),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'fulfilled_at': _iso(order.picked_up_at or order.delivered_at),
        'cancelled_at': _iso(order.cancelled_at or order.no_show_at),
        'items': [{
            'line_id': item.id,
            'product_id': item.product_id,
            'sku': item.product_sku or (item.variant.sku if item.variant else None)
            or (item.product.sku if item.product else None),
            'name': item.display_name,
            'quantity': int(item.quantity or 0),
            'unit_price': _money(item.unit_price),
            'line_total': _money(item.total_price),
            'helium': None,
        } for item in order.items],
    }


@izyops_api_bp.route('/orders', methods=['GET'])
@require_izyops_auth
def list_orders():
    """Orders changed since ``?since=``, oldest change first. A card order
    still waiting for its payment isn't an order yet and is left out.
    ``?scope=stock`` keeps only orders whose stock is still taken."""
    from app.services.izyops_skus import ensure_skus

    scope = (request.args.get('scope') or 'stock').strip().lower()
    if scope not in ('stock', 'all'):
        return _err("'scope' must be 'stock' or 'all'")
    # Lines name their product by item number, so every product needs one.
    ensure_skus()
    q = Order.query.filter(Order.status != 'pending')
    if scope == 'stock':
        q = q.filter(Order.status.notin_(_RESTOCKED))
    since = _since_arg()
    if since is not None:
        q = q.filter(Order.updated_at > since)
    return _paged(q, (Order.updated_at, Order.id), 'orders', serialize_order)


def _note(order, text, now):
    stamp = now.strftime('%Y-%m-%d %H:%M')
    order.notes = ((order.notes or '').rstrip() + f'\n[{stamp} UTC] {text}').strip()


@izyops_api_bp.route('/orders/<int:order_id>', methods=['PATCH'])
@require_izyops_auth
def update_order(order_id):
    """``{"status": "making"|"ready"|"completed"|"canceled", "actor", "note",
    "edit": {customer_name, customer_phone, customer_email, notes}}``.

    ready emails the customer (as the admin's Ready button does); completed
    is Picked up, or Delivered for a delivery; canceled puts the stock back.
    A finished order isn't changed (409). Changing the items of an order
    isn't supported here (400): do that on this site's admin.
    """
    from app.services.pickup import email_customer, restock

    body = request.get_json(silent=True) or {}
    status = body.get('status')
    edit = body.get('edit')
    if status is None and edit is None:
        return _err("Send a 'status', an 'edit', or both")
    if status is not None and status not in ('making', 'ready', 'completed', 'canceled'):
        return _err("'status' must be one of ['canceled', 'completed', 'making', 'ready']")
    if edit is not None and not isinstance(edit, dict):
        return _err("'edit' must be an object")
    if edit and 'items' in edit:
        return _err("Items can't be changed from IZY Ops on this website — change them in the website's admin")

    order = Order.query.filter(Order.id == order_id, Order.status != 'pending').first()
    if order is None:
        return _err('Not found', 404)

    target = {'making': 'processing', 'ready': 'ready', 'canceled': 'cancelled'}.get(status)
    if status == 'completed':
        target = 'delivered' if order.is_pickup is False else 'picked_up'
    if order.status in _FINAL and (edit or (target and target != order.status)):
        return _err(f'Order is already {order.status}', 409, status=_STATUS_OUT.get(order.status, order.status))

    actor = str(body.get('actor') or 'IZY Ops').strip()[:80]
    note = str(body.get('note') or '').strip()[:300]
    now = _now()

    if edit:
        addr = order.shipping_address
        try:
            if 'customer_name' in edit and addr is not None:
                full = _parse_text(edit, 'customer_name', 200, nullable=False)
                first, _, last = full.partition(' ')
                addr.first_name, addr.last_name = first[:100], (last or '')[:100]
            if 'customer_phone' in edit and addr is not None:
                addr.phone = _parse_text(edit, 'customer_phone', 30)
            if 'customer_email' in edit and addr is not None:
                addr.email = _parse_text(edit, 'customer_email', 255, nullable=False)
        except _EditError as exc:
            db.session.rollback()
            return _err(str(exc))
        _note(order, f'Edited in IZY Ops by {actor}' + (f': {note}' if note else ''), now)

    emailed = None
    if status == 'making':
        if order.status == 'processing' and MAKING_NOTE not in (order.notes or ''):
            _note(order, f'{actor}: {MAKING_NOTE}', now)
    elif target and target != order.status:
        if target == 'cancelled':
            restock(order)
        order.set_status(target, now)
        _note(order, f"{actor}: {order.status_label}" + (f' — {note}' if note else ''), now)
    db.session.commit()
    if status == 'ready' and order.status == 'ready' and order.ready_at == now:
        emailed = email_customer(order, 'ready')
    data = serialize_order(order)
    if emailed is not None:
        data['customer_emailed'] = bool(emailed)
    return _ok(data)


@izyops_api_bp.route('/orders/<int:order_id>/refund', methods=['POST'])
@require_izyops_auth
def refund_order(order_id):
    """``{"amount": "6.00", "reason", "actor", "request_id"}`` — give that
    much back to the card the order was paid with (Stripe). The same request
    sent twice refunds once."""
    body = request.get_json(silent=True) or {}
    try:
        amount = _parse_money('amount', body.get('amount'))
    except _EditError as exc:
        return _err(str(exc))
    if amount <= 0:
        return _err("'amount' must be more than 0")
    order = db.session.get(Order, order_id)
    if order is None or order.status == 'pending':
        return _err('Not found', 404)
    if order.is_cash or not order.stripe_payment_intent_id:
        return _err('not_refundable', 409, message='This order was not paid by card online')
    left = Decimal(order.total) - Decimal(order.amount_refunded or 0)
    if amount > left:
        return _err('not_refundable', 409, message=f'At most ${left:.2f} can still be refunded')

    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY')
    cents = int(amount * 100)
    request_id = str(body.get('request_id') or '').strip()[:60]
    key = f'izyops_refund_{order.id}_{cents}' + (f'_{request_id}' if request_id else '')
    try:
        refund = stripe.Refund.create(
            payment_intent=order.stripe_payment_intent_id,
            amount=cents,
            reason='requested_by_customer',
            metadata={'order_number': order.order_number, 'by': str(body.get('actor') or 'IZY Ops')[:80],
                      'note': str(body.get('reason') or '')[:200]},
            idempotency_key=key,
        )
    except stripe.error.StripeError as exc:
        current_app.logger.warning('Stripe refund for %s failed: %s', order.order_number, exc)
        return _err('stripe_error', 502, message=getattr(exc, 'user_message', None) or str(exc))

    order.amount_refunded = (Decimal(order.amount_refunded or 0) + amount).quantize(_CENT)
    _note(order, f"${amount:.2f} refunded to the card by {str(body.get('actor') or 'IZY Ops')[:80]}"
          + (f" — {body.get('reason')}" if body.get('reason') else ''), _now())
    db.session.commit()
    return _ok({'refund_id': refund.get('id') if hasattr(refund, 'get') else getattr(refund, 'id', None),
                'amount': _money(amount), 'order': serialize_order(order)})
