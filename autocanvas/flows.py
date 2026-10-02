"""Explicit application use cases. Leaf modules never import this module."""
import asyncio
import json
import math
import shutil
from pathlib import Path
from . import media
from .execution import blocking
from .outputs import Transcript, atomic_json, finish_reading
from .types import AuthenticationRequired, MediaError, TranscriptSegment


def lecture_key(course_id, kind, lecture_id):
    return f'{course_id}:{kind}:{lecture_id}'


class CatalogSync:
    def __init__(self, canvas, video_factory, store, course_ids=()):
        self.canvas, self.video_factory, self.store = canvas, video_factory, store
        self.course_ids = set(course_ids)

    def courses(self):
        courses = self.canvas.courses()
        for course in courses:
            course['active'] = not self.course_ids or course['id'] in self.course_ids
            self.store.put('courses', course['id'], course)
        current = {c['id'] for c in courses}
        for old in self.store.list('courses'):
            if old['id'] not in current:
                old['active'] = False
                self.store.put('courses', old['id'], old)
        return courses

    def videos(self, course_id):
        client = self.video_factory(course_id)
        try:
            teaching_class = client.context()
            result = []
            for kind in ('vod', 'live'):
                rows = client.lectures(course_id, teaching_class, kind)
                for row in rows:
                    self.store.put('lectures', lecture_key(course_id, kind, row['id']), row)
                result.extend(rows)
            return result
        finally:
            client.close()

    def sources(self, lecture, *, live_protocol=3):
        # Each operation gets fresh course-scoped credentials. Nothing is cached in the DB.
        for attempt in range(2):
            client = self.video_factory(lecture['course_id'])
            try:
                if lecture['kind'] == 'live' and live_protocol != 3:
                    return client.sources(lecture['id'], lecture['kind'], live_protocol=live_protocol)
                return client.sources(lecture['id'], lecture['kind'])
            except AuthenticationRequired:
                if attempt:
                    raise
            finally:
                client.close()


class AssignmentSync:
    def __init__(self, canvas, session_factory, store, root):
        self.canvas, self.session_factory, self.store, self.root = canvas, session_factory, store, root

    def run(self, course_id):
        from .assignments import export
        rows = self.canvas.assignments(course_id)
        failed = False
        with self.session_factory() as session:
            for row in rows:
                result = export(session, row, self.root/str(course_id)/str(row['id']))
                result['course_id'] = str(course_id)
                self.store.put('assignments', f"{course_id}:{row['id']}", result)
                failed |= any(a['status'] == 'failed' for a in result['attachments'])
        if failed:
            raise IOError('Some attachments failed; successful attachments retained')
        return self.root/str(course_id)


async def transcribe_source(source, transcribe, folder, *, chunk_seconds=3, duration=None, offset=0, policy=None):
    if not math.isfinite(offset) or offset < 0 or duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('Invalid audio interval')
    if policy is not None:
        return await transcribe_replay(source, transcribe, folder, policy, duration=duration, offset=offset)
    output = Transcript(folder)
    start = max(offset, output.end)
    reader = media.audio(source, offset=start, duration=max(0, duration-(start-offset)) if duration else None, chunk_seconds=chunk_seconds)
    try:
        async for chunk in reader:
            output.append(await transcribe(chunk))
    finally:
        await reader.aclose()
        output.finish()
    return folder/'transcript.json'


