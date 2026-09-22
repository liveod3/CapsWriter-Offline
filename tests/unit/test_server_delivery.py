"""Bounded server delivery failures with synthetic queues, processes and sockets."""

import asyncio
import base64
import queue
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.protocol import AudioMessage
from core.server.app import CapsWriterServer
from core.server.connection.server_manager import SocketManager
from core.server.connection.ws_recv import ws_recv
from core.server.connection.ws_send import ws_send, _retire_connection
from core.server.delivery import ResultDeliveryError, positive_timeout
from core.server.schema import Result, Task
from core.server.state import WorkerState
from core.server.task_failures import FailedTasks
from core.server.worker.task_handler import TaskHandler
from core.server.worker.worker import RecognizerWorker
from core.server.worker.process_manager import ProcessManager
from core.tools.daemon_executor import SimpleDaemonExecutor


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr('core.server.connection.ws_send.Config.result_send_timeout', 0.01, raising=False)
    monkeypatch.setattr('core.server.connection.ws_send.RESULT_QUEUE_POLL', 0.01)
    monkeypatch.setattr('core.server.connection.ws_send.TASK_ERROR_IO_TIMEOUT', 0.01)
    monkeypatch.setattr('core.server.connection.ws_recv.status_mic', Mock())
    monkeypatch.setattr('core.server.worker.task_handler.GpuMemoryMonitor', Mock())


def socket(socket_id):
    return SimpleNamespace(id=socket_id, send=AsyncMock(), close=AsyncMock(), transport=Mock())


def state():
    sockets = {name: socket(name) for name in ('a', 'b')}
    return SimpleNamespace(queue_out=queue.Queue(), queue_in=queue.Queue(), sockets=sockets,
                           socket_last_activity={'a': 0, 'b': 0}, sockets_id=['a', 'b'],
                           audio_caches={'a': {'task': Mock()}, 'b': {'task': Mock()}},
                           failed_tasks=FailedTasks(), worker_failed=threading.Event(),
                           recognize_process=SimpleNamespace(is_alive=lambda: True))


def result(socket_id='a', task_id='task'):
    return Result(task_id, socket_id, 'mic', text='synthetic private text', is_final=True)


def handler():
    return TaskHandler(queue.Queue(), queue.Queue(), ['a', 'b'], WorkerState(), threading.Event())


def task():
    return Task('mic', b'\0' * 6400, 0, 0, 'task', 'a', True, 0, 0)


@pytest.mark.parametrize('value', [None, True, 0, -1, float('nan'), float('inf'), 'invalid'])
def test_invalid_delivery_limits_fall_back(value):
    assert positive_timeout(SimpleNamespace(value=value), 'value', 10) == 10
    assert positive_timeout(SimpleNamespace(), 'missing', 60) == 60


@pytest.mark.parametrize('stalled', [False, True])
def test_failed_success_send_retires_connection_once_and_delivers_peer(stalled, caplog):
    async def run():
        current = state()
        failed, peer = current.sockets['a'], current.sockets['b']

        async def send(_message):
            if stalled:
                await asyncio.Event().wait()
            raise OSError('synthetic sensitive exception')

        failed.send.side_effect = send
        for item in (result(), result(task_id='later'), result('b'), None):
            current.queue_out.put(item)
        await asyncio.wait_for(ws_send(SimpleNamespace(state=current)), 1)
        failed.send.assert_awaited_once()
        failed.close.assert_awaited_once_with(code=1011, reason='Result delivery failed')
        peer.send.assert_awaited_once()
        peer.close.assert_not_awaited()
        assert set(current.sockets) == {'b'} and current.sockets_id == ['b']
        assert 'a' not in current.audio_caches and 'a' not in current.socket_last_activity

    asyncio.run(run())
    assert 'synthetic private text' not in caplog.text
    assert 'synthetic sensitive exception' not in caplog.text


