from asset_release_policy import decide_subscriber_update


def test_subscriber_update_is_scheduled_only_when_creator_requests_publish() -> None:
    assert decide_subscriber_update(True) == "scheduled"
    assert decide_subscriber_update(False) == "not_requested"
