import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hf_download import HubClient


class SearchTests(unittest.TestCase):
    def client(self):
        client = HubClient(lambda: 'hf_privateToken')
        client.json = Mock(return_value=[
            {'id': 'owner/Falcon-Instruct', 'downloads': 12, 'likes': 2, 'pipeline_tag': 'text-generation'},
            {'id': 'owner/A-falcon', 'downloads': 9},
            {'id': '../bad', 'downloads': 100},
        ])
        return client

    def test_one_letter_prefix_matches_model_name_and_public_search_is_anonymous(self):
        client = self.client()
        result = client.search_models({'query': 'F', 'match': 'prefix'})
        self.assertEqual([m['repository'] for m in result['models']], ['owner/Falcon-Instruct'])
        _, kwargs = client.json.call_args
        self.assertTrue(kwargs['anonymous_first'])
        self.assertEqual(kwargs['token'], '')
        self.assertNotIn('hf_privateToken', str(result))

    def test_words_filters_and_private_search_are_explicit(self):
        client = self.client()
        client.search_models({'query': 'falcon instruct', 'match': 'words', 'task': 'text-generation',
                              'library': 'gguf', 'author': 'owner', 'license': 'apache-2.0',
                              'sort': 'likes', 'private': True, 'limit': 40})
        args, kwargs = client.json.call_args
        query = parse_qs(urlsplit(args[0]).query)
        self.assertEqual(query['pipeline_tag'], ['text-generation'])
        self.assertEqual(query['sort'], ['likes'])
        self.assertEqual(query['author'], ['owner'])
        self.assertFalse(kwargs['anonymous_first'])
        self.assertEqual(kwargs['token'], 'hf_privateToken')

    def test_invalid_inputs_do_not_issue_a_request(self):
        for options in ({'query': 'x', 'sort': 'password'}, {'query': 'x', 'author': '../bad'},
                        {'query': 'x', 'limit': 999999}, {'query': 'x\nsecret'},
                        {'query': 'x', 'task': 'https://evil.invalid'}, {'query': 'x', 'private': 'true'}):
            client = self.client()
            with self.subTest(options=options), self.assertRaises(ValueError):
                client.search_models(options)
            client.json.assert_not_called()

    def test_private_search_requires_a_saved_token(self):
        client = HubClient()
        client.json = Mock()
        with self.assertRaisesRegex(ValueError, 'token'):
            client.search_models({'query': 'F', 'private': True})
        client.json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
