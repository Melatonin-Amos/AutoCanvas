"""Application output utilities; paths never include remote titles or credentials."""
import json
import os
import hashlib
from pathlib import Path
from dataclasses import asdict


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(path)


class Transcript:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.path = folder/'segments.jsonl'
        self.end = 0.0
        if self.path.exists():
            valid = []
            for line in self.path.read_bytes().splitlines():
                try:
                    item = json.loads(line)
                    self.end = max(self.end, float(item['end']))
                    valid.append(json.dumps(item, ensure_ascii=False))
                except (ValueError, KeyError):
                    break
            self.path.write_text(''.join(line+'\n' for line in valid), encoding='utf-8')

    def append(self, segment):
        if segment.end <= self.end:
            return
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(asdict(segment), ensure_ascii=False)+'\n')
            handle.flush()
            os.fsync(handle.fileno())
        self.end = segment.end

    def finish(self):
        rows = [json.loads(s) for s in self.path.read_text(encoding='utf-8').splitlines()] if self.path.exists() else []
        atomic_json(self.folder/'transcript.json', rows)
        tmp = self.folder/'transcript.txt.tmp'
        tmp.write_text(''.join(f"[{int(r['start'])//3600:02}:{int(r['start'])//60%60:02}:{int(r['start'])%60:02}] {r['text']}\n" for r in rows if r['text']), encoding='utf-8')
        tmp.replace(self.folder/'transcript.txt')
        return self.folder/'transcript.json'


def append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False)+'\n')


def finish_reading(folder, *, reviewed=None):
    from .transcript_text import reading_paragraphs, apply_review, markdown, issues_markdown
    signature = hashlib.sha256((folder/'transcript.json').read_bytes()).hexdigest()
    metadata = folder/'reading_manifest.json'
    if reviewed is None and metadata.is_file():
        previous = json.loads(metadata.read_text(encoding='utf-8'))
        if previous.get('reviewed') and previous.get('raw_sha256') == signature and (folder/'reading.json').is_file():
            return folder/'reading.json'
    rows = json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    paragraphs, edits = reading_paragraphs(rows)
    if reviewed is not None:
        paragraphs, review_edits = apply_review(paragraphs, reviewed)
        edits.extend(review_edits)
    issues = [e for e in edits if e.get('reason') == 'uncertain_content']
    atomic_json(folder/'reading.json', paragraphs)
    atomic_json(folder/'edits.json', edits)
    atomic_json(folder/'issues.json', issues)
    for name, text in [('reading.md', markdown(paragraphs)),
                       ('reading.txt', '\n\n'.join(p['text'] for p in paragraphs)),
                       ('issues.md', issues_markdown(issues))]:
        path = folder/name
        temporary = path.with_suffix(path.suffix+'.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(path)
    atomic_json(metadata, {'version': 4, 'raw_sha256': signature, 'reviewed': reviewed is not None,
                          'content_outcome': 'reading_available' if paragraphs else 'empty_reading',
                          'issue_count': len(issues),
                          'explicit_edit_count': sum(e.get('reason') == 'explicit_review' for e in edits)})
    return folder/'reading.json'
