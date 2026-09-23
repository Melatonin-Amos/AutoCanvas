import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from autocanvas.bootstrap import build
from autocanvas.config import Settings
from autocanvas.configuration import Configuration
from autocanvas.http import create_app
from autocanvas.browser_auth import BrowserAuth
from autocanvas.auth import Auth
from autocanvas.types import AuthenticationRequired, TranscriptSegment


class ConfigurationTests(unittest.TestCase):
    def test_validation_persistence_and_application_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(root=Path(folder))
            busy = [True]
            applied = []
            config = Configuration(settings, lambda: busy[0], lambda values, changed: applied.append(changed), lambda: None)
            result = config.save({'port': 9000, 'auto_asr': False, 'chunk_seconds': 12}, 0)
            self.assertEqual(result['restart_required'], ['port'])
            self.assertEqual(result['waiting_for_idle'], ['chunk_seconds'])
            self.assertFalse(settings.auto_asr)
            self.assertEqual(settings.chunk_seconds, 3)
            self.assertEqual(settings.port, 8080)
            busy[0] = False
            config.apply_ready()
            self.assertEqual(settings.chunk_seconds, 12)
            self.assertEqual(applied, [{'chunk_seconds'}])
            reloaded = Settings.load(root=Path(folder))
            self.assertEqual(reloaded.port, 9000)
            with self.assertRaises(ValueError):
                config.save({'auto_live': 'false'}, 1)
            with self.assertRaises(ValueError):
                config.save({'auto_live': False}, 0)
            self.assertTrue(Settings.load(root=Path(folder)).auto_live)


