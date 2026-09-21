"""Bounded failed-task identities, retained until their connection closes."""

from dataclasses import dataclass, field


@dataclass
class FailedTasks:
    MAX_PER_CONNECTION = 64
    tasks: dict[str, set[str]] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)

    def contains(self, socket_id: str, task_id: str) -> bool:
        return socket_id in self.blocked or task_id in self.tasks.get(socket_id, ())

    def add(self, socket_id: str, task_id: str) -> bool:
        """Return whether the caller must close this connection at its budget."""
        if socket_id in self.blocked:
            return True
        tasks = self.tasks.setdefault(socket_id, set())
        tasks.add(task_id)
        if len(tasks) >= self.MAX_PER_CONNECTION:
            self.blocked.add(socket_id)
            return True
        return False

    def retain(self, active_sockets) -> None:
        active = set(active_sockets)
        for socket_id in list(self.tasks):
            if socket_id not in active:
                del self.tasks[socket_id]
        self.blocked.intersection_update(active)

    def discard_connection(self, socket_id: str) -> None:
        self.tasks.pop(socket_id, None)
        self.blocked.discard(socket_id)
