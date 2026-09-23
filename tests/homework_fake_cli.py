#!/usr/bin/env python3
"""Deterministic subprocess fixture; never invokes a model."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

if '--version' in sys.argv:
    print('fixture 1.0');sys.exit()
if '--help' in sys.argv:
    print('--json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --output-format --include-partial-messages --resume --session-id --dangerously-skip-permissions');sys.exit()
provider='codex' if 'exec' in sys.argv else 'claude'
with Path('fixture-invocations.jsonl').open('a') as handle:
    handle.write(json.dumps({'argv':sys.argv,'cwd':str(Path.cwd()),'agents':Path('AGENTS.md').read_text() if Path('AGENTS.md').exists() else None})+'\n')
prompt=sys.stdin.read().split('用户任务：\n')[-1]
if provider=='codex':
    native=sys.argv[sys.argv.index('resume')+1] if 'resume' in sys.argv else str(uuid.uuid4())
else:
    key='--resume' if '--resume' in sys.argv else '--session-id'
    native=sys.argv[sys.argv.index(key)+1]

def emit(row):
    text=json.dumps(row,ensure_ascii=False)+'\n'
    # Exercise JSON spanning multiple pipe writes, including unicode bytes.
    data=text.encode();sys.stdout.buffer.write(data[:7]);sys.stdout.buffer.flush()
    sys.stdout.buffer.write(data[7:]);sys.stdout.buffer.flush()

emit({'type':'thread.started','thread_id':native} if provider=='codex' else {'type':'system','subtype':'init','session_id':native})
if 'BAD_JSON' in prompt:print('{bad-json',flush=True)
if 'CHILD' in prompt:
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
    Path('child.pid').write_text(str(child.pid))
if 'SLEEP' in prompt:time.sleep(2)
if 'WAIT' in prompt:time.sleep(120)
with Path('result.txt').open('a') as handle:handle.write(prompt+'\n')
if 'NO_TERMINAL' in prompt:sys.exit(0)
if 'FAIL' in prompt:
    emit({'type':'turn.failed','error':'fixture failure'} if provider=='codex' else {'type':'result','subtype':'error_during_execution','is_error':True,'errors':['fixture failure'],'session_id':native});sys.exit(1)
if provider=='codex':
    emit({'type':'item.completed','item':{'type':'agent_message','text':'完成 '+prompt}})
    emit({'type':'turn.completed','usage':{'input_tokens':10}})
else:
    emit({'type':'stream_event','event':{'delta':{'type':'text_delta','text':'完成 '}}})
    emit({'type':'assistant','message':{'content':[{'type':'text','text':'完成 '+prompt}]}})
    emit({'type':'result','subtype':'success','result':'完成 '+prompt,'session_id':native,'is_error':False})
