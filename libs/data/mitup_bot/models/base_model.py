from typing import cast


class BaseModel:
    """Base model for all mitup models. These is a class that includes helper methods with no data"""

    @property
    def db_id(self) -> int:
        """Returns the id of the object in the database. Raises a ValueError if the id is not set."""
        id = getattr(self, "id", None)
        if id is None:
            raise ValueError("id is not set")
        return cast(int, id)
