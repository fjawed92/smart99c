"""Store details shown across the site — edit here to change them everywhere."""

STORE = {
    'name': 'Smart 99¢ Plus',
    'address': '66 NY-109, West Babylon, NY 11704',
    'address_short': '66 NY-109, West Babylon',
    'phone': '516-851-8097',
    'phone_href': '+15168518097',
    'hours': [('Mon–Sat', '9 AM – 9 PM'), ('Sunday', '10 AM – 7 PM')],
    'restock_day': 'Friday',
    'restock_weekday': 4,      # Monday=0 … Friday=4
    'restock_hour': 16,        # 4 PM
    'restock_label': 'Fridays at 4 PM',
    'free_delivery_over': 35,
    'pickup_time': 'about 1 hour',
    # Opening hours by weekday (Monday=0) as (open hour, close hour), 24h clock.
    # Cash-pickup holds end at closing time when they'd otherwise land overnight.
    'open_hours': {0: (9, 21), 1: (9, 21), 2: (9, 21), 3: (9, 21),
                   4: (9, 21), 5: (9, 21), 6: (10, 19)},
    'timezone': 'America/New_York',
}