async def transcribe_replay(source, transcribe, folder, policy, *, duration=None, offset=0):
    from .replay_asr import speech_windows
    import hashlib
    from urllib.parse import urlsplit
    if source.location.startswith(('http://', 'https://')):
        url = urlsplit(source.location)
        identity = hashlib.sha256(f'{url.scheme}://{url.netloc}{url.path}'.encode()).hexdigest()
    else:
        path = Path(source.location)
        digest = hashlib.sha256()
        if path.is_file():
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024*1024), b''):
                    digest.update(block)
        else:
            digest.update(source.location.encode())
        identity = digest.hexdigest()
    run_id = policy.fingerprint(view=source.view, offset=offset, duration=duration, audio=identity)
    folder = Path(folder)
    run = folder/'runs'/run_id
    output = Transcript(run)
    start = max(offset, output.end)
    manifest_path = run/'manifest.json'
    previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    quality_path = run/'quality.json'
    quality = json.loads(quality_path.read_text(encoding='utf-8')) if quality_path.exists() else {
        'windows': 0, 'silence_seconds': 0, 'speech_seconds': 0, 'uncertain_seconds': 0,
        'vad_backend': 'silero' if policy.vad_model else 'energy'}
    if previous.get('state') == 'complete':
        finish_reading(run)
        return run/'transcript.json'
    manifest = {'profile': 'replay-quality', 'run_id': run_id, 'parameters': policy.metadata(),
                'offset': offset, 'duration': duration, 'view': source.view, 'state': 'processing'}
    atomic_json(manifest_path, manifest)
    atomic_json(folder/'current.json', {'run_id': run_id})
    remaining = max(0, duration-(start-offset)) if duration is not None else None
    expected_end = offset+duration if duration is not None else None
    if expected_end is None:
        info = await media.probe(source)
        lengths = [float(s['duration']) for s in info.get('streams', [])
                   if s.get('codec_type') == 'audio' and s.get('duration') not in (None, 'N/A')]
        expected_end = max(lengths) if lengths else float(info.get('format', {}).get('duration') or 0)
    reader = media.audio(source, offset=start, duration=remaining, chunk_seconds=1)
    complete = False
    from collections import deque
    pending = deque()

    async def recognize(window):
        chunk = window.chunk
        if window.silent:
            return TranscriptSegment(chunk.start, chunk.start+chunk.duration, '')
        return await transcribe(chunk, context=policy.context, replay=True)

    async def commit():
        window, task = pending[0]
        segment = await task
        chunk = window.chunk
        if abs(segment.start-chunk.start) > .002 or abs(segment.end-chunk.start-chunk.duration) > .002:
            raise ValueError('ASR changed replay window timestamps')
        output.append(segment)
        quality['windows'] += 1
        quality['speech_seconds'] += window.speech_seconds
        quality['uncertain_seconds'] += window.uncertain_seconds
        if window.silent:
            quality['silence_seconds'] += chunk.duration
        atomic_json(quality_path, quality)
        pending.popleft()

    windows = speech_windows(reader, policy, call=blocking)
    try:
        if remaining is None or remaining > 0:
            read_error = None
            while True:
                try:
                    window = await anext(windows)
                except StopAsyncIteration:
                    break
                except Exception as error:
                    read_error = error
                    break
                pending.append((window, asyncio.create_task(recognize(window))))
                # Bounded read-ahead overlaps CPU VAD/decoding with GPU inference.
                if len(pending) >= 16:
                    await commit()
            while pending:
                await commit()
            if read_error is not None:
                raise read_error
        tolerance = 1 if duration is not None else max(15, expected_end*.01)
        if expected_end and output.end < expected_end-tolerance:
            raise MediaError('Replay ended before expected audio coverage')
        complete = True
    finally:
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        await windows.aclose()
        await reader.aclose()
        output.finish()
        finish_reading(run)
        atomic_json(manifest_path, {**manifest, 'state': 'complete' if complete else 'partial',
                                  'processed_seconds': output.end-offset})
    return run/'transcript.json'


