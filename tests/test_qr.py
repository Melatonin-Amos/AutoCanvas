import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autocanvas.qr import QRDetector, scan_images
from autocanvas.classroom_events import classroom_events
from autocanvas.outputs import atomic_json
from autocanvas.flows import slides_source


class QRTests(unittest.TestCase):
    def test_real_qr_and_blank_image(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            qr = cv2.QRCodeEncoder_create().encode('https://example.org/attendance?id=test')
            qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
            qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(str(folder/'qr.png'), qr)
            cv2.imwrite(str(folder/'blank.png'), np.full((400,400,3),255,np.uint8))
            result = scan_images(folder, [{'image':'qr.png','time_seconds':65}, {'image':'blank.png','time_seconds':70}])
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(result['events']), 1)
            event = result['events'][0]
            self.assertEqual(event['start'], 65)
            self.assertTrue(event['decoded'])
            self.assertEqual(event['content'], 'https://example.org/attendance?id=test')
            self.assertEqual(len(event['corners']),4)

    def test_candidate_box_on_blank_slide_is_not_an_event(self):
        import cv2
        import numpy as np
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'slide.png'
            cv2.imwrite(str(path), np.full((400,400,3),255,np.uint8))
            detector = QRDetector()
            real_decode = detector.detector.detectAndDecode
            points = np.float32([[[30,30],[350,30],[350,350],[30,350]]])
            detector.detector = SimpleNamespace(
                detectAndDecodeMulti=lambda image: (False, ('',), points, ()),
                detectAndDecode=real_decode)
            self.assertEqual(detector.detect(path), [])

    def test_rectified_retry_recovers_real_qr(self):
        import cv2
        import numpy as np
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'qr.png'
            code = cv2.QRCodeEncoder_create().encode('verified-qr-retry')
            image = cv2.copyMakeBorder(code,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
            image = cv2.resize(image,None,fx=8,fy=8,interpolation=cv2.INTER_NEAREST)
            image = cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
            cv2.imwrite(str(path),image)
            detector=QRDetector()
            text, points, _=detector.detector.detectAndDecode(image)
            self.assertEqual(text,'verified-qr-retry')
            real_decode=detector.detector.detectAndDecode
            detector.detector=SimpleNamespace(
                detectAndDecodeMulti=lambda image: (False, ('',), points, ()),
                detectAndDecode=real_decode)
            events=detector.detect(path)
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['content'],'verified-qr-retry')

    def test_empty_detection_and_bad_image_are_isolated(self):
        class Detector:
            def detect(self,path):
                if path.name=='bad.jpg':
                    raise ValueError('bad image')
                return []
        with tempfile.TemporaryDirectory() as tmp:
            rows=[{'image':'qr.jpg','time_seconds':0},{'image':'bad.jpg'},{'image':'../outside.jpg'}]
            report=scan_images(Path(tmp),rows,detector=Detector())
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['scanned_images'],1)
            self.assertEqual(len(report['events']),0)
            self.assertEqual(len(report['errors']),2)

    def test_projection_separates_not_scanned_empty_and_failed_and_torn_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for lecture in ('a','b','c','d'):
                folder=root/'outputs'/'1'/lecture/'vod_slides'/'result'
                atomic_json(folder/'slides.json', [])
            a=root/'outputs/1/a/vod_slides/result'
            (a/'qr.jpg').write_bytes(b'image')
            atomic_json(a/'qr.json',{'status':'complete','scanned_images':1,'total_images':1,'events':[{'id':'one','start':2,'image':'qr.jpg','content':'hello','decoded':True},{'id':'false','start':9,'image':'qr.jpg','content':'','decoded':False}]})
            atomic_json(root/'outputs/1/b/vod_slides/result/qr.json',{'status':'complete','events':[]})
            (root/'outputs/1/c/vod_slides/result/qr.json').write_text('{')
            live=root/'outputs/1/a/live/events.jsonl'
            live.parent.mkdir()
            line=json.dumps({'type':'keyword','keyword':'点名','start':3,'text':'现在点名'})+'\n'
            live.write_text(line+line+'{"partial"')
            report=classroom_events(root)
            self.assertEqual(len(report['events']),2)
            self.assertEqual([s['status'] for s in report['scans']],['complete','complete','failed','not_scanned'])
            self.assertEqual({e['source'] for e in report['events']},{'live','vod'})
            # A manipulated image path may not expose any file outside outputs.
            atomic_json(a/'qr.json',{'status':'complete','events':[{'start':0,'image':'../../../../secret'}]})
            self.assertEqual(len(classroom_events(root)['events']),1)

    def test_qr_failure_does_not_lose_slides(self):
        async def sample(source, cache, **kwargs):
            pass
        def extract(cache, pending, **kwargs):
            (pending/'slide.jpg').write_bytes(b'image')
            return [{'image':pending/'slide.jpg','time_seconds':0}]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('autocanvas.media.sample_frames',sample), patch('autocanvas.slides.extract',extract), patch('autocanvas.qr.scan_images',side_effect=RuntimeError('failed')):
                result=asyncio.run(slides_source(None,root/'slides',root/'cache'))
            self.assertTrue(result.exists())
            self.assertTrue(result.with_name('slide.jpg').exists())
            self.assertEqual(json.loads(result.with_name('qr.json').read_text())['status'],'failed')
            self.assertFalse((root/'cache').exists())
