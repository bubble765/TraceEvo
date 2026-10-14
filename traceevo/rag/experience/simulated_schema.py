"""Explicitly non-production schema for synthetic Experience demonstrations."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SimulatedExperienceRecord(BaseModel):
    """A synthetic example that must never be treated as an admitted memory."""

    model_config = ConfigDict(extra="forbid")

    simulation_id: str = Field(min_length=1)
    simulation_type: Literal["simulated_success_pattern", "simulated_prevention_pattern"]
    task_feature_summary: str = Field(min_length=1)
    simulated_payload: dict[str, Any]
    simulation_basis: str = Field(min_length=1)
    simulation_only: Literal[True] = True
    online_eligible: Literal[False] = False

    @field_validator("simulation_id", "task_feature_summary", "simulation_basis")
    @classmethod
    def validate_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("simulation fields cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_simulation_boundary(self) -> "SimulatedExperienceRecord":
        if not self.simulation_id.startswith("sim-"):
            raise ValueError("simulation IDs must have the sim- prefix")
        if "synthetic" not in self.simulation_basis.casefold():
            raise ValueError("simulation basis must explicitly identify the record as synthetic")
        forbidden_keys = {
            "diagnosis", "final_diagnosis", "right_option", "prediction",
            "outcome_verdict", "task_id", "case_id", "source_trace_id",
            "source_trace_ids", "raw_case",
        }

        def inspect(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if str(key).casefold() in forbidden_keys:
                        raise ValueError(f"simulated records cannot include {key!r}")
                    inspect(nested)
            elif isinstance(value, list):
                for nested in value:
                    inspect(nested)

        inspect(self.simulated_payload)
        if self.simulation_type == "simulated_success_pattern":
            if set(self.simulated_payload) != {"guidance", "workflow_profile"}:
                raise ValueError("simulated success payload has an unsupported shape")
            guidance = self.simulated_payload["guidance"]
            profile = self.simulated_payload["workflow_profile"]
            profile_fields = {
                "expert_roles_used", "retrieval_count", "drilldown_count",
                "backtrack_count", "plan_count",
            }
            if not isinstance(guidance, list) or any(not isinstance(item, str) or not item.strip() for item in guidance):
                raise ValueError("simulated guidance must be a list of non-empty strings")
            if not isinstance(profile, dict) or set(profile) - profile_fields:
                raise ValueError("simulated workflow profile contains unsupported fields")
            roles = profile.get("expert_roles_used", [])
            if not isinstance(roles, list) or any(not isinstance(role, str) or not role.strip() for role in roles):
                raise ValueError("simulated expert roles must be non-empty strings")
            for key, value in profile.items():
                if key == "expert_roles_used":
                    continue
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"simulated profile field {key!r} must be a non-negative integer")
        else:
            if set(self.simulated_payload) != {"failure_pattern", "avoid", "recommended_check"}:
                raise ValueError("simulated prevention payload has an unsupported shape")
            if not isinstance(self.simulated_payload["failure_pattern"], str):
                raise ValueError("simulated failure pattern must be a string")
            for key in ("avoid", "recommended_check"):
                values = self.simulated_payload[key]
                if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                    raise ValueError(f"simulated {key} must be a list of non-empty strings")
        return self
