"""Voice activity detection (VAD) for endpointing."""

try:
    import webrtcvad
except ImportError:
    webrtcvad = None


class VoiceActivityDetector:
    """Detects speech end so we know when to stop recording."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 30,
        aggressiveness: int = 2,
        silence_threshold_ms: int = 800,
    ):
        if webrtcvad is None:
            raise ImportError(
                "webrtcvad not installed. Install with: "
                "pip install webrtcvad"
            )
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        self.silence_frames_needed = (
            silence_threshold_ms // frame_duration_ms
        )

    def is_speech(self, audio_frame: bytes) -> bool:
        return self.vad.is_speech(audio_frame, self.sample_rate)

    def collect_until_silence(
        self, stream, max_duration_s: int = 15
    ) -> bytes:
        """Record from stream until speaker pauses or max duration hit."""
        frames = []
        silence_count = 0
        speech_started = False
        max_frames = (
            max_duration_s * 1000
        ) // self.frame_duration_ms

        for _ in range(max_frames):
            frame = stream.read(
                self.frame_size, exception_on_overflow=False
            )
            frames.append(frame)

            if self.is_speech(frame):
                speech_started = True
                silence_count = 0
            elif speech_started:
                silence_count += 1
                if silence_count >= self.silence_frames_needed:
                    break

        return b"".join(frames)
