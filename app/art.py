"""Built-in product illustrations.

Shown wherever a product or category has no photo, so the store never
displays an empty grey box. The shape is picked from keywords in the
product / category name (packs, boxes, board games, plush…).
Generic drawings only — no trademarked artwork.
"""
import math

from markupsafe import Markup

RED = '#E8334A'
YELLOW = '#F5C518'
NAVY = '#1A1A2E'

# (keywords, art kind) — first match wins.
_KEYWORDS = [
    (('single', 'graded', ' ex ', ' vmax', ' gx'), 'single'),
    (('booster pack', 'pack', 'blister'), 'pack'),
    (('elite trainer', 'etb', 'box', 'bundle', 'tin', 'deck', 'collection'), 'box'),
    (('sleeve', 'toploader', 'binder', 'card storage', 'accessor'), 'sleeve'),
    (('pokemon', 'pokémon', 'tcg', 'trading card', 'magic', 'yu-gi-oh', 'card'), 'pack'),
    (('plush', 'stuffed', 'squish', 'teddy'), 'plush'),
    (('figure', 'action', 'doll', 'hero'), 'figure'),
    (('puzzle', 'jigsaw'), 'puzzle'),
    (('rc ', 'car', 'truck', 'vehicle', 'outdoor', 'ball'), 'car'),
    (('game', 'uno', 'jenga', 'monopoly', 'chess', 'board'), 'game'),
]


