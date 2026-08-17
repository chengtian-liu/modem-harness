"""Event bus for service-to-service communication."""

from collections import defaultdict
from enum import Enum, auto
from typing import Callable


class Event(Enum):
    """Events that services can subscribe to."""
    TRANSPORT_OPENED = auto()
    TRANSPORT_CLOSED = auto()

    PPP_STARTING = auto()
    PPP_LCP_UP = auto()
    PPP_AUTH_DONE = auto()
    PPP_IPCP_UP = auto()          # Triggers: PingService, FtpService, IperfService enable
    PPP_DISCONNECTING = auto()
    PPP_DISCONNECTED = auto()

    PPP_IP_RECEIVED = auto()      # IP packet from PPP peer
    PPP_ICMP_REPLY = auto()       # ICMP echo reply received

    AT_RESPONSE = auto()


class EventBus:
    """Simple pub/sub event bus."""

    def __init__(self):
        self._subscribers: dict[Event, list[Callable]] = defaultdict(list)

    def subscribe(self, event: Event, callback: Callable[..., None]):
        """Subscribe to an event. Callback receives **kwargs."""
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: Event, callback: Callable):
        """Remove a subscription."""
        if event in self._subscribers:
            self._subscribers[event] = [cb for cb in self._subscribers[event] if cb != callback]

    def fire(self, event: Event, **kwargs):
        """Fire an event. All subscribers are called synchronously."""
        for callback in self._subscribers.get(event, []):
            try:
                callback(**kwargs)
            except Exception as e:
                print(f"  [EventBus] error in {event.name} handler: {e}")