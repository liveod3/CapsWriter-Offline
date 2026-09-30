"""Bounded observation ownership. No database I/O runs on audio or shortcut callbacks."""

import logging
import queue
import threading
import time
import uuid

from core.i18n import Notice
from .schema import METRICS
from .store import NAME, connection, directory_for, utcnow, write_request


def observe(app, method, *args, **kwargs):
    """Optional facade for existing hosts and synthetic test applications."""
    runtime = getattr(app, 'activity', None)
    if runtime is not None:
        try:
            return getattr(runtime, method)(*args, **kwargs)
        except Exception:
            runtime.report_error()


class ActivityRecorder:
    def __init__(self, root, config, *, app_version='unknown', capacity=512):
        self.config = config
        self.root = root
        self.run_id = uuid.uuid4().hex
        self.app_version = app_version
        self.started_at = utcnow()
        self.epoch = time.perf_counter_ns()
        self.queue = queue.Queue(capacity)
        self.lock = threading.RLock()
        self.tasks = {}
        self.dropped = 0
        self.write_errors = 0
        self.closed = False
        self.thread = None
        try:
            self.path = directory_for(root, config) / NAME
        except Exception:
            self.path = root / 'llm-costs' / NAME
            self.report_error()

    def enabled(self):
        return bool(getattr(self.config, 'save_runtime_statistics', True))

    def report_error(self):
        self.write_errors += 1
        if self.write_errors == 1:
            logging.getLogger('client').warning(Notice('activity.write_failed'))

    def _submit(self, kind, data):
        with self.lock:
            if self.closed:
                return
            if self.thread is None:
                self.thread = threading.Thread(target=self._writer, daemon=True, name='activity-writer')
                self.thread.start()
            try:
                self.queue.put_nowait((kind, data))
            except queue.Full:
                self.dropped += 1
                if self.dropped == 1:
                    logging.getLogger('client').warning(Notice('activity.dropped'))

    def begin(self, task_id, *, now=None):
        if not self.enabled():
            return
        with self.lock:
            if task_id in self.tasks:
                return
            if len(self.tasks) >= 128:
                self.dropped += 1
                return
            self.tasks[task_id] = {'start': now or time.perf_counter_ns()}
            self._submit('begin', (task_id, utcnow()))

    def mark(self, task_id, name, *, now=None):
        with self.lock:
            state = self.tasks.get(task_id)
            if state is None or name in state:
                return
            now = now or time.perf_counter_ns()
            state[name] = now
            if name == 'stop':
                self._submit('stop', (task_id, utcnow()))
            pairs = {
                'ready': ('dictation.start_wait', 'start'),
                'asr': ('dictation.transcribe_wait', 'stop'),
                'processing': ('dictation.result_queue', 'asr'),
                'insert': ('dictation.output_prepare', 'llm_returned'),
                'output_skipped': ('dictation.output_prepare', 'llm_returned'),
                'insert_done': ('dictation.insert', 'insert'),
                'udp_done': ('dictation.udp', 'udp'),
                'enter_done': ('dictation.auto_enter', 'enter'),
            }
            if name in pairs:
                metric, start = pairs[name]
                if start in state:
                    self.measure(task_id, metric, state[start], now)
            if name == 'output_skipped':
                self.measure(task_id, 'dictation.insert', None, None, outcome='skipped',
                             availability='not_applicable', reason='output_not_requested')

    def llm_skipped(self, task_id):
        if task_id in self.tasks:
            for metric in ('llm.prepare', 'llm.request', 'llm.total'):
                self.measure(task_id, metric, None, None, outcome='skipped',
                             availability='not_applicable', reason='no_llm_action')

    def ready(self, task_id, *, wake_started=None, ready_at=None, warm=False):
        with self.lock:
            state = self.tasks.get(task_id)
            if state is None or 'ready' in state:
                return
            now = max(state['start'], ready_at or time.perf_counter_ns())
            self.mark(task_id, 'ready', now=now)
            self.measure(task_id, 'microphone.wake', wake_started, ready_at,
                         availability='not_applicable' if warm else 'observed'
                         if wake_started and ready_at else 'not_collected', reason='already_ready' if warm else None)

    def measure(self, task_id, metric, start, end, *, outcome='completed',
                availability='observed', reason=None, operation_id=None, measured_us=None, parent_id=None):
        if metric not in METRICS:
            self.report_error()
            return
        if availability == 'observed' and measured_us is None and (start is None or end is None or end < start):
            availability, reason = 'lost', 'invalid_clock_boundary'
            start = end = None
        operation_id = operation_id or uuid.uuid5(uuid.UUID(self.run_id), str(task_id) + metric).hex
        self._submit('measurement', (operation_id, task_id, metric,
            max(0, (start - self.epoch) // 1000) if start is not None else None,
            max(0, (end - self.epoch) // 1000) if end is not None else None,
            (measured_us if measured_us is not None else (end - start) // 1000) if availability == 'observed' else None,
            availability, reason, outcome, parent_id))

    def finish(self, task_id, outcome, error=None):
        with self.lock:
            state = self.tasks.pop(task_id, None)
            if state is None:
                return
            now = time.perf_counter_ns()
            if 'stop' in state:
                self.measure(task_id, 'dictation.post_stop', state['stop'],
                             state.get('insert_done', state.get('output_skipped', now)),
                             outcome=outcome)
            if 'ready' not in state:
                self.measure(task_id, 'dictation.start_wait', state['start'], now, outcome=outcome)
                if 'wake' in state:
                    self.measure(task_id, 'microphone.wake', state['wake'], now, outcome=outcome)
            for metric, start, end in (('dictation.transcribe_wait', 'stop', 'asr'),
                    ('dictation.insert', 'insert', 'insert_done'), ('dictation.udp', 'udp', 'udp_done'),
                    ('dictation.auto_enter', 'enter', 'enter_done')):
                if start in state and end not in state:
                    self.measure(task_id, metric, state[start], now, outcome=outcome)
            self._submit('finish', (task_id, utcnow(), outcome, error))

    def llm(self, diagnostic, observed):
        if not self.enabled():
            return
        task_id = diagnostic.task_id if diagnostic.task_id in self.tasks else None
        request_id = diagnostic.request_id
        outcome = diagnostic.terminal_outcome
        total = diagnostic.elapsed_ms
        # The diagnostics clock is perf_counter; epoch uses its nanosecond counterpart.
        start = int(diagnostic.started * 1e9)
        end = start + int(total * 1e6)
        operation_id = uuid.uuid5(uuid.UUID(self.run_id), request_id).hex
        self.measure(task_id, 'llm.total', start, end, outcome=outcome, operation_id=operation_id)
        for metric, bounds in diagnostic.action_timings.items():
            phase_outcome = 'completed' if metric == 'llm.prepare' else getattr(diagnostic, 'request_outcome', outcome)
            self.measure(task_id, metric, *bounds, outcome=phase_outcome,
                         operation_id=uuid.uuid5(uuid.UUID(operation_id), metric).hex, parent_id=operation_id)
        if not diagnostic.action_timings:
            self.measure(task_id, 'llm.prepare', start, end, outcome=outcome,
                         operation_id=uuid.uuid5(uuid.UUID(operation_id), 'llm.prepare').hex, parent_id=operation_id)
            self.measure(task_id, 'llm.request', None, None, outcome=outcome,
                         availability='not_applicable', reason='transport_not_called',
                         operation_id=uuid.uuid5(uuid.UUID(operation_id), 'llm.request').hex, parent_id=operation_id)
        for phase, duration in diagnostic.stage_ms.items():
            metric = 'llm.stage.' + phase
            phase_outcome = outcome if phase == diagnostic.phase else 'completed'
            self.measure(task_id, metric, None, None, outcome=phase_outcome, measured_us=int(duration * 1000),
                         operation_id=uuid.uuid5(uuid.UUID(operation_id), metric).hex, parent_id=operation_id)
        identity = diagnostic.identity
        record = {'schema_version': 1, 'id': request_id, 'started_at': diagnostic.started_at,
                  'finished_at': utcnow(), 'status': outcome, 'elapsed_ms': total,
                  'provider': identity.get('provider'), 'model': identity.get('model'),
                  'preset': identity.get('preset'), 'config_revision': identity.get('config_revision'),
                  'http_status': diagnostic.data.get('http_status'),
                  'dispatch': 'attempted' if observed.sent else 'not_sent',
                  'error_category': diagnostic.terminal_category,
                  'usage': dict(observed.usage), 'usage_invalid': observed.invalid_usage}
        self._submit('llm', (record, operation_id))

    def _writer(self):
        while True:
            kind, data = self.queue.get()
            try:
                if kind == 'barrier':
                    data.set()
                    continue
                self.path = self.path or directory_for(self.root, self.config) / NAME
                with connection(self.path) as db:
                    info = time.get_clock_info('perf_counter')
                    db.execute('INSERT OR IGNORE INTO runs(run_id,component,app_version,started_at,clock,resolution_ns) '
                               'VALUES (?,?,?,?,?,?)', (self.run_id, 'client', self.app_version, self.started_at,
                               info.implementation, max(1, round(info.resolution * 1e9))))
                    self._write(db, kind, data)
                    db.execute('UPDATE runs SET dropped=?,write_errors=? WHERE run_id=?',
                               (self.dropped, self.write_errors, self.run_id))
            except Exception:
                self.report_error()
            finally:
                self.queue.task_done()
            if kind == 'close':
                return

    def _write(self, db, kind, data):
        if kind == 'begin':
            db.execute('INSERT OR IGNORE INTO tasks(task_id,run_id,started_at,state) VALUES (?,?,?,?)',
                       (data[0], self.run_id, data[1], 'running'))
        elif kind == 'stop':
            db.execute('UPDATE tasks SET stopped_at=? WHERE task_id=?', (data[1], data[0]))
        elif kind == 'finish':
            db.execute('UPDATE tasks SET ended_at=?,state=?,outcome=?,error_code=?,complete=? '
                       'WHERE task_id=? AND state=?',
                       (data[1], 'interrupted' if data[2] == 'interrupted' else 'finished', data[2], data[3],
                        int(not (self.dropped or self.write_errors)), data[0], 'running'))
        elif kind == 'measurement':
            identifier, task, metric, start, end, value, availability, reason, outcome, parent = data
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id) DO NOTHING',
                       (identifier, task, self.run_id, parent, metric, start, end, outcome, None))
            db.execute('INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(measurement_id) DO NOTHING',
                       (identifier, identifier, metric, 1, value, availability, 'client', 1, reason))
        elif kind == 'llm':
            write_request(db, data[0], enabled=False, operation_id=data[1])
        elif kind == 'close':
            db.execute('UPDATE runs SET ended_at=? WHERE run_id=?', (utcnow(), self.run_id))

    def flush(self, timeout=5):
        if self.thread is None:
            return True
        event = threading.Event()
        self.queue.put(('barrier', event), timeout=timeout)
        return event.wait(timeout)

    def close(self):
        with self.lock:
            if self.closed:
                return
            for task_id in tuple(self.tasks):
                self.finish(task_id, 'interrupted')
            self.closed = True
        if self.thread:
            try:
                self.queue.put(('close', None), timeout=2)
                self.thread.join(timeout=3)
                if self.thread.is_alive():
                    self.report_error()
            except queue.Full:
                self.report_error()
