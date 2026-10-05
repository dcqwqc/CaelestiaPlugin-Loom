#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from tabby.ipc import send_command

cmd=sys.argv[1] if len(sys.argv)>1 else 'status'
payload={'command':cmd}
if cmd=='send-text': payload['text']=' '.join(sys.argv[2:])
elif cmd in {'work-open','work-voice','work-delete-user','work-complete'} and len(sys.argv)>2: payload['task_id']=sys.argv[2]
elif cmd=='work-pin-current' and len(sys.argv)>2: payload['title']=' '.join(sys.argv[2:])
result=send_command(payload,timeout=5)
print(json.dumps(result,ensure_ascii=False))
raise SystemExit(0 if result.get('ok') else 1)
