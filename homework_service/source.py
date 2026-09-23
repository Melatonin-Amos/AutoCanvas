"""AutoCanvas HTTP connector and immutable material snapshots."""
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import quote
import aiohttp
from .paths import slug, workspace_path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]


class Source:
    def __init__(self,settings,store):
        self.settings,self.store=settings,store
        self.lock=asyncio.Lock()

    async def get(self, client, path):
        async with client.get(self.settings.autocanvas_url.rstrip('/')+path,allow_redirects=False) as response:
            if response.status!=200:
                raise ValueError(f'AutoCanvas 返回 {response.status}')
            return await response.json()

    async def sync(self):
        async with self.lock:
            previous=self.store.get('meta','sync',{})
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
                    courses,assignments,files,lectures=await asyncio.gather(*(self.get(client,p) for p in ['/api/courses','/api/assignments','/api/files','/api/lectures']))
                for row in courses:
                    key=str(row['id']);old=self.store.get('courses',key,{})
                    self.store.put('courses',key,{**old,'id':key,'name':row['name'],
                        'directory':old.get('directory',slug(row['name'])+'-'+key),'source':'canvas'})
                for row in assignments:
                    cid,aid=str(row['course_id']),str(row['id']);key=f'canvas:{cid}:{aid}'
                    old=self.store.get('assignments',key,{})
                    course=self.store.get('courses',cid,{'directory':'course-'+cid})
                    self.store.put('assignments',key,{
                        **old,'id':key,'course_id':cid,'source':'canvas','source_id':aid,
                        'title':row['name'],'description':row.get('description') or '',
                        'due_at':row.get('due_at'),'url':row.get('html_url'),
                        'attachments':row.get('attachments',[]),'source_revision':fingerprint(row),
                        'status':old.get('status','todo'),'notes':old.get('notes',''),
                        'directory':old.get('directory',course['directory']+'/'+slug(row['name'])+'-'+aid),
                        'materials':old.get('materials',[]),'context_revision':old.get('context_revision',''),
                        'created':old.get('created',time.time()),'updated':time.time()})
                self.store.put('meta','files',files)
                self.store.put('meta','lectures',lectures)
                status={'last_success':time.time(),'last_attempt':time.time(),'error':''}
            except Exception as error:
                status={**previous,'last_attempt':time.time(),'error':f'同步失败：{type(error).__name__}: {error}'}
            self.store.put('meta','sync',status)
            return status

    def material_revision(self, assignment):
        selected=set(assignment.get('materials',[]))
        files=[{k:row[k] for k in ('path','size','modified') if k in row} for row in self.store.get('meta','files',[]) if row['path'] in selected]
        return fingerprint({'source':assignment.get('source_revision'), 'notes':assignment.get('notes',''),
                            'materials':assignment.get('materials',[]),'file_versions':sorted(files,key=lambda r:r['path'])})

    async def prepare(self, assignment):
        root=workspace_path(self.settings.workspace,assignment['directory'])
        root.mkdir(parents=True,exist_ok=True)
        context_root=root/'.homework-context'
        if context_root.is_symlink():raise ValueError('上下文目录不能为符号链接')
        revision=self.material_revision(assignment)
        target=context_root/revision
        if target.is_symlink():raise ValueError('上下文目录不能为符号链接')
        if not (target/'READY').is_file():
            # Unique staging names prevent partial downloads from appearing as complete snapshots.
            pending=context_root/('pending-'+uuid.uuid4().hex)
            pending.mkdir(parents=True)
            try:
                files=self.store.get('meta','files',[])
                available={r['path']:r for r in files}
                paths=[]
                for relative in assignment.get('materials',[]):
                    if relative not in available or not relative.startswith('outputs/'+assignment['course_id']+'/'):
                        raise ValueError('所选课堂资料不在当前课程的资料列表中')
                    paths.append(relative)
                if assignment['source']=='canvas':
                    prefix=f"assignments/{assignment['course_id']}/{assignment['source_id']}/"
                    for item in assignment.get('attachments',[]):
                        if item.get('status')!='succeeded':
                            raise ValueError('部分作业附件未同步成功，请先在 AutoCanvas 中重试同步')
                        name=Path(item['path']).name
                        relative=prefix+name
                        if relative not in available:raise ValueError('作业附件尚未出现在来源文件列表中')
                        paths.append(relative)
                entries=[]
                if paths:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as client:
                        for index,relative in enumerate(dict.fromkeys(paths)):
                            name=f'{index+1:03}-'+slug(Path(relative).name)
                            async with client.get(self.settings.autocanvas_url.rstrip('/')+'/api/file?path='+quote(relative,safe=''),allow_redirects=False) as response:
                                if response.status!=200:raise ValueError('资料下载失败，请刷新来源后重试')
                                with (pending/name).open('wb') as handle:
                                    async for chunk in response.content.iter_chunked(1024*1024):handle.write(chunk)
                            entries.append({'source':relative,'file':name})
                (pending/'assignment.md').write_text(f"# {assignment['title']}\n\n{assignment.get('description','')}\n\n## 用户备注\n{assignment.get('notes','')}\n",encoding='utf-8')
                (pending/'materials.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
                (pending/'READY').write_text(revision)
                if target.exists():
                    raise ValueError('快照目录已存在但不完整，请检查上下文目录')
                pending.rename(target)
            except BaseException:
                import shutil
                shutil.rmtree(pending,ignore_errors=True)
                raise
        current=self.store.get('assignments',assignment['id'])
        # Do not overwrite notes/source edits that arrived during download.
        current['context_revision']=revision
        current['context_path']=str(target.relative_to(self.settings.workspace))
        self.store.put('assignments',current['id'],current)
        return target
