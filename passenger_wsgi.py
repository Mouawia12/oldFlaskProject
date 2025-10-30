import os
import sys
from importlib import import_module

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("PYTHON_EGG_CACHE", os.path.join(PROJECT_ROOT, "tmp", "python-eggs"))

module = import_module("noblepaints")

application = getattr(module, "app", None)
if application is None and hasattr(module, "create_app"):
    application = module.create_app()

if application is None:
    raise RuntimeError("Unable to locate Flask application instance in noblepaints package.")
