import unittest
from unittest.mock import patch
from native_echo_backend import NativeBackend


class NativeChatFormat(unittest.TestCase):
    def test_requested_schema_reaches_native_decoder(self):
        backend=NativeBackend('unused','unused')
        format={'type':'json_schema','json_schema':{'name':'action','schema':{
            'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}}
        with patch('native_echo_backend.request',return_value={'choices':[]}) as request:
            backend.chat([{'role':'user','content':'inspect the repository'}],response_format=format)
        self.assertEqual(request.call_args.args[2]['response_format'],format)

    def test_normal_chat_does_not_force_a_json_schema(self):
        backend=NativeBackend('unused','unused')
        with patch('native_echo_backend.request',return_value={'choices':[]}) as request:
            backend.chat([{'role':'user','content':'say hello'}])
        self.assertNotIn('response_format',request.call_args.args[2])


if __name__=='__main__': unittest.main()
