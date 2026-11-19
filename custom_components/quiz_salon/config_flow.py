"""Flujo de configuración de Quiz del Salón."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import CONF_BASE_URL, CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY, DOMAIN, NAME
from .game.netutil import is_loopback_base, normalize_base_url


class QuizSalonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Alta de la integración (una sola instancia, ver manifest)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=NAME,
                data={},
                options={CONF_LOCAL_ONLY: DEFAULT_LOCAL_ONLY, CONF_BASE_URL: ""},
            )
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return QuizSalonOptionsFlow()


class QuizSalonOptionsFlow(OptionsFlow):
    """Opciones: acceso solo en red local y URL base para el QR."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self.config_entry.options
        if user_input is not None:
            raw = (user_input.get(CONF_BASE_URL) or "").strip()
            base = normalize_base_url(raw) if raw else ""
            if raw and base is None:
                errors[CONF_BASE_URL] = "invalid_url"
            elif base and is_loopback_base(base):
                errors[CONF_BASE_URL] = "loopback_url"
            else:
                return self.async_create_entry(
                    data={
                        CONF_LOCAL_ONLY: bool(user_input.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY)),
                        CONF_BASE_URL: base or "",
                    }
                )
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LOCAL_ONLY, default=current.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY)
                ): bool,
                vol.Optional(
                    CONF_BASE_URL,
                    description={"suggested_value": current.get(CONF_BASE_URL, "")},
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
