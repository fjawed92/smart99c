"""Read-only client for the IzyOps catalog API (/api/v2).

Used by Admin → Products → Pull from IzyOps. Credentials come from env vars:

    IZYOPS_URL          e.g. https://izyops.example.com   (no trailing /api/v2)
    IZYOPS_USERNAME     an IzyOps login (best: a dedicated user for this store)
    IZYOPS_PASSWORD
    IZYOPS_STORE_NAME   store whose prices / stock to use (default "Smart 99c")

Every function returns ``(data, error)`` — exactly one is None — so the admin
page can show a plain-language message instead of crashing when IzyOps is
down or the password changed.
"""
import os
import re

import requests

TIMEOUT = 20
PER_PAGE = 60


def settings():
    return {
        'url': (os.environ.get('IZYOPS_URL') or '').rstrip('/'),
        'username': os.environ.get('IZYOPS_USERNAME') or '',
        'password': os.environ.get('IZYOPS_PASSWORD') or '',
        'store': os.environ.get('IZYOPS_STORE_NAME') or 'Smart 99c',
    }


def is_configured():
    s = settings()
    return bool(s['url'] and s['username'] and s['password'])


def _get(path, params=None):
    s = settings()
    if not is_configured():
        return None, None, 'IzyOps is not connected yet. Add IZYOPS_URL, IZYOPS_USERNAME and IZYOPS_PASSWORD in Render → Environment.'
    base = s['url'] if s['url'].endswith('/api/v2') else s['url'] + '/api/v2'
    try:
        resp = requests.get(base + path, params=params or {}, auth=(s['username'], s['password']), timeout=TIMEOUT)
    except requests.Timeout:
        return None, None, 'IzyOps took too long to answer. Try again in a moment.'
    except requests.RequestException:
        return None, None, 'Could not reach IzyOps. Check IZYOPS_URL and that the IzyOps site is running.'

    if resp.status_code == 401:
        return None, None, 'IzyOps rejected the username or password (IZYOPS_USERNAME / IZYOPS_PASSWORD).'
    if resp.status_code == 403:
        return None, None, f'That IzyOps user has no access to store "{s["store"]}".'
    try:
        body = resp.json()
    except ValueError:
        return None, None, f'IzyOps sent an unexpected response (HTTP {resp.status_code}). Check IZYOPS_URL.'
    if resp.status_code >= 400 or body.get('error'):
        return None, None, str(body.get('error') or f'IzyOps error (HTTP {resp.status_code})')
    return body.get('data'), body.get('meta') or {}, None


def get_facets():
    """{'categories': [...], 'vendors': [...]} for the search dropdowns."""
    data, _, err = _get('/catalog/facets')
    return data, err


def search_products(mode, value, page=1):
    """Search the IzyOps catalog.

    mode: 'keyword' (name / UPC / item code), 'category', 'vendor' (vendor_id)
          or 'upc' (exact barcode; also matches item code).
    Returns (items, meta, error).
    """
    s = settings()
    params = {'store_name': s['store'], 'include_stock': 'true', 'page': max(1, page), 'per_page': PER_PAGE}
    value = (value or '').strip()
    if mode == 'category':
        params['category'] = value
    elif mode == 'vendor':
        params['vendor_id'] = value
    elif mode in ('keyword', 'upc'):
        if not value:
            return [], {}, 'Type something to search for.'
        params['search'] = value
    else:
        return [], {}, 'Unknown search type.'

    items, meta, err = _get('/products', params)
    if err:
        return [], {}, err
    if mode == 'upc':
        exact = [p for p in items if value in (p.get('upc_code'), p.get('item_code'), p.get('carton_upc'))]
        items = exact or items
    for p in items:
        p['image_url'] = absolute_image_url(p.get('image_url'))
    return items, meta, None


def absolute_image_url(url):
    if not url:
        return None
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        return settings()['url'] + url
    return url


# Words that should stay in capitals when tidying IzyOps' ALL-CAPS names.
_KEEP_UPPER = {'TCG', 'ETB', 'RC', 'UV', 'LED', 'USB', 'XL', 'XXL', 'NFL', 'NBA', 'MLB', 'DC', 'II', 'III', 'IV', 'EX', 'GX', 'V', 'VMAX', 'VSTAR', 'PK'}
_REPLACE = {'POKEMON': 'Pokémon', 'ASST': 'Assorted', 'W/': 'with '}


def tidy_name(name):
    """'POKEMON TCG SV BOOSTER PK' → 'Pokémon TCG SV Booster PK' (a starting point the owner can edit)."""
    if not name:
        return ''
    if name != name.upper():
        return name.strip()
    words = []
    for w in re.split(r'(\s+)', name.strip()):
        if not w.strip():
            words.append(w)
            continue
        up = w.upper()
        if up in _REPLACE:
            words.append(_REPLACE[up])
        elif up in _KEEP_UPPER or any(ch.isdigit() for ch in w) or (len(w) <= 2 and w.isalpha() and up not in {'OF', 'TO', 'IN', 'ON', 'OR', 'BY', 'AN', 'AT'}):
            words.append(up)
        else:
            words.append(w.capitalize())
    return ''.join(words)
