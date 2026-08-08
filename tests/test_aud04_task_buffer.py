# coding: utf-8
import unittest

from core.server.schema import Task
from core.server.state import WorkerState
from core.server.worker.task_handler import TaskBuffer


def make_task(task_id: str, sequence: int, socket_id: str | None = None) -> Task:
    return Task(
        type='mic',
        data=sequence.to_bytes(1, 'little'),
        offset=float(sequence),
        overlap=0.0,
        task_id=task_id,
        socket_id=socket_id or f'socket-{task_id}',
        is_final=False,
        time_start=0.0,
        time_submit=float(sequence),
    )


class TaskBufferTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkerState()
        self.buffer = TaskBuffer(self.state)

    def enqueue(self, task_id: str, sequence: int, socket_id: str | None = None):
        self.buffer.enqueue(make_task(task_id, sequence, socket_id))

    def test_single_task_preserves_fifo(self):
        for sequence in range(3):
            self.enqueue('task-a', sequence)

        popped = [self.buffer.pop().offset for _ in range(3)]

        self.assertEqual(popped, [0.0, 1.0, 2.0])
        self.assertTrue(self.buffer.is_empty)

    def test_three_tasks_are_scheduled_round_robin(self):
        for task_id in ('task-a', 'task-b', 'task-c'):
            self.enqueue(task_id, 0)
            self.enqueue(task_id, 1)

        popped = [self.buffer.pop() for _ in range(6)]

        self.assertEqual(
            [(task.task_id, task.offset) for task in popped],
            [
                ('task-a', 0.0),
                ('task-b', 0.0),
                ('task-c', 0.0),
                ('task-a', 1.0),
                ('task-b', 1.0),
                ('task-c', 1.0),
            ],
        )

    def test_continuous_newest_producer_does_not_starve_older_tasks(self):
        for task_id in ('task-a', 'task-b'):
            self.enqueue(task_id, 0)
            self.enqueue(task_id, 1)
        self.enqueue('task-c', 0)

        popped = []
        for sequence in range(1, 6):
            popped.append(self.buffer.pop())
            self.enqueue('task-c', sequence)

        self.assertEqual(
            [(task.task_id, task.offset) for task in popped],
            [
                ('task-a', 0.0),
                ('task-b', 0.0),
                ('task-c', 0.0),
                ('task-a', 1.0),
                ('task-b', 1.0),
            ],
        )

    def test_cleanup_removes_disconnected_task_without_breaking_rotation(self):
        self.enqueue('task-a', 0, 'socket-a')
        self.enqueue('task-a', 1, 'socket-a')
        self.enqueue('task-b', 0, 'socket-b')
        self.enqueue('task-b', 1, 'socket-b')

        self.state.cleanup_sessions(['socket-b'])
        self.buffer.cleanup_tasks()

        self.assertEqual(self.buffer.task_count, 2)
        self.assertEqual(
            [self.buffer.pop().task_id, self.buffer.pop().task_id],
            ['task-b', 'task-b'],
        )
        self.assertTrue(self.buffer.is_empty)


if __name__ == '__main__':
    unittest.main()