class BrowserAuthTests(unittest.TestCase):
    def test_challenge_single_use_and_secret_not_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = BrowserAuth(Auth(Path(folder)/'session.json'))
            session = Mock()
            with patch('autocanvas.browser_auth._jaccount.create_session', return_value=session), patch('autocanvas.browser_auth._jaccount._get_jaccount_login_page', return_value=('https://jaccount.sjtu.edu.cn','html')), patch('autocanvas.browser_auth._jaccount._parse_login_form', return_value=({},'uuid',False)), patch('autocanvas.browser_auth._jaccount._submit_login') as submit, patch('autocanvas.browser_auth._jaccount.is_session_valid', return_value=True), patch('autocanvas.browser_auth._jaccount.save_session') as save:
                challenge = browser.begin()
                self.assertTrue(browser.submit(challenge['challenge_id'], 'user', 'secret')['authenticated'])
                submit.assert_called_once()
                save.assert_called_once_with(session, Path(folder)/'session.json')
                self.assertFalse(browser.pending)
                with self.assertRaises(AuthenticationRequired):
                    browser.submit(challenge['challenge_id'], 'user', 'secret')
            browser.close()

    def test_reject_foreign_cookies(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = BrowserAuth(Auth(Path(folder)/'session.json'))
            for cookies in ([{'name':'a','value':'b','domain':'evil.example'}], ['bad']):
                with self.assertRaises(ValueError):
                    browser.import_session(cookies)


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service, self.inference = build(Settings(root=self.root))
        self.store = self.service.store
        self.store.put('courses', '1', {'id':'1','name':'Course','active':True})
        self.store.put('lectures','1:vod:2', {'id':'2','course_id':'1','kind':'vod'})
        self.client = TestClient(TestServer(create_app(self.service)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.service.close()
        await self.inference.close()
        self.tmp.cleanup()

    async def test_classroom_events_endpoint(self):
        folder = self.service.settings.root/'outputs/1/2/live'
        folder.mkdir(parents=True)
        (folder/'events.jsonl').write_text(json.dumps({'type':'keyword','keyword':'签到','text':'请签到','start':12})+'\n')
        response = await self.client.get('/api/classroom-events')
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data['events'][0]['keyword'], '签到')
        self.assertEqual(data['events'][0]['course_id'], '1')
        self.assertEqual(data['scans'], [])

    async def test_settings_origin_and_schema(self):
        response = await self.client.get('/api/settings')
        data = await response.json()
        self.assertEqual(set(f['key'] for f in data['schema']), set(data['desired']))
        response = await self.client.patch('/api/settings', json={'values':{'auto_live':False},'revision':0})
        self.assertEqual(response.status,200)
        self.assertFalse(self.service.settings.auto_live)
        response = await self.client.post('/api/automation', json={'paused':True}, headers={'Origin':'https://evil.example'})
        self.assertEqual(response.status,403)
        response = await self.client.patch('/api/settings', json={'values':{'port':-1},'revision':1})
        self.assertEqual(response.status,400)

    async def test_artifact_traversal_and_symlink(self):
        outputs = self.root/'outputs'
        outputs.mkdir()
        (outputs/'transcript.txt').write_text('hello')
        secret = self.root/'secret.json'
        secret.write_text('PRIVATE')
        (outputs/'leak.txt').symlink_to(secret)
        for path in ('../secret.json','outputs/../secret.json','outputs/leak.txt',str(secret)):
            response = await self.client.get('/api/file',params={'path':path})
            self.assertEqual(response.status,404,path)
        response = await self.client.get('/api/file', params={'path':'outputs/transcript.txt'})
        self.assertEqual(await response.text(),'hello')
        rows = await (await self.client.get('/api/files')).json()
        self.assertEqual([row['name'] for row in rows], ['transcript.txt'])

    async def test_upload_local_and_sample_are_separate(self):
        form = FormData()
        form.add_field('file',b'fake wav',filename='../../test.wav',content_type='audio/wav')
        response = await self.client.post('/api/uploads',data=form)
        self.assertEqual(response.status,201)
        upload = await response.json()
        self.assertEqual(upload['name'],'test.wav')
        response = await self.client.post('/api/local/asr',json={'upload_id':upload['id']})
        self.assertEqual(response.status,202)
        local = self.store.execution((await response.json())['execution_id'])
        async def fake_transcribe(source, transcribe, folder, **kwargs):
            self.assertEqual(Path(source.location).read_bytes(),b'fake wav')
            folder.mkdir(parents=True)
            (folder/'transcript.json').write_text('[]')
            return folder/'transcript.json'
        with patch('autocanvas.flows.transcribe_source', side_effect=fake_transcribe):
            await self.service._execute(self.store.claim('local_asr'))
        self.assertEqual(self.store.execution(local['id'])['status'],'succeeded')
        response = await self.client.post('/api/sample/asr',json={'course_id':'1','lecture_id':'2','duration':10})
        self.assertEqual(response.status,202)
        sample = self.store.execution((await response.json())['execution_id'])
        self.assertEqual(sample['kind'],'sample_asr')
        self.assertTrue(sample['options']['output_key'])
        full = self.service.enqueue_processing('1','2','vod_asr')
        self.assertNotEqual(sample['id'],full)
        response = await self.client.post('/api/sample/slides',json={'course_id':'1','lecture_id':'2','duration':-1})
        self.assertEqual(response.status,400)

    async def test_upload_reject_and_assignment_sync(self):
        form=FormData()
        form.add_field('file',b'<script/>',filename='bad.html')
        response = await self.client.post('/api/uploads',data=form)
        self.assertEqual(response.status,400)
        response = await self.client.post('/api/assignments/sync',json={'course_id':'1'})
        self.assertEqual(response.status,202)
        self.assertEqual(self.store.execution((await response.json())['execution_id'])['kind'],'assignments')

    async def test_sse_and_auth_status(self):
        response = await self.client.get('/api/events')
        self.assertEqual(response.content_type,'text/event-stream')
        self.assertEqual(await response.content.readline(),b'event: state\n')
        data = await response.content.readline()
        self.assertIn('executions',json.loads(data[6:]))
        response.close()
        with patch.object(self.service.browser_auth,'status',return_value={'authenticated':False}):
            response = await self.client.get('/api/auth/status')
            self.assertEqual(await response.json(),{'authenticated':False})

    async def test_automatic_status_never_returns_credentials(self):
        manager = self.service.browser_auth.auth.automatic
        manager.credentials.set('private-user', 'private-password')
        response = await self.client.get('/api/auth/automatic')
        text = await response.text()
        self.assertNotIn('private-user', text)
        self.assertNotIn('private-password', text)
        self.assertTrue(json.loads(text)['configured'])
        response = await self.client.get('/api/file', params={'path':'auth/credentials.yml'})
        self.assertEqual(response.status, 404)
        response = await self.client.post('/api/auth/automatic-test', headers={'Origin':'https://foreign.example'})
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/auth/automatic-disable')
        self.assertEqual(response.status, 200)
        self.assertFalse(manager.state()['enabled'])

    async def test_explicit_fresh_login_recovers_blocked_work(self):
        run = self.store.enqueue('assignments', '1')
        self.store.finish(run, 'needs_login')
        with patch.object(self.service.browser_auth, 'test_automatic', return_value={'authenticated':True}):
            response = await self.client.post('/api/auth/automatic-test')
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['recovered_executions'], 1)
        self.assertEqual(self.store.execution(run)['status'], 'pending')

    async def test_real_uploaded_video_slides(self):
        import subprocess
        video = self.root/'fixture.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=10','-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
        form=FormData()
        form.add_field('file',video.read_bytes(),filename='fixture.mp4',content_type='video/mp4')
        response=await self.client.post('/api/uploads',data=form)
        self.assertEqual(response.status,201)
        upload=await response.json()
        response=await self.client.post('/api/local/slides',json={'upload_id':upload['id']})
        run=(await response.json())['execution_id']
        await self.service.start(automation=False)
        async with asyncio.timeout(15):
            while self.store.execution(run)['status'] in ('pending','running'):
                await asyncio.sleep(.05)
        row=self.store.execution(run)
        self.assertEqual(row['status'],'succeeded',row['error'])
        self.assertTrue(Path(row['artifact']).is_file())
        response=await self.client.get('/api/executions/'+run+'/progress')
        self.assertIn('slides',await response.json())

    async def test_automation_start_idempotent_and_rule_inheritance(self):
        started=asyncio.Event()
        async def schedule():
            started.set()
            await asyncio.Event().wait()
        with patch.object(self.service,'_schedule',side_effect=schedule):
            for _ in range(2):
                response=await self.client.post('/api/automation',json={'paused':False})
                self.assertEqual(response.status,200)
            await asyncio.wait_for(started.wait(),1)
            self.assertEqual(sum(t.get_name()=='automation' for t in self.service.background),1)
            response=await self.client.get('/health')
            self.assertTrue((await response.json())['scheduler_running'])
        await self.client.patch('/api/courses/1/rules',json={'asr':False})
        response=await self.client.patch('/api/courses/1/rules',json={'asr':None})
        self.assertEqual(await response.json(),{})

    async def test_streamed_upload_exceeds_json_limit(self):
        form=FormData()
        form.add_field('file',b'0'*100000,filename='fixture.wav',content_type='audio/wav')
        response=await self.client.post('/api/uploads',data=form)
        self.assertEqual(response.status,201)
        self.assertEqual((await response.json())['size'],100000)
