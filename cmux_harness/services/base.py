"""Service interface — each application module implements this."""

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..harness import CmuxHarness


class ServiceInterface(ABC):
    """Application module base class.

    Each service is independent and communicates only through the harness:
    - Read shared state via harness.state
    - Subscribe to events via harness.events
    - Send data via harness.transport
    - Register commands via harness.command_registry
    """

    name: str = ''
    commands: list[str] = []

    @abstractmethod
    def on_register(self, harness: 'CmuxHarness') -> None:
        """Called when the service is registered with the harness.

        Use this to:
        - Store harness reference
        - Subscribe to events via harness.events.subscribe()
        - Register commands via harness.register_command()
        """
        ...

    @abstractmethod
    def on_command(self, args: list[str]) -> Optional[str]:
        """Handle a user command. Return optional output string.

        Args:
            args: Tokenized command arguments (first element is the command name)

        Returns:
            Output string to display, or None if no output.
        """
        ...

    @abstractmethod
    def on_shutdown(self) -> None:
        """Called when the harness is shutting down. Clean up resources."""
        ...