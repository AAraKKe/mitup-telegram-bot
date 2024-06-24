from typing import Any, Self

from pydantic import BaseModel
from sqlalchemy.ext.mutable import Mutable


class MutableModel(BaseModel, Mutable):
    def __setattr__(self, name: str, value: Any) -> None:
        """Allows SQLAlchmey Session to track mutable behavior when updating any field"""
        self.changed()
        return super().__setattr__(name, value)

    @classmethod
    def coerce(cls, key: str, value: Any) -> Self | None:
        """Convert JSON to model object allowing for mutable behavior"""
        if isinstance(value, cls) or value is None:
            return value

        if isinstance(value, str):
            return cls.model_validate_json(value)

        if isinstance(value, dict):
            return cls(**value)

        super().coerce(key, value)
