"""In-process event bus used to decouple notifications from ordering."""

from collections import defaultdict
from collections.abc import Callable


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable[[dict], None]) -> None:
        self._handlers[topic].append(handler)

    def publish(self, topic: str, payload: dict) -> None:
        for handler in self._handlers[topic]:
            handler(payload)
