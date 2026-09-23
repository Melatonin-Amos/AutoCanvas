"""Read-only progress projection from workflow outputs; no processing hooks."""
import json
from pathlib import Path


def execution_progress(root: Path, row):
    kind = row['kind']
    if kind.startswith('sample_'):
        folder = root/'samples'/row['options']['output_key']/row['course_id']/row['lecture_id']/kind.replace('sample_', 'vod_')
    else:
        folder = root/'outputs'/row['course_id']/row['lecture_id']/kind
    result = {'processed_seconds': 0, 'events': []}
    segments = folder/'segments.jsonl'
    if segments.exists():
        # The last committed line is enough; a partial tail is ignored.
        with segments.open('rb') as handle:
            handle.seek(max(0, segments.stat().st_size-16384))
            lines = handle.read().splitlines()
        for line in reversed(lines):
            try:
                result['processed_seconds'] = json.loads(line)['end']
                break
            except (ValueError, KeyError):
                continue
    events = folder/'events.jsonl'
    if events.exists():
        with events.open('rb') as handle:
            handle.seek(max(0, events.stat().st_size-32768))
            for line in handle.read().splitlines():
                try:
                    result['events'].append(json.loads(line))
                except ValueError:
                    pass
        result['events'] = result['events'][-30:]
    slides = folder/'result'/'slides.json'
    if slides.exists():
        try:
            result['slides'] = len(json.loads(slides.read_text()))
        except ValueError:
            pass
    return result
