"""Constants for the Climado integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "climado"
VERSION = "0.3.5"

PLATFORMS = [
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.TIME,
]

# ---- Config / options keys ----
CONF_NAME = "name"
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_MAIN_TEMP_SENSOR = "main_temp_sensor"
CONF_BEDROOM_TEMP_SENSOR = "bedroom_temp_sensor"
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_OCCUPANCY_ENTITIES = "occupancy_entities"
CONF_WORKDAY_SENSOR = "workday_sensor"
CONF_RATE_PLAN = "rate_plan"  # custom schedule {weekday:[[s,e,tier]...], weekend:[...]}

CONF_COMFORT_HOME = "comfort_home"
CONF_AWAY_TEMP = "away_temp"
CONF_VACATION_TEMP = "vacation_temp"

CONF_AWAY_DELAY = "away_delay_minutes"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"

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
DEFAULT_AWAY_DELAY = 45
DEFAULT_NIGHT_START = "23:00:00"
DEFAULT_NIGHT_END = "07:00:00"
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
SERVICE_SET_RATE_PLAN = "set_rate_plan"
ATTR_PLAN = "plan"

ATTR_LEAD_MINUTES = "lead_minutes"
ATTR_TARGET = "target"
ATTR_ONLY_IF_ABOVE = "only_if_above"
ATTR_FORCE = "force"

# ---- Tunable config exposed as device entities (entity_category=config) ----
# (key, name, min, max, step, unit, icon, default)
NUMBER_TUNABLES = [
    (CONF_COMFORT_HOME, "Home comfort", 10, 33.5, 0.5, "°C", "mdi:home-thermometer", DEFAULT_COMFORT_HOME),
    (CONF_AWAY_TEMP, "Away setpoint", 10, 33.5, 0.5, "°C", "mdi:home-export-outline", DEFAULT_AWAY_TEMP),
    (CONF_VACATION_TEMP, "Vacation setpoint", 10, 33.5, 0.5, "°C", "mdi:bag-suitcase", DEFAULT_VACATION_TEMP),
    (CONF_AWAY_DELAY, "Away delay", 5, 240, 5, "min", "mdi:timer-sand", DEFAULT_AWAY_DELAY),
    (CONF_ONPEAK_COAST, "On-peak coast", 0, 6, 0.5, "°C", "mdi:trending-up", DEFAULT_ONPEAK_COAST),
    (CONF_PRECOOL_LEAD, "Pre-cool lead", 0, 240, 15, "min", "mdi:snowflake-alert", DEFAULT_PRECOOL_LEAD),
    (CONF_PRECOOL_DEPTH, "Pre-cool depth", 0, 6, 0.5, "°C", "mdi:snowflake", DEFAULT_PRECOOL_DEPTH),
    (CONF_PREARRIVAL_LEAD, "Pre-arrival lead", 0, 360, 15, "min", "mdi:home-clock", DEFAULT_PREARRIVAL_LEAD),
    (CONF_PREARRIVAL_TARGET, "Pre-arrival target", 10, 30, 0.5, "°C", "mdi:home-import-outline", DEFAULT_PREARRIVAL_TARGET),
    (CONF_PREARRIVAL_ONLY_IF_ABOVE, "Pre-arrival only if above", 10, 40, 0.5, "°C", "mdi:thermometer-alert", DEFAULT_PREARRIVAL_ONLY_IF_ABOVE),
]

# (key, name, icon, default "HH:MM:SS")
TIME_TUNABLES = [
    (CONF_NIGHT_START, "Night start", "mdi:weather-night", DEFAULT_NIGHT_START),
    (CONF_NIGHT_END, "Night end", "mdi:weather-sunset-up", DEFAULT_NIGHT_END),
]

# Structural keys edited via the options flow (everything else is a device entity)
STRUCTURAL_KEYS = [
    CONF_NAME,
    CONF_CLIMATE_ENTITY,
    CONF_MAIN_TEMP_SENSOR,
    CONF_BEDROOM_TEMP_SENSOR,
    CONF_PRESENCE_ENTITIES,
    CONF_OCCUPANCY_ENTITIES,
    CONF_WORKDAY_SENSOR,
]
