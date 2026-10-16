from typing import Literal


SubscriberUpdate = Literal["scheduled", "not_requested"]


def decide_subscriber_update(publish_to_subscribers: bool) -> SubscriberUpdate:
    return "scheduled" if publish_to_subscribers else "not_requested"
