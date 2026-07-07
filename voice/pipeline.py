"""End-to-end voice pipeline orchestrator."""

import asyncio
import logging

try:
    import pyaudio
except ImportError:
    pyaudio = None

from .stt import SpeechToText
from .tts import StreamingTTS
from .vad import VoiceActivityDetector
from .wake_word import WakeWordDetector

logger = logging.getLogger("jarvis.voice")


class VoicePipeline:
    """Full conversational voice loop with interruption support."""

    def __init__(self, brain, config: dict):
        self.brain = brain
        self.config = config

        self.wake = WakeWordDetector(
            access_key=config["porcupine_key"],
            keyword_paths=config.get("custom_wake_word_paths"),
            keywords=config.get("wake_words", ["jarvis"]),
            sensitivity=config.get("sensitivity", 0.5),
            on_detected=self._on_wake,
        )

        self.vad = VoiceActivityDetector(
            silence_threshold_ms=config.get("silence_ms", 800)
        )

        self.stt = SpeechToText(
            backend=config.get("stt_backend", "local"),
            model_size=config.get("whisper_size", "base"),
            api_key=config.get("stt_api_key"),
        )

        self.tts = StreamingTTS(
            backend=config.get("tts_backend", "elevenlabs"),
            api_key=config.get("tts_api_key"),
            voice_id=config.get("voice_id", "JARVIS"),
        )

        self.pa = pyaudio.PyAudio()
        self.processing = False

    def _on_wake(self, keyword_index: int):
        if self.processing:
            return
        logger.info("Wake word detected")
        asyncio.run(self._handle_turn())

    async def _handle_turn(self):
        self.processing = True
        try:
            audio = await self._record_user_input()
            if not audio:
                return

            text = await asyncio.to_thread(self.stt.transcribe, audio)
            logger.info("User: %s", text)

            if not text or len(text) < 2:
                return

            self.tts.reset()
            await self._stream_response(text)
        finally:
            self.processing = False

    async def _record_user_input(self) -> bytes:
        stream = self.pa.open(
            rate=self.vad.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.vad.frame_size,
        )
        try:
            audio = await asyncio.to_thread(
                self.vad.collect_until_silence, stream, 15
            )
            return audio
        finally:
            stream.stop_stream()
            stream.close()

    async def _stream_response(self, user_text: str):
        """Stream Claude's response to TTS sentence by sentence.

        For now, this uses non-streaming and feeds it into the speak_stream
        as a single chunk. Replace with brain.client.messages.stream() for
        true streaming once the brain supports it natively.
        """
        response_text = await asyncio.to_thread(
            self.brain.think_and_act, user_text
        )

        async def text_stream():
            yield response_text

        await self.tts.speak_stream(text_stream())

    def start(self):
        """Begin the always-on listening loop."""
        logger.info("JARVIS voice pipeline online")
        self.wake.start()

    def stop(self):
        self.wake.stop()
        self.pa.terminate()
