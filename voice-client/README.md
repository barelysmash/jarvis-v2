# JARVIS voice client (rosencrantz)

`voice_client.py` here is the master copy. **It is not what runs.** The live
copy sits in `C:\Users\bgama\jarvis-voice\` next to its own `.env` (API keys
and audio device settings are not in this repo; the docstring in
`voice_client.py` lists the variables).

So after a `git pull` that changes the voice client, the running client is
still the old one until you deploy it:

```bash
bash /c/bSmash-dev/jarvis-v2/voice-client/deploy-voice.sh
```

The script backs up the live copy, copies this one over it, verifies the
result and leaves `.env` alone. Then close and relaunch the client. Set
`JARVIS_VOICE_DIR` if the live copy lives somewhere else.

Patch here first, then deploy. Never edit the live copy directly.

## How to tell which code is running

On start, the client prints `[announce] connected; reminders will be spoken
here.` once it reaches JARVIS. If that line never appears, the live copy
predates the reminder listener (2026-11-13) and needs deploying.
