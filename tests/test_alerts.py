"""Advisory-only heating monitoring with synthetic, timestamped readings."""
from datetime import timedelta
from unittest.mock import Mock

import pytest


def report(e, *, room=15, temp=18, equipment="heatPump", unit="°C", mode="heat", preset="home"):
    e.hass.states.async_set("sensor.main", str(room), {"unit_of_measurement": unit}, timestamp=e.clock.utcnow().timestamp())
    old = e.hass.states.get("climate.test")
    e.hass.states.async_set("climate.test", mode, {**old.attributes, "current_temperature": temp,
        "temperature_unit": unit, "equipment_running": equipment, "preset_mode": preset}, timestamp=e.clock.utcnow().timestamp())


def heat_data(**extra):
    return {"hvac_mode": "heat", "hvac_action": "heating", "running_source": "heat_pump",
            "selected_source": "heat_pump", "target": 20, **extra}


@pytest.fixture
def notifications(monkeypatch):
    create, dismiss = Mock(), Mock()
    monkeypatch.setattr("custom_components.climado.alerts.persistent_notification.async_create", create)
    monkeypatch.setattr("custom_components.climado.alerts.persistent_notification.async_dismiss", dismiss)
    return create, dismiss


async def test_cold_windows_debounce_notify_once_and_clear(engine, notifications):
    e = engine
    report(e)
    data = {"windows_open": True}
    assert e.c.alerts.update(data)["issues"] == []
    e.clock.value += timedelta(minutes=9)
    report(e)
    assert not e.c.alerts.update(data)["issues"]
    e.clock.value += timedelta(minutes=1)
    report(e)
    assert e.c.alerts.update(data)["status"] == "windows_cold"
    e.c.alerts.update(data)
    assert notifications[0].call_count == 1
    assert e.c.alerts.update({"windows_open": False})["status"] == "clear"
    assert notifications[1].call_args.args[1].endswith("windows_cold")
    e.service.assert_not_called()


async def test_stale_reading_resets_cold_duration(engine, notifications):
    report(engine)
    data = {"windows_open": True}
    engine.c.alerts.update(data)
    engine.clock.value += timedelta(minutes=15)
    assert engine.c.alerts.update(data)["monitoring"] == "unavailable"
    report(engine)
    assert not engine.c.alerts.update(data)["issues"]
    notifications[0].assert_not_called()


async def test_wrong_units_or_invalid_temperatures_never_trigger_cold_alert(engine):
    for room, unit in [(10, "°F"), ("nan", "°C"), ("unavailable", "°C")]:
        report(engine, room=room, unit=unit)
        assert not engine.c.alerts.update({"windows_open": True})["issues"]
        assert engine.c.alerts._cold_since is None


async def test_continuous_heating_without_progress_alerts_then_resolves(engine, notifications):
    e = engine
    data = heat_data()
    for minute in range(61):
        report(e, temp=18)
        result = e.c.alerts.update(data)
        if minute < 60:
            assert not result["issues"]
            e.clock.value += timedelta(minutes=1)
    assert result["status"] == "heat_not_improving"
    assert notifications[0].call_count == 1
    e.clock.value += timedelta(minutes=1)
    report(e, temp=18.5)
    assert not e.c.alerts.update(data)["issues"]
    e.service.assert_not_called()


@pytest.mark.parametrize("change", [
    {"hvac_action": "idle"}, {"target": 18.4}, {"selected_source": "gas"},
    {"system_control": {"pending": {"mode": "off"}}},
])
async def test_progress_window_resets_on_stop_target_or_source_change(engine, change):
    for minute in range(60):
        report(engine)
        engine.c.alerts.update(heat_data())
        engine.clock.value += timedelta(minutes=1)
    report(engine)
    assert not engine.c.alerts.update(heat_data(**change))["issues"]


async def test_sensor_handoff_or_observation_gap_does_not_create_false_progress_alert(engine):
    report(engine)
    engine.c.alerts.update(heat_data())
    engine.clock.value += timedelta(hours=1)
    report(engine, preset="sleep")
    assert not engine.c.alerts.update(heat_data())["issues"]


async def test_notifications_can_be_muted_without_hiding_card_alert(engine, notifications):
    engine.entry.options["heating_alert_notifications"] = False
    data = {"system_control": {"error": "offline"}}
    assert engine.c.alerts.update(data)["status"] == "command_failed"
    notifications[0].assert_not_called()
    engine.entry.options["heating_alerts_enabled"] = False
    assert engine.c.alerts.update(data)["status"] == "disabled"


async def test_alert_thresholds_use_existing_number_tunables(engine):
    engine.c.set_tunable("alert_low_temperature", 18)
    engine.c.set_tunable("alert_cold_delay_minutes", 1)
    report(engine, room=17)
    engine.c.alerts.update({"windows_open": True})
    engine.clock.value += timedelta(minutes=1)
    report(engine, room=17)
    assert engine.c.alerts.update({"windows_open": True})["status"] == "windows_cold"


async def test_cold_monitor_does_not_bridge_a_gap_even_with_a_new_reading(engine):
    report(engine)
    engine.c.alerts.update({"windows_open": True})
    engine.clock.value += timedelta(hours=1)
    report(engine)
    assert not engine.c.alerts.update({"windows_open": True})["issues"]


async def test_invalid_restored_alert_setting_falls_back_without_breaking_control(engine):
    engine.c.set_tunable("alert_cold_delay_minutes", float("nan"))
    report(engine)
    assert not engine.c.alerts.update({"windows_open": True})["issues"]
