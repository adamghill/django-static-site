from copy import deepcopy
from pathlib import Path

from django.conf import settings

from coltrane import _get_default_template_settings
from coltrane.config.settings import DEFAULT_COLTRANE_SETTINGS


def pytest_configure():
    base_dir = Path(".")

    templates = deepcopy(_get_default_template_settings(base_dir))
    coltrane_settings = deepcopy(DEFAULT_COLTRANE_SETTINGS)
    coltrane_settings["SITE"] = "http://localhost"

    settings.configure(
        BASE_DIR=base_dir,
        SECRET_KEY="this-is-a-secret",
        TEMPLATES=templates,
        ROOT_URLCONF="coltrane.urls",
        INSTALLED_APPS=[
            "django.contrib.sitemaps",
            "django_fastdev.apps.FastDevConfig",
            "coltrane",
        ],
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.dummy.DummyCache",
            }
        },
        STATIC_ROOT=base_dir / "output" / "static",
        SETTINGS_MODULE="coltrane",
        COLTRANE=coltrane_settings,
    )
