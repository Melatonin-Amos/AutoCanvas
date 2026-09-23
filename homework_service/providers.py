"""CLI-specific argument construction and stream normalization only."""
import asyncio
import json
import shutil


async def probe(settings, provider):
    executable=shutil.which(getattr(settings,provider))
    if not executable:
        return {'provider':provider,'available':False,'error':'CLI 未安装或不在 PATH 中'}
    async def output(*args):
        proc=await asyncio.create_subprocess_exec(executable,*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        try:
            data=await asyncio.wait_for(proc.communicate(),10)
        except asyncio.TimeoutError:
            proc.kill(); await proc.communicate(); raise
        return data[0].decode(errors='replace')
    try:
        version=(await output('--version')).strip()
        help_text=await output(*(['exec','--help'] if provider=='codex' else ['--help']))
        required=['--json','--skip-git-repo-check','--dangerously-bypass-approvals-and-sandbox'] if provider=='codex' else ['--output-format','--include-partial-messages','--resume','--session-id','--dangerously-skip-permissions']
        missing=[flag for flag in required if flag not in help_text]
        if provider=='codex':
            resume_help=await output('exec','resume','--help')
            missing += [flag for flag in required if flag not in resume_help]
        return {'provider':provider,'available':not missing,'executable':executable,'version':version,
                'error':'缺少参数：'+', '.join(missing) if missing else ''}
    except Exception as error:
        return {'provider':provider,'available':False,'error':f'CLI 检查失败：{type(error).__name__}'}


def command(executable, session):
    model=session.get('model')
    native=session.get('native_id')
    if session['provider']=='claude':
        args=[executable,'-p','--output-format','stream-json','--verbose','--include-partial-messages','--dangerously-skip-permissions']
        args += ['--resume' if session.get('started') else '--session-id',native]
    else:
        args=[executable]
        if session.get('working_directory'):args += ['--cd',session['working_directory']]
        args += ['exec']
        if session.get('started'):
            if not native:
                raise ValueError('会话未返回可恢复的 ID，请新建会话')
            args += ['resume',native]
        args += ['--json','--skip-git-repo-check','--dangerously-bypass-approvals-and-sandbox']
        args += ['-']
    if model:
        args += ['--model',model]
    return args


class Stream:
    def __init__(self,provider):
        self.provider=provider
        self.native_id=None
        self.terminal=False
        self.failed=False
        self.result=''
        self.error=''

    def parse(self,line):
        row=json.loads(line)
        if not isinstance(row,dict):
            raise ValueError('Expected an event object')
        kind=row.get('type','unknown'); events=[]
        if self.provider=='codex':
            if kind=='thread.started':
                self.native_id=row.get('thread_id')
            elif kind=='turn.completed':
                self.terminal=True
                events.append(('usage',row.get('usage',{})))
            elif kind in ('turn.failed','error'):
                self.failed=True; self.error=str(row.get('error',row.get('message','Agent 执行失败')))
                if kind=='turn.failed': self.terminal=True
                events.append(('error',{'message':self.error}))
            elif kind.startswith('item.'):
                item=row.get('item',{})
                if item.get('type')=='agent_message' and kind=='item.completed':
                    self.result=item.get('text','');events.append(('message',{'text':self.result}))
                else:
                    events.append(('activity',{'phase':kind,**item}))
        else:
            if kind=='system' and row.get('subtype')=='init':
                self.native_id=row.get('session_id')
            elif kind=='stream_event':
                delta=row.get('event',{}).get('delta',{})
                if delta.get('type')=='text_delta':
                    events.append(('delta',{'text':delta.get('text','')}))
            elif kind=='assistant':
                for item in row.get('message',{}).get('content',[]):
                    if item.get('type')=='text':
                        events.append(('message',{'text':item.get('text',''),'parent_tool_use_id':row.get('parent_tool_use_id')}))
                    else: events.append(('activity',item))
            elif kind=='user':
                events.append(('activity',{'type':'tool_result','message':row.get('message',{}),'parent_tool_use_id':row.get('parent_tool_use_id')}))
            elif kind=='result':
                self.native_id=row.get('session_id') or self.native_id
                self.terminal=True;self.failed=bool(row.get('is_error')) or row.get('subtype')!='success'
                self.result=row.get('result','')
                if self.failed:self.error=str(row.get('errors') or self.result or row.get('subtype'))
                events.append(('usage',{'usage':row.get('usage',{}),'cost_usd':row.get('total_cost_usd')}))
        return events
