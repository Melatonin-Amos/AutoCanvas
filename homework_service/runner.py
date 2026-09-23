"""Own process lifetime, queue serialization and durable execution events."""
import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path
import psutil
from .paths import workspace_path, overlap
from .providers import Stream, command, probe


def snapshot(folder):
    result={}
    for base,dirs,files in os.walk(folder,followlinks=False):
        dirs[:]=[d for d in dirs if d not in ('.git','.homework-context','node_modules','.venv','__pycache__') and not (Path(base)/d).is_symlink()]
        for name in files:
            path=Path(base)/name
            try:
                if path.is_symlink():continue
                stat=path.stat();result[str(path.relative_to(folder))]=[stat.st_size,stat.st_mtime_ns]
            except OSError:pass
    return result


async def terminate(proc, *, interrupt=True):
    try:
        parent=psutil.Process(proc.pid)
        children=parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children=[]
    try:
        if os.name=='nt':proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:os.killpg(proc.pid,signal.SIGINT if interrupt else signal.SIGTERM)
    except (ProcessLookupError,PermissionError,OSError):pass
    try:
        await asyncio.wait_for(proc.wait(),5)
    except asyncio.TimeoutError:
        try:
            if os.name=='nt':proc.kill()
            else:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        for child in children:
            try:child.kill()
            except (psutil.NoSuchProcess,psutil.AccessDenied):pass
        await proc.wait()
    for child in children:
        try:child.kill()
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass


