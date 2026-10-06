from __future__ import annotations
import json, os, re, subprocess, threading, time
from configparser import ConfigParser
from pathlib import Path

class ZenClient:
    def __init__(self, debug=False):
        self.debug=bool(debug); self._lock=threading.Lock(); self._route_lock=threading.RLock(); self._last_seq=0
        self._voice_audio_restore=[]
        self._voice_audio_generation=0
        self._workspace_visibility_state=None
        self.profile=self._profile(); self.command=self.profile/'tabby-bridge-command.json'; self.state=self.profile/'tabby-bridge-state.json'
        self.bridge_deploy=self._ensure_bridge_deployed()
        self._cursor_guard_file=Path.home()/'.cache/tabby/cursor-no-warps-guard.json'
        self._recover_cursor_no_warps_guard()

    @staticmethod
    def _profile():
        root=Path.home()/'.var/app/app.zen_browser.zen/.zen'; cfg=ConfigParser(); cfg.read(root/'profiles.ini')
        for sec in cfg.sections():
            if sec.startswith('Install') and cfg.has_option(sec,'Default'): return root/cfg.get(sec,'Default')
        for sec in cfg.sections():
            if sec.startswith('Profile') and cfg.get(sec,'Default',fallback='0')=='1': return root/cfg.get(sec,'Path')
        return root/'3bes0gjg.Default (release)'

    def _bundled_bridge_root(self):
        return Path(__file__).resolve().parent.parent/'bridge'/'zen'

    def _bridge_sync_needed(self):
        if os.environ.get('TABBY_SKIP_BRIDGE_DEPLOY') == '1':
            return False
        src=self._bundled_bridge_root()
        if not (src/'theme.json').is_file() or not (self.profile/'chrome/JS').is_dir():
            return False
        mod=self.profile/'chrome/sine-mods/qwqc-hey-tabby-bridge'
        pairs=(
            (src/'theme.json', mod/'theme.json'),
            (src/'README.md', mod/'README.md'),
            (src/'.hey-tabby.uc.js', mod/'hey-tabby.uc.js'),
            (src/'actors/QwqcHeyTabbyChild.sys.mjs', self.profile/'chrome/JS/actors/QwqcHeyTabbyChild.sys.mjs'),
            (src/'engine/tabby-engine.xhtml', self.profile/'chrome/JS/tabby-engine.xhtml'),
        )
        for source,dest in pairs:
            try:
                if not dest.is_file() or source.read_bytes() != dest.read_bytes():
                    return True
            except OSError:
                return True
        try:
            mods=json.loads((self.profile/'chrome/sine-mods/mods.json').read_text())
            entry=mods.get('qwqc-hey-tabby-bridge') or {}
            if entry.get('origin') != 'local' or not entry.get('no-updates'):
                return True
        except Exception:
            return True
        return False

    def _ensure_bridge_deployed(self):
        if not self._bridge_sync_needed():
            return {'ok': True, 'changed': False}
        script=self._bundled_bridge_root()/'scripts/install.sh'
        if not script.is_file():
            return {'ok': False, 'changed': False, 'error': 'bundled-installer-missing'}
        env=os.environ.copy(); env['ZEN_PROFILE']=str(self.profile)
        try:
            result=subprocess.run([str(script)],capture_output=True,text=True,timeout=8,env=env)
            return {
                'ok': result.returncode == 0,
                'changed': result.returncode == 0,
                'stdout': (result.stdout or '').strip(),
                'stderr': (result.stderr or '').strip(),
            }
        except Exception as exc:
            return {'ok': False, 'changed': False, 'error': str(exc)}

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

    def _hypr_clients(self):
        try:
            result=subprocess.run(
                ['hyprctl','clients','-j'], capture_output=True, text=True,
                timeout=1.5, env=self._hypr_env(),
            )
            data=json.loads(result.stdout or '[]')
            return data if isinstance(data,list) else []
        except Exception:
            return []

    @staticmethod
    def _is_tabby_engine_client(client):
        title=str((client or {}).get('title','')).lower()
        workspace=str(((client or {}).get('workspace') or {}).get('name','')).lower()
        return 'tabby engine' in title or workspace.startswith('special:tabby')

    def _user_focus_address(self):
        env=self._hypr_env()
        try:
            result=subprocess.run(
                ['hyprctl','activewindow','-j'], capture_output=True, text=True,
                timeout=1, env=env,
            )
            active=json.loads(result.stdout or '{}')
            addr=str(active.get('address') or '')
            if addr and not self._is_tabby_engine_client(active):
                return addr
        except Exception:
            pass

        # Firefox can make the hidden special-workspace window compositor-active
        # while we prime getUserMedia. Fall back to Hyprland's most recently
        # focused non-Tabby client on an ordinary workspace.
        candidates=[]
        for client in self._hypr_clients():
            if self._is_tabby_engine_client(client):
                continue
            ws=client.get('workspace') or {}
            name=str(ws.get('name') or '')
            try: ws_id=int(ws.get('id'))
            except Exception: ws_id=-1
            if name.startswith('special:') or ws_id < 0:
                continue
            addr=str(client.get('address') or '')
            if not addr:
                continue
            try: rank=int(client.get('focusHistoryID'))
            except Exception: rank=10**9
            candidates.append((rank,addr))
        return min(candidates, default=(None,''))[1]

    def _recover_cursor_no_warps_guard(self):
        path=getattr(self,'_cursor_guard_file',None)
        if not path or not path.exists():
            return
        try:
            data=json.loads(path.read_text())
            original=bool(data.get('original',False))
            if self._set_cursor_no_warps(original):
                path.unlink(missing_ok=True)
        except Exception:
            pass

    def _begin_cursor_no_warps_guard(self):
        original=self._cursor_no_warps()
        path=getattr(self,'_cursor_guard_file',None)
        try:
            if path:
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps({'original':bool(original),'pid':os.getpid(),'time':time.time()}))
        except Exception:
            pass
        ok=self._set_cursor_no_warps(True) and self._cursor_no_warps()
        if not ok:
            try:
                if path: path.unlink(missing_ok=True)
            except Exception:
                pass
        return bool(original), bool(ok)

    def _end_cursor_no_warps_guard(self, original):
        ok=self._set_cursor_no_warps(bool(original))
        try:
            path=getattr(self,'_cursor_guard_file',None)
            if ok and path:
                path.unlink(missing_ok=True)
        except Exception:
            pass
        return ok

    def _cursor_no_warps(self):
        try:
            r=subprocess.run(['hyprctl','getoption','cursor:no_warps'],capture_output=True,text=True,timeout=1,env=self._hypr_env())
            m=re.search(r'bool:\s*(true|false)',(r.stdout or '').lower())
            return (m.group(1) == 'true') if m else False
        except Exception:
            return False

    def _set_cursor_no_warps(self, value):
        rendered='true' if bool(value) else 'false'
        expr=f'hl.config({{ cursor = {{ no_warps = {rendered} }} }})'
        try:
            r=subprocess.run(['hyprctl','eval',expr],capture_output=True,text=True,timeout=1,env=self._hypr_env())
            return r.returncode == 0 and 'error' not in (r.stdout or '').lower()
        except Exception:
            return False

    def _focus_address(self, address):
        address=str(address or '').strip()
        if not address:
            return False
        selector=address if address.startswith('address:') else f'address:{address}'
        expr=f'hl.dsp.focus({{ window = "{selector}" }})'
        try:
            result=subprocess.run(
                ['hyprctl','dispatch',expr], capture_output=True, text=True,
                timeout=1.5, env=self._hypr_env(),
            )
            return result.returncode == 0 and 'error' not in (result.stdout or '').lower()
        except Exception:
            return False

    def _engine_clients(self):
        try:
            result=subprocess.run(['hyprctl','clients','-j'],capture_output=True,text=True,timeout=1.5,env=self._hypr_env())
            clients=json.loads(result.stdout or '[]')
            return [c for c in clients if 'tabby engine' in str(c.get('title','')).lower()]
        except Exception:
            return []

    def _recycle_engine_window(self):
        """Close only Tabby's hidden main engine so the bridge can recreate it.

        This is the recovery path for a live Zen controller whose content actor
        got stuck during navigation/focus. Normal Zen windows are untouched.
        """
        env=self._hypr_env()
        closed=False
        for client in self._engine_clients():
            addr=str(client.get('address') or '')
            if not addr:
                continue
            selector=f'address:{addr}'
            expr=f'hl.dsp.window.close({{ window = "{selector}" }})'
            try:
                result=subprocess.run(
                    ['hyprctl','dispatch',expr], capture_output=True, text=True,
                    timeout=1.5, env=env,
                )
                closed = closed or result.returncode == 0
            except Exception:
                pass
        if closed:
            time.sleep(.65)
        return closed

    def _set_engine_opacity(self, value):
        """Set effective Tabby engine alpha through a named runtime rule.

        Hyprland 0.56's Lua set_prop accepts the old opacity call but does not
        alter effective alpha. Re-declaring a named window rule does, and applies
        atomically to the existing engine as well as a recycled replacement.
        """
        env=self._hypr_env()
        rendered=("%.3f" % float(value)).rstrip('0').rstrip('.') or '0'
        expr=(
            'hl.window_rule({ name = "tabby-runtime-alpha", '
            'match = { title = ".*Tabby Engine.*" }, '
            f'opacity = "{rendered} override" }})'
        )
        try:
            r=subprocess.run(['hyprctl','eval',expr],capture_output=True,text=True,timeout=1.2,env=env)
            return r.returncode == 0 and 'ok' in (r.stdout or '').lower()
        except Exception:
            return False

    def _engine_opacity(self):
        env=self._hypr_env()
        clients=self._engine_clients()
        if not clients:
            return None
        addr=str(clients[0].get('address') or '')
        if not addr:
            return None
        try:
            r=subprocess.run(['hyprctl','getprop',f'address:{addr}','opacity'],capture_output=True,text=True,timeout=1,env=env)
            return float((r.stdout or '').strip())
        except Exception:
            return None

    def _wait_engine_opacity(self, value, timeout=.7):
        target=float(value); end=time.monotonic()+max(.05,float(timeout))
        while time.monotonic()<end:
            current=self._engine_opacity()
            if current is not None and abs(current-target) <= .01:
                return True
            time.sleep(.015)
        return False

    @staticmethod
    def _pulse_unquote(value):
        value=str(value or '').strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return value[1:-1]
        return value

    def _pulse_sink_map(self):
        out={}
        try:
            r=subprocess.run(['pactl','list','short','sinks'],capture_output=True,text=True,timeout=1.5)
            for line in (r.stdout or '').splitlines():
                parts=line.split('\t')
                if len(parts) >= 2: out[str(parts[0]).strip()]=parts[1].strip()
        except Exception:
            pass
        return out

    def _pulse_sink_inputs(self):
        rows=[]; cur=None
        try:
            r=subprocess.run(['pactl','list','sink-inputs'],capture_output=True,text=True,timeout=1.8)
        except Exception:
            return rows
        for raw in (r.stdout or '').splitlines():
            line=raw.strip()
            if line.startswith('Sink Input #'):
                if cur: rows.append(cur)
                cur={'id':line.split('#',1)[1].strip()}
                continue
            if cur is None: continue
            if line.startswith('Sink:'):
                cur['sink_index']=line.split(':',1)[1].strip()
            elif line.startswith('Volume:'):
                m=re.search(r'/\s*([0-9.]+)%',line)
                if m: cur['volume_percent']=float(m.group(1))
            elif line.startswith('application.name ='):
                cur['application_name']=self._pulse_unquote(line.split('=',1)[1])
            elif line.startswith('media.name ='):
                cur['media_name']=self._pulse_unquote(line.split('=',1)[1])
        if cur: rows.append(cur)
        return rows

    def _pipewire_objects(self):
        try:
            r=subprocess.run(['pw-dump'],capture_output=True,text=True,timeout=2.0)
            data=json.loads(r.stdout or '[]')
            return data if isinstance(data,list) else []
        except Exception:
            return []

    def _speaker_pipewire_target(self):
        for obj in self._pipewire_objects():
            props=((obj.get('info') or {}).get('props') or {}) if isinstance(obj,dict) else {}
            if props.get('media.class') != 'Audio/Sink':
                continue
            name=str(props.get('node.name') or '')
            if '__Speaker__sink' in name:
                try:
                    return int(obj.get('id')), int(props.get('object.serial'))
                except Exception:
                    return None
        return None

    def _voice_pipewire_nodes(self, title):
        title=str(title or '').strip()
        out=[]
        if not title:
            return out
        for obj in self._pipewire_objects():
            if not isinstance(obj,dict):
                continue
            props=(obj.get('info') or {}).get('props') or {}
            if (props.get('media.class') == 'Stream/Output/Audio'
                    and props.get('application.name') == 'Zen'
                    and str(props.get('media.name') or '') == title):
                try:
                    out.append((int(obj.get('id')), props))
                except Exception:
                    pass
        return out

    def _hardware_speaker_sink(self):
        sinks=self._pulse_sink_map()
        for name in sinks.values():
            if '__Speaker__sink' in name:
                return name
        for name in sinks.values():
            if name.startswith('alsa_output.') and 'HDMI' not in name.upper():
                return name
        return ''

    def _route_voice_audio(self, title):
        """Route Tabby's real PipeWire voice nodes directly to speakers."""
        title=str(title or '').strip()
        if not title:
            return False
        target=self._speaker_pipewire_target()
        if not target:
            return False
        speaker_node,speaker_serial=target
        routed=False
        for node_id,props in self._voice_pipewire_nodes(title):
            node_key=f'pw:{node_id}'
            if not any(str(x.get('id')) == node_key for x in self._voice_audio_restore):
                self._voice_audio_restore.append({
                    'id':node_key,
                    'node_id':node_id,
                    'target_object':str(props.get('target.object') or ''),
                })
            try:
                a=subprocess.run([
                    'pw-metadata','-n','default',str(node_id),'target.object',
                    str(speaker_serial),'Spa:Id'
                ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1.2)
                b=subprocess.run([
                    'pw-metadata','-n','default',str(node_id),'target.node',
                    str(speaker_node),'Spa:Id'
                ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1.2)
                subprocess.run([
                    'wpctl','set-volume',str(node_id),'1.0'
                ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1.2)
                routed = routed or (a.returncode == 0 and b.returncode == 0)
            except Exception:
                pass
        return routed

    def _start_voice_audio_burst(self, title):
        title=str(title or '').strip()
        if not title:
            return
        self._voice_audio_generation += 1
        generation=self._voice_audio_generation
        def work():
            # WebRTC creates/replaces and may re-route output nodes throughout
            # the whole call. Watch PipeWire only; do not poke the hidden browser
            # actor while Voice is running. end() advances the generation.
            while generation == self._voice_audio_generation:
                self._route_voice_audio(title)
                time.sleep(.15)
        threading.Thread(target=work,name='tabby-voice-audio-route',daemon=True).start()

    def _restore_voice_audio(self):
        rows=list(self._voice_audio_restore); self._voice_audio_restore=[]
        for row in rows:
            node_id=row.get('node_id')
            if node_id is not None:
                # Voice nodes normally disappear on end. Remove our temporary
                # WirePlumber target metadata if the node still exists/lingers.
                for key in ('target.object','target.node'):
                    try:
                        subprocess.run([
                            'pw-metadata','-n','default','-d',str(node_id),key
                        ],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1)
                    except Exception:
                        pass
                continue
            sid=str(row.get('id') or '')
            if not sid:
                continue
            try:
                sink=str(row.get('sink') or '')
                if sink:
                    subprocess.run(['pactl','move-sink-input',sid,sink],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1)
                volume=max(0.0,float(row.get('volume') or 100.0))
                subprocess.run(['pactl','set-sink-input-volume',sid,f'{volume:.2f}%'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1)
            except Exception:
                pass

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

                # Keep the parked browser alive on special:tabby. Do not set
                # no_focus here: on Hyprland 0.56 that dynamic property becomes
                # sticky and prevents the debug window from ever receiving real
                # focus again. A hidden special workspace already keeps it out
                # of the user's input path.
                render_expr=f'hl.dsp.window.set_prop({{ prop = "render_unfocused", value = 1, window = "{selector}" }})'
                subprocess.run(['hyprctl','eval',render_expr],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,env=env)

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
        if self._running() and str(self._read().get('version','')).startswith(('0.3.','0.4.','0.5.','0.6.','0.7.','0.8.','0.9.','0.10.')): return True
        if not self._running():
            try: subprocess.Popen(['flatpak','run','app.zen_browser.zen'],env=self._env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            except Exception:return False
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if self._running() and str(self._read().get('version','')).startswith(('0.3.','0.4.','0.5.','0.6.','0.7.','0.8.','0.9.','0.10.')): return True
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
                # If Zen is alive but the command poller/content actor is stuck,
                # recycle only the hidden Tabby engine. Closing that content
                # process also rejects any wedged JSWindowActor query, allowing
                # the parent controller to resume. Then retry this command once.
                if attempt == 0 and self._running():
                    if not str(command).startswith('worker-'):
                        self._recycle_engine_window()
                    time.sleep(.8)
                    continue
                break
            return {"ok":False,"result":"zen-bridge-timeout"}

    def open_chat(self,url):
        with self._route_lock:
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

    def status(self):
        # Read-only by design. The backend polls this frequently from monitor
        # threads; changing workspaces here races the WebRTC focus handshake and
        # can expose special:tabby for a frame/second.
        return self.call('status',timeout=3)
    def new_chat(self):
        with self._route_lock:
            result=self.call('new-chat',timeout=25,debug=self.debug)
            self._route_engine_window(self.debug)
            return result
    def continue_chat(self):
        with self._route_lock:
            result=self.call('continue-chat',timeout=12,debug=self.debug)
            self._route_engine_window(self.debug)
            return result

    def _tabby_special_open(self):
        try:
            r=subprocess.run(['hyprctl','monitors','-j'],capture_output=True,text=True,timeout=1,env=self._hypr_env())
            monitors=json.loads(r.stdout or '[]')
            return any(str((m.get('specialWorkspace') or {}).get('name') or '') == 'special:tabby' for m in monitors)
        except Exception:
            return False

    def sync_workspace_visibility(self, force=False):
        """Reveal Zen only when the user explicitly has special:tabby open."""
        if self.debug:
            return
        clients=self._engine_clients()
        if not clients:
            self._workspace_visibility_state=None
            return
        c=clients[0]
        addr=str(c.get('address') or '')
        ws=str((c.get('workspace') or {}).get('name') or '')
        desired=1 if (ws == 'special:tabby' and self._tabby_special_open()) else 0
        state=(addr,desired)
        if force or state != getattr(self,'_workspace_visibility_state',None):
            self._set_engine_opacity(desired)
            self._workspace_visibility_state=state

    def _wait_engine_workspace(self, name, timeout=.8):
        end=time.monotonic()+max(.05,float(timeout))
        wanted=str(name)
        while time.monotonic()<end:
            clients=self._engine_clients()
            if clients:
                current=str((clients[0].get('workspace') or {}).get('name') or (clients[0].get('workspace') or {}).get('id') or '')
                if current == wanted:
                    return True
            time.sleep(.015)
        return False

    def _stage_hidden_engine(self):
        self._workspace_visibility_state=None
        # Hide first and VERIFY effective compositor alpha before the engine is
        # ever moved onto a physical workspace. This is the no-flash invariant.
        if not self._set_engine_opacity(0):
            return False
        if not self._wait_engine_opacity(0,.7):
            return False
        self._route_engine_window(True)
        if not self._wait_engine_opacity(0,.35):
            return False
        time.sleep(.02)
        return True

    def _park_hidden_engine(self, restore_address=''):
        # Critical ordering: the engine is currently focused. Restore the user's
        # real window BEFORE moving Tabby back to special:tabby. Moving a focused
        # window to a special workspace makes Hyprland expose that workspace for
        # a frame even with follow=false. Once user focus is restored, parking is
        # a pure background move and cannot switch workspaces.
        self._set_engine_opacity(0)
        if restore_address:
            self._focus_address(restore_address)
            time.sleep(.025)
        self._route_engine_window(False)
        self._wait_engine_workspace('special:tabby',.7)
        self._workspace_visibility_state=None
        self.sync_workspace_visibility(force=True)

    def activate(self):
        with self._route_lock:
            restore_address=self._user_focus_address() if not self.debug else ''
            hidden=not self.debug
            restore_no_warps=False
            guard_ok=True
            if hidden:
                restore_no_warps,guard_ok=self._begin_cursor_no_warps_guard()
            # Existing audio contexts already carry the page title before Voice is
            # clicked, so route them off the spatial/150% path before speech starts.
            pre_title=str(self._read().get('title') or '')
            if pre_title:
                self._route_voice_audio(pre_title)
            try:
                if hidden and not guard_ok:
                    return {"ok":False,"result":"cursor-warp-guard-failed"}
                if hidden and not self._stage_hidden_engine():
                    return {"ok":False,"result":"engine-hide-verification-failed"}
                result=self.call('activate',timeout=8.0,debug=self.debug)
                title=str(result.get('title') or pre_title)
                if title:
                    for _ in range(3):
                        self._route_voice_audio(title)
                        time.sleep(.08)
                    if result.get('ok') and result.get('active'):
                        self._start_voice_audio_burst(title)
            finally:
                if hidden:
                    self._park_hidden_engine(restore_address)
                    self._end_cursor_no_warps_guard(restore_no_warps)
                else:
                    self._route_engine_window(True)
            return result
    def mic_on(self):
        with self._route_lock:
            restore_address=self._user_focus_address() if not self.debug else ''
            hidden=not self.debug
            restore_no_warps=False
            guard_ok=True
            if hidden:
                restore_no_warps,guard_ok=self._begin_cursor_no_warps_guard()
            try:
                if hidden and not guard_ok:
                    return {"ok":False,"result":"cursor-warp-guard-failed"}
                if hidden and not self._stage_hidden_engine():
                    return {"ok":False,"result":"engine-hide-verification-failed"}
                if hidden:
                    self.call('focus-engine-content',timeout=4)
                result=self.call('mic-on',timeout=5)
            finally:
                if hidden:
                    self._park_hidden_engine(restore_address)
                    self._end_cursor_no_warps_guard(restore_no_warps)
                else:
                    self._route_engine_window(True)
            return result
    def end(self, reset=False):
        with self._route_lock:
            restore_address=self._user_focus_address() if not self.debug else ''
            hidden=not self.debug
            restore_no_warps=False
            guard_ok=True
            if hidden:
                restore_no_warps,guard_ok=self._begin_cursor_no_warps_guard()
            self._voice_audio_generation += 1
            try:
                staged = self._stage_hidden_engine() if (hidden and guard_ok) else (not hidden)
                result=self.call('end',timeout=6,debug=self.debug,reset=bool(reset))
            finally:
                self._restore_voice_audio()
                if hidden:
                    self._park_hidden_engine(restore_address)
                    self._end_cursor_no_warps_guard(restore_no_warps)
                else:
                    self._route_engine_window(True)
            return result
    def send_text(self,text): return self.call('send-text',timeout=10,text=text)
    def latest_response(self): return self.call('latest-response',timeout=4)
    def read_aloud(self): return self.call('read-aloud',timeout=5)
    def paste_image(self,data,mime='image/png',name='tabby-paste.png'):
        import base64
        return self.call('paste-image',timeout=18,base64=base64.b64encode(data).decode(),mime=mime,name=name)
    def set_debug(self,value):
        with self._route_lock:
            self.debug=bool(value)
            if self.debug:
                # Bring it over while still transparent, then reveal only after the
                # move has completed. This is the sole path allowed to set opacity 1.
                self._set_engine_opacity(0)
                result=self.call('show',timeout=10)
                self._route_engine_window(True)
                time.sleep(.05)
                self._set_engine_opacity(1)
                return result
            # Hide during the move, then leave the parked engine normally visible
            # inside special:tabby. Dev-off means "do not auto-show it", not
            # "make the workspace contents permanently transparent".
            self._set_engine_opacity(0)
            time.sleep(.03)
            result=self.call('hide',timeout=10)
            self._route_engine_window(False)
            self._wait_engine_workspace('special:tabby',.7)
            self._workspace_visibility_state=None
            self.sync_workspace_visibility(force=True)
            return result
    def hide(self):
        with self._route_lock:
            result=self.call('hide',timeout=8)
            self._route_engine_window(False)
            self._workspace_visibility_state=None
            self.sync_workspace_visibility(force=True)
            return result
