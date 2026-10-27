import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hf_download import HubClient, HubError


class SearchTests(unittest.TestCase):
    def client(self):
        client = HubClient(lambda: 'hf_privateToken')
        client.json_page = Mock(return_value=([
            {'id': 'owner/Falcon-Instruct', 'downloads': 12, 'likes': 2, 'pipeline_tag': 'text-generation'},
            {'id': 'owner/A-falcon', 'downloads': 9},
            {'id': '../bad', 'downloads': 100},
        ], None))
        return client

    def test_one_letter_prefix_matches_model_name_and_public_search_is_anonymous(self):
        client = self.client()
        result = client.search_models({'query': 'F', 'match': 'prefix'})
        self.assertEqual([m['repository'] for m in result['models']], ['owner/Falcon-Instruct'])
        _, kwargs = client.json_page.call_args
        self.assertTrue(kwargs['anonymous_first'])
        self.assertEqual(kwargs['token'], '')
        self.assertNotIn('hf_privateToken', str(result))

    def test_words_filters_and_private_search_are_explicit(self):
        client = self.client()
        client.search_models({'query': 'falcon instruct', 'match': 'words', 'task': 'text-generation',
                              'library': 'gguf', 'author': 'owner', 'license': 'apache-2.0',
                              'sort': 'likes', 'private': True, 'limit': 40})
        args, kwargs = client.json_page.call_args
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
            client.json_page.assert_not_called()

    def test_private_search_requires_a_saved_token(self):
        client = HubClient()
        client.json_page = Mock()
        with self.assertRaisesRegex(ValueError, 'token'):
            client.search_models({'query': 'F', 'private': True})
        client.json_page.assert_not_called()

    def test_100_matching_results_per_page_across_upstream_pages(self):
        client = self.client()
        pages = {
            None: ([{'id': f'org/{"H" if i % 2 == 0 else "A"}{i:03d}'} for i in range(100)], 'cursor-one'),
            'cursor-one': ([{'id': f'org/H{i:03d}'} for i in range(100, 200)], 'cursor-two'),
            'cursor-two': ([{'id': f'org/H{i:03d}'} for i in range(200, 230)], None),
        }
        def page(path, **kwargs):
            return pages.get(parse_qs(urlsplit(path).query).get('cursor', [None])[0])
        client.json_page.side_effect = page
        first = client.search_models({'query': 'h', 'match': 'prefix', 'limit': 100})
        second = client.search_models({'query': 'h', 'match': 'prefix', 'limit': 100, 'cursor': first['nextCursor']})
        all_ids = [model['repository'] for model in first['models'] + second['models']]
        self.assertEqual(len(first['models']), 100)
        self.assertEqual(len(second['models']), 80)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertIsNone(second['nextCursor'])

    def test_cursor_cannot_be_reused_with_different_filters_or_forged(self):
        client = self.client()
        client.json_page.return_value = ([{'id': f'owner/H{i}'} for i in range(100)], 'cursor-one')
        first = client.search_models({'query': 'h', 'limit': 100})
        for value in ({'query': 'other', 'cursor': first['nextCursor']}, {'query': 'h', 'cursor': first['nextCursor'] + 'x'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.search_models(value)

    def test_untrusted_next_link_is_never_followed(self):
        client = HubClient()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'[]'
        response.headers = {'Link': '<https://evil.example/api/models?cursor=one>; rel="next"'}
        client.open = Mock(return_value=response)
        with self.assertRaises(HubError):
            client.json_page('/api/models?limit=100', token='', anonymous_first=True)
        self.assertEqual(client.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
