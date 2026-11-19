"""Constantes de Quiz del Salón."""

from __future__ import annotations

DOMAIN = "quiz_salon"
NAME = "Quiz del Salón"
VERSION = "1.0.0"

PANEL_URL_PATH = "quiz-salon"
PANEL_ELEMENT = "quiz-salon-panel"
PANEL_ICON = "mdi:head-question"

CONF_LOCAL_ONLY = "local_only"
CONF_BASE_URL = "base_url"
DEFAULT_LOCAL_ONLY = True

CUSTOM_QUESTIONS_DIR = "quiz_salon"  # dentro de /config
EVENT_NAME = "quiz_salon_event"

DATA_GLOBAL = f"{DOMAIN}_global"
