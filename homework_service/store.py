import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS entities(kind TEXT,key TEXT,body TEXT NOT NULL,PRIMARY KEY(kind,key));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,request_id TEXT UNIQUE NOT NULL,
                    prompt TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
                    pid INTEGER,process_created REAL,error TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,at REAL NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id,seq);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT body FROM entities WHERE kind=? AND key=?',(kind,key)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, kind, key, body):
        with self.connect() as db:
            db.execute('INSERT INTO entities VALUES(?,?,?) ON CONFLICT(kind,key) DO UPDATE SET body=excluded.body',
                       (kind,key,json.dumps(body,ensure_ascii=False)))
        return body

    def list(self, kind):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM entities WHERE kind=? ORDER BY key',(kind,))]

    def runs(self, session_id=None):
        with self.connect() as db:
            query='SELECT * FROM runs'
            return [dict(r) for r in db.execute(query+(' WHERE session_id=?' if session_id else '')+' ORDER BY created,id',
                                                (session_id,) if session_id else ())]

    def run(self, run_id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            return dict(row) if row else None

    def enqueue(self, session_id, request_id, prompt):
        if not request_id or len(request_id)>128 or not isinstance(prompt,str) or not prompt.strip() or len(prompt)>100000:
            raise ValueError('需要有效的请求 ID 和任务说明（最多 100000 字符）')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM runs WHERE request_id=?',(request_id,)).fetchone()
            if row:
                if row['session_id']!=session_id or row['prompt']!=prompt:
                    raise ValueError('请求 ID 已被另一条消息使用')
                return dict(row)
            now=time.time(); key=uuid.uuid4().hex
            db.execute('INSERT INTO runs(id,session_id,request_id,prompt,status,created,updated) VALUES(?,?,?,?,?,?,?)',
                       (key,session_id,request_id,prompt,'queued',now,now))
            return dict(db.execute('SELECT * FROM runs WHERE id=?',(key,)).fetchone())

    def update_run(self, key, **values):
        allowed={'status','pid','process_created','error','result'}
        if not set(values)<=allowed:
            raise ValueError('Invalid run fields')
        values['updated']=time.time()
        with self.connect() as db:
            db.execute('UPDATE runs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),key))

    def event(self, run, kind, body):
        with self.connect() as db:
            cursor=db.execute('INSERT INTO events(run_id,session_id,at,kind,body) VALUES(?,?,?,?,?)',
                             (run['id'],run['session_id'],time.time(),kind,json.dumps(body,ensure_ascii=False)))
            return cursor.lastrowid

    def events(self, session_id, after=0, limit=500):
        with self.connect() as db:
            return [{**dict(r),'body':json.loads(r['body'])} for r in db.execute(
                'SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?', (session_id,after,limit))]
