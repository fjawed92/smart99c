"""Storefront bits the owner edits in Admin → Banner & Filters.

- The scrolling news banner at the top of every page.
- The "Shop by price" buttons on the home and shop pages.

Both are saved as JSON lists in SiteSettings. Until the owner saves them
the site shows the starter lists below, so nothing changes on deploy.
"""
import json
from decimal import Decimal, InvalidOperation

from app.helpers import get_site_setting, set_site_setting
from app.store import STORE

TICKER_KEY = 'ticker_messages'
FILTERS_KEY = 'shop_filters'
MAX_TEXT = 160


def default_ticker():
    # The old single "Announcement" (Admin → Settings) leads the list.
    announcement = get_site_setting('announcement', '').strip()
    texts = ([announcement] if announcement else []) + [
        f"Pokémon TCG restock {STORE['restock_label']}",
        'Same-day pickup in West Babylon',
        'Board games, plush & action figures',
        'Sleeves, binders & toploaders',
    ]
    return [{'text': t, 'active': True} for t in texts]


DEFAULT_FILTERS = [
    {'kind': 'price', 'label': '99¢', 'note': '& under', 'min': None, 'max': '0.99', 'active': True},
    {'kind': 'price', 'label': '$1.99', 'note': '& under', 'min': None, 'max': '1.99', 'active': True},
    {'kind': 'price', 'label': '$4.99', 'note': '& under', 'min': None, 'max': '4.99', 'active': True},
    {'kind': 'price', 'label': '$9.99', 'note': '& under', 'min': None, 'max': '9.99', 'active': True},
    {'kind': 'sale', 'label': 'On sale', 'note': '', 'min': None, 'max': None, 'active': True},
]


def _load(key):
    raw = get_site_setting(key, '')
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _money(value):
    """'1.99' / '$1.99' / '99¢' style input → '1.99' string, or None."""
    text = str(value or '').strip().replace('$', '').replace(',', '')
    cents = text.endswith('¢') or text.endswith('c')
    text = text.rstrip('¢c').strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if cents:
        amount = amount / 100
    if amount < 0:
        return None
    return f'{amount.quantize(Decimal("0.01"))}'


# ─── Scrolling banner ────────────────────────────────────────────────────────

def ticker_messages():
    """Every banner message, shown or hidden, in the owner's order."""
    data = _load(TICKER_KEY)
    if data is None:
        return default_ticker()
    out = []
    for item in data:
        if isinstance(item, dict) and str(item.get('text', '')).strip():
            out.append({'text': str(item['text']).strip()[:MAX_TEXT],
                        'active': bool(item.get('active', True))})
    return out


def save_ticker_messages(texts, actives):
    """Save from the admin form: parallel lists of text and '1'/'' flags."""
    items = []
    for text, active in zip(texts, actives):
        text = (text or '').strip()[:MAX_TEXT]
        if text:
            items.append({'text': text, 'active': active == '1'})
    set_site_setting(TICKER_KEY, json.dumps(items))
    return items


# ─── Shop-by-price buttons ───────────────────────────────────────────────────

def shop_filters(include_hidden=False):
    data = _load(FILTERS_KEY)
    if data is None:
        data = DEFAULT_FILTERS
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        f = {
            'kind': 'sale' if item.get('kind') == 'sale' else 'price',
            'label': str(item.get('label') or '').strip()[:40],
            'note': str(item.get('note') or '').strip()[:40],
            'min': _money(item.get('min')),
            'max': _money(item.get('max')),
            'active': bool(item.get('active', True)),
        }
        if f['kind'] == 'price' and f['min'] is None and f['max'] is None:
            continue
        if not f['label']:
            f['label'] = 'On sale' if f['kind'] == 'sale' else _auto_label(f)
        if include_hidden or f['active']:
            out.append(f)
    return out


def _auto_label(f):
    def show(m):
        d = Decimal(m)
        return f'{int(d * 100)}¢' if d < 1 else f'${m}'
    if f['min'] and f['max']:
        return f"{show(f['min'])}–{show(f['max'])}"
    return show(f['max'] or f['min'])


def save_shop_filters(kinds, labels, notes, mins, maxes, actives):
    """Save from the admin form. Returns (saved list, problems)."""
    items, problems = [], []
    for i, kind in enumerate(kinds):
        kind = 'sale' if kind == 'sale' else 'price'
        label = (labels[i] if i < len(labels) else '').strip()[:40]
        note = (notes[i] if i < len(notes) else '').strip()[:40]
        lo = _money(mins[i] if i < len(mins) else '')
        hi = _money(maxes[i] if i < len(maxes) else '')
        active = (actives[i] if i < len(actives) else '') == '1'
        if kind == 'price':
            if lo is None and hi is None:
                if label or note:
                    problems.append(f'"{label or note}" needs a price, so it was left out.')
                continue
            if lo is not None and hi is not None and Decimal(lo) > Decimal(hi):
                lo, hi = hi, lo
        else:
            lo = hi = None
        items.append({'kind': kind, 'label': label, 'note': note,
                      'min': lo, 'max': hi, 'active': active})
    set_site_setting(FILTERS_KEY, json.dumps(items))
    return items, problems


def reset(key):
    set_site_setting(key, '')