class Runner:
    def __init__(self,settings,store,source, *, standalone=False):
        self.settings,self.store,self.source=settings,store,source
        self.standalone=standalone
        self.log_root=settings.state/'logs'
        self.tasks={};self.processes={};self.stops={};self.closing=False
        self.providers={};self.preparing=set()

    def directory(self, session):
        return self.settings.workspace if self.standalone else workspace_path(self.settings.workspace,session['directory'])

    async def check_providers(self):
        rows=await asyncio.gather(*(probe(self.settings,p) for p in (('codex',) if self.standalone else ('claude','codex'))))
        self.providers={r['provider']:r for r in rows}
        return rows

    def pause(self,sid):
        session=self.store.get('sessions',sid)
        session['paused']=True;self.store.put('sessions',sid,session)

    async def recover(self):
        # Only terminate processes bearing our per-run marker, never a reused PID.
        for run in self.store.runs():
            if run['status']!='running':continue
            for process in psutil.process_iter(['pid','create_time']):
                try:
                    if process.environ().get('HOMEWORK_RUN_ID')==run['id']:
                        process.kill()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            self.store.update_run(run['id'],status='interrupted',error='服务中断，需手动恢复')
            self.pause(run['session_id'])
            self.store.event(run,'state',{'status':'interrupted'})

    async def loop(self):
        await self.recover()
        while not self.closing:
            occupied=list(self.preparing)
            for rid in self.tasks:
                run=self.store.run(rid);session=self.store.get('sessions',run['session_id'])
                occupied.append(session['directory'])
            for run in self.store.runs():
                if len(self.tasks)>=self.settings.concurrency:break
                if run['status']!='queued':continue
                session=self.store.get('sessions',run['session_id'])
                if session['paused']:continue
                directory=self.directory(session)
                if any(overlap(directory,self.settings.workspace if self.standalone else workspace_path(self.settings.workspace,p)) for p in occupied):continue
                # No await between claim and task registration.
                self.store.update_run(run['id'],status='running')
                task=asyncio.create_task(self.execute(run),name='homework-'+run['id'])
                self.tasks[run['id']]=task
                task.add_done_callback(lambda t,rid=run['id']:self.tasks.pop(rid,None))
                occupied.append(session['directory'])
            await asyncio.sleep(.25)

    async def stop(self,run_id, reason='cancelled'):
        run=self.store.run(run_id)
        if not run:raise KeyError(run_id)
        if run['status'] not in ('queued','running'):return
        if run['status']=='queued':
            self.store.update_run(run_id,status='cancelled');self.store.event(run,'state',{'status':'cancelled'});return
        self.pause(run['session_id'])
        self.stops[run_id]=reason
        task=self.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            if self.store.run(run_id)['status']=='running':
                self.store.update_run(run_id,status=reason,error='用户停止' if reason=='cancelled' else '执行中断')

    async def close(self):
        self.closing=True
        await asyncio.gather(*(self.stop(rid,'interrupted') for rid in list(self.tasks)),return_exceptions=True)

    async def execute(self,run):
        sid=run['session_id'];session=self.store.get('sessions',sid)
        assignment=None if self.standalone else self.store.get('assignments',session['assignment_id'])
        cwd=self.directory(session)
        if self.standalone:session={**session,'working_directory':str(cwd)}
        stream=Stream(session['provider']);proc=None;readers=[];before={};status='failed';error='';children={};tracker=None
        self.store.event(run,'state',{'status':'running'})
        try:
            capability=self.providers.get(session['provider'])
            if not capability or not capability.get('available'):
                capability=await probe(self.settings,session['provider']);self.providers[session['provider']]=capability
            if not capability['available']:raise ValueError(capability['error'])
            if self.standalone:
                if not cwd.is_dir():raise ValueError('配置的会话目录不存在，请先设置已有目录')
                prompt=run['prompt']
            else:
                if not assignment.get('context_revision'):
                    context=await self.source.prepare(assignment)
                else:
                    context=workspace_path(self.settings.workspace,assignment['context_path'])
                    if not (context/'READY').exists():raise ValueError('上下文快照不存在，请刷新资料')
                cwd.mkdir(parents=True,exist_ok=True)
                before=await asyncio.to_thread(snapshot,cwd)
                prompt=(f'你正在管理的作业目录：{cwd}\n题目与附件快照：{context}\n'
                        '请读取 assignment.md 和 materials.json，并遵守工作区已有的 AGENTS.md / CLAUDE.md。'
                        '旧说明中的 AutoCanvas 资料路径可能已过时，应以本次快照为准。'
                        '本轮只在作业工作区完成任务，不提交 Canvas 作业，也不发送 iMessage、邮件或其他外部消息。'
                        '需要用户补充信息时，在最终回复中明确问题。\n\n用户任务：\n'+run['prompt'])
            env={**os.environ,'HOMEWORK_RUN_ID':run['id']}
            env.pop('CLAUDECODE',None)
            options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
            proc=await asyncio.create_subprocess_exec(*command(capability['executable'],session),cwd=cwd,env=env,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                limit=4*1024*1024,**options)
            self.processes[run['id']]=proc
            self.store.update_run(run['id'],pid=proc.pid,process_created=psutil.Process(proc.pid).create_time())
            logs=self.log_root;logs.mkdir(parents=True,exist_ok=True)
            async def read(pipe,name):
                with (logs/(run['id']+'.'+name+'.jsonl')).open('ab') as raw:
                    while line:=await pipe.readline():
                        raw.write(line);raw.flush()
                        text=line.decode('utf-8',errors='replace').strip()
                        if not text:continue
                        if name=='stderr':
                            self.store.event(run,'diagnostic',{'text':text[:16000]});continue
                        try:
                            for kind,body in stream.parse(text):self.store.event(run,kind,body)
                            if stream.native_id:
                                current=self.store.get('sessions',sid)
                                if current.get('started') and current.get('native_id')!=stream.native_id:
                                    raise RuntimeError('CLI 返回了不匹配的 session ID')
                                current.update(native_id=stream.native_id,started=True)
                                self.store.put('sessions',sid,current)
                        except (ValueError,TypeError,AttributeError) as parse_error:
                            self.store.event(run,'diagnostic',{'text':text[:16000],'parse_error':type(parse_error).__name__})
            async def track_children():
                while True:
                    try:
                        for child in psutil.Process(proc.pid).children(recursive=True):children[child.pid]=child
                    except (psutil.NoSuchProcess,psutil.AccessDenied):return
                    await asyncio.sleep(.2)
            tracker=asyncio.create_task(track_children())
            readers=[asyncio.create_task(read(proc.stdout,'stdout')),asyncio.create_task(read(proc.stderr,'stderr'))]
            proc.stdin.write(prompt.encode());await proc.stdin.drain();proc.stdin.close()
            # Wait for the CLI, not inherited pipe handles held by background children.
            # Process.wait can itself wait for pipe EOF, so observe returncode as well.
            while proc.returncode is None:
                for reader in readers:
                    if reader.done() and not reader.cancelled() and reader.exception():raise reader.exception()
                await asyncio.sleep(.05)
            code=proc.returncode
            if os.name!='nt':
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
            for child in children.values():
                try:child.kill()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            await asyncio.wait_for(asyncio.gather(*readers),5)
            if not stream.native_id:
                raise RuntimeError('CLI 未返回会话 ID，无法安全续聊')
            if code!=0 or not stream.terminal or stream.failed:
                raise RuntimeError(stream.error or f'CLI 退出码 {code}，'+('未收到完成事件' if not stream.terminal else '执行未成功'))
            status='succeeded'
        except asyncio.CancelledError:
            status=self.stops.get(run['id'],'interrupted');error='用户停止' if status=='cancelled' else '服务中断'
        except Exception as exc:
            error=str(exc)
        finally:
            if proc:
                # Also clean background descendants when the root has already exited.
                if proc.returncode is None:await terminate(proc)
                elif os.name!='nt':
                    try:os.killpg(proc.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
            if tracker:
                tracker.cancel();await asyncio.gather(tracker,return_exceptions=True)
            for child in children.values():
                try:child.kill()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            for reader in readers:
                if not reader.done():reader.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            self.processes.pop(run['id'],None);self.stops.pop(run['id'],None)
            try:
                after={} if self.standalone else await asyncio.to_thread(snapshot,cwd)
                changes=[{'path':p,'change':'created' if p not in before else 'modified'} for p,v in after.items() if before.get(p)!=v]
                changes += [{'path':p,'change':'deleted'} for p in before if p not in after]
                if not self.standalone and (before or proc):self.store.event(run,'files',{'changes':changes})
            except OSError:pass
            self.store.update_run(run['id'],status=status,error=error,result=stream.result)
            if status!='succeeded':self.pause(sid)
            if not self.standalone:
                current=self.store.get('assignments',assignment['id'])
                if current['status'] not in ('done','archived'):
                    current['status']='review' if status=='succeeded' else 'doing'
                    self.store.put('assignments',current['id'],current)
            self.store.event(run,'state',{'status':status,'error':error,'result':stream.result})
