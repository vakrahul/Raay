"""Pluggable audio device backends and native Windows WinMM implementation (Milestone 2).

Uses Windows Multimedia API (`waveIn*` and `waveOut*`) via `ctypes` with Win32 event
signaling (`CALLBACK_EVENT`), avoiding C-callback GIL contention and requiring zero
third-party native extensions.
"""

import ctypes
import os
import threading
from abc import ABC, abstractmethod
from ctypes import wintypes
from typing import List, Optional

from src.voice.audio_types import (
    AudioDeviceError,
    AudioDeviceInfo,
    AudioDeviceKind,
    AudioDeviceUnavailableError,
    AudioFormatConfig,
    AudioFormatError,
    AudioPermissionError,
)

# WinMM Constants
WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_EVENT = 0x00050000
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002

MMSYSERR_NOERROR = 0
MMSYSERR_ERROR = 1
MMSYSERR_BADDEVICEID = 2
MMSYSERR_NOTENABLED = 3
MMSYSERR_ALLOCATED = 4
MMSYSERR_INVALHANDLE = 5
MMSYSERR_NODRIVER = 6
MMSYSERR_NOMEM = 7
WAVERR_BADFORMAT = 32
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


class WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


def _map_winmm_error(mmresult: int, operation: str) -> AudioDeviceError:
    if mmresult in (MMSYSERR_NODRIVER, MMSYSERR_BADDEVICEID):
        return AudioDeviceUnavailableError(
            code="AUDIO_DEVICE_UNAVAILABLE",
            message=f"Audio device unavailable during {operation} (WinMM code {mmresult}).",
        )
    if mmresult in (MMSYSERR_ALLOCATED, MMSYSERR_NOTENABLED):
        return AudioPermissionError(
            code="AUDIO_DEVICE_ACCESS_DENIED",
            message=(
                f"Audio device access denied or in exclusive use during {operation} "
                f"(WinMM code {mmresult})."
            ),
        )
    if mmresult == WAVERR_BADFORMAT:
        return AudioFormatError(
            code="AUDIO_UNSUPPORTED_HARDWARE_FORMAT",
            message=(
                f"Hardware rejected requested PCM format during {operation} (WAVERR_BADFORMAT)."
            ),
        )
    return AudioDeviceError(
        code=f"AUDIO_WINMM_{mmresult}",
        message=f"WinMM error {mmresult} during {operation}.",
    )


# ---------------------------------------------------------------------------
# Abstract Audio Input & Output Backend Interfaces
# ---------------------------------------------------------------------------


class BaseAudioInputBackend(ABC):
    """Pluggable interface for microphone hardware or deterministic test doubles."""

    @abstractmethod
    def list_devices(self) -> List[AudioDeviceInfo]:
        """Enumerate available audio input devices."""

    @abstractmethod
    def open_stream(self, device_id: Optional[int], fmt: AudioFormatConfig) -> None:
        """Open and start hardware audio capture stream."""

    @abstractmethod
    def read_chunk(self, timeout_seconds: float = 0.1) -> Optional[bytes]:
        """Read next available PCM chunk, or return None on timeout."""

    @abstractmethod
    def stop_stream(self) -> None:
        """Stop active audio capture."""

    @abstractmethod
    def close_stream(self) -> None:
        """Release all OS handles and buffers."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True while the hardware stream is open."""


class BaseAudioOutputBackend(ABC):
    """Pluggable interface for speaker hardware or deterministic test doubles."""

    @abstractmethod
    def list_devices(self) -> List[AudioDeviceInfo]:
        """Enumerate available audio output devices."""

    @abstractmethod
    def open_stream(
        self,
        device_id: Optional[int],
        sample_rate: int,
        channels: int,
        sample_width: int,
    ) -> None:
        """Open hardware output stream for the specified PCM parameters."""

    @abstractmethod
    def play_pcm(self, pcm_bytes: bytes, cancel_event: threading.Event) -> int:
        """Play PCM bytes, polling cancel_event for immediate abort. Return bytes played."""

    @abstractmethod
    def stop_stream(self) -> None:
        """Immediately halt ongoing hardware playback."""

    @abstractmethod
    def close_stream(self) -> None:
        """Release all OS handles and buffers."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True while the hardware output stream is open."""


# ---------------------------------------------------------------------------
# Native Windows WinMM Input Backend (`waveIn*`)
# ---------------------------------------------------------------------------


