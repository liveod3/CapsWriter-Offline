"""Bounded metadata-only queries, separate from collection and accounting consent."""

from collections import Counter
import math
import statistics
import sqlite3
import time

from .store import NAME, connection, directory_for

DISPLAY_METRICS = (
    'microphone.wake', 'dictation.start_wait', 'dictation.transcribe_wait',
    'dictation.result_queue', 'llm.prepare', 'llm.request', 'llm.total',
    'dictation.output_prepare', 'dictation.insert', 'dictation.post_stop',
)


MAX_TASKS = 10000
MAX_MEASUREMENTS = 200000
MAX_GROUPS_PER_METRIC = 32


def timing_summary(root, config, window=None):
    result = {'state': 'empty', 'enabled': config.get('save_runtime_statistics', True),
              'limited': False, 'groups': [], 'outcomes': {}, 'latest': None, 'tasks': [],
              'count': 0, 'health': {'pending': 0, 'incomplete': 0, 'dropped': 0, 'write_errors': 0,
                                    'not_applicable': 0, 'missing': 0}}
    try:
        path = directory_for(root, config) / NAME
        if not path.exists():
            return result
        with connection(path, readonly=True) as db:
            db.execute('BEGIN')
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            where = 'WHERE started_at>=? AND started_at<?' if window else ''
            params = (window['utc_start'], window['utc_end']) if window else ()
            limit = MAX_TASKS if window else 500
            task_query = f'SELECT task_id FROM tasks {where} ORDER BY started_at DESC,task_id LIMIT {limit}'
            tasks = db.execute(f'SELECT task_id,outcome,complete,started_at,state FROM tasks {where} '
                               f'ORDER BY started_at DESC,task_id LIMIT {limit + 1}', params).fetchall()
            result['limited'] = len(tasks) > limit
            tasks = tasks[:limit]
            task_order = {row[0]: index for index, row in enumerate(tasks)}
            task_dates = {row[0]: row[3] for row in tasks}
            result['count'] = len(tasks)
            result['outcomes'] = dict(Counter(row[1] or 'unfinished' for row in tasks))
            health = result['health']
            health['pending'] = sum(row[1] is None for row in tasks)
            health['incomplete'] = sum(not row[2] for row in tasks)
            run_where = 'WHERE started_at<? AND (ended_at IS NULL OR ended_at>=?)' if window else ''
            run_params = (window['utc_end'], window['utc_start']) if window else ()
            health['dropped'], health['write_errors'] = db.execute(
                'SELECT COALESCE(SUM(dropped),0),COALESCE(SUM(write_errors),0) FROM runs ' + run_where,
                run_params).fetchone()
            if not tasks:
                return result
            result['state'] = 'ready'
            rows = db.execute('SELECT m.metric,m.value_us,m.availability,o.outcome,o.task_id, '
                              "json_extract(q.record,'$.provider'),json_extract(q.record,'$.model'), "
                              'm.metric_version,m.reason,q.id,o.started_offset_us,o.ended_offset_us '
                              'FROM measurements m JOIN operations o USING(operation_id) '
                              'LEFT JOIN llm_requests q ON q.operation_id=COALESCE(o.parent_id,o.operation_id) '
                              f'WHERE o.task_id IN ({task_query}) '
                              f'ORDER BY o.task_id,m.metric,m.metric_version LIMIT {MAX_MEASUREMENTS + 1}', params).fetchall()
            result['limited'] |= len(rows) > MAX_MEASUREMENTS
            rows = rows[:MAX_MEASUREMENTS]
            health['not_applicable'] = sum(row[2] == 'not_applicable' for row in rows)
            health['missing'] = sum(row[2] in ('not_collected', 'lost') for row in rows)
            result['latest'] = {'id': tasks[0][0], 'outcome': tasks[0][1] or 'unfinished',
                'values': {row[0]: round(row[1] / 1000, 3) for row in rows
                           if row[4] == tasks[0][0] and row[0] in DISPLAY_METRICS
                           and row[2] == 'observed' and row[7] == 1}}
            by_task = {}
            grouped = {}
            for row in rows:
                by_task.setdefault(row[4], []).append(row)
                grouped.setdefault((row[0], row[5], row[6], row[7]), []).append(row)
            result['tasks'] = [
                {'id': task[0], 'outcome': task[1] or 'unfinished', 'started_at': task[3],
                 'complete': bool(task[2]), 'operations': [
                     {'metric': row[0], 'value_ms': round(row[1] / 1000, 3) if row[1] is not None else None,
                      'availability': row[2], 'outcome': row[3], 'provider': row[5], 'model': row[6],
                      'version': row[7], 'reason': row[8], 'request_id': row[9]}
                     for row in by_task.get(task[0], [])[:128]]}
                for task in tasks[:50]]
            result['limited'] |= any(len(by_task.get(task[0], [])) > 128 for task in tasks[:50])
            for metric in DISPLAY_METRICS:
                groups = [key for key in grouped if key[0] == metric] or [(metric, None, None, 1)]
                result['limited'] |= len(groups) > MAX_GROUPS_PER_METRIC
                for _, provider, model, version in sorted(groups, key=str)[:MAX_GROUPS_PER_METRIC]:
                    selected = grouped.get((metric, provider, model, version), [])
                    values = sorted(row[1] / 1000 for row in selected
                                    if row[2] == 'observed' and row[3] in ('completed', 'fallback'))
                    # Task start order is comparable across runs; per-run offsets are not.
                    recent = min(selected, key=lambda row: (
                        task_order[row[4]], -(row[11] or row[10] or 0), row[9] or '')) if selected else None
                    result['groups'].append({'metric': metric, 'version': version,
                        'provider': provider, 'model': model, 'count': len(values),
                        'median': round(statistics.median(values), 3) if values else None,
                        'mean': round(statistics.fmean(values), 3) if values else None,
                        'max': round(values[-1], 3) if values else None,
                        'p95': round(values[math.ceil(len(values) * .95) - 1], 3) if values else None,
                        'recent': {'value_ms': round(recent[1] / 1000, 3) if recent[1] is not None else None,
                                   'availability': recent[2], 'outcome': recent[3], 'task_id': recent[4],
                                   'started_at': task_dates[recent[4]]} if recent else None,
                        'missing': sum(row[2] != 'observed' for row in selected),
                        'not_applicable': sum(row[2] == 'not_applicable' for row in selected),
                        'unavailable': sum(row[2] in ('not_collected', 'lost') for row in selected),
                        'failed': sum(row[3] not in ('completed', 'fallback', 'skipped') for row in selected)})
    except (OSError, ValueError, TypeError, sqlite3.Error):
        result['state'] = 'unavailable'
    return result


def task_detail(directory, task_id):
    with connection(directory / NAME, readonly=True) as db:
        db.row_factory = __import__('sqlite3').Row
        task = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        rows = db.execute('SELECT o.*,m.metric,m.metric_version,m.value_us,m.availability,m.reason '
                          'FROM operations o LEFT JOIN measurements m USING(operation_id) '
                          'WHERE o.task_id=? LIMIT 2001', (task_id,)).fetchall()
        return {'task': dict(task) if task else None, 'operations': [dict(row) for row in rows[:2000]],
                'limited': len(rows) > 2000}
