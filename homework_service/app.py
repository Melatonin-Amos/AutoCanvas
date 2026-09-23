import asyncio
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from .store import Store
from .source import Source
from .runner import Runner
from .paths import workspace_path, slug, overlap

PREFIX='/api/homework'
STATUSES={'todo','doing','review','done','archived'}


def create_app(settings):
    store=Store(settings.state/'state.sqlite3');source=Source(settings,store);runner=Runner(settings,store,source)
    @web.middleware
    async def errors(request,handler):
        try:
            origin=request.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=request.host:
                raise web.HTTPForbidden(text='Cross-origin requests are not allowed')
            return await handler(request)
        except (KeyError,FileNotFoundError):return web.json_response({'error':'资源不存在'},status=404)
        except (ValueError,TypeError) as exc:return web.json_response({'error':str(exc)},status=400)
        except web.HTTPException:raise
        except Exception as exc:return web.json_response({'error':f'服务异常：{type(exc).__name__}'},status=500)
    app=web.Application(middlewares=[errors],client_max_size=1024*1024)
    app['store']=store;app['runner']=runner;app['source']=source

    def required(kind,key):
        result=store.get(kind,key)
        if result is None:raise KeyError(key)
        return result

    def directory_idle(relative):
        path=workspace_path(settings.workspace,relative)
        for run in store.runs():
            if run['status']=='running':
                session=required('sessions',run['session_id'])
                if overlap(path,workspace_path(settings.workspace,session['directory'])):
                    raise ValueError('该目录正在执行，请先停止当前任务')

    async def health(request):
        return web.json_response({'status':'ok','workspace':str(settings.workspace),'default_provider':settings.default_provider,
            'concurrency':settings.concurrency,'providers':list(runner.providers.values()),'sync':store.get('meta','sync',{})})

    async def providers(request):
        return web.json_response(await runner.check_providers())

    async def synchronize(request):return web.json_response(await source.sync())

    async def courses(request):
        if request.method=='PATCH':
            key=request.match_info['id'];row=required('courses',key);body=await request.json()
            directory=body['directory'];workspace_path(settings.workspace,directory)
            row['directory']=directory;store.put('courses',key,row)
            return web.json_response(row)
        return web.json_response(store.list('courses'))

    async def assignments(request):
        if request.method=='GET':
            if 'id' in request.match_info:
                row=required('assignments',request.match_info['id'])
                return web.json_response({**row,'materials_changed':row.get('context_revision','')!=source.material_revision(row)})
            rows=[]
            sessions=store.list('sessions');runs=store.runs()
            for row in store.list('assignments'):
                ids={s['id'] for s in sessions if s['assignment_id']==row['id']}
                related=[r for r in runs if r['session_id'] in ids]
                live=[r for r in related if r['status'] in ('running','queued')]
                rows.append({**row,'agent_status':'running' if any(r['status']=='running' for r in live) else 'queued' if live else related[-1]['status'] if related else 'idle',
                             'materials_changed':row.get('context_revision','')!=source.material_revision(row)})
            return web.json_response(rows)
        body=await request.json()
        if request.method=='POST':
            title=str(body.get('title','')).strip()
            if not title:raise ValueError('请输入作业名称')
            cid=str(body.get('course_id') or 'manual')
            if cid=='manual' and not store.get('courses',cid):
                store.put('courses',cid,{'id':cid,'name':'手工作业','directory':'Manual','source':'manual'})
            course=required('courses',cid);key='manual:'+uuid.uuid4().hex
            directory=body.get('directory') or course['directory']+'/'+slug(title)+'-'+key[-6:]
            workspace_path(settings.workspace,directory)
            row={'id':key,'title':title,'course_id':cid,'source':'manual','source_id':key,'description':body.get('description',''),
                 'due_at':body.get('due_at') or None,'notes':body.get('notes',''),'directory':directory,'status':'todo','materials':[],
                 'attachments':[],'source_revision':uuid.uuid4().hex,'context_revision':'','created':time.time(),'updated':time.time()}
        else:
            key=request.match_info['id'];row=required('assignments',key)
            for field in body:
                if field not in ('title','description','due_at','notes','directory','status','materials'):raise ValueError('不支持修改该字段')
                if row['source']=='canvas' and field in ('title','description','due_at'):raise ValueError('Canvas 来源字段由同步维护')
            if 'status' in body and body['status'] not in STATUSES:raise ValueError('无效作业状态')
            if 'materials' in body and (not isinstance(body['materials'],list) or not all(isinstance(p,str) for p in body['materials'])):raise ValueError('无效资料列表')
            if body.get('directory',row['directory'])!=row['directory']:
                if any(s['assignment_id']==key for s in store.list('sessions')):raise ValueError('已有会话的作业目录不能修改，请新建作业绑定其他目录')
                workspace_path(settings.workspace,body['directory'])
                row['context_revision']='';row.pop('context_path',None)
            source_changed=any(body[field]!=row.get(field) for field in ('title','description','due_at') if field in body)
            row.update(body);row['updated']=time.time()
            if row['source']=='manual' and source_changed:row['source_revision']=uuid.uuid4().hex
        store.put('assignments',key,row)
        return web.json_response(row)

    async def materials(request):
        cid=request.query.get('course_id','')
        return web.json_response({'files':[r for r in store.get('meta','files',[]) if r['path'].startswith('outputs/'+cid+'/') and not any(p in ('pending','previous') for p in Path(r['path']).parts)],
                                  'lectures':[r for r in store.get('meta','lectures',[]) if r['course_id']==cid]})

    async def context(request):
        row=required('assignments',request.match_info['id']);directory_idle(row['directory'])
        # Serialize preparation with queued execution using the session pause gate.
        if any(r['status']=='queued' for r in store.runs() if required('sessions',r['session_id'])['assignment_id']==row['id']):
            raise ValueError('有消息等待执行，请撤回队列消息后刷新资料')
        if any(overlap(workspace_path(settings.workspace,row['directory']),workspace_path(settings.workspace,p)) for p in runner.preparing):
            raise ValueError('该目录正在准备资料')
        runner.preparing.add(row['directory'])
        try:
            target=await source.prepare(row)
            return web.json_response({'path':str(target.relative_to(settings.workspace))})
        finally:runner.preparing.discard(row['directory'])

    async def sessions(request):
        if request.method=='GET':
            return web.json_response([r for r in store.list('sessions') if not request.query.get('assignment_id') or r['assignment_id']==request.query['assignment_id']])
        body=await request.json();assignment=required('assignments',body['assignment_id'])
        provider=body.get('provider',settings.default_provider)
        if provider not in ('claude','codex'):raise ValueError('不支持的 agent')
        directory=body.get('directory') or assignment['directory']
        if directory!=assignment['directory']:raise ValueError('接入会话工作目录必须与作业绑定目录一致')
        workspace_path(settings.workspace,directory)
        native=body.get('native_id')
        if native:
            try:native=str(uuid.UUID(native))
            except (ValueError,AttributeError):raise ValueError('请输入原生 session UUID') from None
            if any(s['provider']==provider and s.get('native_id')==native for s in store.list('sessions')):raise ValueError('该原生会话已接入')
        key=uuid.uuid4().hex
        session={'id':key,'assignment_id':assignment['id'],'provider':provider,'directory':directory,
                 'native_id':native or (str(uuid.uuid4()) if provider=='claude' else None),'started':bool(native),
                 'imported':bool(native),'paused':False,'model':str(body.get('model') or getattr(settings,provider+'_model')),
                 'name':str(body.get('name') or provider+' · '+time.strftime('%m/%d %H:%M')),'created':time.time()}
        return web.json_response(store.put('sessions',key,session),status=201)

    async def turns(request):
        sid=request.match_info['id'];session=required('sessions',sid)
        if request.method=='GET':return web.json_response(store.runs(sid))
        body=await request.json()
        assignment=required('assignments',session['assignment_id'])
        if assignment['status']=='archived':raise ValueError('请先取消归档')
        result=store.enqueue(sid,str(body.get('request_id','')),body.get('prompt'))
        if result['status']=='queued':
            assignment['status']='doing';store.put('assignments',assignment['id'],assignment)
        return web.json_response(result,status=202)

    async def resume(request):
        sid=request.match_info['id'];session=required('sessions',sid)
        if any(r['status']=='running' for r in store.runs(sid)):raise ValueError('会话仍在运行')
        session['paused']=False;store.put('sessions',sid,session)
        return web.json_response(session)

    async def stop(request):
        await runner.stop(request.match_info['id'])
        return web.json_response(store.run(request.match_info['id']))

    async def events(request):
        sid=request.match_info['id'];required('sessions',sid)
        after=max(0,int(request.query.get('after','0')),int(request.headers.get('Last-Event-ID','0')))
        if request.query.get('stream')!='1':return web.json_response(store.events(sid,after))
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no'})
        await response.prepare(request)
        try:
            while not runner.closing:
                batch=store.events(sid,after)
                for row in batch:
                    await response.write(f"id: {row['seq']}\ndata: {json.dumps(row,ensure_ascii=False)}\n\n".encode());after=row['seq']
                if not batch:
                    await response.write(b': keepalive\n\n');await asyncio.sleep(1)
        except (ConnectionResetError,asyncio.CancelledError):pass
        return response

    async def directories(request):
        relative=request.query.get('path','')
        folder=workspace_path(settings.workspace,relative) if relative else settings.workspace
        if not folder.is_dir():raise ValueError('目录不存在')
        return web.json_response({'path':relative,'directories':[{'name':p.name,'path':str(p.relative_to(settings.workspace))} for p in sorted(folder.iterdir()) if p.is_dir() and not p.is_symlink() and not p.name.startswith('.') and p.name!='node_modules']})

    async def files(request):
        assignment=required('assignments',request.match_info['id'])
        folder=workspace_path(settings.workspace,assignment['directory'])
        relative=request.query.get('path','')
        path=(folder/relative).resolve()
        if not path.is_relative_to(folder) or '.homework-manager' in path.parts or Path(relative).is_absolute():raise ValueError('无效文件路径')
        if request.match_info.get('action')=='file':
            if not path.is_file():raise FileNotFoundError()
            response=web.FileResponse(path)
            if request.query.get('download')=='1' or path.suffix.lower() not in ('.txt','.md','.json','.py','.tex','.csv','.pdf','.png','.jpg','.jpeg','.webp'):
                response.headers['Content-Disposition']='attachment'
            if path.suffix.lower() in ('.md','.py','.tex','.json','.csv'):response.content_type='text/plain'
            response.headers['X-Content-Type-Options']='nosniff';response.headers['Cache-Control']='no-store'
            return response
        if not path.exists():return web.json_response([])
        if not path.is_dir():raise ValueError('请选择目录')
        rows=[]
        for entry in sorted(path.iterdir(),key=lambda p:(not p.is_dir(),p.name)):
            if entry.is_symlink() or entry.name in ('.git','node_modules','.venv','.homework-manager'):continue
            rows.append({'name':entry.name,'path':str(entry.relative_to(folder)),'directory':entry.is_dir(),'size':entry.stat().st_size if entry.is_file() else 0})
        return web.json_response(rows)

    app.router.add_get(PREFIX+'/health',health)
    app.router.add_post(PREFIX+'/providers/check',providers)
    app.router.add_post(PREFIX+'/sync',synchronize)
    app.router.add_get(PREFIX+'/courses',courses)
    app.router.add_patch(PREFIX+'/courses/{id}',courses)
    app.router.add_get(PREFIX+'/assignments',assignments)
    app.router.add_post(PREFIX+'/assignments',assignments)
    app.router.add_get(PREFIX+'/assignments/{id}',assignments)
    app.router.add_patch(PREFIX+'/assignments/{id}',assignments)
    app.router.add_post(PREFIX+'/assignments/{id}/context',context)
    app.router.add_get(PREFIX+'/materials',materials)
    app.router.add_get(PREFIX+'/sessions',sessions)
    app.router.add_post(PREFIX+'/sessions',sessions)
    app.router.add_get(PREFIX+'/sessions/{id}/turns',turns)
    app.router.add_post(PREFIX+'/sessions/{id}/turns',turns)
    app.router.add_post(PREFIX+'/sessions/{id}/resume',resume)
    app.router.add_post(PREFIX+'/turns/{id}/stop',stop)
    app.router.add_get(PREFIX+'/sessions/{id}/events',events)
    app.router.add_get(PREFIX+'/directories',directories)
    app.router.add_get(PREFIX+'/assignments/{id}/{action:files|file}',files)

    async def lifetime(app):
        settings.workspace.mkdir(parents=True,exist_ok=True)
        await runner.check_providers()
        worker=asyncio.create_task(runner.loop())
        async def sync_loop():
            while True:
                await source.sync();await asyncio.sleep(settings.sync_seconds)
        sync_task=asyncio.create_task(sync_loop())
        yield
        await runner.close()
        worker.cancel();sync_task.cancel()
        await asyncio.gather(worker,sync_task,return_exceptions=True)
    async def shutdown(app):
        await runner.close()
    app.on_shutdown.append(shutdown)
    app.cleanup_ctx.append(lifetime)
    return app
