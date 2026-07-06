from mitup_bot.models import Broadcast, BroadcastDelivery, BroadcastMessage
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus


def test_broadcast_equality_ignores_id_and_timestamps():
    left = Broadcast(id=1, name="Camp", author_tg_id=99, status=BroadcastStatus.QUEUED)
    right = Broadcast(id=2, name="Camp", author_tg_id=99, status=BroadcastStatus.QUEUED)

    # id, created_time and updated_time are excluded from the identity hash.
    assert left == right
    assert hash(left) == hash(right)


def test_broadcast_inequality_on_differing_content():
    left = Broadcast(id=1, name="Camp", author_tg_id=99, status=BroadcastStatus.QUEUED)
    right = Broadcast(id=1, name="Other", author_tg_id=99, status=BroadcastStatus.QUEUED)

    assert left != right


def test_broadcast_eq_returns_not_implemented_for_other_types():
    broadcast = Broadcast(id=1, name="Camp", author_tg_id=99)

    assert broadcast.__eq__("not a broadcast") is NotImplemented


def test_broadcast_message_equality_ignores_id_and_timestamps():
    left = BroadcastMessage(id=1, broadcast_id=7, language="en", body_html="hi")
    right = BroadcastMessage(id=2, broadcast_id=7, language="en", body_html="hi")

    assert left == right
    assert hash(left) == hash(right)


def test_broadcast_message_inequality_on_differing_body():
    left = BroadcastMessage(id=1, broadcast_id=7, language="en", body_html="hi")
    right = BroadcastMessage(id=1, broadcast_id=7, language="en", body_html="bye")

    assert left != right


def test_broadcast_message_eq_returns_not_implemented_for_other_types():
    message = BroadcastMessage(id=1, broadcast_id=7, language="en", body_html="hi")

    assert message.__eq__(object()) is NotImplemented


def test_broadcast_delivery_equality_ignores_only_id():
    left = BroadcastDelivery(id=1, broadcast_id=7, user_id=3, language_sent="en", status=BroadcastDeliveryStatus.SENT)
    right = BroadcastDelivery(id=2, broadcast_id=7, user_id=3, language_sent="en", status=BroadcastDeliveryStatus.SENT)

    assert left == right
    assert hash(left) == hash(right)


def test_broadcast_delivery_inequality_on_differing_status():
    left = BroadcastDelivery(id=1, broadcast_id=7, user_id=3, language_sent="en", status=BroadcastDeliveryStatus.SENT)
    right = BroadcastDelivery(
        id=1, broadcast_id=7, user_id=3, language_sent="en", status=BroadcastDeliveryStatus.FAILED
    )

    assert left != right


def test_broadcast_delivery_eq_returns_not_implemented_for_other_types():
    delivery = BroadcastDelivery(id=1, broadcast_id=7, user_id=3, language_sent="en")

    assert delivery.__eq__(42) is NotImplemented
