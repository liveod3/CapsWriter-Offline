# coding: utf-8
"""
Audio stream management.

Use AudioStreamManager to create, start, and stop input streams
and detect input devices.
"""

from __future__ import annotations

from core.i18n import Notice, tr

import time
import threading
from functools import partial
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from config_client import ClientConfig as Config
from core.client.state import console
from core.ui.recording_indicator import show_status_hint
from . import logger
from .portaudio_compat import refresh_devices

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    Manage audio input streams.
    
    Own the input stream lifecycle:
    - Detect and select input devices.
    - Create and start streams.
    - Handle audio callbacks.
    - Restart and close streams.
    - Refresh PortAudio device enumeration while idle.
    
    Attributes:
        state: Client state instance.
        sample_rate: Sample rate in Hz (default: 48000).
        block_duration: Block duration in seconds (default: 0.05).
    """
    
    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    
    def __init__(self, app: CapsWriterClient):
        """
        Initialize stream management.
        
        Args:
            app: Client App instance.
        """
        self.app = app
        # Lifecycle operations can nest, such as reopen() calling stop() and start().
        self._stream_lock = threading.RLock()
        self._ready_event = threading.Event()
        self._channels = 1
        self._running = False  # Whether the stream should run.
        self._last_input_device = None
        self._monitor_thread = None
        self._monitor_running = False
        self._shutdown = threading.Event()
        self._monitor_wakeup = threading.Event()
        self._recovery_requested = None
        self._last_recovery = 0.0

    @property
    def state(self) -> ClientState:
        """Access shared client state."""
        return self.app.state

    @staticmethod
    def _get_input_device_selector():
        """Resolve the input device; empty or legacy settings use the system default."""
        selector = getattr(Config, 'input_device', None)
        if isinstance(selector, str):
            selector = selector.strip()
            return selector or None
        return selector

    def get_ready_event(self) -> threading.Event:
        """Return the stream readiness event for asynchronous UI feedback."""
        return self._ready_event

    def is_ready(self, ready_event: Optional[threading.Event] = None) -> bool:
        """Return whether the stream has delivered its first audio callback."""
        event = ready_event or self._ready_event
        return self._running and event is self._ready_event and event.is_set()

    def _commit_input_device(self, device_name: str) -> None:
        """Remember the selected device and report changes consistently."""
        previous_device = self._last_input_device
        self._last_input_device = device_name
        if not previous_device or previous_device == device_name:
            return

        message = tr('audio.changed', value0=device_name)
        logger.info(Notice('diagnostic.stream.input_audio_device_changed', value0=previous_device, value1=device_name))
        console.print(
            tr('audio.changed_console', value0=previous_device, value1=device_name)
        )
        show_status_hint(message, duration_ms=2600, dot_color='#F59E0B')

    def _handle_monitored_device(self, device_name: str) -> None:
        """Update observed devices without reopening the microphone while suspended."""
        if self._shutdown.is_set() or not device_name or self.state.recording:
            return

        if self._last_input_device and device_name != self._last_input_device:
            if self.state.dictation_paused:
                self._commit_input_device(device_name)
            else:
                logger.info(
                    Notice('diagnostic.stream.input_device_change_detected_reopening_audio_stream', value0=self._last_input_device, value1=device_name)
                )
                self.reopen()
            return

        # Retry failed stream recovery once the device becomes available.
        if not self._running and not self.state.dictation_paused:
            self.start(silent=True)

    def _query_monitored_input_device(self):
        """Query the target device, refreshing PortAudio when suspended without a stream."""
        if not self._running and self.state.stream is None:
            refresh_devices(sd)

        return sd.query_devices(
            device=self._get_input_device_selector(),
            kind='input'
        )
    
    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
        ready_event: Optional[threading.Event] = None,
    ) -> None:
        """
        Handle incoming audio.
        
        Place new audio samples in the asynchronous queue.
        """
        capture = getattr(self.state, 'capture', None)
        # start() does not establish hardware readiness; the first callback does.
        event = ready_event or self._ready_event
        if self._shutdown.is_set() or event is not self._ready_event:
            return
        if not event.is_set():
            event.set()

        # Process samples only while recording.
        if not self.state.recording:
            return
        
        # A retained snapshot can only append to its own recording. finish()
        # closes that bridge before another shortcut can publish a new one.
        if capture is not None:
            capture.push_audio(indata, time.time())
    
    def _on_stream_finished(self, ready_event=None) -> None:
        """Notify the single recovery owner; never close or log in this callback."""
        event = ready_event or self._ready_event
        if (self._shutdown.is_set() or not self._running
                or event is not self._ready_event):
            return
        self._recovery_requested = event
        self._monitor_wakeup.set()

    def _cancel_interrupted_capture(self):
        """Cancel only the capture interrupted by this device failure."""
        capture = getattr(self.state, 'capture', None)
        if capture is None:
            return

        def cancel():
            with self.state.recording_lock:
                if self.state.capture is not capture:
                    return
                owner = self.state.recording_owner
                if owner is not None:
                    owner.cancel()
                    show_status_hint(tr('mic.disconnected'),
                                     duration_ms=3000, dot_color='#EF4444')
        try:
            self.app.loop.call_soon_threadsafe(cancel)
        except RuntimeError:
            pass

    def _ensure_monitor_locked(self):
        if self._shutdown.is_set():
            return
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_running = True
            return
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._device_monitor_loop, daemon=True, name='audio-device-monitor')
        self._monitor_thread.start()

    def _device_monitor_loop(self) -> None:
        """Monitor default input device changes in the background."""
        while self._monitor_running and not self._shutdown.is_set():
            self._monitor_wakeup.wait(4.0)
            self._monitor_wakeup.clear()
            if not self._monitor_running or self._shutdown.is_set():
                break
            # Repeated backend failures must not create a hot restart loop.
            delay = 1.0 - (time.monotonic() - self._last_recovery)
            if delay > 0 and self._shutdown.wait(delay):
                break
            with self._stream_lock:
                event = self._recovery_requested
                self._recovery_requested = None
                if (event is self._ready_event and event is not None
                        and self._running and not self.state.dictation_paused):
                    self._last_recovery = time.monotonic()
                    self._cancel_interrupted_capture()
                    try:
                        self.reopen()
                    except Exception as exc:
                        logger.warning(Notice('diagnostic.stream.audio_recovery_failed'), type(exc).__name__)
                    continue
            
            # Do not interrupt the stream during recording.
            # Read device changes while suspended without reopening the microphone.
            if self.state.recording:
                continue
                
            try:
                # Hold the lock to serialize queries with PortAudio reinitialization in reopen().
                with self._stream_lock:
                    if self._shutdown.is_set() or not self._monitor_running:
                        break
                    device = self._query_monitored_input_device()
                    current_device_name = device.get('name')
                    self._handle_monitored_device(current_device_name)

            except Exception as e:
                logger.debug(Notice('diagnostic.stream.hardware_monitor_loop_failed', value0=e))
                # Attempt stream recovery after unexpected errors.
                if (not self._running) and (not self.state.dictation_paused):
                    self.start(silent=True)

    def start(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """Start the stream under the lifecycle lock."""
        with self._stream_lock:
            if self._shutdown.is_set():
                return None
            self._ensure_monitor_locked()
            return self._start_locked(silent=silent, force=force)

    def _start_locked(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """
        Start the input stream.
        
        Args:
            silent: Suppress console feedback about device selection.
            
        Returns:
            Created input stream, or None on failure.
        """
        if self._shutdown.is_set():
            return None
        if self._running:
            logger.debug(Notice('diagnostic.stream.audio_stream_already_running_startup_skipped'))
            return self.state.stream

        if self.state.dictation_paused and not force:
            logger.debug(Notice('diagnostic.stream.dictation_suspended_audio_stream_startup_skipped'))
            return None
        if self.state.stream is not None:
            try:
                self._stop_locked(keep_monitor=True)
            except Exception:
                return None
            
        # Detect input devices.
        device_selector = self._get_input_device_selector()
        try:
            device = sd.query_devices(device=device_selector, kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', tr('audio.unknown_device'))
            selection_mode = Notice('audio.system_default' if device_selector is None else 'audio.configured')
            
            if not silent:
                console.print(
                    tr('audio.device_console', value0=device_name, value1=selection_mode, value2=self._channels),
                    end='\n\n'
                )
            logger.info(
                Notice('diagnostic.stream.audio_device_found_channels_selection', value0=device_name, value1=self._channels, value2=selection_mode)
            )
        except UnicodeDecodeError:
            logger.warning(Notice('diagnostic.stream.could_not_decode_input_device_information'))
            return None
        except (ValueError, sd.PortAudioError) as e:
            if device_selector is None:
                logger.error(Notice('diagnostic.stream.system_default_microphone_not_found', value0=e))
            else:
                logger.error(Notice('diagnostic.stream.selected_microphone_not_found', value0=device_selector, value1=e))
            return None
        
        # Create the input stream.
        stream = None
        try:
            ready_event = threading.Event()
            self._ready_event = ready_event
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=device_selector,
                dtype="float32",
                channels=self._channels,
                callback=partial(self._audio_callback, ready_event=ready_event),
                finished_callback=partial(self._on_stream_finished, ready_event=ready_event),
            )
            self.state.stream = stream
            self._running = True
            stream.start()
            if self._shutdown.is_set():
                self._stop_locked(keep_monitor=False)
                return None
            
            self.state.stream = stream
            self._running = True
            self._commit_input_device(device_name)
            logger.debug(
                Notice('diagnostic.stream.audio_stream_started_sample_rate_block_size', value0=self.SAMPLE_RATE, value1=int(self.BLOCK_DURATION * self.SAMPLE_RATE))
            )

            return stream
            
        except Exception as e:
            logger.error(Notice('diagnostic.stream.failed_to_create_audio_stream', value0=e), exc_info=True)
            self._running = False
            self._ready_event = threading.Event()
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.warning(Notice('diagnostic.stream.failed_to_close_partially_started_input_stream'))
                else:
                    self.state.stream = None
            return None
    
    def stop(self, keep_monitor: bool = False) -> None:
        """Stop the stream under the lifecycle lock."""
        try:
            with self._stream_lock:
                self._stop_locked(keep_monitor=keep_monitor)
        finally:
            if not keep_monitor:
                self._join_monitor()

    def _stop_locked(self, keep_monitor: bool = False) -> None:
        """
        Stop the input stream.
        
        Args:
            keep_monitor: Keep monitoring enabled while rebuilding the stream.
        """
        self._ready_event = threading.Event()
        self._recovery_requested = None
            
        self._running = False  # Mark the stream as stopped.

        # Stop monitoring only when fully releasing hardware resources.
        if not keep_monitor:
            self._monitor_running = False
            self._monitor_wakeup.set()

        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug(Notice('diagnostic.stream.audio_stream_stopped'))
            except Exception as e:
                logger.debug(Notice('diagnostic.stream.failed_to_stop_audio_stream', value0=e))
                raise
            else:
                self.state.stream = None

    def _join_monitor(self):
        thread = self._monitor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
            if not thread.is_alive():
                self._monitor_thread = None
            else:
                logger.warning(Notice('diagnostic.stream.audio_monitor_has_not_exited_before_the_shutdown'))

    def request_shutdown(self):
        """Publish the permanent stop barrier before waiting for backend calls."""
        self._shutdown.set()
        self._monitor_wakeup.set()

    def close(self):
        """Stop the backend and join the one monitor/recovery owner."""
        self.request_shutdown()
        self.stop()
    
    def reopen(self) -> Optional[sd.InputStream]:
        """
        Restart the input stream.
        
        Returns:
            Newly created input stream.
        """
        logger.info(Notice('diagnostic.stream.restarting_audio_stream'))
        
        with self._stream_lock:
            if self._shutdown.is_set() or self.state.dictation_paused:
                return None
            # Stop the old stream while keeping the monitor alive.
            self.stop(keep_monitor=True)

            # Refresh PortAudio device enumeration.
            # Do not unload/reload DLLs through private sd._ffi.dlclose/dlopen calls.
            # On Windows these can race with monitoring and cause access violations
            # that terminate the process without a Python exception.
            # sd._terminate() and sd._initialize() are sufficient to refresh enumeration.
            refresh_devices(sd)

            # Allow the device to settle.
            if self._shutdown.wait(0.1):
                return None

            # Start the replacement stream.
            return self.start()
