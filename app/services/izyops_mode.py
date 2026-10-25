"""Who manages this website's products: IZY Ops, the website, or both.

IZY Ops sends its choice (Settings → Websites → Catalog) to POST
/api/izyops/settings, and it is kept as a site setting:

  izyops   product pages here are view-only; IZY Ops sends every change
  website  product pages here are the only place to edit; IZY Ops sends
           stock only
  both     either place; IZY Ops sends its changes and reads the ones made
           here; the latest change wins

Before IZY Ops has said anything it is 'both': nothing here is locked, and
IZY Ops' product updates are taken. The IZYOPS_OWNS_CATALOG=1 environment
setting forces 'izyops'.
"""
from flask import current_app

from app.helpers import get_site_setting

MODES = ('izyops', 'website', 'both')
SETTING_KEY = 'izyops_catalog_managed_by'


def catalog_mode():
    if current_app.config.get('IZYOPS_OWNS_CATALOG'):
        return 'izyops'
    value = (get_site_setting(SETTING_KEY, '') or '').strip()
    return value if value in MODES else 'both'


def locked():
    """True when product pages here are view-only."""
    return catalog_mode() == 'izyops'
