from enum import StrEnum


class MetricUnit(StrEnum):
    COUNT = "Count"
    MILLISECONDS = "Milliseconds"
    BYTES = "Bytes"
    SECONDS = "Seconds"
    NONE = "None"
