import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace

from hf_chat_media import ChatMedia, confined


class ChatMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data';self.data.mkdir()
        directory=Path(self.temp.name)/'hub';directory.mkdir()
        self.images=SimpleNamespace(directory=directory,lock=threading.RLock(),jobs=[],conversations={})
        self.media=ChatMedia(self.data,self.images)
        (self.data/'studio').mkdir()
        with closing(sqlite3.connect(self.data/'studio/jobs.sqlite3')) as db,db:db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def job(self,key,chat,status='completed',category='image'):
        folder=self.data/'studio'/key;folder.mkdir()
        image=folder/'image.png';image.write_bytes(b'private image')
        value={'id':key,'category':category,'request':{'conversationId':chat},'status':status,'outputs':[str(image)]}
        with closing(sqlite3.connect(self.data/'studio/jobs.sqlite3')) as db,db:db.execute('INSERT INTO jobs VALUES(?,?)',(key,json.dumps(value)))
        return folder,image

    def test_deleting_one_chat_removes_native_and_hub_images_and_keeps_other_jobs(self):
        folder,image=self.job('one','chat');other,_=self.job('two','other');video,_=self.job('video','chat',category='video')
        root=self.images.directory/'hub-image-one';root.mkdir();(root/'out.png').write_bytes(b'hub private image')
        self.images.jobs=[{'id':'hub-image-one','request':{'conversationId':'chat'},'status':'completed','outputs':[str(root/'out.png')]}]
        self.images.conversations['chat']={'title':'Private image'}
        request=self.images.directory/'hub-image-one.json';request.write_text('{}')
        result=self.media.delete('chat')
        self.assertEqual(result['imageJobs'],2);self.assertFalse(folder.exists());self.assertFalse(root.exists());self.assertFalse(request.exists())
        self.assertTrue(other.exists());self.assertTrue(video.exists());self.assertEqual(self.images.jobs,[])
        self.assertEqual(self.media.delete('chat')['imageJobs'],0)

    def test_active_image_prevents_all_deletion(self):
        folder,_=self.job('one','chat',status='running')
        with self.assertRaisesRegex(ValueError,'Stop image'):self.media.delete('chat')
        self.assertTrue(folder.exists())

    def test_snapshot_removed_only_when_no_other_chat_references_it(self):
        folder,image=self.job('one','chat')
        root=self.data/'workspace-history/workspace-files';(root/'objects').mkdir(parents=True)
        digest=hashlib.sha256(b'private image').hexdigest();blob=root/'objects'/digest[:2]/(digest+'.blob');blob.parent.mkdir();blob.write_bytes(b'private image')
        with closing(sqlite3.connect(root/'ledger.sqlite3')) as db,db:
            db.executescript('CREATE TABLE turns(capture_id TEXT,conversation_id TEXT,kind TEXT,turn_id TEXT);CREATE TABLE files(id TEXT,capture_id TEXT,path TEXT,before_hash TEXT,after_hash TEXT);CREATE TABLE capture_assets(capture_id TEXT);')
            db.executemany('INSERT INTO turns VALUES(?,?,?,?)',[('a','chat','output','one'),('b','other','output','two')])
            db.executemany('INSERT INTO files VALUES(?,?,?,?,?)',[('a','a',str(image),None,digest),('b','b','another-image.png',None,digest)])
        self.media.delete('chat');self.assertTrue(blob.exists())
        self.job('two','other')
        self.media.delete('other');self.assertFalse(blob.exists())

    def test_malicious_job_id_and_link_cannot_delete_outside_managed_storage(self):
        outside=self.data/'keep';outside.mkdir();(outside/'image.png').write_text('keep')
        with closing(sqlite3.connect(self.data/'studio/jobs.sqlite3')) as db,db:db.execute('INSERT INTO jobs VALUES(?,?)',('../keep',json.dumps({'category':'image','request':{'conversationId':'chat'},'status':'completed'})))
        with self.assertRaises(ValueError):self.media.delete('chat')
        self.assertTrue((outside/'image.png').exists())
        with self.assertRaises(ValueError):confined(outside,self.data/'studio')

    def test_echo_image_archive_and_cold_shard_are_removed_only_for_the_deleted_chat(self):
        root=Path(self.temp.name)/'installation';directory=root/'echo/archives';directory.mkdir(parents=True)
        def shard(chat):
            return directory/(chat+'-'+hashlib.sha1(chat.encode()).hexdigest()[:12]+'.db')
        target=shard('chat');target.write_bytes(b'private archived image bytes')
        cold=target.with_name(target.stem+'-cold.db');cold.write_bytes(b'older archived image bytes')
        other=shard('other');other.write_bytes(b'other chat image')
        ChatMedia(self.data,self.images,root).delete('chat')
        self.assertFalse(target.exists());self.assertFalse(cold.exists());self.assertTrue(other.exists())

if __name__=='__main__':unittest.main()