@pytest.mark.parametrize('error', [OSError, EOFError, ValueError])
def test_broken_result_queue_is_terminal_without_retry(error):
    async def run():
        current = state()
        current.queue_out = Mock()
        current.queue_out.get.side_effect = error('synthetic')
        with pytest.raises(ResultDeliveryError, match='ResultQueueFailed'):
            await asyncio.wait_for(ws_send(SimpleNamespace(state=current)), 1)
        current.queue_out.get.assert_called_once()

    asyncio.run(run())


def test_invalid_internal_result_is_terminal():
    async def run():
        current = state()
        current.queue_out.put(True)
        with pytest.raises(ResultDeliveryError, match='InvalidResultQueueItem'):
            await ws_send(SimpleNamespace(state=current))

    asyncio.run(run())


def test_normal_shutdown_does_not_report_worker_death_as_delivery_failure():
    async def run():
        current = state()
        app = SimpleNamespace(state=current, is_alive=True)
        reading = threading.Event()
        original_get = current.queue_out.get

        def get(**kwargs):
            reading.set()
            return original_get(**kwargs)

        current.queue_out.get = get
        sender = asyncio.create_task(ws_send(app))
        assert await asyncio.to_thread(reading.wait, 1)
        app.is_alive = False
        current.recognize_process.is_alive = lambda: False
        current.worker_failed.set()
        await asyncio.wait_for(sender, 1)

    asyncio.run(run())


@pytest.mark.parametrize('mode', ['dead', 'signaled', 'read_stall'])
def test_worker_health_and_read_deadline_interrupt_one_stuck_read(monkeypatch, mode):
    monkeypatch.setattr('core.server.connection.ws_send.Config.result_queue_timeout', 0.02, raising=False)

    async def run():
        current = state()
        entered, release = threading.Event(), threading.Event()
        alive = True

        def read(**kwargs):
            entered.set()
            assert release.wait(1)
            raise queue.Empty

        current.queue_out = Mock(get=Mock(side_effect=read))
        current.recognize_process.is_alive = lambda: alive
        operation = asyncio.create_task(ws_send(SimpleNamespace(state=current)))
        assert await asyncio.to_thread(entered.wait, 1)
        if mode == 'dead':
            alive = False
        elif mode == 'signaled':
            current.worker_failed.set()
        try:
            with pytest.raises(ResultDeliveryError):
                await asyncio.wait_for(operation, 0.5)
            current.queue_out.get.assert_called_once()
        finally:
            release.set()

    asyncio.run(run())


def test_receiver_cannot_enqueue_after_sender_revokes_ownership():
    async def run():
        current = state()
        current.sockets_id = []
        client = socket('a')
        client.remote_address = ('127.0.0.1', 1234)

        async def recv():
            await _retire_connection(current, client, 'Result delivery failed')
            return AudioMessage('tail', 'mic', base64.b64encode(b'\0' * 6400).decode(), True, 0).to_json()

        client.recv = recv
        await ws_recv(client, SimpleNamespace(state=current))
        assert current.queue_in.empty()
        assert 'a' not in current.sockets and 'a' not in current.socket_last_activity

    asyncio.run(run())


def test_broken_input_submission_signals_service_failure(monkeypatch):
    monkeypatch.setattr('core.server.connection.ws_recv.Config.gpu_boost_enabled', False)

    async def run():
        current = state()
        current.sockets_id = []
        current.queue_in = Mock(put_nowait=Mock(side_effect=OSError('synthetic')))
        client = socket('a')
        client.remote_address = ('127.0.0.1', 1234)
        client.recv = AsyncMock(return_value=AudioMessage(
            'task', 'mic', base64.b64encode(b'\0' * 6400).decode(), True, 0).to_json())
        await asyncio.wait_for(ws_recv(client, SimpleNamespace(state=current)), 1)
        assert current.worker_failed.is_set()
        current.queue_in.put_nowait.assert_called_once()
        assert 'a' not in current.sockets and 'a' not in current.audio_caches

    asyncio.run(run())


