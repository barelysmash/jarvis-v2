"""Speech-to-text with multiple backend options."""

import io
import wave
from typing import Optional


class SpeechToText:
    """STT with local or hosted Whisper backend."""

    def __init__(
        self,
        backend: str = "local",
        model_size: str = "base",
        api_key: Optional[str] = None,
    ):
        self.backend = backend

        if backend == "local":
            try:
                import whisper

                self.model = whisper.load_model(model_size)
            except ImportError:
                raise ImportError(
                    "openai-whisper not installed. "
                    "Install with: pip install openai-whisper"
                )
        elif backend == "openai":
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
        elif backend == "deepgram":
            from deepgram import DeepgramClient

            self.client = DeepgramClient(api_key)
        else:
            raise ValueError(f"Unknown STT backend: {backend}")

    def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000
    ) -> str:
        if self.backend == "local":
            return self._transcribe_local(audio_bytes, sample_rate)
        elif self.backend == "openai":
            return self._transcribe_openai(audio_bytes, sample_rate)
        elif self.backend == "deepgram":
            return self._transcribe_deepgram(audio_bytes, sample_rate)
        return ""

    def _transcribe_local(
        self, audio_bytes: bytes, sample_rate: int
    ) -> str:
        import numpy as np

        audio_np = (
            np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
            / 32768.0
        )
        result = self.model.transcribe(
            audio_np, language="en", fp16=False
        )
        return result["text"].strip()

    def _transcribe_openai(
        self, audio_bytes: bytes, sample_rate: int
    ) -> str:
        wav_buffer = self._pcm_to_wav(audio_bytes, sample_rate)
        wav_buffer.name = "audio.wav"
        response = self.client.audio.transcriptions.create(
            model="whisper-1",
            file=wav_buffer,
            response_format="text",
        )
        return response.strip()

    def _transcribe_deepgram(
        self, audio_bytes: bytes, sample_rate: int
    ) -> str:
        from deepgram import PrerecordedOptions

        wav = self._pcm_to_wav(audio_bytes, sample_rate).read()
        response = self.client.listen.rest.v("1").transcribe_file(
            {"buffer": wav},
            PrerecordedOptions(model="nova-2", smart_format=True),
        )
        return response.results.channels[0].alternatives[0].transcript

    def _pcm_to_wav(
        self, pcm_bytes: bytes, sample_rate: int
    ) -> io.BytesIO:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm_bytes)
        buf.seek(0)
        return buf
