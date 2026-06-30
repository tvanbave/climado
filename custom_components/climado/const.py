"""Constants for the Climado integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "climado"

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BUTTON, Platform.SWITCH]

# ---- Config / options keys ----
CONF_NAME = "name"
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_MAIN_TEMP_SENSOR = "main_temp_sensor"
CONF_BEDROOM_TEMP_SENSOR = "bedroom_temp_sensor"
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_OCCUPANCY_ENTITIES = "occupancy_entities"
CONF_WORKDAY_SENSOR = "workday_sensor"

CONF_COMFORT_HOME = "comfort_home"
CONF_AWAY_TEMP = "away_temp"
CONF_VACATION_TEMP = "vacation_temp"
CONF_BEDROOM_TARGET = "bedroom_target"

CONF_AWAY_DELAY = "away_delay_minutes"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
CONF_NIGHT_CLAMP_MIN = "night_clamp_min"
CONF_NIGHT_CLAMP_MAX = "night_clamp_max"

# Rate engine (Ontario ULO preset knobs for M1; full tier editor is M2/M3)
CONF_ONPEAK_COAST = "onpeak_coast_offset"
CONF_PRECOOL_LEAD = "precool_lead_minutes"
CONF_PRECOOL_DEPTH = "precool_depth"

# Pre-arrival ("Early-On")
CONF_PREARRIVAL_LEAD = "prearrival_lead_minutes"
CONF_PREARRIVAL_TARGET = "prearrival_target"
CONF_PREARRIVAL_ONLY_IF_ABOVE = "prearrival_only_if_above"

# ---- Defaults (port of automation.ulo_climate_controller_main_floor) ----
DEFAULT_NAME = "Main Floor"
DEFAULT_COMFORT_HOME = 23.5
DEFAULT_AWAY_TEMP = 28.0
DEFAULT_VACATION_TEMP = 28.0
DEFAULT_BEDROOM_TARGET = 23.0
DEFAULT_AWAY_DELAY = 45
DEFAULT_NIGHT_START = "23:00:00"
DEFAULT_NIGHT_END = "07:00:00"
DEFAULT_NIGHT_CLAMP_MIN = 19.0
DEFAULT_NIGHT_CLAMP_MAX = 25.0
DEFAULT_ONPEAK_COAST = 2.0
DEFAULT_PRECOOL_LEAD = 90
DEFAULT_PRECOOL_DEPTH = 2.0
DEFAULT_PREARRIVAL_LEAD = 120
DEFAULT_PREARRIVAL_TARGET = 23.0
DEFAULT_PREARRIVAL_ONLY_IF_ABOVE = 25.0

# ecobee cooling-range fallback (used only if the climate entity omits min/max)
DEVICE_COOL_MIN = 18.5
DEVICE_COOL_MAX = 33.5

# ---- Modes ----
MODE_AUTO = "auto"
MODE_HOME = "home"
MODE_AWAY = "away"
MODE_SLEEP = "sleep"
MODE_VACATION = "vacation"
MODE_PREARRIVAL = "pre_arrival"
MODE_DISABLED = "disabled"

# Options offered by the manual-override select
SELECT_MODES = [MODE_AUTO, MODE_HOME, MODE_AWAY, MODE_SLEEP, MODE_VACATION]

# ---- Services ----
SERVICE_START_PREARRIVAL = "start_pre_arrival"
SERVICE_CLEAR_PREARRIVAL = "clear_pre_arrival"

ATTR_LEAD_MINUTES = "lead_minutes"
ATTR_TARGET = "target"
ATTR_ONLY_IF_ABOVE = "only_if_above"
