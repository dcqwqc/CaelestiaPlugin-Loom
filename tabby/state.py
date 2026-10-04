from __future__ import annotations
import copy, json, sys, threading
from typing import Any

VALID_STATES={"idle","wake","listening","thinking","tool","speaking","approval","success","error"}

class TabbyState:
    def __init__(self, enabled=True):
        self._lock=threading.RLock(); self._write_lock=threading.Lock()
        self._state={
            "enabled":bool(enabled),"summoned":False,"state":"idle","inputArmed":False,
            "audioLevel":0.0,"attachmentPending":False,"whiteboardVisible":False,
            "items":[],"sequence":0
        }
        self.publish()

    def snapshot(self):
        with self._lock: return copy.deepcopy(self._state)

    def publish(self):
        payload=json.dumps(self.snapshot(),separators=(",",":"),ensure_ascii=False)
        with self._write_lock:
            try: sys.stdout.write(f"TABBY_STATE {payload}\n"); sys.stdout.flush()
            except (BrokenPipeError,OSError): pass

    def update(self, **values):
        changed=False
        with self._lock:
            for k,v in values.items():
                if self._state.get(k)!=v:
                    self._state[k]=v; changed=True
            if changed: self._state["sequence"]+=1
        if changed: self.publish()

    def set_state(self, value):
        value=str(value).lower()
        if value not in VALID_STATES: value="idle"
        self.update(state=value)

    def whiteboard(self, command: dict[str,Any]):
        kind=str(command.get("command","")).lower()
        with self._lock:
            if kind=="clear": self._state["items"]=[]; self._state["whiteboardVisible"]=False
            elif kind=="show": self._state["whiteboardVisible"]=True
            elif kind=="hide": self._state["whiteboardVisible"]=False
            elif kind in {"text","progress","choice","shape"}:
                item={k:v for k,v in command.items() if k!="command"}; item["type"]=kind
                self._state["items"]=(list(self._state["items"])+[item])[-32:]
                self._state["whiteboardVisible"]=True
            else: return {"ok":False,"error":"unsupported whiteboard command"}
            self._state["sequence"]+=1
        self.publish(); return {"ok":True,"state":self.snapshot()}
