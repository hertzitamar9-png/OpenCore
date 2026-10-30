"""Remove only managed image-job data belonging to an explicitly deleted chat."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import urllib.request
import urllib.error
from urllib.parse import urlsplit

ACTIVE = {'queued', 'starting', 'preparing', 'running', 'cancelling'}
JOB = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')

class LocalOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def confined(path, root):
    root, path = Path(root).absolute(), Path(path).absolute()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Image cleanup left its managed job directory')
    for part in (root, *reversed(path.parents), path):
        if part == root or part.is_relative_to(root):
            if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                raise ValueError('Image cleanup cannot follow a directory link')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Image cleanup resolved outside its managed directory')
    return path


def saved_conversations_page(app_data, after=None, limit=200):
    """Read saved identities beyond the native UI's 500-row recent-list limit."""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('Choose a saved-chat page size between 1 and 500')
    if after is not None and (not isinstance(after, str) or not after or len(after)>512 or '\x00' in after):
        raise ValueError('Invalid saved-chat cursor')
    root=Path(app_data).absolute()
    database=confined(root/'control-center.sqlite3',root)
    if not database.is_file():
        raise ValueError('The local chat database is unavailable; nothing was deleted')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        rows=db.execute('SELECT id,title,client FROM conversations WHERE id>? ORDER BY id LIMIT ?',
                        (after or '',limit+1)).fetchall()
    values=[{'id':key,'title':title,'client':client} for key,title,client in rows[:limit]]
    return {'conversations':values,'nextCursor':values[-1]['id'] if len(rows)>limit else None}


class ChatMedia:
    def __init__(self, app_data, images, root=None, echo_urls=()):
        self.data, self.images = Path(app_data).resolve(), images
        self.root=Path(root).resolve() if root is not None else None
        self.echo_urls=echo_urls

    def _echo(self,identity):
        if self.root is None:return
        for base in self.echo_urls:
            url=urlsplit(base)
            if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port:continue
            request=urllib.request.Request(base+'/echo/conversation/delete',data=json.dumps({'conversation_id':identity}).encode(),headers={'Content-Type':'application/json'})
            try:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),LocalOnly())
                with opener.open(request,timeout=5) as response:
                    if json.load(response).get('deleted') is not True:raise ValueError('ECHO did not confirm conversation deletion')
            except urllib.error.HTTPError as error:
                if error.code not in (404,405):raise ValueError('ECHO could not remove this conversation yet') from error
            except urllib.error.URLError:pass
        directory=self.root/'echo/archives'
        safe=re.sub(r'[^A-Za-z0-9_-]','',identity)[:32] or 'conv'
        stem=safe+'-'+hashlib.sha1(identity.encode()).hexdigest()[:12]
        paths=[confined(directory/(name+suffix),directory) for name in (stem,stem+'-cold') for suffix in ('.db','.db-wal','.db-shm')]
        for path in paths:path.unlink(missing_ok=True)

    def _native(self, identity):
        database = self.data / 'studio/jobs.sqlite3'
        confined(database,self.data)
        if not database.is_file():
            return []
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            return [(key, job) for key, raw in db.execute('SELECT id,payload FROM jobs')
                    if (job := json.loads(raw)).get('category') == 'image'
                    and job.get('request', {}).get('conversationId') == identity]

    def check(self, identity):
        if not isinstance(identity, str) or not identity.strip() or len(identity)>512 or '\x00' in identity:
            raise ValueError('Invalid conversation identity')
        native = self._native(identity)
        with self.images.lock:
            hub = [job for job in self.images.jobs if job['request'].get('conversationId') == identity]
            if any(job.get('status') in ACTIVE for _, job in native) or any(job.get('status') in ACTIVE for job in hub):
                raise ValueError('Stop image generation before deleting this chat')
        return native, hub

    def _ledger(self, identity, job_ids, outputs):
        root = self.data / 'workspace-history/workspace-files'
        database = root / 'ledger.sqlite3'
        confined(database,self.data)
        if not database.is_file():
            return
        with closing(sqlite3.connect(database, timeout=3)) as db, db:
            rows = db.execute('SELECT f.id,f.path,f.before_hash,f.after_hash,t.capture_id,t.kind,t.turn_id '
                              'FROM files f JOIN turns t ON f.capture_id=t.capture_id WHERE t.conversation_id=?', (identity,)).fetchall()
            targets = [row for row in rows if row[1] in outputs or row[5] == 'output' and row[6] in job_ids]
            hashes = {value for row in targets for value in row[2:4] if isinstance(value, str) and HASH.fullmatch(value)}
            for row in targets:
                db.execute('DELETE FROM files WHERE id=?', (row[0],))
            for capture in {row[4] for row in targets}:
                if not db.execute('SELECT 1 FROM files WHERE capture_id=?', (capture,)).fetchone():
                    db.execute('DELETE FROM capture_assets WHERE capture_id=?', (capture,))
                    db.execute('DELETE FROM turns WHERE capture_id=?', (capture,))
            for digest in hashes:
                if not db.execute('SELECT 1 FROM files WHERE before_hash=? OR after_hash=? LIMIT 1', (digest, digest)).fetchone():
                    # Content objects may be stored flat or by hash prefix.
                    for candidate in (root/'objects'/digest, root/'objects'/digest[:2]/digest[2:], root/'objects'/digest[:2]/(digest+'.blob')):
                        confined(candidate, root/'objects').unlink(missing_ok=True)

    def delete(self, identity):
        native, hub = self.check(identity)
        outputs = {path for job in [*(job for _, job in native), *hub] for path in job.get('outputs', []) if isinstance(path, str)}
        ids = {key for key, _ in native} | {job['id'] for job in hub}
        for key in ids:
            if not JOB.fullmatch(key):
                raise ValueError('Invalid managed image job identity')
        # Resolve every removal target before changing any filesystem state.
        folders = [confined(self.data/'studio'/key, self.data/'studio') for key, _ in native]
        folders += [confined(self.images.directory/job['id'], self.images.directory) for job in hub]
        requests = [confined(self.images.directory/(job['id']+'.json'), self.images.directory) for job in hub]
        self._echo(identity)
        for folder in folders:
            if folder.exists():
                shutil.rmtree(folder)
        for request in requests:
            request.unlink(missing_ok=True)
        self._ledger(identity, ids, outputs)
        if native:
            with closing(sqlite3.connect(self.data/'studio/jobs.sqlite3', timeout=3)) as db, db:
                db.executemany('DELETE FROM jobs WHERE id=?', [(key,) for key, _ in native])
        activity=self.data/'agent-platform.sqlite3'
        confined(activity,self.data)
        if activity.is_file():
            with closing(sqlite3.connect(activity,timeout=3)) as db,db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity'").fetchone():
                    db.executemany('DELETE FROM activity WHERE id=?',[(f'studio:{key}:{status}',) for key in ids for status in ('queued','completed','failed','cancelled')])
        with self.images.lock:
            self.images.jobs = [job for job in self.images.jobs if job not in hub]
            self.images.conversations.pop(identity, None)
            from hf_download import atomic_json
            atomic_json(self.images.directory/'jobs.json', {'schema':1, 'jobs':self.images.jobs, 'conversations':self.images.conversations})
        return {'deleted': True, 'imageJobs': len(ids)}
