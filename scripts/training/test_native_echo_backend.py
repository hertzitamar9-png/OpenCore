import unittest
from unittest.mock import patch
from native_echo_backend import NativeBackend


class NativeChatFormat(unittest.TestCase):
    def test_runtime_profile_explicitly_controls_kv_residency_and_cpu_threads(self):
        backend=NativeBackend('home','folder')
        build=getattr(backend,'server_args',None)
        self.assertTrue(callable(build),'runtime profile cannot yet be qualified explicitly')
        original=build()
        self.assertIn('--no-kv-offload',original)
        self.assertEqual(original[original.index('-t')+1],'1')
        optimized=NativeBackend('home','folder',kv_offload=True,threads=4).server_args()
        self.assertNotIn('--no-kv-offload',optimized)
        self.assertEqual(optimized[optimized.index('-t')+1],'4')
        for flag in ('-m','-c','-ngl','--cache-type-k','--cache-type-v'):
            self.assertEqual(original[original.index(flag)+1],optimized[optimized.index(flag)+1])
        with self.assertRaises(ValueError): NativeBackend('home','folder',threads=0)

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
