from __future__ import annotations
import copy, json, sys, threading
from typing import Any

from tabby import display
from tabby.ui_tree import UIError, UIViews

VALID_STATES={"idle","wake","listening","thinking","tool","speaking","approval","success","error"}

class TabbyState:
    def __init__(self, enabled=True):
        self._lock=threading.RLock(); self._write_lock=threading.Lock()
        self.ui=UIViews()
        self._state={
            "enabled":bool(enabled),"summoned":False,"voiceActive":False,"state":"idle","inputArmed":False,
            "audioLevel":0.0,"attachmentPending":False,"whiteboardVisible":False,
            "items":[],"uiViews":[],"working":[],"notifications":[],"fnHotkeyAvailable":False,"altHotkeyAvailable":False,"sequence":0
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
        try:
            with self._lock:
                items=list(self._state["items"])
                result: dict[str,Any]={"ok":True}
                if kind=="clear": items=[]; self.ui.views.clear(); self._state["whiteboardVisible"]=False
                elif kind=="show": self._state["whiteboardVisible"]=True
                elif kind=="hide": self._state["whiteboardVisible"]=False
                elif kind in {"text","progress","choice","shape"}:
                    # Legacy one-item commands: the command name is the item type.
                    raw={k:v for k,v in command.items() if k!="command"}; raw["type"]=kind
                    items,item=display.upsert(items,raw)
                    result["item"]=item; self._state["whiteboardVisible"]=True
                elif kind=="display":
                    raw_items=command.get("items")
                    if not isinstance(raw_items,list) or not raw_items:
                        return {"ok":False,"error":"display needs a non-empty 'items' list"}
                    mode=str(command.get("mode") or "append").lower()
                    if mode=="replace": items=[]
                    shown=[]
                    for raw in raw_items[:display.MAX_ITEMS]:
                        items,item=display.upsert(items,raw,merge=mode!="replace")
                        shown.append(item)
                    result["items"]=shown
                    if command.get("show",True): self._state["whiteboardVisible"]=True
                elif kind=="ui-remove":
                    wanted={display.item_id(i) for i in (command.get("ids") or [command.get("item_id")]) if i}
                    before=len(items); items=[i for i in items if i.get("id") not in wanted]
                    result["removed"]=before-len(items)
                    if not items and not self.ui.views: self._state["whiteboardVisible"]=False
                elif kind=="choose":
                    wanted=display.item_id(command.get("item_id")); option=str(command.get("option") or "")
                    hit=next((i for i in items if i.get("id")==wanted and i.get("type")=="choice"),None)
                    if not hit or option not in hit.get("options",[]):
                        return {"ok":False,"error":"unknown choice or option"}
                    items=[dict(i,selected=option) if i is hit else i for i in items]
                    result["selected"]=option
                else: return {"ok":False,"error":"unsupported whiteboard command"}
                self._state["items"]=items[-display.MAX_ITEMS:]
                self._state["uiViews"]=self.ui.snapshot()
                self._state["sequence"]+=1
        except ValueError as error:
            return {"ok":False,"error":str(error)}
        self.publish()
        result["whiteboardVisible"]=self._state["whiteboardVisible"]
        return result

    def ui_view(self, command: dict[str,Any]):
        """Declarative views (tabby.ui_tree) rendered on the board beside items."""
        kind=str(command.get("command","")).lower()
        view_id=command.get("view_id")
        try:
            with self._lock:
                if kind=="ui-events":
                    return {"ok":True,**self.ui.read_events(command.get("since",0),view_id)}
                if kind=="ui-get":
                    return {"ok":True,"view":self.ui.get(view_id)} if view_id is not None else {"ok":True,"views":self.ui.snapshot()}
                if kind=="ui-render":
                    result={"view":self.ui.render(view_id,command.get("root"),command.get("title") or "",command.get("base_revision"))}
                elif kind=="ui-patch":
                    result={"view":self.ui.patch(view_id,command.get("ops"),command.get("base_revision"))}
                elif kind in {"ui-undo","ui-redo"}:
                    result={"view":self.ui.undo(view_id,redo=kind=="ui-redo")}
                elif kind=="ui-close":
                    result=self.ui.close(view_id)
                elif kind=="ui-event":
                    result={"event":self.ui.dispatch(view_id,command.get("node_id"),command.get("event"),command.get("value"))}
                else: return {"ok":False,"error":"unsupported ui command"}
                self._state["uiViews"]=self.ui.snapshot()
                if kind in {"ui-render","ui-patch","ui-undo","ui-redo"}: self._state["whiteboardVisible"]=True
                elif kind=="ui-close" and not self._state["items"] and not self.ui.views: self._state["whiteboardVisible"]=False
                self._state["sequence"]+=1
        except UIError as error:
            return {"ok":False,"error":str(error)}
        self.publish()
        return {"ok":True,**result,"whiteboardVisible":self._state["whiteboardVisible"]}

    def ui_snapshot(self):
        with self._lock:
            return {"ok":True,"whiteboardVisible":self._state["whiteboardVisible"],
                    "summoned":self._state["summoned"],"voiceActive":self._state["voiceActive"],"face":self._state["state"],
                    "items":copy.deepcopy(self._state["items"])}
