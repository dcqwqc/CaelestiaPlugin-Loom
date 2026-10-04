from __future__ import annotations
import math, os, struct, subprocess, threading, time

class AudioMeter:
    def __init__(self, callback, sensitivity=1.8):
        self.callback=callback; self.sensitivity=float(sensitivity); self._stop=threading.Event(); self._thread=None; self._proc=None

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread=threading.Thread(target=self._run,name='tabby-audio-meter',daemon=True); self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            try:self._proc.terminate()
            except Exception:pass
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=1)

    def _monitor(self):
        try:
            sink=subprocess.run(['pactl','get-default-sink'],capture_output=True,text=True,timeout=1).stdout.strip()
            return sink+'.monitor' if sink else '@DEFAULT_SINK@.monitor'
        except Exception:return '@DEFAULT_SINK@.monitor'

    def _run(self):
        while not self._stop.is_set():
            try:
                self._proc=subprocess.Popen(['parec',f'--device={self._monitor()}','--format=s16le','--rate=16000','--channels=1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                while not self._stop.is_set():
                    raw=self._proc.stdout.read(2560) if self._proc.stdout else b''
                    if not raw: break
                    count=len(raw)//2
                    if not count: continue
                    vals=struct.unpack('<'+'h'*count,raw[:count*2])
                    rms=math.sqrt(sum(v*v for v in vals)/count)/32768.0
                    level=max(0.0,min(1.0,(rms*self.sensitivity)**0.62))
                    self.callback(level)
                self.callback(0.0)
            except Exception:self.callback(0.0)
            if self._proc:
                try:self._proc.terminate()
                except Exception:pass
            self._proc=None
            self._stop.wait(.5)
