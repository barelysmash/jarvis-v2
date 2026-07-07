# Platform Notes

The core JARVIS package (brain, memory, server, calendar) installs cleanly
from `requirements.txt` on any modern OS. The voice pipeline has C
dependencies that require platform-specific setup. This doc covers the
gotchas.

## Python version

Use **Python 3.11 or 3.12**. Many of JARVIS's dependencies (especially
PyAudio, webrtcvad, numba via Whisper) are C extensions that need prebuilt
wheels matching your Python version. Python 3.13 mostly works. **Python 3.14
will fail** for several packages because no one has published wheels for it
yet — you'll see errors like:

```
fatal error C1083: Cannot open include file: 'portaudio.h'
```

Install Python 3.12 from python.org (or via pyenv/homebrew/asdf) and use
that for JARVIS specifically.

## Voice pipeline dependencies

`requirements-voice.txt` pulls in `pyaudio` (which wraps PortAudio, a C
library), `webrtcvad`, and `openai-whisper` (which pulls in `numba` and
`torch`). Before installing it, make sure the system-level prerequisites
are in place.

### Linux

```bash
sudo apt-get install -y portaudio19-dev python3-pyaudio ffmpeg
pip install -r requirements-voice.txt
```

`ffmpeg` is needed by Whisper for non-WAV audio decoding.

### macOS

```bash
brew install portaudio ffmpeg
pip install -r requirements-voice.txt
```

If you're on Apple Silicon and pip tries to compile from source, make sure
your terminal is *not* running under Rosetta. Native arm64 wheels exist for
all of these.

### Windows

PyAudio on Windows is the trickiest piece. Three options, in order of
preference:

**Option 1: pipwin** (easiest)
```bash
pip install pipwin
pipwin install pyaudio
pip install -r requirements-voice.txt
```

**Option 2: prebuilt wheel from Christoph Gohlke's archive**
```bash
# Download the .whl matching your Python version from
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio
pip install PyAudio-0.2.13-cp312-cp312-win_amd64.whl
pip install -r requirements-voice.txt
```

**Option 3: build from source** (requires Visual Studio Build Tools + PortAudio)
```bash
# Install VS Build Tools with the "C++ build tools" workload from:
# https://visualstudio.microsoft.com/downloads/

# Install PortAudio via vcpkg:
vcpkg install portaudio:x64-windows
set VCPKG_PATH=C:\path\to\vcpkg

pip install -r requirements-voice.txt
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'dotenv'" right after a failed install

This is misleading. If `pip install -r requirements.txt` fails to build *any*
wheel, modern pip aborts the entire install and **doesn't install any of the
packages it downloaded**. Fix the underlying wheel build error first
(usually a missing C library), then re-run `pip install`. The dotenv error
will go away on its own.

### "fatal error C1083: Cannot open include file: 'portaudio.h'"

You're on Windows trying to build PyAudio from source without PortAudio
installed. See "Option 1: pipwin" above for the fast fix.

### Whisper download is slow / hangs

Whisper downloads its model on first use (~150MB for `base`, ~500MB for
`small`). Pre-download it once:

```bash
python -c "import whisper; whisper.load_model('base')"
```

### "VCPKG_PATH environment variable not set" warning

Harmless if you're using pipwin or a prebuilt wheel. Only matters if you're
trying to build PyAudio from source.

### ChromaDB "Downloaded file does not match expected SHA256 hash"

The first time ChromaDB runs, it downloads a small ONNX embedding model
(~80MB). If your network blocks the CDN or the download is partial, you'll
see a hash mismatch. Clear the cache and retry:

```bash
# Linux/macOS
rm -rf ~/.cache/chroma

# Windows
rmdir /s %LOCALAPPDATA%\chroma
```

### "torch wheel is huge" / disk full

Whisper depends on torch, which is ~2GB on disk. If you don't need the local
Whisper backend, you can avoid the torch install by editing
`requirements-voice.txt`:

```
# Comment out or remove this line:
# openai-whisper>=20231117
```

And use Deepgram or OpenAI's hosted Whisper backend instead by setting
`stt_backend: deepgram` (or `openai`) in `config/voice.yaml`.
