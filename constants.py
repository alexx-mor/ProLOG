"""Shared constants for ProLOG."""

from pathlib import Path
import sys

APP_NAME = "ProLOG"
APP_VERSION = "0.4.0"
GITHUB_OWNER = "alexx-mor"
GITHUB_REPO = "ProLOG"

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = BASE_DIR / "exports"
BACKUPS_DIR = BASE_DIR / "backups"
DICTIONARIES_DIR = BASE_DIR / "dictionaries"
TEMPLATES_DIR = BUNDLE_DIR / "templates"
RESOURCES_DIR = BUNDLE_DIR / "resources"
BUNDLED_DICTIONARIES_DIR = BUNDLE_DIR / "dictionaries"
APP_ICON_FILE = RESOURCES_DIR / "app.ico"
APP_LOGO_FILE = RESOURCES_DIR / "app_logo.png"
DIRECTORY_ICONS_DIR = RESOURCES_DIR / "directory_icons"

CONFIG_FILE = BASE_DIR / "config.json"
AUTH_FILE = DATA_DIR / "auth.json"
DATABASE_FILE = DATA_DIR / "prolog.sqlite3"

DATE_FORMAT = "%Y-%m-%d"
DISPLAY_DATETIME_FORMAT = "%H:%M"
