import asyncio
import ast
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import psutil
from aiohttp.test_utils import TestClient,TestServer
from homework_service.config import Settings
from homework_service.app import create_app
from homework_service.providers import command,Stream
from homework_service.paths import workspace_path
from homework_service.source import Source
from homework_service.store import Store


class Architecture(unittest.TestCase):
    def test_service_has_no_autocanvas_imports(self):
        for path in Path('homework_service').glob('*.py'):
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertFalse((node.module or '').startswith('autocanvas'))
                if isinstance(node,ast.Import):self.assertFalse(any(n.name.startswith('autocanvas') for n in node.names))

    def test_commands_pin_sessions_and_use_stdin(self):
        for provider in ('claude','codex'):
            session={'provider':provider,'native_id':str(uuid.uuid4()),'started':True,'model':'test-model'}
            args=command(provider,session)
            self.assertIn(session['native_id'],args)
            self.assertNotIn('--last',args)
            self.assertNotIn('--continue',args)
            self.assertIn('test-model',args)
            if provider=='codex':self.assertIn('-',args)

    def test_paths_block_escape_and_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('../x','/tmp/x','.homework-manager/state.sqlite3','.'):
                with self.assertRaises(ValueError):workspace_path(root,name)
            (root/'outside').symlink_to('/tmp',target_is_directory=True)
            with self.assertRaises(ValueError):workspace_path(root,'outside/x')


class HomeworkAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        fake=str(Path('tests/homework_fake_cli.py').resolve())
        self.settings=Settings(workspace=self.root,claude=fake,codex=fake,autocanvas_url='http://127.0.0.1:1',sync_seconds=300)
        self.app=create_app(self.settings);self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.store=self.app['store']

    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()

    async def post(self,path,body=None,expected=200):
        response=await self.client.post('/api/homework'+path,json=body or {})
        data=await response.json();self.assertEqual(response.status,expected,data);return data

    async def setup_session(self,provider='claude',directory='Course/Task'):
        a=await self.post('/assignments',{'title':'Test','directory':directory})
        s=await self.post('/sessions',{'assignment_id':a['id'],'provider':provider},201)
        return a,s

    async def send(self,s,prompt,key=None):
        return await self.post('/sessions/'+s['id']+'/turns',{'prompt':prompt,'request_id':key or uuid.uuid4().hex},202)

    async def wait(self,run,status=None):
        end=time.monotonic()+10
        while time.monotonic()<end:
            row=self.store.run(run['id'])
            if row['status']==status if status else row['status'] not in ('running','queued'):return row
            await asyncio.sleep(.05)
        self.fail('Timed out waiting for '+str(self.store.run(run['id'])))

    async def test_both_providers_multi_turn_resume_and_events(self):
        for provider in ('claude','codex'):
            a,s=await self.setup_session(provider,'Course/'+provider)
            first=await self.wait(await self.send(s,'FIRST BAD_JSON'))
            self.assertEqual(first['status'],'succeeded')
            native=self.store.get('sessions',s['id'])['native_id']
            second=await self.wait(await self.send(s,'SECOND'))
            self.assertEqual(second['status'],'succeeded')
            self.assertEqual(native,self.store.get('sessions',s['id'])['native_id'])
            self.assertEqual((self.root/a['directory']/'result.txt').read_text(),'FIRST BAD_JSON\nSECOND\n')
            events=self.store.events(s['id'])
            self.assertTrue(any(e['kind']=='diagnostic' for e in events))
            self.assertTrue(any(e['kind']=='files' and e['body']['changes'] for e in events))
            after=events[1]['seq']
            response=await self.client.get('/api/homework/sessions/'+s['id']+'/events?after='+str(after))
            self.assertTrue(all(e['seq']>after for e in await response.json()))

    async def test_idempotent_submission_and_directory_mutex(self):
        a,s=await self.setup_session()
        _,s2=await self.setup_session('codex','Course/Task')
        first=await self.send(s,'SLEEP FIRST','once')
        duplicate=await self.send(s,'SLEEP FIRST','once')
        self.assertEqual(first['id'],duplicate['id'])
        await self.wait(first,'running')
        second=await self.send(s2,'SECOND')
        await asyncio.sleep(.4)
        self.assertEqual(self.store.run(second['id'])['status'],'queued')
        self.assertEqual((await self.wait(first))['status'],'succeeded')
        self.assertEqual((await self.wait(second))['status'],'succeeded')

    async def test_failed_turn_pauses_queue_until_explicit_resume(self):
        _,s=await self.setup_session()
        first=await self.send(s,'SLEEP FAIL')
        second=await self.send(s,'SECOND')
        self.assertEqual((await self.wait(first))['status'],'failed')
        await asyncio.sleep(.4)
        self.assertEqual(self.store.run(second['id'])['status'],'queued')
        self.assertTrue(self.store.get('sessions',s['id'])['paused'])
        await self.post('/sessions/'+s['id']+'/resume')
        self.assertEqual((await self.wait(second))['status'],'succeeded')

    async def test_exit_zero_without_terminal_is_failure(self):
        _,s=await self.setup_session()
        row=await self.wait(await self.send(s,'NO_TERMINAL'))
        self.assertEqual(row['status'],'failed');self.assertIn('未收到完成事件',row['error'])

    async def test_completed_cli_cleans_background_child_holding_stdout(self):
        a,s=await self.setup_session()
        row=await self.wait(await self.send(s,'CHILD'))
        self.assertEqual(row['status'],'succeeded')
        pid=int((self.root/a['directory']/'child.pid').read_text())
        self.assertTrue(not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE)

    async def test_stop_cleans_child_and_preserves_resume(self):
        a,s=await self.setup_session()
        run=await self.send(s,'WAIT CHILD')
        child=self.root/a['directory']/'child.pid'
        for _ in range(100):
            if child.exists():break
            await asyncio.sleep(.05)
        self.assertTrue(child.exists())
        pid=int(child.read_text())
        await self.post('/turns/'+run['id']+'/stop')
        self.assertEqual(self.store.run(run['id'])['status'],'cancelled')
        await asyncio.sleep(.1)
        self.assertTrue(not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE)
        second=await self.send(s,'RESUMED')
        await self.post('/sessions/'+s['id']+'/resume')
        self.assertEqual((await self.wait(second))['status'],'succeeded')

    async def test_withdraw_queued_does_not_pause_running_session(self):
        _,s=await self.setup_session();first=await self.send(s,'SLEEP FIRST');await self.wait(first,'running')
        second=await self.send(s,'WITHDRAW');await self.post('/turns/'+second['id']+'/stop')
        self.assertFalse(self.store.get('sessions',s['id'])['paused'])
        self.assertEqual(self.store.run(second['id'])['status'],'cancelled')
        self.assertEqual((await self.wait(first))['status'],'succeeded')

    async def test_import_and_directory_freeze(self):
        a,s=await self.setup_session();native=str(uuid.uuid4())
        imported=await self.post('/sessions',{'assignment_id':a['id'],'provider':'codex','native_id':native,'directory':a['directory']},201)
        self.assertTrue(imported['started'])
        row=await self.wait(await self.send(imported,'IMPORT'))
        self.assertEqual(row['status'],'succeeded')
        self.assertEqual(self.store.get('sessions',imported['id'])['native_id'],native)
        response=await self.client.patch('/api/homework/assignments/'+a['id'],json={'directory':'Elsewhere'})
        self.assertEqual(response.status,400)

    async def test_file_preview_and_path_escape(self):
        a,_=await self.setup_session();folder=self.root/a['directory'];folder.mkdir(parents=True)
        (folder/'answer.md').write_text('# Answer')
        response=await self.client.get('/api/homework/assignments/'+a['id']+'/file',params={'path':'answer.md'})
        self.assertEqual(await response.text(),'# Answer');self.assertEqual(response.content_type,'text/plain')
        (folder/'escape').symlink_to('/tmp',target_is_directory=True)
        response=await self.client.get('/api/homework/assignments/'+a['id']+'/files',params={'path':'escape'})
        self.assertEqual(response.status,400)

    async def test_recovery_interrupts_without_replaying(self):
        _,s=await self.setup_session();s['paused']=True;self.store.put('sessions',s['id'],s)
        run=await self.send(s,'NEVER REPLAY');self.store.update_run(run['id'],status='running',pid=999999)
        await self.app['runner'].recover()
        self.assertEqual(self.store.run(run['id'])['status'],'interrupted')
        self.assertTrue(self.store.get('sessions',s['id'])['paused'])

    async def test_sync_preserves_notes_binding_status_and_cached_data(self):
        rows={'/api/courses':[{'id':'123','name':'Course'}],'/api/assignments':[{'id':'456','course_id':'123','name':'HW','description':'v1','attachments':[]}],'/api/files':[],'/api/lectures':[]}
        async def get(client,path):return rows[path]
        source=self.app['source']
        with patch.object(source,'get',get):await source.sync()
        key='canvas:123:456';row=self.store.get('assignments',key)
        row.update(notes='keep',directory='Existing/HW',status='done');self.store.put('assignments',key,row)
        rows['/api/assignments'][0]['description']='v2'
        with patch.object(source,'get',get):await source.sync()
        row=self.store.get('assignments',key)
        self.assertEqual((row['description'],row['notes'],row['directory'],row['status']),('v2','keep','Existing/HW','done'))
        await source.sync()
        self.assertEqual(self.store.get('assignments',key)['description'],'v2')
        self.assertTrue(self.store.get('meta','sync')['error'])

    async def test_snapshot_is_versioned_and_does_not_overwrite_answers(self):
        a,_=await self.setup_session();folder=self.root/a['directory'];folder.mkdir(parents=True)
        (folder/'answer.txt').write_text('keep')
        source=self.app['source'];first=await source.prepare(a)
        a=self.store.get('assignments',a['id']);a['notes']='new notes';self.store.put('assignments',a['id'],a)
        second=await source.prepare(a)
        self.assertNotEqual(first,second);self.assertTrue(first.exists())
        self.assertEqual((folder/'answer.txt').read_text(),'keep')
