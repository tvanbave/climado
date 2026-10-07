"""Local feedback may acknowledge state, never infer fuel or bypass stop guards."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.climado.config_flow import ClimadoOptionsFlow
from custom_components.climado.coordinator import ClimadoCoordinator


def report(e, entity, mode, **attrs):
    e.hass.states.async_set(entity, mode, attrs, timestamp=e.clock.utcnow().timestamp())


def setup(e, mode="off", aux="off", equipment="", action="idle"):
    e.entry.options.update(local_feedback_entity="climate.local", aux_heat_switch="switch.aux")
    e.c._aux_entity = "switch.aux"
    report(e, "climate.test", mode, hvac_modes=["off", "heat", "cool"],
           equipment_running=equipment, hvac_action=action, temperature=21, preset_mode="temp")
    report(e, "climate.local", mode, hvac_action=action, temperature=21, current_temperature=20)
    report(e, "switch.aux", aux)
    e.hass.services.async_register("climate", "set_hvac_mode", AsyncMock())
    e.hass.services.async_register("switch", "turn_on", AsyncMock())
    e.service.side_effect = None


async def test_local_feedback_is_optional_and_read_only(engine):
    e = engine
    setup(e)
    data = e.c.feedback.snapshot()
    assert data["source"] == "local"
    assert data["target"] == 21
    assert data["temperature"] == 20
    e.entry.options.pop("local_feedback_entity")
    assert e.c.feedback.snapshot()["source"] == "cloud"
    e.service.assert_not_called()


@pytest.mark.parametrize("fault", ["missing", "unavailable", "unknown", "stale", "future", "same_entity"])
async def test_invalid_local_feedback_falls_back_to_cloud(engine, fault):
    e = engine
    setup(e)
    if fault == "missing":
        e.hass.states.async_remove("climate.local")
    elif fault in ("unknown", "unavailable"):
        report(e, "climate.local", fault)
    elif fault == "same_entity":
        e.entry.options["local_feedback_entity"] = "climate.test"
    else:
        e.clock.value += timedelta(minutes=10 if fault == "stale" else -1)
    assert e.c.feedback.snapshot()["source"] == "cloud"
    assert e.c.feedback.snapshot()["local_status"] == "unavailable_or_stale"


async def test_cached_local_state_cannot_confirm_new_command(engine):
    e = engine
    setup(e, mode="heat", aux="on")
    e.clock.value += timedelta(seconds=1)
    await e.c.async_set_system_mode("off")
    assert e.c.system.pending
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "off", hvac_action="idle")
    e.c.system.confirm()
    assert e.c.system.pending is None
    assert e.c.system.snapshot()["request"]["confirmation_source"] == "local"
    # Cloud still says heat: do not unlock another start or issue a comfort target.
    assert not e.c.system.snapshot()["can_start"]
    data = await e.c._async_update_data()
    assert data["reason"] == "feedback_mode_mismatch"
    assert e.service.await_count == 1


async def test_local_heat_requires_separate_new_fuel_report(engine):
    e = engine
    setup(e)
    await e.c.async_set_system_mode("heat", "gas")
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "heat", hvac_action="heating", temperature=21)
    e.c.system.confirm()
    status = e.c.system.snapshot()["request"]
    assert status["mode_confirmed_locally"]
    assert status["status"] == "pending"
    report(e, "switch.aux", "on")
    e.c.system.confirm()
    assert e.c.system.pending is None
    assert e.c.system.snapshot()["request"]["confirmation_source"] == "local + Ecobee fuel"
    assert e.c._control_blocked() == "feedback_mode_mismatch"
    assert e.service.call_args.args[:2] == ("switch", "turn_on")
    assert e.service.await_count == 1


async def test_unchanged_cached_aux_cannot_confirm_local_heat(engine):
    e = engine
    setup(e, aux="on")
    await e.c.async_set_system_mode("heat", "gas")
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "heat", hvac_action="heating")
    e.c.system.confirm()
    assert e.c.system.pending
    report(e, "switch.aux", "on")
    e.c.system.confirm()
    assert e.c.system.pending is None


async def test_local_heating_does_not_claim_running_fuel(engine):
    e = engine
    setup(e, mode="heat", aux="on", action="idle")
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "heat", hvac_action="heating")
    result = await e.c._async_update_data()
    assert result["thermostat_feedback"]["hvac_action"] == "heating"
    assert result["running_source"] == "idle"
    assert result["selected_source"] == "gas"


@pytest.mark.parametrize("cloud_equipment,cloud_action,local_mode,local_action", [
    ("heatPump", "heating", "off", "idle"),
    ("", "idle", "heat", "heating"),
    ("", "idle", "off", "heating"),
    ("", "idle", "off", None),
])
async def test_local_feedback_never_weakens_idle_start_gate(engine, cloud_equipment, cloud_action, local_mode, local_action):
    e = engine
    setup(e, equipment=cloud_equipment, action=cloud_action)
    report(e, "climate.local", local_mode, hvac_action=local_action)
    with pytest.raises(ServiceValidationError):
        await e.c.system.async_set("heat", "gas")
    e.service.assert_not_called()


async def test_new_local_conflict_blocks_cloud_confirmation(engine):
    e = engine
    setup(e)
    await e.c.async_set_system_mode("cool")
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.test", "cool", hvac_action="idle")
    e.c.system.confirm()
    assert e.c.system.pending
    report(e, "climate.local", "cool", hvac_action="idle")
    e.c.system.confirm()
    assert e.c.system.pending is None


async def test_local_target_ack_does_not_confirm_sleep_or_change_cloud_hold_logic(engine):
    e = engine
    setup(e, mode="heat")
    e.c._pending_command = ("temp", 22)
    e.c._commanded_at = e.clock.utcnow()
    report(e, "climate.local", "heat", hvac_action="idle", temperature=22)
    assert not e.c.feedback.snapshot()["target_confirmed_locally"]
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "heat", hvac_action="idle", temperature=22)
    assert e.c.feedback.snapshot()["target_confirmed_locally"]
    e.c._confirm_pending()
    assert e.c._pending_command == ("temp", 22)  # Native hold/preset semantics remain cloud-owned.
    e.c._pending_command = ("preset", "sleep")
    assert not e.c.feedback.snapshot()["target_confirmed_locally"]


async def test_refreshes_are_bounded_read_only_and_keep_cooldown(engine, monkeypatch):
    e = engine
    setup(e)
    e.hass.services.async_register("homeassistant", "update_entity", AsyncMock())
    timer = Mock(return_value=Mock())
    monkeypatch.setattr("custom_components.climado.feedback.async_track_point_in_time", timer)
    e.c.async_request_refresh = AsyncMock()
    f = e.c.feedback
    f.request_refresh()
    f.request_refresh()
    assert timer.call_count == 1
    assert timer.call_args.args[2] == e.clock.utcnow() + timedelta(seconds=2)
    e.clock.value += timedelta(seconds=2)
    f._unsub = None
    await f._refresh()
    e.service.assert_awaited_once_with("homeassistant", "update_entity",
                                      {"entity_id": ["climate.test", "switch.aux"]}, blocking=True)
    assert timer.call_args.args[2] == e.clock.utcnow() + timedelta(minutes=3)
    f.request_refresh()  # New command cannot shorten the refresh cooldown.
    assert timer.call_count == 2
    e.clock.value += timedelta(minutes=3)
    f._unsub = None
    await f._refresh()
    assert timer.call_count == 2
    assert e.service.await_count == 2


async def test_refresh_failure_is_not_an_equipment_failure(engine, monkeypatch):
    e = engine
    setup(e)
    e.c.async_request_refresh = AsyncMock()
    monkeypatch.setattr("custom_components.climado.feedback.async_track_point_in_time", Mock(return_value=Mock()))
    e.service.side_effect = RuntimeError("Cloud unavailable")
    await e.c.feedback._refresh()
    assert e.c.feedback.refresh_error == "Cloud unavailable"
    assert e.c.system.error is None


async def test_unload_cancels_inflight_refresh(engine):
    e = engine
    setup(e)
    e.c.async_request_refresh = AsyncMock()
    entered = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    e.service.side_effect = wait_forever
    e.c.feedback._start(e.clock.utcnow())
    await entered.wait()
    task = e.c.feedback._task
    await e.c.feedback.async_close()
    assert task.done()
    e.c.async_request_refresh.assert_not_called()


async def test_unload_cancels_coordinator_refresh_tail(engine):
    e = engine
    setup(e)
    entered = asyncio.Event()

    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()

    e.c.async_request_refresh = wait_forever
    e.c.feedback._start(e.clock.utcnow())
    await entered.wait()
    task = e.c.feedback._task
    assert task is not None
    await e.c.feedback.async_close()
    assert task.done()


async def test_off_is_not_skipped_when_only_local_reports_off(engine):
    e = engine
    setup(e, mode="heat", aux="on")
    report(e, "climate.local", "off", hvac_action="idle")
    await e.c.async_set_system_mode("off")
    e.service.assert_awaited_once()
    assert e.service.call_args.args[2]["hvac_mode"] == "off"


async def test_old_confirmation_is_hidden_after_mode_changes(engine):
    e = engine
    setup(e)
    await e.c.async_set_system_mode("cool")
    e.clock.value += timedelta(seconds=1)
    report(e, "climate.local", "cool", hvac_action="idle")
    e.c.system.confirm()
    assert e.c.system.snapshot()["request"]["status"] == "confirmed"
    report(e, "climate.local", "off", hvac_action="idle")
    assert e.c.system.snapshot()["request"] is None


async def test_restart_preserves_local_request_baseline_without_replay(engine):
    e = engine
    setup(e)
    await e.c.async_set_system_mode("heat", "gas")
    fresh = ClimadoCoordinator(e.hass, e.entry)
    try:
        await fresh.async_restore_runtime()
        assert fresh.system.pending["requested_at"] == e.c.system.pending["requested_at"]
        e.clock.value += timedelta(seconds=1)
        report(e, "climate.local", "heat", hvac_action="heating")
        fresh.system.confirm()
        assert fresh.system.pending
        report(e, "switch.aux", "on")
        fresh.system.confirm()
        assert fresh.system.pending is None
        assert e.service.await_count == 1
    finally:
        await fresh.async_unload()


async def test_feedback_option_can_be_added_and_removed_without_losing_other_options():
    entry = SimpleNamespace(data={"local_feedback_entity": "climate.original"},
                            options={"rate_plan": {"saved": True}, "heat_home": 21})
    flow = ClimadoOptionsFlow(entry)
    flow.async_create_entry = Mock(side_effect=lambda **kw: kw)
    flow.async_show_form = Mock(side_effect=lambda **kw: kw)
    result = await flow.async_step_init({"climate_entity": "climate.cloud", "local_feedback_entity": "climate.local"})
    assert result["data"]["heat_home"] == 21
    entry.options = result["data"]
    result = await flow.async_step_init({"climate_entity": "climate.cloud"})
    assert result["data"]["local_feedback_entity"] is None
    assert result["data"]["rate_plan"] == {"saved": True}
    result = await flow.async_step_init({"climate_entity": "climate.cloud", "local_feedback_entity": "climate.cloud"})
    assert result["errors"]["local_feedback_entity"] == "same_feedback_entity"
