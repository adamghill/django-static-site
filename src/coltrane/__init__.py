import logging
import sys
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from os import environ, getcwd, getenv
from pathlib import Path
from typing import Any

from django import setup as django_setup
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.core.management import execute_from_command_line
from dotenv import load_dotenv

from coltrane.config.settings import DEFAULT_COLTRANE_SETTINGS, get_config
from coltrane.module_finder import (
    is_django_compressor_installed,
    is_django_unicorn_installed,
    is_module_available,
    is_unicorn_module_available,
    is_whitenoise_installed,
)
from coltrane.utils import dict_merge

logger = logging.getLogger(__name__)

__all__ = [
    "initialize",
    "run",
]


DEFAULT_CACHES_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

DEFAULT_MIDDLEWARE = [
    "coltrane.middleware.IsSecureMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.http.ConditionalGetMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


DEFAULT_INSTALLED_APPS = [
    "django.contrib.humanize",
    "django.contrib.sitemaps",
    "django.contrib.staticfiles",
    "django_fastdev",
    "coltrane",
]

COLTRANE_SETTINGS_THAT_ARE_ARRAYS = ("EXTRA_FILE_NAMES",)
COLTRANE_SETTINGS_THAT_ARE_BOOLEANS = (
    "DISABLE_WILDCARD_TEMPLATES",
    "IS_SECURE",
    "DATA_JSON5",
)


def _get_current_command():
    if len(sys.argv) > 1:
        return sys.argv[1]


def _get_base_dir(base_dir: str | Path | None) -> Path:
    """
    Gets the base directory.
    """

    if base_dir is None:
        cwd = getcwd()

        base_dir = cwd / Path("sites")

        if not base_dir.exists():
            base_dir = cwd / Path("site")

            if not base_dir.exists():
                base_dir = Path(".")

    if isinstance(base_dir, str):
        base_dir = Path(base_dir)

    return base_dir


def _merge_installed_apps(django_settings: dict[str, Any], installed_apps: list[str]) -> list[str]:
    """
    Gets the installed apps from the passed-in settings and adds `coltrane` to it.
    """

    if "INSTALLED_APPS" in django_settings:
        installed_apps = list(django_settings["INSTALLED_APPS"]) + installed_apps

        # Remove INSTALLED_APPS from django_settings since it overrides the default settings
        # that gets merged later
        del django_settings["INSTALLED_APPS"]

    return installed_apps


def _get_caches(django_settings: dict[str, Any]) -> dict:
    """Gets the configured cache. Defaults to the dummy cache."""

    caches = django_settings.get("CACHES", DEFAULT_CACHES_SETTINGS)

    if "default" not in caches:
        caches = DEFAULT_CACHES_SETTINGS

    if coltrane_cache := getenv("CACHE"):
        if coltrane_cache == "dummy":
            logging.info("Use dummy cache")

            caches["default"] = {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
        elif coltrane_cache == "memory":
            logging.info("Use local memory cache")

            caches["default"] = {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": getenv("CACHE_LOCATION", "unique-snowflake"),
            }
        elif coltrane_cache in ["filesystem", "redis", "memcache"]:
            if cache_locations := _get_from_env("CACHE_LOCATION"):
                cache_backend = None

                if coltrane_cache == "filesystem":
                    logging.info("Use filesystem cache")

                    cache_backend = "django.core.cache.backends.filebased.FileBasedCache"
                    cache_locations = cache_locations[0]
                elif coltrane_cache == "redis":
                    logging.info("Use redis cache")

                    cache_backend = "django.core.cache.backends.redis.RedisCache"
                elif coltrane_cache == "memcache":
                    logging.info("Use memcache cache")

                    cache_backend = "django.core.cache.backends.memcached.PyMemcacheCache"

                caches["default"] = {
                    "BACKEND": cache_backend,
                    "LOCATION": cache_locations,
                }
            else:
                raise Exception(f"Missing CACHE_LOCATION: '{coltrane_cache}'")
        else:
            raise Exception(f"Unknown cache backend: '{coltrane_cache}'")

    return caches


def _get_from_env(env_name: str) -> list[str]:
    """
    Retrieves environment value that could potentially be a list of strings.
    """

    env_values = []

    if value_from_env := getenv(env_name):
        env_values = value_from_env.split(",")

    return env_values


def _set_coltrane_setting(settings: dict, initialize_settings: dict, setting_name: str) -> dict:
    """
    Sets a setting on the `COLTRANE` dictionary that is in the environment or passed
    in to `initialize`. Environment takes precedence.

    For example:
    - `COLTRANE_TITLE=Awesome Blog 1` in `.env` equals `COLTRANE['TITLE'] = 'Awesome Blog 1'` in settings
    - `initialize(COLTRANE_TITLE='Awesome Blog 2')` equals `COLTRANE['TITLE'] = 'Awesome Blog 2'` in settings
    - `COLTRANE_TITLE=Awesome Blog 3` in `.env` and `initialize(COLTRANE_TITLE='Awesome Blog 4')` equals
        `COLTRANE['TITLE'] = 'Awesome Blog 3'` in settings
    """

    coltrane_setting_name = f"COLTRANE_{setting_name}"
    value = getenv(coltrane_setting_name) or initialize_settings.get(coltrane_setting_name)

    if value:
        # Make sure there is a `COLTRANE` setting
        if "COLTRANE" not in settings:
            settings["COLTRANE"] = {}

        if setting_name in COLTRANE_SETTINGS_THAT_ARE_ARRAYS:
            settings["COLTRANE"][setting_name] = value.split(",")
        elif setting_name in COLTRANE_SETTINGS_THAT_ARE_BOOLEANS:
            settings["COLTRANE"][setting_name] = False

            if isinstance(value, str):
                settings["COLTRANE"][setting_name] = value.lower() == "true"
            elif isinstance(value, bool):
                settings["COLTRANE"][setting_name] = value is True
        else:
            settings["COLTRANE"][setting_name] = value

    return settings


def _get_from_env_or_settings(django_settings: dict, key: str, default: Any) -> Any:
    """
    Get a setting from (in precedence order) the environment or the `COLTRANE` dictionary
    in settings. Normal `get_config_settings()` method does not work because it requires
    the Django settings to be configured.
    """

    val = default

    if setting_value := django_settings.get("COLTRANE", {}).get(key):
        val = setting_value

    if environment_value := getenv(f"COLTRANE_{key}"):
        val = environment_value

    return val


def _merge_settings(base_dir: Path, django_settings: dict[str, Any]) -> dict[str, Any]:
    """
    Merges the passed-in settings into the default `coltrane` settings.
    Passed-in settings will override the defaults.
    """

    # Assume that `argv[1] == "build"` means that the `build`
    # management command is currently being run
    is_build_management_command = _get_current_command() == "build"
    is_collectstatic_management_command = _get_current_command() == "collectstatic"

    debug = django_settings.get("DEBUG", getenv("DEBUG", "True") == "True")
    time_zone = django_settings.get("TIME_ZONE", getenv("TIME_ZONE", "UTC"))

    config = get_config(base_dir)

    staticfiles_dirs = []

    if not is_build_management_command and not is_collectstatic_management_command:
        # Add base directory for static files, the overriden static templatetag will handle the rest
        staticfiles_dirs = [base_dir]

    middleware = deepcopy(DEFAULT_MIDDLEWARE)
    installed_apps = deepcopy(DEFAULT_INSTALLED_APPS)

    if is_django_unicorn_installed():
        installed_apps.append("django_unicorn")

        if is_unicorn_module_available():
            installed_apps.append("unicorn")

        if config.has_custom_sites:
            for site in config.sites:
                site_unicorn_module = site.folder + ".unicorn"

                if is_module_available(site_unicorn_module):
                    installed_apps.append(site_unicorn_module)

    if is_whitenoise_installed():
        middleware.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
        installed_apps.insert(0, "whitenoise.runserver_nostatic")

    if is_django_compressor_installed():
        installed_apps.append("compressor")

    if debug and not is_build_management_command:
        # Add settings required for django-browser-reload when appropriate
        middleware.append("django_browser_reload.middleware.BrowserReloadMiddleware")
        installed_apps.append("django_browser_reload")

        # Add content to "staticfiles" so django-browser-reload can monitor it
        content_directory = _get_from_env_or_settings(
            django_settings, "CONTENT_DIRECTORY", DEFAULT_COLTRANE_SETTINGS["CONTENT_DIRECTORY"]
        )

        # Add data to "staticfiles" so django-browser-reload can monitor it
        data_directory = _get_from_env_or_settings(
            django_settings, "DATA_DIRECTORY", DEFAULT_COLTRANE_SETTINGS["DATA_DIRECTORY"]
        )

        # Add content and data to staticfiles for each site so that they can be monitored by django-browser-reload
        for site in config.sites:
            site_content_directory = base_dir / "sites" / site.folder / content_directory

            if site_content_directory.exists():
                staticfiles_dirs.append(site_content_directory)

            site_data_directory = base_dir / "sites" / site.folder / data_directory

            if site_data_directory.exists():
                staticfiles_dirs.append(site_data_directory)

    templates = deepcopy(config.get_templates_settings())

    default_settings = {
        "BASE_DIR": base_dir,
        "ROOT_URLCONF": "coltrane.urls",
        "DEBUG": debug,
        "SECRET_KEY": getenv("SECRET_KEY"),
        "INSTALLED_APPS": _merge_installed_apps(django_settings, installed_apps),
        "CACHES": _get_caches(django_settings),
        "MIDDLEWARE": middleware,
        "TEMPLATES": templates,
        "INTERNAL_IPS": _get_from_env("INTERNAL_IPS"),
        "ALLOWED_HOSTS": _get_from_env("ALLOWED_HOSTS"),
        "CSRF_TRUSTED_ORIGINS": _get_from_env("CSRF_TRUSTED_ORIGINS"),
        "STATIC_ROOT": base_dir / "output" / "static",
        "STATIC_URL": "static/",
        "STATICFILES_DIRS": staticfiles_dirs,
        "TIME_ZONE": time_zone,
        "LOGGING": {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "ERROR",
            },
        },
        "COLTRANE": DEFAULT_COLTRANE_SETTINGS,
        # Hard-coding `SETTINGS_MODULE` even though it kind of doesn't make sense, but
        # is needed for rendering a nice 500 page on local when debugging
        "SETTINGS_MODULE": "coltrane",
    }

    # Check for `COLTRANE` settings in env variables or passed into app.initialize()
    for setting_name in DEFAULT_COLTRANE_SETTINGS.keys():
        default_settings = _set_coltrane_setting(default_settings, django_settings, setting_name)

    if is_whitenoise_installed():
        default_settings["WHITENOISE_MANIFEST_STRICT"] = False
        default_settings["STATICFILES_STORAGE"] = "whitenoise.storage.CompressedManifestStaticFilesStorage"

    default_settings["STATICFILES_FINDERS"] = (
        "django.contrib.staticfiles.finders.FileSystemFinder",
        "django.contrib.staticfiles.finders.AppDirectoriesFinder",
        "coltrane.finders.ColtraneSiteFinder",
    )

    if is_django_compressor_installed():
        default_settings["COMPRESS_ENABLED"] = True

        default_settings["STATICFILES_FINDERS"] = (
            "django.contrib.staticfiles.finders.FileSystemFinder",
            "django.contrib.staticfiles.finders.AppDirectoriesFinder",
            "coltrane.finders.ColtraneSiteFinder",
            "compressor.finders.CompressorFinder",
        )

        default_settings["TEMPLATES"][0]["OPTIONS"]["builtins"].append("compressor.templatetags.compress")

        if _get_current_command() == "compress":
            default_settings["COMPRESS_OFFLINE"] = True

    # Make sure BASE_DIR is a `Path` if it got passed in
    if "BASE_DIR" in django_settings and isinstance(django_settings["BASE_DIR"], str):
        django_settings["BASE_DIR"] = Path(django_settings["BASE_DIR"])

    # Override STATIC_ROOT if the output directory is manually set
    try:
        django_settings["STATIC_ROOT"] = Path(django_settings["COLTRANE"]["OUTPUT"]["DIRECTORY"]) / "static"
    except KeyError:
        pass

    django_settings = dict_merge(default_settings, django_settings, destination_overrides_source=True)
    logger.debug(f"Merged settings: {django_settings}")

    return django_settings


def _configure_settings(django_settings: dict[str, Any]) -> None:
    """
    Configures the settings in Django.
    """

    settings.configure(**django_settings)


def _load_environment_variables(django_settings: dict[str, Any]) -> None:
    load_dotenv(".env")

    if "ENV" not in django_settings:
        django_settings["ENV"] = {}

    django_settings["ENV"].update(dict(environ.items()))


def initialize(**django_settings) -> WSGIHandler:
    """
    Initializes the Django static site.
    """

    base_dir = _get_base_dir(django_settings.get("BASE_DIR"))
    _load_environment_variables(django_settings=django_settings)

    # coltrane_sites = django_settings.get("COLTRANE_SITES", {})

    django_settings = _merge_settings(base_dir, django_settings)
    _configure_settings(django_settings)

    django_setup()

    return WSGIHandler()


def run() -> None:
    """
    Run the Django management command based on `argv`.
    """

    if _get_current_command() == "compress":
        try:
            from compressor.exceptions import OfflineGenerationError
        except ImportError:
            logger.error("django-compressor is not installed.")
        else:
            try:
                stdout = StringIO()

                with redirect_stdout(stdout):
                    execute_from_command_line()

                compress_stdout = stdout.getvalue().replace("Compressing... done\n", "")
                print(compress_stdout)  # noqa: T201
            except OfflineGenerationError as e:
                if "No 'compress' template tags found in templates." in e.args[0]:
                    logger.error("No compress blocks found.")
                else:
                    raise
    else:
        execute_from_command_line()
