from enum import StrEnum


class ImageLayout(StrEnum):
    """How a meeting's photos are arranged on its card."""

    COLLAGE = "collage"
    SLIDESHOW = "slideshow"
