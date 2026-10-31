from contextlib import closing
from pathlib import Path
import sqlite3,sys,tempfile,threading,time,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hf_chat_media as media

class ProjectCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.database=self.root/'control-center.sqlite3'
        self.source=self.root/'real-project';self.source.mkdir();(self.source/'index.html').write_text('keep source files',encoding='utf-8')
        with closing(sqlite3.connect(self.database)) as db,db:
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,folder_path TEXT)')
            db.execute('CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,client TEXT,project_id TEXT)')
            db.executemany('INSERT INTO projects VALUES(?,?,?)',[(key,key,str(self.source)) for key in ('empty','shared','other')])
            db.execute("INSERT INTO conversations VALUES('new-chat','New chat','Codex','shared')")

    def test_deletes_only_requested_empty_groups_and_keeps_project_files(self):
        self.assertTrue(callable(getattr(media,'delete_empty_projects',None)))
        result=media.delete_empty_projects(self.root,['empty','shared','missing'])
        self.assertEqual(result,{'deleted':['empty']})
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual({row[0] for row in db.execute('SELECT id FROM projects')},{'shared','other'})
            self.assertEqual(db.execute('SELECT project_id FROM conversations').fetchone()[0],'shared')
        self.assertEqual((self.source/'index.html').read_text(encoding='utf-8'),'keep source files')

    def test_invalid_ids_are_rejected_before_any_group_changes(self):
        self.assertTrue(callable(getattr(media,'delete_empty_projects',None)))
        for ids in ('empty',['empty',None],[''],['x'*513]):
            with self.assertRaises(ValueError):media.delete_empty_projects(self.root,ids)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],3)

    def test_saved_pages_keep_project_ids_beyond_the_recent_native_list(self):
        page=media.saved_conversations_page(self.root)
        self.assertEqual(page['conversations'][0].get('projectId'),'shared')

    def test_a_chat_committing_during_cleanup_keeps_its_project_group(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute('BEGIN IMMEDIATE');writer.execute("INSERT INTO conversations VALUES('arriving','Arriving','OpenCore','empty')")
            results=[];started=threading.Event()
            def cleanup():
                started.set();results.append(media.delete_empty_projects(self.root,['empty']))
            worker=threading.Thread(target=cleanup);worker.start();started.wait(1)
            time.sleep(.03);writer.commit();worker.join(3)
            self.assertFalse(worker.is_alive());self.assertEqual(results,[{'deleted':[]}])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT project_id FROM conversations WHERE id='arriving'").fetchone()[0],'empty')
