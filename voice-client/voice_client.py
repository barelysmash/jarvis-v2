#!/usr/bin/env python3
"""
JARVIS voice client — runs on rosencrantz (real audio hardware).

Pipeline:
  openWakeWord detects "hey jarvis" (local, always-on, no account/key)
    -> Deepgram streaming STT (mic -> final transcript)
    -> query_jarvis() -> jarvis-api on the tailnet -> response text (streamed)
    -> ElevenLabs streaming TTS -> speaker
    -> (say "hey jarvis" during the reply to barge in -> straight to new capture)
    -> back to wake-word listen

guildenstern stays headless; all audio lives here.

Audio I/O uses sounddevice (bundles PortAudio — no compiler needed, works on py3.14).
Wake word uses openWakeWord (open source, no account/key, local ONNX inference).

deps:  pip install openwakeword onnxruntime sounddevice numpy deepgram-sdk elevenlabs websockets aiohttp python-dotenv

env (client-side .env on rosencrantz, NOT ~/jarvis/deploy/.env):
  DEEPGRAM_API_KEY=...
  ELEVENLABS_API_KEY=...
  ELEVENLABS_VOICE_ID=...
  JARVIS_API_URL=http://100.113.110.44:8765
  JARVIS_API_TOKEN=           # bearer token, if jarvis-api auth is enabled; else leave blank
  JARVIS_STREAMS=false        # /api/text returns a complete response, so keep false
  MIC_DEVICE=                 # input device index from `python -m sounddevice`; blank=default
                              #   pin this if you get "Error querying device -1" (Bluetooth drops)
  WAKE_MODEL=hey_jarvis       # built-in openWakeWord model; say "hey jarvis"
  WAKE_THRESHOLD=0.6          # initial wake; raise if false-triggering, lower if missing you
  BARGE_ENABLED=false         # interrupt JARVIS mid-reply by saying "hey jarvis"
                              #   OFF by default — on speakers it self-triggers (echo).
                              #   set true ONLY with headphones.
  BARGE_THRESHOLD=0.7         # mid-reply interrupt sensitivity (only used if BARGE_ENABLED)
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import aiohttp
import numpy as np
import sounddevice as sd
import websockets
from dotenv import load_dotenv
from openwakeword.model import Model as OWWModel

# Resolve .env next to this script, so launching from any CWD (e.g. a .bat
# shortcut) still finds it.
load_dotenv(Path(__file__).parent / ".env")

DEEPGRAM_KEY      = os.environ["DEEPGRAM_API_KEY"]
ELEVEN_KEY        = os.environ["ELEVENLABS_API_KEY"]
ELEVEN_VOICE      = os.environ["ELEVENLABS_VOICE_ID"]
JARVIS_URL        = os.environ.get("JARVIS_API_URL", "http://100.113.110.44:8765")
JARVIS_TOKEN      = os.environ.get("JARVIS_API_TOKEN") or None   # bearer auth, if enabled
JARVIS_STREAMS    = os.environ.get("JARVIS_STREAMS", "false").lower() == "true"
WAKE_MODEL        = os.environ.get("WAKE_MODEL", "hey_jarvis")   # built-in openWakeWord model
WAKE_THRESHOLD    = float(os.environ.get("WAKE_THRESHOLD", "0.6"))    # initial wake
BARGE_THRESHOLD   = float(os.environ.get("BARGE_THRESHOLD", "0.7"))   # stricter mid-reply interrupt
# Barge-in opens a mic WHILE JARVIS speaks. On speakers, JARVIS's own voice leaks
# into that mic and self-triggers (echo, overlap, double-calls), so it's OFF by
# default. Turn on only with headphones (no speaker->mic path).
BARGE_ENABLED     = os.environ.get("BARGE_ENABLED", "false").lower() == "true"
# Explicit input device index (from `python -m sounddevice`). Pinning avoids the
# PortAudioError when the Bluetooth default drops/re-enumerates. None = system default.
_mic = os.environ.get("MIC_DEVICE")
MIC_DEVICE        = int(_mic) if _mic not in (None, "") else None

DG_RATE = 16000          # Deepgram STT sample rate (mono pcm16)
TTS_RATE = 16000         # ElevenLabs pcm_16000 output
ELEVEN_MODEL = "eleven_turbo_v2_5"   # lowest-latency streaming model


# ─────────────────────────────────────────────────────────────────────────────
# 1. WAKE WORD  (blocking, runs in an executor — openWakeWord inference is sync)
# ─────────────────────────────────────────────────────────────────────────────
# Loaded once at module level (downloads model files on first ever run).
# inference_framework="onnx" avoids the tflite-runtime dependency, which has no
# wheel on py3.14. The "hey_jarvis" model triggers on "hey jarvis" (and sometimes
# bare "jarvis", with higher false-rejects).
_oww = OWWModel(wakeword_models=[WAKE_MODEL], inference_framework="onnx")

# Separate instance for the barge-in monitor that runs DURING TTS playback.
# openWakeWord models hold internal frame state, so the monitor needs its own
# instance rather than sharing _oww with the main wake loop.
_oww_monitor = OWWModel(wakeword_models=[WAKE_MODEL], inference_framework="onnx")


def wait_for_wake_word() -> None:
    """Block until the wake phrase is heard. openWakeWord wants int16 frames @ 16k."""
    FRAME = 1280  # 80ms @ 16k — openWakeWord's native chunk size
    _oww.reset()  # clear any leftover internal state from a prior activation
    print(f"[wake] listening for '{WAKE_MODEL.replace('_', ' ')}'…")
    with sd.RawInputStream(
        samplerate=16000, blocksize=FRAME, channels=1, dtype="int16",
        device=MIC_DEVICE,
    ) as stream:
        while True:
            data, _ = stream.read(FRAME)
            pcm = np.frombuffer(data, dtype=np.int16)
            scores = _oww.predict(pcm)          # {model_name: score in [0,1]}
            if scores.get(WAKE_MODEL, 0.0) >= WAKE_THRESHOLD:
                print(f"[wake] detected (score={scores[WAKE_MODEL]:.2f}).")
                return


# ─────────────────────────────────────────────────────────────────────────────
# 2. STT  — stream mic to Deepgram until endpointing fires, return final transcript
# ─────────────────────────────────────────────────────────────────────────────
async def transcribe_once(silence_ms: int = 1000, max_seconds: int = 15) -> str:
    """Open Deepgram WS, push mic frames, return the final transcript on endpoint."""
    url = (
        "wss://api.deepgram.com/v1/listen?"
        f"encoding=linear16&sample_rate={DG_RATE}&channels=1"
        f"&model=nova-3&smart_format=true&interim_results=true"
        f"&endpointing={silence_ms}&utterance_end_ms=1000&vad_events=true"
    )
    headers = {"Authorization": f"Token {DEEPGRAM_KEY}"}

    transcript_parts: list[str] = []
    done = asyncio.Event()
    loop = asyncio.get_event_loop()

    try:
        ws = await websockets.connect(url, additional_headers=headers)
    except Exception as e:
        print(f"[stt] Deepgram connect failed: {e}", file=sys.stderr)
        return ""

    async with ws:
        audio_q: asyncio.Queue = asyncio.Queue()
        BLOCK = 1600  # 100ms @ 16k

        def on_audio(indata, frames, time_info, status):
            loop.call_soon_threadsafe(audio_q.put_nowait, bytes(indata))

        stream = sd.RawInputStream(
            samplerate=DG_RATE, blocksize=BLOCK, channels=1,
            dtype="int16", callback=on_audio, device=MIC_DEVICE,
        )

        async def sender():
            try:
                while not done.is_set():
                    frame = await audio_q.get()
                    await ws.send(frame)
            except websockets.ConnectionClosed:
                pass

        async def receiver():
            async for msg in ws:
                data = json.loads(msg)
                t = data.get("type")
                if t == "Results":
                    alt = data["channel"]["alternatives"][0]
                    text = alt.get("transcript", "")
                    if text and data.get("is_final"):
                        transcript_parts.append(text)
                    if data.get("speech_final"):
                        done.set(); return
                elif t == "UtteranceEnd":
                    done.set(); return

        stream.start()
        print("[stt] listening — speak now.")
        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            await asyncio.wait_for(done.wait(), timeout=max_seconds)
        except asyncio.TimeoutError:
            done.set()
        finally:
            stream.stop(); stream.close()
            try:
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            send_task.cancel(); recv_task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)

    return " ".join(transcript_parts).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 3. BRAIN  — adapter boundary. Two impls; keep the one that matches jarvis-api.
# ─────────────────────────────────────────────────────────────────────────────
async def query_jarvis(transcript: str):
    """Yield response text chunks from jarvis-api. Swap impl to match your route."""
    if JARVIS_STREAMS:
        ws_url = JARVIS_URL.replace("http", "ws", 1) + "/voice/stream"
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"text": transcript}))
            async for msg in ws:
                evt = json.loads(msg)
                if evt.get("type") == "token":
                    yield evt["text"]
                elif evt.get("type") == "done":
                    return
    else:
        # jarvis-api: POST /api/text {"text": ...} -> {"response": ...}
        # (same endpoint the HUD uses; it also broadcasts to the WS bus, so the
        #  HUD will mirror voice conversations automatically.)
        headers = {}
        if JARVIS_TOKEN:
            headers["Authorization"] = f"Bearer {JARVIS_TOKEN}"
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{JARVIS_URL}/api/text",
                json={"text": transcript},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                yield data.get("response", "")


# ─────────────────────────────────────────────────────────────────────────────
# 4. TTS  — stream brain text into ElevenLabs WS, play audio as it arrives.
#    A concurrent wake-word monitor lets the user say "hey jarvis" to barge in.
# ─────────────────────────────────────────────────────────────────────────────
async def _run_playback(feed, play) -> None:
    """Run the feed and play coroutines together; let exceptions propagate."""
    await asyncio.gather(feed(), play())


async def speak(text_chunks) -> bool:
    """Stream text_chunks to ElevenLabs and play. Returns True if barged-in.

    During playback a background thread runs openWakeWord on the mic. If the
    wake phrase is heard, `interrupted` fires, playback stops immediately, and
    we return True so the caller can jump straight into a new capture.
    """
    url = (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}/stream-input"
        f"?model_id={ELEVEN_MODEL}&output_format=pcm_16000"
        f"&inactivity_timeout=180"   # max; default 20s closes mid-reply on long answers
    )

    loop = asyncio.get_event_loop()
    interrupted = asyncio.Event()

    # --- barge-in monitor: own mic stream + own OWW instance, in a thread ---
    monitor_stop = asyncio.Event()

    def monitor_thread():
        FRAME = 1280  # 80ms @ 16k
        _oww_monitor.reset()
        try:
            with sd.RawInputStream(
                samplerate=16000, blocksize=FRAME, channels=1, dtype="int16",
                device=MIC_DEVICE,
            ) as mic:
                while not monitor_stop.is_set():
                    data, _ = mic.read(FRAME)
                    pcm = np.frombuffer(data, dtype=np.int16)
                    scores = _oww_monitor.predict(pcm)
                    if scores.get(WAKE_MODEL, 0.0) >= BARGE_THRESHOLD:
                        loop.call_soon_threadsafe(interrupted.set)
                        return
        except Exception as e:
            print(f"[barge] monitor error: {e}", file=sys.stderr)

    # Only run the barge-in monitor when explicitly enabled. When off, no mic is
    # opened during playback, so JARVIS can't hear itself (no echo/overlap/double-call).
    if BARGE_ENABLED:
        monitor = loop.run_in_executor(None, monitor_thread)
    else:
        monitor = None

    out = sd.RawOutputStream(samplerate=TTS_RATE, channels=1, dtype="int16")
    out.start()
    was_interrupted = False
    try:
        # ping_interval=None: don't let the client's own keepalive tear down a
        # healthy connection during a long reply (same class of bug as the 1011
        # we saw on the briefing). ElevenLabs' inactivity_timeout governs instead.
        async with websockets.connect(url, ping_interval=None) as ws:
            await ws.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                "xi_api_key": ELEVEN_KEY,
            }))

            async def feed():
                # ElevenLabs' streaming model expects text incrementally. Sending
                # one large blob then going silent can trip its timeout on long
                # replies, so split into sentences and stream them. The split
                # protects decimals ($25,293.48, VIX 22.6, +0.10R) so they aren't
                # broken across pieces, which would mangle the spoken numbers.
                import re

                def sentences(s):
                    protected = re.sub(r'(?<=\d)\.(?=\d)', '\x00', s)
                    parts = re.split(r'(?<=[.!?])\s+', protected)
                    return [p.replace('\x00', '.').strip()
                            for p in parts if p.strip()]

                async for chunk in text_chunks:
                    if interrupted.is_set():
                        return
                    if not chunk:
                        continue
                    for piece in sentences(chunk):
                        if interrupted.is_set():
                            return
                        # trailing space nudges ElevenLabs to start synthesizing
                        await ws.send(json.dumps({"text": piece + " "}))
                await ws.send(json.dumps({"text": ""}))   # EOS flush

            async def play():
                async for msg in ws:
                    if interrupted.is_set():
                        return
                    data = json.loads(msg)
                    if data.get("audio"):
                        # Write in a worker thread: out.write() blocks for
                        # roughly the duration of the audio; doing that on the
                        # event loop starves websocket ping/pong handling and
                        # ElevenLabs closes the connection mid-reply on long
                        # responses (the real cause of the long-answer cutoff).
                        await loop.run_in_executor(
                            None, out.write, base64.b64decode(data["audio"])
                        )
                    elif data.get("error") or data.get("message"):
                        print(f"[tts] ElevenLabs error: {data}", file=sys.stderr)
                    if data.get("isFinal"):
                        return

            # Race playback against the interrupt signal.
            playback = asyncio.ensure_future(_run_playback(feed, play))
            interrupt_wait = asyncio.ensure_future(interrupted.wait())
            done, pending = await asyncio.wait(
                {playback, interrupt_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if interrupted.is_set():
                was_interrupted = True
                print("[barge] interrupted — JARVIS stopping.")
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # Surface any error that occurred during normal (non-interrupted)
            # playback instead of letting asyncio.wait swallow it.
            if playback in done and not was_interrupted:
                playback.result()
    finally:
        # Interrupted: abort immediately (drop the buffer, silence now).
        # Normal completion or error: stop() drains the buffered tail so the
        # last words aren't clipped.
        try:
            if was_interrupted or interrupted.is_set():
                out.abort()
            else:
                out.stop()
        except Exception:
            pass
        out.close()
        monitor_stop.set()
        if monitor is not None:
            await asyncio.gather(monitor, return_exceptions=True)

    return was_interrupted


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────
async def conversation_turn() -> None:
    """One exchange. If the user barges in during the reply, loop straight into
    a fresh capture instead of returning to the wake word."""
    while True:
        transcript = await transcribe_once()
        if not transcript:
            print("[stt] (nothing heard)")
            return
        print(f"[you] {transcript}")

        # Collect the full brain response before speaking.
        try:
            parts = []
            async for chunk in query_jarvis(transcript):
                parts.append(chunk)
            response = "".join(parts).strip()
        except Exception as e:
            print(f"[brain] query failed: {e!r}", file=sys.stderr)
            return
        if not response:
            print("[brain] empty response.", file=sys.stderr)
            return
        print(f"[jarvis] {response}")

        async def one_chunk():
            yield response
        try:
            barged_in = await speak(one_chunk())
        except Exception as e:
            print(f"[tts] speak failed: {e!r}", file=sys.stderr)
            return

        if not barged_in:
            return
        # User cut in with "hey jarvis" — capture their new request immediately.
        # Brief settle so the tail of JARVIS's audio doesn't bleed into capture.
        await asyncio.sleep(0.3)
        print("[barge] go ahead.")


async def main() -> None:
    print(f"[init] barge-in {'ENABLED' if BARGE_ENABLED else 'disabled'} "
          f"(set BARGE_ENABLED=true in .env to interrupt mid-reply; headphones only).")
    loop = asyncio.get_event_loop()
    while True:
        await loop.run_in_executor(None, wait_for_wake_word)
        try:
            await conversation_turn()
        except Exception as e:
            print(f"[error] turn failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[exit]")
