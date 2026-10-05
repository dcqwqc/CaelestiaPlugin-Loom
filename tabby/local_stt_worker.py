from __future__ import annotations
import contextlib, json, sys, time
from pathlib import Path

P7 = Path.home() / 'protocol-7'
sys.path.insert(0, str(P7))

import numpy as np
import sounddevice as sd
from config import load_config
from whisper_engine import WhisperEngine

config = load_config()
engine = WhisperEngine(config)
sample_rate = 16000
device = config.get('input_device')
block = 1600  # 100 ms
speech_rms = 0.010
silence_needed = 7
min_speech_chunks = 3
max_chunks = 120
active: list[np.ndarray] = []
speaking = False
silent = 0
speech = 0
capture_enabled = True


def emit(kind: str, **payload):
    try:
        print(json.dumps({'type': kind, **payload}, ensure_ascii=False), flush=True)
    except BrokenPipeError:
        raise SystemExit(0)


def transcribe(audio: np.ndarray) -> str:
    try:
        # WhisperEngine logs are useful for debugging but stdout is our JSON IPC.
        with contextlib.redirect_stdout(sys.stderr):
            return str(engine.transcribe(audio, live=False, allow_local_fallback=True) or '').strip()
    except Exception as exc:
        emit('error', error=str(exc))
        return ''


def control_reader():
    global capture_enabled, active, speaking, silent, speech
    for raw in sys.stdin:
        cmd = str(raw or '').strip().lower()
        if cmd == 'pause':
            capture_enabled = False
            active = []; speaking = False; silent = 0; speech = 0
            emit('paused')
        elif cmd == 'resume':
            active = []; speaking = False; silent = 0; speech = 0
            capture_enabled = True
            emit('resumed')
        elif cmd == 'stop':
            raise SystemExit(0)


def callback(indata, frames, time_info, status):
    global active, speaking, silent, speech
    if not capture_enabled:
        return
    chunk = np.asarray(indata[:, 0], dtype=np.float32).copy()
    rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
    emit('level', level=min(1.0, max(0.0, (rms - 0.004) / 0.08)))
    voiced = rms >= speech_rms
    if voiced:
        speaking = True; silent = 0; speech += 1; active.append(chunk)
    elif speaking:
        silent += 1; active.append(chunk)
    if speaking and ((silent >= silence_needed and speech >= min_speech_chunks) or len(active) >= max_chunks):
        audio = np.concatenate(active).astype(np.float32) if active else np.array([], dtype=np.float32)
        active = []; speaking = False; silent = 0; speech = 0
        if len(audio) >= int(sample_rate * 0.5):
            text = transcribe(audio)
            if text:
                emit('transcript', text=text)


try:
    import threading
    threading.Thread(target=control_reader, name='tabby-local-stt-control', daemon=True).start()
    with sd.InputStream(samplerate=sample_rate, device=device, channels=1, blocksize=block, dtype='float32', callback=callback):
        emit('ready', sample_rate=sample_rate, device=device)
        while True:
            time.sleep(0.2)
except KeyboardInterrupt:
    pass
except Exception as exc:
    emit('error', error=str(exc))
    raise
