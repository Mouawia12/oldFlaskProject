import logging
import os
from datetime import timedelta

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from flask_bcrypt import Bcrypt
from flask_mail import Mail

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR,
)

# Core configuration tuned for production defaults
app.config.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///noblepaints.db")
app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
app.config.setdefault("SECRET_KEY", "526af4fbd93bc393a6392db7")
app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(days=7))
app.config.setdefault("DEBUG", False)
app.config.setdefault("ENV", "production")
app.config.setdefault("PREFERRED_URL_SCHEME", "https")

# Mail configuration
app.config.setdefault("MAIL_SERVER", "mail.noblepaints.com.sa")
app.config.setdefault("MAIL_PORT", 465)
app.config.setdefault("MAIL_USE_TLS", False)
app.config.setdefault("MAIL_USE_SSL", True)
app.config.setdefault("MAIL_USERNAME", "info@noblepaints.com.sa")
app.config.setdefault("MAIL_PASSWORD", "m^_EHej(LNG.@@@#*@@@@@@")

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
mail = Mail(app)
ma = Marshmallow(app)

# Reduce logging noise in production
app.logger.setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

from noblepaints import routes  # noqa: E402  (delayed import for app context)
