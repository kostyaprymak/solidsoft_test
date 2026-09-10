"""Validated request and integration data; ORM persistence lives in models.py."""

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Quote(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    amount: Decimal = Field(
        gt=0, lt=Decimal("10000000000"), decimal_places=2, allow_inf_nan=False
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProviderResult(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    status: Literal["succeeded", "failed"]
    reference: str | None = Field(default=None, min_length=1)
    failure_code: Literal["card_declined"] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status == "succeeded":
            valid = self.reference is not None and self.failure_code is None
        else:
            valid = self.reference is None and self.failure_code == "card_declined"
        if not valid:
            raise ValueError("Inconsistent provider outcome")
        return self


class PaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    payment_method_id: UUID
