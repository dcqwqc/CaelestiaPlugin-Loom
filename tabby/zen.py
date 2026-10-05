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

    def _hypr_env(self):
        env=self._env()
        if not env.get('HYPRLAND_INSTANCE_SIGNATURE'):
            try:
                result=subprocess.run(['systemctl','--user','show-environment'],capture_output=True,text=True,timeout=1)
                for line in result.stdout.splitlines():
                    if '=' not in line: continue
                    k,v=line.split('=',1)
                    if k in {'HYPRLAND_INSTANCE_SIGNATURE','XDG_RUNTIME_DIR','WAYLAND_DISPLAY','DISPLAY'}:
                        env[k]=v
            except Exception:
                pass
        return env

    def _engine_clients(self):
        try:
            result=subprocess.run(['hyprctl','clients','-j'],capture_output=True,text=True,timeout=1.5,env=self._hypr_env())
            clients=json.loads(result.stdout or '[]')
            return [c for c in clients if 'tabby engine' in str(c.get('title','')).lower()]
        except Exception:
            return []

    def _route_engine_window(self, visible: bool):
        env=self._hypr_env()
        clients=self._engine_clients()
        if not clients: return
        try:
            active_ws='1'
            monitor=None
            if visible:
                aw=subprocess.run(['hyprctl','activeworkspace','-j'],capture_output=True,text=True,timeout=1,env=env)
                info=json.loads(aw.stdout or '{}')
                active_ws=str(info.get('id') or info.get('name') or '1')
                mons=subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=1,env=env)
                monitors=json.loads(mons.stdout or '[]')
                monitor=next((m for m in monitors if m.get('focused')), monitors[0] if monitors else None)

            for c in clients:
                addr=c.get('address')
                if not addr: continue
                selector=f'address:{addr}'
                workspace=active_ws if visible else 'special:tabby'
                current_ws=str((c.get('workspace') or {}).get('name') or (c.get('workspace') or {}).get('id') or '')
                moved=current_ws != workspace
                geometry_changed=False

                # Hidden AI surfaces must never own keyboard focus. Debug mode
                # explicitly makes the main engine focusable again.
                no_focus = 'false' if visible else 'true'
                prop_expr=f'hl.dsp.window.set_prop({{ prop = "no_focus", value = "{no_focus}", window = "{selector}" }})'
                subprocess.run(['hyprctl','eval',prop_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)

                if moved:
                    move_expr=f'hl.dsp.window.move({{ window = "{selector}", workspace = "{workspace}", follow = false }})'
                    subprocess.run(['hyprctl','dispatch',move_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)

                if int(c.get('fullscreen') or 0) != 0:
                    fs_expr=f'hl.dsp.window.fullscreen_state({{ internal = 0, client = 0, action = "set", window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',fs_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                    geometry_changed=True
                if not c.get('floating'):
                    float_expr=f'hl.dsp.window.float({{ window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',float_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                    geometry_changed=True
                size=list(c.get('size') or [])
                if size != [900,760]:
                    resize_expr=f'hl.dsp.window.resize({{ x = 900, y = 760, relative = false, window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',resize_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                    geometry_changed=True

                if visible and monitor and (moved or geometry_changed):
                    scale=float(monitor.get('scale') or 1.0)
                    mw=float(monitor.get('width') or 1440)/scale
                    mh=float(monitor.get('height') or 900)/scale
                    mx=float(monitor.get('x') or 0)/scale
                    my=float(monitor.get('y') or 0)/scale
                    x=int(mx + max(0,(mw-900)/2))
                    y=int(my + max(0,(mh-760)/2))
                    pos_expr=f'hl.dsp.window.move({{ x = {x}, y = {y}, relative = false, window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',pos_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                if visible and moved:
                    focus_expr=f'hl.dsp.focus({{ window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',focus_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
        except Exception:
            pass

    def _read(self):
        try:
            x=json.loads(self.state.read_text()); return x if isinstance(x,dict) else {}
        except Exception:return {}

    def _running(self):
        try:return subprocess.run(['pgrep','-f','/app/zen/zen --name app.zen_browser.zen'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=.5).returncode==0
        except Exception:return False

    def ensure(self, timeout=10):
        if self._running() and str(self._read().get('version','')).startswith(('0.3.','0.4.','0.5.','0.6.','0.7.','0.8.','0.9.')): return True
        if not self._running():
            try: subprocess.Popen(['flatpak','run','app.zen_browser.zen'],env=self._env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            except Exception:return False
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if self._running() and str(self._read().get('version','')).startswith(('0.3.','0.4.','0.5.','0.6.','0.7.','0.8.','0.9.')): return True
            time.sleep(.2)
        return False

    def call(self, command, timeout=12, **extra):
        if not self.ensure(): return {"ok":False,"result":"zen-bridge-unavailable"}
        with self._lock:
            for attempt in range(2):
                seq=max(int(time.time()*1000),self._last_seq+1); self._last_seq=seq
                payload={"seq":seq,"command":command,**extra}
                tmp=self.command.with_name(self.command.name+f'.{os.getpid()}.tmp')
                tmp.write_text(json.dumps(payload,separators=(",",":")))
                tmp.replace(self.command)
                end=time.monotonic()+timeout
                while time.monotonic()<end:
                    state=self._read()
                    if state.get('seq')==seq:
                        return state
                    time.sleep(.08)
                # A browser-window controller can disappear while Zen itself is
                # still alive. Bridge v0.8.1 has takeover watchdogs in every
                # normal Zen window; give them a moment, then retry once instead
                # of surfacing a false Tabby error immediately.
                if attempt == 0 and self._running():
                    time.sleep(1.2)
                    continue
                break
            return {"ok":False,"result":"zen-bridge-timeout"}

    def open_chat(self,url):
        result=self.call('open-chat',timeout=18,url=str(url or ''))
        self._route_engine_window(self.debug)
        return result

    def _worker_clients(self, task_id=None):
        try:
            result=subprocess.run(['hyprctl','clients','-j'],capture_output=True,text=True,timeout=1.5,env=self._hypr_env())
            clients=json.loads(result.stdout or '[]')
            prefix='tabby work · '
            out=[]
            for c in clients:
                title=str(c.get('title','')).lower()
                if not title.startswith(prefix): continue
                if task_id is not None and title != (prefix + str(task_id).lower()): continue
                out.append(c)
            return out
        except Exception:
            return []

    def _route_worker_window(self, task_id):
        env=self._hypr_env()
        try:
            for c in self._worker_clients(task_id):
                addr=c.get('address')
                if not addr: continue
                selector=f'address:{addr}'
                prop_expr=f'hl.dsp.window.set_prop({{ prop = "no_focus", value = "true", window = "{selector}" }})'
                subprocess.run(['hyprctl','eval',prop_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                ws=str((c.get('workspace') or {}).get('name') or '')
                if ws != 'special:tabby-work':
                    expr=f'hl.dsp.window.move({{ window = "{selector}", workspace = "special:tabby-work", follow = false }})'
                    subprocess.run(['hyprctl','dispatch',expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                if int(c.get('fullscreen') or 0) != 0:
                    expr=f'hl.dsp.window.fullscreen_state({{ internal = 0, client = 0, action = "set", window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
                if not c.get('floating'):
                    expr=f'hl.dsp.window.float({{ window = "{selector}" }})'
                    subprocess.run(['hyprctl','dispatch',expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)
        except Exception:
            pass

    def worker_open(self,task_id,url,reload=False):
        result=self.call('worker-open',timeout=18,taskId=str(task_id),url=str(url),reload=bool(reload))
        self._route_worker_window(task_id)
        return result

    def worker_status(self,task_id): return self.call('worker-status',timeout=4,taskId=str(task_id))
    def worker_latest_response(self,task_id): return self.call('worker-latest-response',timeout=4,taskId=str(task_id))
    def worker_close(self,task_id): return self.call('worker-close',timeout=5,taskId=str(task_id))

    def status(self): return self.call('status',timeout=3)
    def new_chat(self):
        result=self.call('new-chat',timeout=25,debug=self.debug)
        self._route_engine_window(self.debug)
        return result
    def continue_chat(self):
        result=self.call('continue-chat',timeout=12,debug=self.debug)
        self._route_engine_window(self.debug)
        return result
    def activate(self): return self.call('activate',timeout=5.5,debug=self.debug)
    def mic_on(self): return self.call('mic-on',timeout=5)
    def end(self, reset=False): return self.call('end',timeout=8,debug=self.debug,reset=bool(reset))
    def send_text(self,text): return self.call('send-text',timeout=10,text=text)
    def latest_response(self): return self.call('latest-response',timeout=4)
    def read_aloud(self): return self.call('read-aloud',timeout=5)
    def paste_image(self,data,mime='image/png',name='tabby-paste.png'):
        import base64
        return self.call('paste-image',timeout=18,base64=base64.b64encode(data).decode(),mime=mime,name=name)
    def set_debug(self,value):
        self.debug=bool(value)
        result=self.call('show' if self.debug else 'hide',timeout=10)
        self._route_engine_window(self.debug)
        return result
    def hide(self):
        result=self.call('hide',timeout=8)
        self._route_engine_window(False)
        return result
