"""Opt-in loopback stub for manually reviewing task failure feedback.

Stop the regular server first. This stub fails every new task, saves no audio,
and never loads a model. It exercises client presentation, not server inference.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.protocol import AudioMessage, RecognitionMessage
from core.server.task_failures import FailedTasks


async def reject_tasks(websocket):
    failures = FailedTasks()
    async for raw in websocket:
        message = AudioMessage.from_dict(json.loads(raw))
        if failures.contains('manual', message.task_id):
            continue
        at_limit = failures.add('manual', message.task_id)
        if not message.supports_task_errors:
            await websocket.close(code=1011, reason='Recognition task failed')
            return
        now = time.time()
        await websocket.send(RecognitionMessage(
            task_id=message.task_id, is_final=True, duration=0,
            time_start=message.time_start, time_submit=now, time_complete=now,
            text='', error_code='recognition_failed',
        ).to_json())
        print(f'Injected task error: source={message.source} task={message.task_id[:8]}')
        if at_limit:
            await websocket.close(code=1011, reason='Task failure limit reached; reconnect')
            return


async def serve(port):
    async with websockets.serve(reject_tasks, '127.0.0.1', port, subprotocols=['binary'],
                                max_size=16 * 1024 * 1024):
        print(f'Task-error stub listening on 127.0.0.1:{port}; every task will fail.')
        print('Press Ctrl+C to stop, then restart the regular server.')
        await asyncio.Event().wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=6016)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.port))
    except KeyboardInterrupt:
        pass
