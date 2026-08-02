from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol


class ProfileConfig(Protocol):
    """Expose only the configuration values used by profile construction."""

    night_plan_path: Path
    operation_conditions_path: Path
    night_charge_window_start: str
    night_charge_window_end: str
    day_discharge_window_start: str
    day_discharge_window_end: str
    default_charge_power_kw: float
    green_mode_max_charge_percent: float


@dataclass(frozen=True)
class ProfileOverrides:
    name: str
    battery_operating_mode: str
    soc_safety_mode: str
    soc_economy_mode: str
    soc_contact_input: str
    soc_charge_mode: str
    charge_start_h: str
    charge_start_m: str
    charge_end_h: str
    charge_end_m: str
    discharge_start_h: str
    discharge_start_m: str
    discharge_end_h: str
    discharge_end_m: str
    agreement_ampere: str
    on_power_outage_mode: str = "0"
    on_power_outage_charge_power_w: str = "65535"


FORCED_CHARGE_PROFILE = ProfileOverrides(
    # 充電を夜間単価の時間帯内に完了させ、07:00以降はPVを優先する。
    name="night-green",
    battery_operating_mode="1",
    soc_safety_mode="50",
    soc_economy_mode="0",
    soc_contact_input="100",
    soc_charge_mode="50",
    charge_start_h="4",
    charge_start_m="30",
    charge_end_h="6",
    charge_end_m="30",
    discharge_start_h="7",
    discharge_start_m="0",
    discharge_end_h="23",
    discharge_end_m="0",
    agreement_ampere="50",
)

GREEN_MODE_PROFILE = ProfileOverrides(
    # 日中の買電充電を避け、PV余剰を蓄電池に受ける通常運転とする。
    name="green-mode",
    battery_operating_mode="1",
    soc_safety_mode="0",
    soc_economy_mode="0",
    soc_contact_input="0",
    soc_charge_mode="0",
    charge_start_h="23",
    charge_start_m="0",
    charge_end_h="7",
    charge_end_m="0",
    discharge_start_h="7",
    discharge_start_m="0",
    discharge_end_h="23",
    discharge_end_m="0",
    agreement_ampere="50",
)

STANDBY_PROFILE = replace(
    GREEN_MODE_PROFILE,
    name="standby-mode",
    battery_operating_mode="0",
    soc_contact_input="0",
    soc_charge_mode="0",
)