@pytest.mark.parametrize('which', ['input', 'output'])
def test_worker_queue_exception_sets_shared_failure_before_cleanup(which):
    worker = handler()
    worker.state.get_session('task', 'a', 'mic')
    if which == 'input':
        worker.queue_in = Mock(get=Mock(side_effect=OSError('synthetic')))
    else:
        worker.buffer.enqueue(task())
        worker.drain_queue = Mock(return_value=True)
        worker.pipeline = SimpleNamespace(process=Mock(return_value=result()))
        worker.queue_out = Mock(put=Mock(side_effect=OSError('synthetic')))
    worker.gpu_monitor.close.side_effect = lambda: worker.failure_event.is_set() or pytest.fail('Signal too late')
    with pytest.raises(ResultDeliveryError):
        worker.loop()
    assert worker.failure_event.is_set()
    assert not worker.state.sessions and worker.buffer.is_empty
    worker.gpu_monitor.close.assert_called_once()
    if which == 'input':
        worker.queue_in.get.assert_called_once()
    else:
        worker.queue_out.put.assert_called_once()


def test_full_worker_output_queue_has_deadline_and_signals_failure():
    worker = handler()
    worker.result_queue_timeout = 0.01
    worker.queue_out = queue.Queue(maxsize=1)
    worker.queue_out.put('occupied')
    worker.buffer.enqueue(task())
    worker.drain_queue = Mock(return_value=True)
    worker.pipeline = SimpleNamespace(process=Mock(return_value=result()))
    with pytest.raises(ResultDeliveryError, match='OutputQueueTimeout'):
        worker.loop()
    assert worker.failure_event.is_set()
    worker.pipeline.process.assert_called_once()
    assert not worker.state.sessions


def test_temporary_output_backpressure_can_recover_without_losing_result():
    worker = handler()
    worker.pipeline = SimpleNamespace(process=Mock(return_value=result()))
    worker.queue_out = Mock(put=Mock(side_effect=[queue.Full, None]))
    worker.handle_audio_task(task())
    assert worker.queue_out.put.call_count == 2
    assert not worker.failure_event.is_set()


@pytest.mark.parametrize('during_init', [False, True])
def test_worker_signals_failure_before_model_cleanup(during_init):
    worker = RecognizerWorker.__new__(RecognizerWorker)
    worker._is_running = False
    worker.failure_event = threading.Event()
    worker.initialize = Mock(side_effect=RuntimeError('synthetic') if during_init else None)
    worker.handler = SimpleNamespace(loop=Mock(side_effect=ResultDeliveryError('OutputQueueFailed')))
    worker.loader = SimpleNamespace(cleanup=Mock())
    worker.loader.cleanup.side_effect = lambda: worker.failure_event.is_set() or pytest.fail('Signal too late')
    with pytest.raises(RuntimeError):
        worker.start()
    worker.loader.cleanup.assert_called_once()
    assert not worker._is_running


def test_socket_manager_closes_all_clients_and_listener_on_shared_fault(monkeypatch):
    async def run():
        current = state()
        clients = list(current.sockets.values())
        current.queue_out = Mock(get=Mock(side_effect=OSError('synthetic')))

        class Server:
            close = Mock()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.close()

        server = Server()
        monkeypatch.setattr('core.server.connection.server_manager.websockets.serve', Mock(return_value=server))
        manager = SocketManager(SimpleNamespace(state=current, loop=asyncio.get_running_loop()))
        manager._prepared = True
        manager._check_port = lambda: True
        await asyncio.wait_for(manager.start(), 1)
        assert manager._delivery_failed and not manager._is_running
        assert manager._server is None and not current.sockets and not current.sockets_id
        assert not current.audio_caches
        for client in clients:
            client.close.assert_awaited_once_with(code=1011, reason='Recognition channel unavailable')
        newcomer = socket('new')
        await manager._handle_connection(newcomer)
        newcomer.close.assert_awaited_once()
        current.queue_out.get.assert_called_once()
        assert server.close.called

    asyncio.run(run())


def test_daemon_read_future_is_not_invalidated_by_async_cancellation():
    executor = SimpleDaemonExecutor()
    entered, release = threading.Event(), threading.Event()

    def read():
        entered.set()
        assert release.wait(1)
        return 'done'

    future = executor.submit(read)
    assert entered.wait(1)
    assert not future.cancel()
    release.set()
    assert future.result(timeout=1) == 'done'


