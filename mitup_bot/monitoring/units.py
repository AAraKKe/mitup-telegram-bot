from enum import StrEnum


class MetricUnit(StrEnum):
    COUNT = "Count"
    MILLISECONDS = "Milliseconds"
    BYTES = "Bytes"
    SECONDS = "Seconds"
    PERCENT = "Percent"
    NONE = "None"
