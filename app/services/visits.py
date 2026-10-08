"""Built-in visitor tracking for Admin → Visitors.

Each storefront page view (and add-to-cart / order) becomes one SiteVisit row.
Shoppers are told apart by a random id in their session cookie; no IP address,
name or email is stored. Admins, bots and non-page requests are skipped.
Tracking never breaks a page: any database error is logged and ignored.
"""
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import current_app, g, request, session
from flask_login import current_user

from app.extensions import db

STORE_TZ = ZoneInfo('America/New_York')

# Storefront pages worth counting (JSON endpoints, admin and static are left out).
TRACKED_ENDPOINTS = {
    'main.index': 'Home',
    'main.about': 'About',
    'main.contact': 'Contact',
    'shop.shop': 'Shop',
    'shop.shop_category': 'Category',
    'shop.product_detail': 'Product',
    'cart.cart': 'Cart',
    'checkout.checkout': 'Checkout',
    'checkout.order_confirmation': 'Order confirmation',
}

BOT_WORDS = ('bot', 'spider', 'crawl', 'slurp', 'facebookexternalhit', 'preview',
             'headless', 'python-requests', 'curl', 'wget', 'monitor', 'uptime')

# Known referrer domains → friendly source name.
SOURCES = [
    ('google.', 'Google'), ('bing.', 'Bing'), ('yahoo.', 'Yahoo'),
    ('duckduckgo.', 'DuckDuckGo'), ('facebook.', 'Facebook'), ('fb.', 'Facebook'),
    ('instagram.', 'Instagram'), ('tiktok.', 'TikTok'), ('youtube.', 'YouTube'),
    ('twitter.', 'X / Twitter'), ('t.co', 'X / Twitter'), ('x.com', 'X / Twitter'),
    ('pinterest.', 'Pinterest'), ('reddit.', 'Reddit'), ('yelp.', 'Yelp'),
    ('nextdoor.', 'Nextdoor'), ('chatgpt.', 'ChatGPT'), ('openai.', 'ChatGPT'),
]

RETENTION_DAYS = 400


def _is_bot(ua):
    ua = (ua or '').lower()
    return not ua or any(word in ua for word in BOT_WORDS)


def _device(ua):
    ua = (ua or '').lower()
    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        return 'Tablet'
    if 'mobi' in ua or 'iphone' in ua or 'android' in ua:
        return 'Mobile'
    return 'Desktop'


def source_name(referrer_host):
    """Turn a stored referrer host into a label like 'Google' or 'Direct'."""
    if not referrer_host:
        return 'Direct / typed in'
    for needle, name in SOURCES:
        if needle in referrer_host:
            return name
    return referrer_host