def test_app_finally_stops_components_when_result_service_returns(monkeypatch):
    app = CapsWriterServer.__new__(CapsWriterServer)
    app.is_alive = False
    app.state = SimpleNamespace(queue_out=Mock(put_nowait=Mock(side_effect=OSError('synthetic'))))
    app.process_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app.socket_manager = SimpleNamespace(prepare=Mock(), start=AsyncMock(), stop=Mock())
    app.tray_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app._print_banner = Mock()
    app.loop = asyncio.new_event_loop()
    monkeypatch.setattr('core.server.app.register_signal', Mock())
    try:
        app.start()
        assert not app.is_alive
        app.process_manager.stop.assert_called_once()
        app.tray_manager.stop.assert_called_once()
        app.socket_manager.stop.assert_called_once()
    finally:
        app.loop.close()


def test_startup_runtime_error_is_not_silently_suppressed(monkeypatch):
    app = CapsWriterServer.__new__(CapsWriterServer)
    app.is_alive = False
    app.state = SimpleNamespace(queue_out=Mock())
    app.process_manager = SimpleNamespace(start=Mock(side_effect=RuntimeError('synthetic')), stop=Mock())
    app.socket_manager = SimpleNamespace(prepare=Mock(), start=AsyncMock(), stop=Mock())
    app.tray_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app._print_banner = Mock()
    app.loop = Mock(is_running=Mock(return_value=False))
    monkeypatch.setattr('core.server.app.register_signal', Mock())
    with pytest.raises(RuntimeError, match='synthetic'):
        app.start()
    app.process_manager.stop.assert_called_once()
    app.tray_manager.stop.assert_called_once()
    app.socket_manager.start.assert_not_awaited()


def test_process_shutdown_joins_after_broken_input_queue():
    current = SimpleNamespace(queue_in=Mock(put=Mock(side_effect=OSError('synthetic'))))
    manager = ProcessManager(SimpleNamespace(state=current))
    manager.is_alive = True
    manager._process = SimpleNamespace(pid=123, is_alive=Mock(return_value=True),
                                       terminate=Mock(), join=Mock())
    manager.stop()
    manager._process.terminate.assert_called_once()
    assert manager._process.join.call_count == 2
    assert not manager.is_alive


def test_process_manager_passes_shared_failure_event_to_spawned_worker(monkeypatch):
    current = SimpleNamespace(queue_in=object(), queue_out=object(),
                              align_queue_in=object(), align_queue_out=object())
    manager = ProcessManager(SimpleNamespace(state=current))
    event = threading.Event()
    process = SimpleNamespace(start=Mock(), pid=123)
    constructor = Mock(return_value=process)
    monkeypatch.setattr('core.server.worker.process_manager.Process', constructor)
    monkeypatch.setattr('core.server.worker.process_manager.Event', lambda: event)
    monkeypatch.setattr('core.server.worker.process_manager.Manager', lambda: SimpleNamespace(list=lambda: []))
    monkeypatch.setattr('core.server.worker.process_manager.check_model', Mock())
    monkeypatch.setattr('core.server.worker.process_manager.sys.stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr('core.server.worker.process_manager.threading.Thread', Mock())
    manager._start_aligner_process = Mock()
    manager._wait_for_models = Mock()
    manager.start()
    assert current.worker_failed is event
    assert constructor.call_args.kwargs['args'][-2] is event
    assert constructor.call_args.kwargs['args'][-1] is current.worker_progress
    process.start.assert_called_once()


def test_startup_failure_signal_does_not_wait_for_model_cleanup():
    current = SimpleNamespace(worker_failed=threading.Event(), queue_out=Mock())
    current.worker_failed.set()
    app = SimpleNamespace(state=current, stop=Mock())
    manager = ProcessManager(app)
    manager.is_alive = True
    manager._wait_for_models()
    app.stop.assert_called_once()
    current.queue_out.get.assert_not_called()
