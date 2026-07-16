from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SettingChange:
    field: str
    current_value: str
    desired_value: str


@dataclass(frozen=True)
class SettingsIntent:
    profile_name: str
    desired_values: Mapping[str, str]
    reasons: tuple[str, ...]
    expected_changes: tuple[SettingChange, ...]
    dry_run: bool

    @property
    def has_changes(self) -> bool:
        return bool(self.expected_changes)


def build_settings_intent(
    *,
    profile_name: str,
    current_values: Mapping[str, object],
    desired_values: Mapping[str, str],
    changed_fields: list[str],
    dry_run: bool,
    reasons: tuple[str, ...] = (),
) -> SettingsIntent:
    """Describe a settings write without performing KP-NET I/O."""
    unique_fields = tuple(dict.fromkeys(changed_fields))
    missing_fields = [field for field in unique_fields if field not in desired_values]
    if missing_fields:
        raise ValueError(f"changed field missing from desired_values: {missing_fields[0]}")
    changes = tuple(
        SettingChange(
            field=field,
            current_value=str(current_values.get(field, "")),
            desired_value=str(desired_values.get(field, "")),
        )
        for field in unique_fields
    )
    return SettingsIntent(
        profile_name=profile_name,
        desired_values=dict(desired_values),
        reasons=reasons or (f"profile:{profile_name}",),
        expected_changes=changes,
        dry_run=dry_run,
    )
