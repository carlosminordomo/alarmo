"""Test the codes given to arm and disarm: held back after misses, per account."""

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import Context
from homeassistant.auth.const import GROUP_ID_USER
from pytest_homeassistant_custom_component.common import async_capture_events

from tests.helpers import (
    advance_time,
    assert_alarm_state,
    setup_alarmo_entry,
    patch_alarmo_integration_dependencies,
)
from tests.factories import AreaFactory, UserFactory
from custom_components.alarmo import code_guard

AREA_1 = "alarm_control_panel.test_area_1"
AREA_2 = "alarm_control_panel.test_area_2"
MASTER = "alarm_control_panel.master"


async def setup_alarm(hass: Any, areas: int = 1) -> None:
    """Set up Alarmo with Ana's code 1234, and a master when there are two areas."""
    storage, entry = setup_alarmo_entry(
        hass,
        areas=[
            AreaFactory.create_area(area_id=f"area_{n}", name=f"Test Area {n}")
            for n in range(1, areas + 1)
        ],
        sensors=[],
        entry_id="test_keypad_codes",
        users={"ana": UserFactory.create_user(user_id="ana", name="Ana")},
        master_enabled=areas > 1,
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def account(hass: Any, name: str) -> Context:
    """Return the context of a new Home Assistant user, as their keypad sends."""
    user = await hass.auth.async_create_user(name, group_ids=[GROUP_ID_USER])
    return Context(user_id=user.id)


async def alarm(
    hass: Any, service: str, entity: str, context: Context, **data: Any
) -> None:
    """Call an alarm service as this account."""
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {"entity_id": entity, **data},
        blocking=True,
        context=context,
    )
    await hass.async_block_till_done()


async def armed_away(hass: Any, entity: str, context: Context) -> None:
    """Arm away with Ana's code and let the exit delay run out."""
    await alarm(hass, "alarm_arm_away", entity, context, code="1234")
    await advance_time(hass, 11)
    assert_alarm_state(hass, entity, "armed_away")


@pytest.mark.asyncio
async def test_wrong_codes_are_held_back_per_account(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """From the third wrong code even the right one waits, only for that account."""
    await setup_alarm(hass)
    keypad = await account(hass, "Keypad")
    phone = await account(hass, "Phone")
    failures = async_capture_events(hass, "alarmo_failed_to_arm")
    await armed_away(hass, AREA_1, phone)

    for _ in range(3):
        await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
    await alarm(hass, "alarm_disarm", AREA_1, keypad, code="1234")
    assert_alarm_state(hass, AREA_1, "armed_away")
    assert [event.data["reason"] for event in failures] == [
        *["invalid_code"] * 3,
        "too_many_attempts",
    ]

    await alarm(hass, "alarm_disarm", AREA_1, phone, code="1234")
    assert_alarm_state(hass, AREA_1, "disarmed")


@pytest.mark.asyncio
async def test_the_wait_ends_and_a_right_code_clears_it(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """After the wait the right code works, and the count starts again."""
    await setup_alarm(hass)
    keypad = await account(hass, "Keypad")
    now = 1000.0
    with patch.object(code_guard, "_now", lambda: now):
        await armed_away(hass, AREA_1, keypad)
        for _ in range(3):
            await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
        now += 1
        await alarm(hass, "alarm_disarm", AREA_1, keypad, code="1234")
        assert_alarm_state(hass, AREA_1, "disarmed")

        await armed_away(hass, AREA_1, keypad)
        await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
        await alarm(hass, "alarm_disarm", AREA_1, keypad, code="1234")
        assert_alarm_state(hass, AREA_1, "disarmed")


@pytest.mark.asyncio
async def test_the_third_wrong_code_tells(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """The third wrong code in a row fires one event naming the account."""
    await setup_alarm(hass)
    keypad = await account(hass, "Keypad")
    events = async_capture_events(hass, "alarmo_wrong_codes")
    await armed_away(hass, AREA_1, keypad)

    for _ in range(2):
        await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
    assert not events
    await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
    await alarm(hass, "alarm_disarm", AREA_1, keypad, code="0000")
    assert [event.data for event in events] == [
        {"ha_user_id": keypad.user_id, "area_id": "area_1", "entity_id": AREA_1}
    ]


@pytest.mark.asyncio
async def test_cancel_the_exit_delay_without_a_code(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """Arming from disarmed is cancelled without a code; armed, it still takes one."""
    await setup_alarm(hass)
    phone = await account(hass, "Phone")

    await alarm(hass, "alarm_arm_away", AREA_1, phone, code="1234")
    assert_alarm_state(hass, AREA_1, "arming")
    await alarm(hass, "alarm_disarm", AREA_1, phone)
    assert_alarm_state(hass, AREA_1, "disarmed")

    await armed_away(hass, AREA_1, phone)
    await alarm(hass, "alarm_disarm", AREA_1, phone)
    assert_alarm_state(hass, AREA_1, "armed_away")


@pytest.mark.asyncio
async def test_switching_modes_is_not_cancelled_without_a_code(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """An exit delay begun while armed leaves the alarm armed, so needs the code."""
    await setup_alarm(hass)
    phone = await account(hass, "Phone")

    await alarm(hass, "alarm_arm_home", AREA_1, phone, code="1234")
    assert_alarm_state(hass, AREA_1, "armed_home")
    await alarm(hass, "alarm_arm_away", AREA_1, phone, code="1234")
    assert_alarm_state(hass, AREA_1, "arming")
    await alarm(hass, "alarm_disarm", AREA_1, phone)
    assert_alarm_state(hass, AREA_1, "arming")


@pytest.mark.asyncio
async def test_master_cancels_without_a_code_only_when_all_areas_are_arming(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """The master skips the code only when no area would be disarmed from armed."""
    await setup_alarm(hass, areas=2)
    phone = await account(hass, "Phone")

    await alarm(hass, "alarm_arm_away", MASTER, phone, code="1234")
    assert_alarm_state(hass, MASTER, "arming")
    await alarm(hass, "alarm_disarm", MASTER, phone)
    assert_alarm_state(hass, AREA_1, "disarmed")
    assert_alarm_state(hass, AREA_2, "disarmed")

    await alarm(hass, "alarm_arm_home", AREA_2, phone, code="1234")
    await alarm(hass, "alarm_arm_away", AREA_1, phone, code="1234")
    assert_alarm_state(hass, MASTER, "arming")
    await alarm(hass, "alarm_disarm", MASTER, phone)
    assert_alarm_state(hass, AREA_1, "arming")
    assert_alarm_state(hass, AREA_2, "armed_home")
