import base64
from pathlib import Path
import tempfile
import unittest
from echo_server import ArchiveSet


class ConversationDeletionTests(unittest.TestCase):
    def test_pooled_archive_releases_image_bytes_and_only_the_chosen_conversation(self):
        with tempfile.TemporaryDirectory() as temporary:
            pool=ArchiveSet(Path(temporary),60)
            self.addCleanup(pool.close)
            archive=pool.get('chat')
            archive.record_source_event({'role':'user','content':'data:image/png;base64,'+base64.b64encode(b'private image').decode()},'chat')
            self.assertEqual(archive.db.execute('SELECT COUNT(*) FROM source_assets').fetchone()[0],1)
            other=pool.get('other');other.append('keep another chat','other')
            pool.delete('chat')
            self.assertFalse(pool.path_for('chat').exists())
            self.assertTrue(pool.path_for('other').exists())
            self.assertNotIn('chat',pool._open)
            self.assertTrue(other.recent_pages('other',1))
            pool.close()

if __name__=='__main__':unittest.main()