async def select_replay_source(sources, policy, *, view=None, offset=600):
    """Speech-activity sampling is a heuristic, never an ASR accuracy score."""
    if view is not None:
        return await media.select(sources, 'audio', view)
    from .replay_asr import VoiceDetector
    import numpy as np
    candidates = []
    for source in sources:
        try:
            info = await media.probe(source)
            if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
                continue
            detector = VoiceDetector(policy.vad_model)
            duration = float(info.get('format', {}).get('duration') or 0)
            reader = media.audio(source, offset=min(offset, max(0, duration-12)), duration=12, chunk_seconds=1)
            scores, clipping = [], []
            try:
                async for chunk in reader:
                    pcm = chunk.pcm
                    values = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
                    clipping.append(float(np.mean(np.abs(values) >= 32700)))
                    for start in range(0, len(pcm), 1024):
                        frame = pcm[start:start+1024]
                        scores.append(detector.score(frame+b'\0'*(1024-len(frame))))
            finally:
                await reader.aclose()
            if scores:
                voiced = sum(s >= .5 for s in scores)/len(scores)
                bitrate = int(info.get('format', {}).get('bit_rate') or 10**12)
                candidates.append((voiced, -sum(clipping)/len(clipping), -bitrate, source))
        except (MediaError, ValueError, KeyError, asyncio.TimeoutError):
            continue
    if not candidates:
        raise MediaError('No readable speech source')
    return max(candidates, key=lambda c: c[:3])[-1]


async def slides_source(source, folder, cache, *, sample_every=5, duration=None):
    from .slides import extract
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    pending = folder/'pending'
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir(parents=True, exist_ok=True)
    try:
        await media.sample_frames(source, cache, every=sample_every, duration=duration)
        rows = await blocking(extract, cache, pending, sample_every=sample_every)
        serial = []
        for row in rows:
            row = dict(row)
            row['image'] = Path(row['image']).name
            row.pop('frame', None)
            serial.append(row)
        atomic_json(pending/'slides.json', serial)
        # Optional analysis is isolated: a QR failure must not discard extracted slides.
        from .qr import scan_images
        try:
            qr_report = await blocking(scan_images, pending, serial)
        except Exception as error:
            qr_report = {'version': 1, 'status': 'failed', 'events': [], 'error': type(error).__name__}
        atomic_json(pending/'qr.json', qr_report)
        target = folder/'result'
        backup = folder/'previous'
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.rename(backup)
        pending.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
        return target/'slides.json'
    finally:
        shutil.rmtree(cache, ignore_errors=True)


class Replay:
    def __init__(self, resolve_sources, transcribe, root, cache, *, chunk_seconds=3, sample_every=5, policy=None, context_loader=None):
        self.resolve_sources, self.transcribe = resolve_sources, transcribe
        self.root, self.cache = root, cache
        self.chunk_seconds, self.sample_every = chunk_seconds, sample_every
        self.policy = policy
        self.context_loader = context_loader

    async def run(self, lecture, kind, options):
        folder = self.root/lecture['course_id']/lecture['id']/kind
        purpose = 'audio' if kind == 'vod_asr' else 'screen'
        quality = self.policy if kind == 'vod_asr' and options.get('profile') != 'legacy' else None
        if quality is not None and self.context_loader is not None:
            from dataclasses import replace
            quality = replace(quality, context=self.context_loader(lecture)).validate()
        view = options.get('view')
        if quality is not None and view is None and (folder/'current.json').is_file():
            try:
                run_id = json.loads((folder/'current.json').read_text(encoding='utf-8'))['run_id']
                if isinstance(run_id, str) and len(run_id) == 16 and all(c in '0123456789abcdef' for c in run_id):
                    previous = json.loads((folder/'runs'/run_id/'manifest.json').read_text(encoding='utf-8'))
                    if previous.get('state') == 'partial' and previous.get('parameters') == quality.metadata():
                        view = previous.get('view')
            except (OSError, ValueError, KeyError, TypeError):
                pass
        sources = await self.resolve_sources(lecture)
        source = (await select_replay_source(sources, quality, view=view, offset=options.get('offset') or 600)
                  if quality is not None else await media.select(sources, purpose, options.get('view')))
        if kind == 'vod_asr':
            return await transcribe_source(source, self.transcribe, folder, chunk_seconds=self.chunk_seconds,
                duration=options.get('duration'), offset=options.get('offset', 0),
                policy=quality)
        return await slides_source(source, folder, self.cache/lecture['course_id']/lecture['id'], sample_every=self.sample_every, duration=options.get('duration'))