def _star(cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = math.pi / 5 * i - math.pi / 2
        rr = r * 0.45 if i % 2 else r
        pts.append(f'{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}')
    return f'<polygon points="{" ".join(pts)}" fill="{fill}"/>'


def _body(kind, t):
    R, Y, N = RED, YELLOW, NAVY
    if kind == 'pack':
        return (f'<path d="M58 22h84l6 10-6 8H58l-6-8z" fill="{N}"/>'
                f'<rect x="56" y="38" width="88" height="124" rx="6" fill="{t}"/>'
                f'<rect x="56" y="38" width="44" height="124" rx="6" fill="#fff" opacity=".18"/>'
                f'<circle cx="100" cy="96" r="30" fill="{Y}"/>{_star(100, 96, 22, N)}'
                f'<rect x="68" y="136" width="64" height="10" rx="3" fill="{N}" opacity=".85"/>'
                f'<path d="M58 160h84l6 8-6 10H58l-6-10z" fill="{N}"/>')
    if kind == 'box':
        return (f'<path d="M34 70l66-30 66 30v78l-66 30-66-30z" fill="{N}"/>'
                f'<path d="M34 70l66 30 66-30-66-30z" fill="{t}"/>'
                f'<path d="M100 100v78l66-30V70z" fill="{t}" opacity=".7"/>'
                f'{_star(66, 128, 18, Y)}')
    if kind == 'single':
        return (f'<g transform="rotate(-6 100 100)"><rect x="52" y="24" width="96" height="140" rx="7" fill="{Y}"/>'
                f'<rect x="59" y="31" width="82" height="126" rx="4" fill="#fff"/>'
                f'<rect x="64" y="46" width="72" height="54" rx="3" fill="{t}"/>'
                f'<circle cx="100" cy="73" r="16" fill="{Y}"/><path d="M96 60l-8 16h10l-4 12 14-18h-10l6-10z" fill="{N}"/>'
                f'<rect x="64" y="36" width="48" height="6" rx="2" fill="{N}"/><circle cx="130" cy="39" r="5" fill="{R}"/>'
                f'<rect x="64" y="108" width="72" height="5" rx="2" fill="{N}" opacity=".6"/>'
                f'<rect x="64" y="118" width="56" height="5" rx="2" fill="{N}" opacity=".35"/>'
                f'<rect x="64" y="134" width="30" height="12" rx="3" fill="{N}"/></g>')
    if kind == 'game':
        return (f'<rect x="30" y="52" width="140" height="100" rx="8" fill="{t}"/>'
                f'<rect x="30" y="140" width="140" height="14" rx="4" fill="{N}"/>'
                f'<circle cx="72" cy="96" r="22" fill="{Y}"/><rect x="104" y="80" width="50" height="10" rx="3" fill="#fff"/>'
                f'<rect x="104" y="98" width="36" height="10" rx="3" fill="#fff" opacity=".7"/>'
                f'<rect x="120" y="30" width="30" height="30" rx="6" fill="#fff" stroke="{N}" stroke-width="3" transform="rotate(14 135 45)"/>'
                f'<circle cx="130" cy="42" r="3" fill="{N}"/><circle cx="140" cy="50" r="3" fill="{N}"/>')
    if kind == 'plush':
        return (f'<circle cx="66" cy="62" r="20" fill="{t}"/><circle cx="134" cy="62" r="20" fill="{t}"/>'
                f'<circle cx="66" cy="62" r="10" fill="{Y}"/><circle cx="134" cy="62" r="10" fill="{Y}"/>'
                f'<ellipse cx="100" cy="112" rx="60" ry="56" fill="{t}"/>'
                f'<ellipse cx="100" cy="128" rx="30" ry="22" fill="#fff" opacity=".9"/>'
                f'<circle cx="80" cy="102" r="7" fill="{N}"/><circle cx="120" cy="102" r="7" fill="{N}"/>'
                f'<ellipse cx="100" cy="120" rx="8" ry="5" fill="{N}"/>')
    if kind == 'figure':
        return (f'<rect x="46" y="20" width="108" height="160" rx="10" fill="{N}"/>'
                f'<rect x="56" y="30" width="88" height="118" rx="44" fill="#fff" opacity=".12"/>'
                f'<circle cx="100" cy="62" r="16" fill="{Y}"/>'
                f'<path d="M80 82h40l6 44h-14l-2 26h-8l-2-22-2 22h-8l-2-26H74z" fill="{t}"/>'
                f'<rect x="56" y="156" width="88" height="14" rx="3" fill="{t}"/>')
    if kind == 'puzzle':
        return (f'<path d="M40 60h40a14 14 0 1 1 28 0h40v40a14 14 0 1 0 0 28v40H108a14 14 0 1 0-28 0H40v-40a14 14 0 1 1 0-28z" fill="{t}"/>'
                f'<path d="M40 60h40a14 14 0 1 1 28 0h-8v40H40z" fill="{Y}" opacity=".9"/>')
    if kind == 'car':
        return (f'<path d="M30 120l18-34h70l30 28h22v26H30z" fill="{t}"/>'
                f'<path d="M58 92h26v22H48zm32 0h24l22 22H90z" fill="#fff" opacity=".85"/>'
                f'<circle cx="64" cy="142" r="18" fill="{N}"/><circle cx="64" cy="142" r="7" fill="{Y}"/>'
                f'<circle cx="146" cy="142" r="18" fill="{N}"/><circle cx="146" cy="142" r="7" fill="{Y}"/>')
    if kind == 'sleeve':
        return (f'<rect x="66" y="34" width="84" height="120" rx="6" fill="{t}" opacity=".35"/>'
                f'<rect x="56" y="44" width="84" height="120" rx="6" fill="{t}" opacity=".6"/>'
                f'<rect x="46" y="54" width="84" height="120" rx="6" fill="{t}"/>'
                f'<rect x="46" y="54" width="84" height="18" rx="6" fill="#fff" opacity=".35"/>')
    # default: gift / general toy
    return (f'<rect x="44" y="84" width="112" height="84" rx="6" fill="{t}"/>'
            f'<rect x="36" y="64" width="128" height="26" rx="5" fill="{N}"/>'
            f'<rect x="92" y="64" width="16" height="104" fill="{Y}"/>'
            f'<path d="M100 64c-10-22-38-26-38-8 0 10 22 8 38 8zm0 0c10-22 38-26 38-8 0 10-22 8-38 8z" fill="{Y}"/>')


def art_kind(*texts):
    hay = ' ' + ' '.join(t for t in texts if t).lower() + ' '
    for words, kind in _KEYWORDS:
        if any(w in hay for w in words):
            return kind
    return 'gift'


def art_svg(kind, tint=RED, label=''):
    return Markup(
        '<svg class="art-svg" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{Markup.escape(label)}">{_body(kind, tint)}</svg>'
    )


def product_art(product, tint=None):
    """Illustration for a product without a photo."""
    cat = product.category.name if getattr(product, 'category', None) else ''
    kind = art_kind(product.name, cat)
    palette = (RED, YELLOW, NAVY)
    return art_svg(kind, tint or palette[(product.id or 0) % 3 if kind != 'pack' else 0], product.name)


def category_art(category, tint=RED):
    return art_svg(art_kind(category.name, category.description or ''), tint, category.name)
