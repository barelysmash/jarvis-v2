"""Streaming text-to-speech with interruption support."""

import io
import wave
from typing import AsyncIterator, Optional

try:
    import pyaudio
except ImportError:
    pyaudio = None


class StreamingTTS:
    """Streams TTS audio with low latency. Supports interruption."""

    def __init__(
        self,
        backend: str = "elevenlabs",
        api_key: Optional[str] = None,
        voice_id: str = "JARVIS",
        model: str = "eleven_turbo_v2_5",
    ):
        if pyaudio is None:
            raise ImportError(
                "pyaudio not installed. Install with: "
                "pip install pyaudio"
            )

        self.backend = backend
        self.voice_id = voice_id
        self.model = model
        self.pa = pyaudio.PyAudio()
        self.current_stream = None
        self.interrupted = False

        if backend == "elevenlabs":
            from elevenlabs.client import ElevenLabs

            self.client = ElevenLabs(api_key=api_key)
        elif backend == "openai":
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
        elif backend == "local":
            import piper

            self.voice = piper.PiperVoice.load(
                "./models/en_US-jarvis.onnx"
            )

    async def speak_stream(self, text_iterator: AsyncIterator[str]):
        """Consume text chunks; speak each sentence as it completes."""
        buffer = ""
        sentence_endings = {".", "!", "?", "\n"}

        async for chunk in text_iterator:
            buffer += chunk

            while any(end in buffer for end in sentence_endings):
                idx = min(
                    (
                        buffer.index(e)
                        for e in sentence_endings
                        if e in buffer
                    ),
                    default=-1,
                )
                if idx == -1:
                    break

                sentence = buffer[: idx + 1].strip()
                buffer = buffer[idx + 1 :]

                if sentence and not self.interrupted:
                    await self._speak_chunk(sentence)

        if buffer.strip() and not self.interrupted:
            await self._speak_chunk(buffer.strip())

    async def speak(self, text: str):
        """Speak a complete string."""
        await self._speak_chunk(text)

    async def _speak_chunk(self, text: str):
        if self.backend == "elevenlabs":
            await self._speak_elevenlabs(text)
        elif self.backend == "openai":
            await self._speak_openai(text)
        elif self.backend == "local":
            await self._speak_local(text)

    async def _speak_elevenlabs(self, text: str):
        audio_stream = self.client.text_to_speech.convert_as_stream(
            voice_id=self.voice_id,
            text=text,
            model_id=self.model,
            output_format="pcm_22050",
        )

        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=22050,
            output=True,
        )
        self.current_stream = stream

        try:
            for chunk in audio_stream:
                if self.interrupted:
                    break
                if chunk:
                    stream.write(chunk)
        finally:
            stream.stop_stream()
            stream.close()
            self.current_stream = None

    async def _speak_openai(self, text: str):
        response = self.client.audio.speech.create(
            model="tts-1",
            voice="onyx",
            input=text,
            response_format="pcm",
        )

        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
        )
        self.current_stream = stream

        try:
            for chunk in response.iter_bytes(chunk_size=4096):
                if self.interrupted:
                    break
                stream.write(chunk)
        finally:
            stream.stop_stream()
            stream.close()

    async def _speak_local(self, text: str):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            self.voice.synthesize(text, wav)
        buf.seek(0)

        with wave.open(buf, "rb") as wav:
            stream = self.pa.open(
                format=self.pa.get_format_from_width(wav.getsampwidth()),
                channels=wav.getnchannels(),
                rate=wav.getframerate(),
                output=True,
            )
            self.current_stream = stream
            data = wav.readframes(1024)
            while data and not self.interrupted:
                stream.write(data)
                data = wav.readframes(1024)
            stream.stop_stream()
            stream.close()

    def interrupt(self):
        """Stop current speech immediately."""
        self.interrupted = True
        if self.current_stream:
            try:
                self.current_stream.stop_stream()
            except Exception:
                pass

    def reset(self):
        self.interrupted = False
