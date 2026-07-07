"""Wake word detection using Picovoice Porcupine."""

import struct
from typing import Callable, Optional

try:
    import pvporcupine
    import pyaudio
except ImportError:
    pvporcupine = None
    pyaudio = None


class WakeWordDetector:
    """Always-on wake word listener using Porcupine."""

    def __init__(
        self,
        access_key: str,
        keyword_paths: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        sensitivity: float = 0.5,
        on_detected: Optional[Callable] = None,
    ):
        if pvporcupine is None:
            raise ImportError(
                "pvporcupine not installed. Install with: "
                "pip install pvporcupine"
            )

        if keyword_paths:
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=keyword_paths,
                sensitivities=[sensitivity] * len(keyword_paths),
            )
        else:
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=keywords or ["jarvis"],
                sensitivities=[sensitivity],
            )

        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.on_detected = on_detected
        self.listening = False

    def start(self):
        """Begin listening for wake word."""
        self.stream = self.pa.open(
            rate=self.porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.porcupine.frame_length,
        )
        self.listening = True

        try:
            while self.listening:
                pcm = self.stream.read(
                    self.porcupine.frame_length,
                    exception_on_overflow=False,
                )
                pcm = struct.unpack_from(
                    "h" * self.porcupine.frame_length, pcm
                )

                keyword_index = self.porcupine.process(pcm)
                if keyword_index >= 0:
                    if self.on_detected:
                        self.on_detected(keyword_index)
        finally:
            self.stop()

    def pause(self):
        """Pause without tearing down."""
        self.listening = False

    def stop(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self.pa.terminate()
        self.porcupine.delete()
