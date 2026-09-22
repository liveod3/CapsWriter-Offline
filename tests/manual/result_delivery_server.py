"""Loopback-only server-channel failure fixture; no models or audio files are used.

Stop the regular server first. The first submitted audio task triggers a shared
channel failure and shuts this fixture down. Restart the regular server afterward.
"""

import argparse
import asyncio
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config_server import ServerConfig as Config
from core.server.connection.server_manager import SocketManager
from core.server.task_failures import FailedTasks


class FaultQueue(queue.Queue):
    def __init__(self):
        super().__init__(maxsize=4)
        self.failed = threading.Event()

    def get(self, *args, **kwargs):
        if self.failed.is_set():
            raise OSError('SyntheticResultQueueFailure')
        return super().get(*args, **kwargs)


class TriggerQueue:
    def __init__(self, trigger):
        self.trigger = trigger

    def put_nowait(self, task):
        if task.type != 'cmd':
            self.trigger.set()
            print(f'Injected shared-channel failure: task={task.task_id[:8]}')


async def serve(port, mode):
    # Override only this isolated process, without modifying local configuration.
    Config.network_mode, Config.addr, Config.port = 'local', '127.0.0.1', port
    Config.tls_certfile = Config.tls_keyfile = ''
    output = FaultQueue()
    worker_failed = threading.Event()
    state = SimpleNamespace(
        queue_in=TriggerQueue(output.failed if mode == 'queue' else worker_failed),
        queue_out=output, worker_failed=worker_failed, recognize_process=None,
        sockets={}, sockets_id=[], socket_last_activity={}, audio_caches={},
        failed_tasks=FailedTasks(),
    )
    app = SimpleNamespace(state=state, loop=asyncio.get_running_loop(), is_alive=True)
    print(f'Failure fixture on 127.0.0.1:{port}; mode={mode}; no models are loaded.')
    print('Submit a short dictation or file task to trigger service shutdown.')
    await SocketManager(app).start()
    print('Failure fixture stopped. Restart the regular server to test recovery.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=6016)
    parser.add_argument('--mode', choices=('queue', 'worker'), default='queue')
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.port, args.mode))
    except KeyboardInterrupt:
        pass
