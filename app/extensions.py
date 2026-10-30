from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()
mail = Mail()
# No global limit: the forms people could hammer (login, sign-up, password
# change) opt in with @limiter.limit. Memory storage is enough for the single
# gunicorn process in the Procfile.
limiter = Limiter(key_func=get_remote_address)
