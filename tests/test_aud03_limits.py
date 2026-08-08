# coding: utf-8
import base64
import math
import queue
import unittest

from core.protocol import AudioMessage, ProtocolValidationError
from core.server.connection.ws_recv import (
    AudioCache,
    ClientLimitError,
    ServerBusyError,
    _put_task,
)


def valid_message(**overrides):
    data = {
        'task_id': 'task-1',
        'source': 'mic',
        'data': base64.b64encode(b'\0' * 4).decode('ascii'),
        'is_final': False,
        'time_start': 1.0,
        'seg_duration': 60,
        'seg_overlap': 4,
        'context': '',
        'language': 'auto',
    }
    data.update(overrides)
    return data


class AudioMessageValidationTests(unittest.TestCase):
    def assert_invalid(self, **overrides):
        with self.assertRaises(ProtocolValidationError):
            AudioMessage.from_dict(valid_message(**overrides))

    def test_valid_message_is_normalized_and_decoded_once(self):
        message = AudioMessage.from_dict(valid_message())
        self.assertEqual(message.source, 'mic')
        self.assertEqual(message.seg_duration, 60.0)
        self.assertEqual(message.decode_audio(), b'\0' * 4)
        self.assertNotIn('_audio_bytes', message.to_json())

    def test_rejects_invalid_source_and_field_types(self):
        self.assert_invalid(source='socket')
        self.assert_invalid(source=[])
        self.assert_invalid(is_final=1)
        self.assert_invalid(time_start='1')
        self.assert_invalid(time_start=-1)
        self.assert_invalid(time_start=10 ** 1000)
        self.assert_invalid(task_id='')
        self.assert_invalid(language='')

    def test_rejects_malformed_or_misaligned_audio(self):
        self.assert_invalid(data='%%%')
        self.assert_invalid(data=base64.b64encode(b'123').decode('ascii'))
        self.assert_invalid(data='', is_final=False)

    def test_rejects_unsafe_segmentation_values(self):
        self.assert_invalid(seg_duration=0)
        self.assert_invalid(seg_duration=math.inf)
        self.assert_invalid(seg_overlap=-1)
        self.assert_invalid(seg_duration=4, seg_overlap=4)

    def test_rejects_context_and_audio_over_limits(self):
        with self.assertRaises(ProtocolValidationError):
            AudioMessage.from_dict(valid_message(context='12345'), max_context_length=4)
        with self.assertRaises(ProtocolValidationError):
            AudioMessage.from_dict(valid_message(), max_audio_bytes=3)


class ResourceLimitTests(unittest.TestCase):
    def test_audio_cache_uses_bytearray_and_enforces_total_limit(self):
        message = AudioMessage.from_dict(valid_message())
        cache = AudioCache(message)
        cache.append(b'\0' * 4, max_task_audio_bytes=4)
        self.assertIsInstance(cache.chunks, bytearray)
        with self.assertRaises(ClientLimitError):
            cache.append(b'\0' * 4, max_task_audio_bytes=4)

    def test_cache_rejects_metadata_changes_but_allows_timestamp_drift(self):
        message = AudioMessage.from_dict(valid_message())
        cache = AudioCache(message)
        cache.validate_metadata(AudioMessage.from_dict(valid_message(time_start=2.0)))
        with self.assertRaises(ProtocolValidationError):
            cache.validate_metadata(AudioMessage.from_dict(valid_message(seg_duration=30)))

    def test_full_task_queue_reports_server_busy(self):
        task_queue = queue.Queue(maxsize=1)
        task_queue.put_nowait(object())
        with self.assertRaises(ServerBusyError):
            _put_task(task_queue, object())


if __name__ == '__main__':
    unittest.main()
