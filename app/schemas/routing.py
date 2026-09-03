"""Service-routing contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class ServiceRoute(str, Enum):
    """Commercial services available to a qualified lead."""

    FINANCIAL_PLANNING = "financial_planning"
    INVESTMENT_ADVISORY = "investment_advisory"
    BOTH = "both"
    NO_CURRENT_FIT = "no_current_fit"


class RoutingSignals(BaseModel):
    """Explicit conversational evidence used by deterministic routing."""

    model_config = ConfigDict(extra="forbid")

    planning_need: bool
    investment_need: bool
    out_of_scope_only: bool

    @model_validator(mode="after")
    def out_of_scope_is_exclusive(self) -> "RoutingSignals":
        if self.out_of_scope_only and (self.planning_need or self.investment_need):
            raise ValueError("out_of_scope_only cannot coexist with in-scope needs")
        return self