class WinMMAudioInputBackend(BaseAudioInputBackend):
    """Native Windows microphone capture backend using `winmm.dll`."""

    NUM_RING_BUFFERS = 8

    def __init__(self) -> None:
        self._is_windows = os.name == "nt"
        self._h_wavein = ctypes.c_void_p(0)
        self._h_event = ctypes.c_void_p(0)
        self._buffers: List[ctypes.Array] = []
        self._headers: List[WAVEHDR] = []
        self._next_buf_idx: int = 0
        self._open: bool = False
        self._stopped: bool = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._open

    def list_devices(self) -> List[AudioDeviceInfo]:
        if not self._is_windows:
            return []
        winmm = ctypes.windll.winmm
        count = int(winmm.waveInGetNumDevs())
        devices: List[AudioDeviceInfo] = []
        for idx in range(count):
            caps = WAVEINCAPSW()
            res = winmm.waveInGetDevCapsW(idx, ctypes.byref(caps), ctypes.sizeof(WAVEINCAPSW))
            if res == MMSYSERR_NOERROR:
                devices.append(
                    AudioDeviceInfo(
                        device_id=idx,
                        name=caps.szPname or f"Microphone {idx}",
                        kind=AudioDeviceKind.INPUT,
                        max_channels=max(1, int(caps.wChannels)),
                        is_default=(idx == 0),
                    )
                )
        return devices

    def open_stream(self, device_id: Optional[int], fmt: AudioFormatConfig) -> None:
        if not self._is_windows:
            raise AudioDeviceUnavailableError(
                code="AUDIO_NOT_WINDOWS",
                message="WinMMAudioInputBackend is only available on Windows.",
            )
        with self._lock:
            if self._open:
                self.close_stream()

            winmm = ctypes.windll.winmm
            kernel32 = ctypes.windll.kernel32

            num_devs = int(winmm.waveInGetNumDevs())
            if num_devs <= 0:
                raise AudioDeviceUnavailableError(
                    code="AUDIO_NO_INPUT_DEVICES",
                    message="No microphone input devices are installed on this system.",
                )
            if device_id is not None and (device_id < 0 or device_id >= num_devs):
                raise AudioDeviceUnavailableError(
                    code="AUDIO_INVALID_INPUT_DEVICE_ID",
                    message=(
                        f"Input device_id {device_id} does not exist "
                        f"(available: 0..{num_devs - 1})."
                    ),
                )

            kernel32.CreateEventW.restype = ctypes.c_void_p
            event_handle = kernel32.CreateEventW(None, False, False, None)
            if not event_handle:
                raise AudioDeviceError(
                    code="AUDIO_EVENT_CREATE_FAILED",
                    message="Failed to create Win32 event handle for audio capture.",
                )
            self._h_event = ctypes.c_void_p(event_handle)

            wfx = WAVEFORMATEX(
                wFormatTag=WAVE_FORMAT_PCM,
                nChannels=fmt.channels,
                nSamplesPerSec=fmt.sample_rate,
                nAvgBytesPerSec=fmt.bytes_per_second,
                nBlockAlign=fmt.channels * fmt.sample_width,
                wBitsPerSample=fmt.sample_width * 8,
                cbSize=0,
            )

            target_dev = WAVE_MAPPER if device_id is None else int(device_id)
            h_in = ctypes.c_void_p(0)
            res = winmm.waveInOpen(
                ctypes.byref(h_in),
                wintypes.UINT(target_dev),
                ctypes.byref(wfx),
                self._h_event,
                ctypes.c_size_t(0),
                wintypes.DWORD(CALLBACK_EVENT),
            )
            if res != MMSYSERR_NOERROR:
                kernel32.CloseHandle(self._h_event)
                self._h_event = ctypes.c_void_p(0)
                raise _map_winmm_error(res, "waveInOpen")

            self._h_wavein = h_in
            self._buffers = []
            self._headers = []
            self._next_buf_idx = 0

            try:
                for _ in range(self.NUM_RING_BUFFERS):
                    raw_buf = ctypes.create_string_buffer(fmt.frame_bytes)
                    hdr = WAVEHDR(
                        lpData=ctypes.cast(raw_buf, ctypes.c_char_p),
                        dwBufferLength=fmt.frame_bytes,
                        dwBytesRecorded=0,
                        dwUser=0,
                        dwFlags=0,
                        dwLoops=0,
                        lpNext=None,
                        reserved=0,
                    )
                    prep_res = winmm.waveInPrepareHeader(
                        self._h_wavein, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                    )
                    if prep_res != MMSYSERR_NOERROR:
                        raise _map_winmm_error(prep_res, "waveInPrepareHeader")
                    add_res = winmm.waveInAddBuffer(
                        self._h_wavein, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                    )
                    if add_res != MMSYSERR_NOERROR:
                        raise _map_winmm_error(add_res, "waveInAddBuffer")
                    self._buffers.append(raw_buf)
                    self._headers.append(hdr)

                start_res = winmm.waveInStart(self._h_wavein)
                if start_res != MMSYSERR_NOERROR:
                    raise _map_winmm_error(start_res, "waveInStart")

                self._open = True
                self._stopped = False
            except Exception:
                self._cleanup_locked()
                raise

    def read_chunk(self, timeout_seconds: float = 0.1) -> Optional[bytes]:
        if not self._open or self._stopped:
            return None

        winmm = ctypes.windll.winmm
        kernel32 = ctypes.windll.kernel32

        hdr = self._headers[self._next_buf_idx]
        if not (hdr.dwFlags & WHDR_DONE):
            wait_ms = max(1, int(timeout_seconds * 1000))
            kernel32.WaitForSingleObject(self._h_event, wintypes.DWORD(wait_ms))

        if not self._open or self._stopped:
            return None

        hdr = self._headers[self._next_buf_idx]
        if not (hdr.dwFlags & WHDR_DONE):
            return None

        recorded = int(hdr.dwBytesRecorded)
        raw_buf = self._buffers[self._next_buf_idx]
        data = bytes(raw_buf[:recorded]) if recorded > 0 else b""

        # Re-queue buffer if still recording
        if self._open and not self._stopped:
            hdr.dwBytesRecorded = 0
            hdr.dwFlags &= ~WHDR_DONE
            res = winmm.waveInAddBuffer(self._h_wavein, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
            if res != MMSYSERR_NOERROR and res != MMSYSERR_INVALHANDLE:
                raise _map_winmm_error(res, "waveInAddBuffer")

        self._next_buf_idx = (self._next_buf_idx + 1) % len(self._headers)
        return data

    def stop_stream(self) -> None:
        with self._lock:
            if not self._open or self._stopped:
                return
            self._stopped = True
            if self._h_wavein:
                ctypes.windll.winmm.waveInStop(self._h_wavein)
                ctypes.windll.winmm.waveInReset(self._h_wavein)
            if self._h_event:
                ctypes.windll.kernel32.SetEvent(self._h_event)

    def close_stream(self) -> None:
        with self._lock:
            self._cleanup_locked()

    def _cleanup_locked(self) -> None:
        winmm = ctypes.windll.winmm if self._is_windows else None
        kernel32 = ctypes.windll.kernel32 if self._is_windows else None
        self._stopped = True
        self._open = False

        if winmm and self._h_wavein:
            winmm.waveInReset(self._h_wavein)
            for hdr in self._headers:
                if hdr.dwFlags & WHDR_PREPARED:
                    winmm.waveInUnprepareHeader(
                        self._h_wavein, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                    )
            winmm.waveInClose(self._h_wavein)
            self._h_wavein = ctypes.c_void_p(0)

        self._headers.clear()
        self._buffers.clear()

        if kernel32 and self._h_event:
            kernel32.CloseHandle(self._h_event)
            self._h_event = ctypes.c_void_p(0)


# ---------------------------------------------------------------------------
# Native Windows WinMM Output Backend (`waveOut*`)
# ---------------------------------------------------------------------------


class WinMMAudioOutputBackend(BaseAudioOutputBackend):
    """Native Windows speaker playback backend using `winmm.dll`."""

    CHUNK_BYTES = 8192

    def __init__(self) -> None:
        self._is_windows = os.name == "nt"
        self._h_waveout = ctypes.c_void_p(0)
        self._h_event = ctypes.c_void_p(0)
        self._open: bool = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._open

    def list_devices(self) -> List[AudioDeviceInfo]:
        if not self._is_windows:
            return []
        winmm = ctypes.windll.winmm
        count = int(winmm.waveOutGetNumDevs())
        devices: List[AudioDeviceInfo] = []
        for idx in range(count):
            caps = WAVEOUTCAPSW()
            res = winmm.waveOutGetDevCapsW(idx, ctypes.byref(caps), ctypes.sizeof(WAVEOUTCAPSW))
            if res == MMSYSERR_NOERROR:
                devices.append(
                    AudioDeviceInfo(
                        device_id=idx,
                        name=caps.szPname or f"Speaker {idx}",
                        kind=AudioDeviceKind.OUTPUT,
                        max_channels=max(1, int(caps.wChannels)),
                        is_default=(idx == 0),
                    )
                )
        return devices

    def open_stream(
        self,
        device_id: Optional[int],
        sample_rate: int,
        channels: int,
        sample_width: int,
    ) -> None:
        if not self._is_windows:
            raise AudioDeviceUnavailableError(
                code="AUDIO_NOT_WINDOWS",
                message="WinMMAudioOutputBackend is only available on Windows.",
            )
        with self._lock:
            if self._open:
                self.close_stream()

            winmm = ctypes.windll.winmm
            kernel32 = ctypes.windll.kernel32

            num_devs = int(winmm.waveOutGetNumDevs())
            if num_devs <= 0:
                raise AudioDeviceUnavailableError(
                    code="AUDIO_NO_OUTPUT_DEVICES",
                    message="No speaker output devices are installed on this system.",
                )
            if device_id is not None and (device_id < 0 or device_id >= num_devs):
                raise AudioDeviceUnavailableError(
                    code="AUDIO_INVALID_OUTPUT_DEVICE_ID",
                    message=(
                        f"Output device_id {device_id} does not exist "
                        f"(available: 0..{num_devs - 1})."
                    ),
                )

            kernel32.CreateEventW.restype = ctypes.c_void_p
            event_handle = kernel32.CreateEventW(None, False, False, None)
            if not event_handle:
                raise AudioDeviceError(
                    code="AUDIO_EVENT_CREATE_FAILED",
                    message="Failed to create Win32 event handle for speaker playback.",
                )
            self._h_event = ctypes.c_void_p(event_handle)

            block_align = channels * sample_width
            wfx = WAVEFORMATEX(
                wFormatTag=WAVE_FORMAT_PCM,
                nChannels=channels,
                nSamplesPerSec=sample_rate,
                nAvgBytesPerSec=sample_rate * block_align,
                nBlockAlign=block_align,
                wBitsPerSample=sample_width * 8,
                cbSize=0,
            )

            target_dev = WAVE_MAPPER if device_id is None else int(device_id)
            h_out = ctypes.c_void_p(0)
            res = winmm.waveOutOpen(
                ctypes.byref(h_out),
                wintypes.UINT(target_dev),
                ctypes.byref(wfx),
                self._h_event,
                ctypes.c_size_t(0),
                wintypes.DWORD(CALLBACK_EVENT),
            )
            if res != MMSYSERR_NOERROR:
                kernel32.CloseHandle(self._h_event)
                self._h_event = ctypes.c_void_p(0)
                raise _map_winmm_error(res, "waveOutOpen")

            self._h_waveout = h_out
            self._open = True

    def play_pcm(self, pcm_bytes: bytes, cancel_event: threading.Event) -> int:
        if not self._open or not self._h_waveout:
            raise AudioDeviceUnavailableError(
                code="AUDIO_OUTPUT_NOT_OPEN",
                message="Output stream is not open.",
            )

        winmm = ctypes.windll.winmm
        kernel32 = ctypes.windll.kernel32
        bytes_played = 0

        for offset in range(0, len(pcm_bytes), self.CHUNK_BYTES):
            if cancel_event.is_set() or not self._open:
                self.stop_stream()
                break

            chunk = pcm_bytes[offset : offset + self.CHUNK_BYTES]
            raw_buf = ctypes.create_string_buffer(chunk, len(chunk))
            hdr = WAVEHDR(
                lpData=ctypes.cast(raw_buf, ctypes.c_char_p),
                dwBufferLength=len(chunk),
                dwBytesRecorded=0,
                dwUser=0,
                dwFlags=0,
                dwLoops=0,
                lpNext=None,
                reserved=0,
            )

            prep_res = winmm.waveOutPrepareHeader(
                self._h_waveout, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
            )
            if prep_res != MMSYSERR_NOERROR:
                raise _map_winmm_error(prep_res, "waveOutPrepareHeader")

            try:
                write_res = winmm.waveOutWrite(
                    self._h_waveout, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                )
                if write_res != MMSYSERR_NOERROR:
                    raise _map_winmm_error(write_res, "waveOutWrite")

                while not (hdr.dwFlags & WHDR_DONE):
                    if cancel_event.is_set() or not self._open:
                        winmm.waveOutReset(self._h_waveout)
                        break
                    kernel32.WaitForSingleObject(self._h_event, wintypes.DWORD(20))

                if not cancel_event.is_set() and (hdr.dwFlags & WHDR_DONE):
                    bytes_played += len(chunk)
            finally:
                if self._h_waveout and (hdr.dwFlags & WHDR_PREPARED):
                    winmm.waveOutUnprepareHeader(
                        self._h_waveout, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                    )

        return bytes_played

    def stop_stream(self) -> None:
        with self._lock:
            if self._open and self._h_waveout:
                ctypes.windll.winmm.waveOutReset(self._h_waveout)
            if self._h_event:
                ctypes.windll.kernel32.SetEvent(self._h_event)

    def close_stream(self) -> None:
        with self._lock:
            winmm = ctypes.windll.winmm if self._is_windows else None
            kernel32 = ctypes.windll.kernel32 if self._is_windows else None
            self._open = False
            if winmm and self._h_waveout:
                winmm.waveOutReset(self._h_waveout)
                winmm.waveOutClose(self._h_waveout)
                self._h_waveout = ctypes.c_void_p(0)
            if kernel32 and self._h_event:
                kernel32.CloseHandle(self._h_event)
                self._h_event = ctypes.c_void_p(0)