def _referrer():
    """Outside site the visitor came from, or None (direct, or clicked within the site)."""
    utm = request.args.get('utm_source', '').strip().lower()
    if utm:
        return utm[:120]
    ref = request.referrer
    if not ref:
        return None
    host = (urlparse(ref).hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    own = (request.host.split(':')[0] or '').lower()
    if own.startswith('www.'):
        own = own[4:]
    if not host or host == own:
        return None
    return host[:120]


def _visitor_id():
    vid = session.get('vid')
    if not vid:
        vid = secrets.token_hex(8)
        session['vid'] = vid
    return vid


def _should_track():
    if _is_bot(request.headers.get('User-Agent')):
        return False
    if request.headers.get('DNT') == '1' and current_app.config.get('VISITS_RESPECT_DNT'):
        return False
    try:
        if current_user.is_authenticated and current_user.is_admin:
            return False
    except Exception:
        pass
    return True


def track_event(event, product_id=None, order_total=None):
    """Record an action (add_to_cart, order) for the current shopper."""
    if not _should_track():
        return
    _save(event=event, endpoint=request.endpoint, product_id=product_id,
          order_total=order_total)


def _save(**fields):
    try:
        visit = _new_visit(**fields)
        db.session.add(visit)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Could not record site visit')


def _new_visit(event, endpoint, product_id=None, order_total=None,
               search=None, search_results=None):
    from app.models import SiteVisit
    ua = request.headers.get('User-Agent')
    return SiteVisit(
        visitor_id=_visitor_id(),
        event=event,
        endpoint=endpoint,
        path=request.path[:300],
        product_id=product_id,
        referrer=_referrer() if event == 'view' else None,
        device=_device(ua),
        search=search,
        search_results=search_results,
        order_total=order_total,
    )


def record_page_view(response):
    """after_request hook: count successful storefront page views."""
    if (request.method == 'GET'
            and response.status_code == 200
            and request.endpoint in TRACKED_ENDPOINTS
            and response.mimetype == 'text/html'
            and _should_track()):
        search = (request.args.get('q') or '').strip().lower()[:120] or None
        _save(event='view', endpoint=request.endpoint,
              product_id=g.get('visit_product_id'),
              search=search,
              search_results=g.get('visit_search_results') if search else None)
    return response


def purge_old_visits():
    """Delete visits older than RETENTION_DAYS. Returns rows removed."""
    from app.models import SiteVisit
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    removed = SiteVisit.query.filter(SiteVisit.created_at < cutoff).delete()
    db.session.commit()
    return removed


# ─── Report for Admin → Visitors ─────────────────────────────────────────────

def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0


def build_report(days=30, now=None):
    """Everything the Visitors page shows, for the last `days` days (store time)."""
    from app.models import SiteVisit, Product

    now = now or datetime.utcnow()
    local_today = now.replace(tzinfo=ZoneInfo('UTC')).astimezone(STORE_TZ).date()
    first_day = local_today - timedelta(days=days - 1)
    start_local = datetime.combine(first_day, datetime.min.time(), STORE_TZ)
    start_utc = start_local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)

    # Same-length period just before, for "vs previous" comparisons.
    prev_start_utc = start_utc - timedelta(days=days)

    rows = db.session.query(
        SiteVisit.created_at, SiteVisit.visitor_id, SiteVisit.event,
        SiteVisit.endpoint, SiteVisit.path, SiteVisit.product_id,
        SiteVisit.referrer, SiteVisit.device, SiteVisit.search,
        SiteVisit.search_results, SiteVisit.order_total,
    ).filter(SiteVisit.created_at >= start_utc).all()

    prev = db.session.query(SiteVisit.visitor_id, SiteVisit.event).filter(
        SiteVisit.created_at >= prev_start_utc, SiteVisit.created_at < start_utc).all()

    views = [r for r in rows if r.event == 'view']
    visitors = {r.visitor_id for r in views}
    orders = [r for r in rows if r.event == 'order']
    order_visitors = {r.visitor_id for r in orders}
    revenue = sum((r.order_total or 0) for r in orders)

    prev_visitors = {r.visitor_id for r in prev if r.event == 'view'}
    prev_orders = sum(1 for r in prev if r.event == 'order')

    # Visitors seen before this period count as returning.
    returning = set()
    if visitors:
        seen_before = db.session.query(SiteVisit.visitor_id).filter(
            SiteVisit.created_at < start_utc,
            SiteVisit.visitor_id.in_(list(visitors))).distinct().all()
        returning = {v for (v,) in seen_before}

    def local(dt):
        return dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(STORE_TZ)

    # Daily unique visitors.
    per_day = defaultdict(set)
    for r in views:
        per_day[local(r.created_at).date()].add(r.visitor_id)
    daily = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        daily.append({'date': day, 'visitors': len(per_day.get(day, ()))})

    # Busiest hours and weekdays (unique visitors).
    hour_sets = defaultdict(set)
    weekday_sets = defaultdict(set)
    for r in views:
        t = local(r.created_at)
        hour_sets[t.hour].add(r.visitor_id)
        weekday_sets[t.weekday()].add((t.date(), r.visitor_id))
    hours = [{'hour': h, 'visitors': len(hour_sets.get(h, ()))} for h in range(24)]
    weekday_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    weekdays = [{'day': weekday_names[d], 'visitors': len(weekday_sets.get(d, ()))}
                for d in range(7)]

    # Where visitors come from — first known source per visitor.
    first_source = {}
    for r in sorted(views, key=lambda r: r.created_at):
        if r.visitor_id not in first_source:
            first_source[r.visitor_id] = source_name(r.referrer)
        elif r.referrer and first_source[r.visitor_id] == 'Direct / typed in':
            first_source[r.visitor_id] = source_name(r.referrer)
    source_counts = Counter(first_source.values())
    source_orders = Counter(first_source.get(v) for v in order_visitors if v in first_source)
    sources = [{'name': name, 'visitors': n, 'orders': source_orders.get(name, 0)}
               for name, n in source_counts.most_common(10)]

    # Devices (per visitor, last device seen).
    device_of = {}
    for r in views:
        device_of[r.visitor_id] = r.device or 'Desktop'
    device_counts = Counter(device_of.values())
    devices = [{'name': name, 'visitors': n, 'pct': _pct(n, len(visitors))}
               for name, n in device_counts.most_common()]

    # Pages.
    page_views = Counter()
    page_visitors = defaultdict(set)
    for r in views:
        label = TRACKED_ENDPOINTS.get(r.endpoint, r.endpoint)
        if r.endpoint == 'shop.shop_category':
            label = 'Category: ' + r.path.rsplit('/', 1)[-1].replace('-', ' ').title()
        if r.endpoint == 'shop.product_detail':
            label = 'Product pages (all)'
        page_views[label] += 1
        page_visitors[label].add(r.visitor_id)
    pages = [{'name': name, 'views': n, 'visitors': len(page_visitors[name])}
             for name, n in page_views.most_common(12)]

    # Products: viewed → added to cart → ordered (unique visitors).
    p_view = defaultdict(set)
    p_cart = defaultdict(set)
    for r in rows:
        if not r.product_id:
            continue
        if r.event == 'view':
            p_view[r.product_id].add(r.visitor_id)
        elif r.event == 'add_to_cart':
            p_cart[r.product_id].add(r.visitor_id)
    product_ids = set(p_view) | set(p_cart)
    products_by_id = {p.id: p for p in Product.query.filter(Product.id.in_(product_ids)).all()} \
        if product_ids else {}
    product_rows = []
    for pid in product_ids:
        product = products_by_id.get(pid)
        if not product:
            continue
        v, c = len(p_view[pid]), len(p_cart[pid])
        product_rows.append({'product': product, 'viewers': v, 'carted': c,
                             'cart_rate': _pct(c, v)})
    top_products = sorted(product_rows, key=lambda x: (-x['viewers'], -x['carted']))[:15]
    # Popular but rarely added to cart — price or photo may need a look.
    not_converting = sorted([p for p in product_rows if p['viewers'] >= 5 and p['cart_rate'] < 10],
                            key=lambda x: -x['viewers'])[:8]

    # Searches.
    search_counts = Counter()
    search_zero = {}
    for r in views:
        if r.search:
            search_counts[r.search] += 1
            if r.search_results is not None:
                search_zero[r.search] = r.search_results == 0
    searches = [{'term': term, 'count': n, 'no_results': search_zero.get(term, False)}
                for term, n in search_counts.most_common(15)]
    missed_searches = [s for s in searches if s['no_results']]

    # Shopping funnel (unique visitors reaching each step).
    product_viewers = {r.visitor_id for r in views if r.endpoint == 'shop.product_detail'}
    carters = {r.visitor_id for r in rows if r.event == 'add_to_cart'}
    checkout_visitors = {r.visitor_id for r in views if r.endpoint == 'checkout.checkout'}
    total = len(visitors)
    funnel = []
    for label, group in [('Visited the site', visitors),
                         ('Looked at a product', product_viewers),
                         ('Added to cart', carters),
                         ('Started checkout', checkout_visitors),
                         ('Placed an order', order_visitors)]:
        funnel.append({'label': label, 'visitors': len(group), 'pct': _pct(len(group), total)})

    tips = _tips(total, hours, weekdays, sources, devices, missed_searches,
                 not_converting, funnel)

    return {
        'tips': tips,
        'days': days,
        'first_day': first_day,
        'last_day': local_today,
        'visitors': total,
        'prev_visitors': len(prev_visitors),
        'visitors_change': _pct(total - len(prev_visitors), len(prev_visitors)) if prev_visitors else None,
        'page_views': len(views),
        'pages_per_visitor': round(len(views) / total, 1) if total else 0,
        'new_visitors': total - len(returning),
        'returning_visitors': len(returning),
        'orders': len(orders),
        'prev_orders': prev_orders,
        'revenue': revenue,
        'conversion': _pct(len(order_visitors), total),
        'daily': daily,
        'daily_max': max([d['visitors'] for d in daily] + [1]),
        'hours': hours,
        'hours_max': max([h['visitors'] for h in hours] + [1]),
        'weekdays': weekdays,
        'weekdays_max': max([w['visitors'] for w in weekdays] + [1]),
        'sources': sources,
        'sources_max': max([s['visitors'] for s in sources] + [1]),
        'devices': devices,
        'pages': pages,
        'top_products': top_products,
        'not_converting': not_converting,
        'searches': searches,
        'missed_searches': missed_searches,
        'funnel': funnel,
    }


