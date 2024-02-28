import re

from pydantic import BaseModel, Field, field_validator


class CallbackData(BaseModel):
    entity: str
    action: str = "show"
    id: int | None = Field(default=None, ge=1)

    def __str__(self):
        return f"{self.action};{self.entity}:{self.id or ''}"

    @staticmethod
    def parse(match: re.Match | None) -> "CallbackData":
        assert match is not None, "CallbackData.parse should be called only when a match is ensured!"
        return CallbackData.model_validate(match.groupdict())

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: str | int):
        # If id is parsed as an empty string from the callback data, let it be None
        # Allow pydantic to handle conversion from str to int later.
        return value or None if isinstance(value, str) else value

    @property
    def pattern(self) -> str:
        return f"^(?P<action>{self.action});(?P<entity>{self.entity}):(?P<id>\\d*)$"

    def with_id(self, id: int) -> "CallbackData":
        """Creates a CallbackData with the same information but different ID"""
        return CallbackData(entity=self.entity, action=self.action, id=id)
