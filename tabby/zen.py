from __future__ import annotations
import json, os, subprocess, threading, time
from configparser import ConfigParser
from pathlib import Path

class ZenClient:
    def __init__(self, debug=False):
        self.debug=bool(debug); self._lock=threading.Lock(); self._last_seq=0
        self.profile=self._profile(); self.command=self.profile/'tabby-bridge-command.json'; self.state=self.profile/'tabby-bridge-state.json'

    @staticmethod
    def _profile():
        root=Path.home()/'.var/app/app.zen_browser.zen/.zen'; cfg=ConfigParser(); cfg.read(root/'profiles.ini')
        for sec in cfg.sections():
            if sec.startswith('Install') and cfg.has_option(sec,'Default'): return root/cfg.get(sec,'Default')
        for sec in cfg.sections():
            if sec.startswith('Profile') and cfg.get(sec,'Default',fallback='0')=='1': return root/cfg.get(sec,'Path')
        return root/'3bes0gjg.Default (release)'

    def _env(self):
        e=os.environ.copy(); e.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'); e.setdefault('DISPLAY',':1'); e.setdefault('WAYLAND_DISPLAY','wayland-1'); return e

    def _read(self):
        try:
            x=json.loads(self.state.read_text()); return x if isinstance(x,dict) else {}
        except Exception:return {}

    def _running(self):
        try:return subprocess.run(['pgrep','-f','/app/zen/zen --name app.zen_browser.zen'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=.5).returncode==0
        except Exception:return False

    def ensure(self, timeout=10):
        if self._running() and str(self._read().get('version','')).startswith('0.3.'): return True
        if not self._running():
            try: subprocess.Popen(['flatpak','run','app.zen_browser.zen'],env=self._env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            except Exception:return False
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if self._running() and str(self._read().get('version','')).startswith('0.3.'): return True
            time.sleep(.2)
        return False

    def call(self, command, timeout=12, **extra):
        if not self.ensure(): return {"ok":False,"result":"zen-bridge-unavailable"}
        with self._lock:
            seq=max(int(time.time()*1000),self._last_seq+1); self._last_seq=seq
            payload={"seq":seq,"command":command,**extra}
            tmp=self.command.with_name(self.command.name+f'.{os.getpid()}.tmp'); tmp.write_text(json.dumps(payload,separators=(",",":"))); tmp.replace(self.command)
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                s=self._read()
                if s.get('seq')==seq: return s
                time.sleep(.08)
            return {"ok":False,"result":"zen-bridge-timeout"}

    def status(self): return self.call('status',timeout=3)
    def new_chat(self): return self.call('new-chat',timeout=25,debug=self.debug)
    def activate(self): return self.call('activate',timeout=18,debug=self.debug)
    def end(self): return self.call('end',timeout=8,debug=self.debug)
    def send_text(self,text): return self.call('send-text',timeout=10,text=text)
    def paste_image(self,data,mime='image/png',name='tabby-paste.png'):
        import base64
        return self.call('paste-image',timeout=18,base64=base64.b64encode(data).decode(),mime=mime,name=name)
    def set_debug(self,value):
        self.debug=bool(value); return self.call('show' if self.debug else 'hide',timeout=6)
    def hide(self): return self.call('hide',timeout=5)