def _hour_label(h):
    return f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}"


def _tips(total, hours, weekdays, sources, devices, missed_searches, not_converting, funnel):
    """Plain-English takeaways for the store owner."""
    if total < 5:
        return []
    tips = []
    best_hour = max(hours, key=lambda h: h['visitors'])
    best_day = max(weekdays, key=lambda w: w['visitors'])
    tips.append(f"Most shoppers visit around {_hour_label(best_hour['hour'])}, and "
                f"{best_day['day']} is your busiest day. Post on social media or send "
                f"deals just before then.")
    mobile = next((d for d in devices if d['name'] == 'Mobile'), None)
    if mobile and mobile['pct'] >= 50:
        tips.append(f"{mobile['pct']:.0f}% of visitors are on a phone. Check new products "
                    f"look good on a phone first.")
    if sources:
        top = sources[0]
        tips.append(f"Your top source of visitors is {top['name']} "
                    f"({top['visitors']} visitors).")
    if missed_searches:
        terms = ', '.join(f"\u201c{s['term']}\u201d" for s in missed_searches[:3])
        tips.append(f"People searched for {terms} and found nothing. "
                    f"These could be products worth stocking or adding online.")
    if not_converting:
        p = not_converting[0]
        tips.append(f"\u201c{p['product'].name}\u201d gets a lot of views but few add it "
                    f"to their cart. Check its price, photo or description.")
    carted = funnel[2]['visitors']
    ordered = funnel[4]['visitors']
    if carted >= 5 and ordered / carted < 0.3:
        tips.append(f"{carted - ordered} of {carted} shoppers who added something to "
                    f"their cart did not order. A free-pickup message or a small "
                    f"first-order deal can help.")
    return tips
