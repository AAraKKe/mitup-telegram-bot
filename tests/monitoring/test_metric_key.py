from mitup_bot import monitoring


def test_metrics_key_preffix():
    key = monitoring.MetricKey.TIME
    assert key.with_prefix("MyPrefix") == "MyPrefix/Time"
    assert key.with_prefix("MyPrefix", separator=":") == "MyPrefix:Time"
