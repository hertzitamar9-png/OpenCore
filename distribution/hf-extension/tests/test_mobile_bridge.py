import unittest
from mobile_bridge import MobileBridge


class MobileBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = MobileBridge()
        self.sessions = {device:self.bridge.open_session(device)['session'] for device in ('phone-a','phone-b','a','b')}

    def send(self, device, value): self.bridge.enqueue(device, {'session':self.sessions[device],**value})
    def result(self, device, value, devices): self.bridge.result({'session':self.sessions[device],'device':device,**value}, devices)
    def event(self, device, value, devices): self.bridge.event({'session':self.sessions[device],'device':device,**value}, devices)
    def updates(self, device): return self.bridge.updates(device,self.sessions[device])

    def test_commands_and_results_are_scoped_to_the_paired_device(self):
        self.send('phone-a', {'id':'call-1','kind':'invoke','command':'get_snapshot','args':{},'device':'phone-b'})
        tasks = self.bridge.take({'phone-a','phone-b'})['tasks']
        self.assertEqual(tasks[0]['device'], 'phone-a')
        with self.assertRaises(ValueError): self.result('phone-b',{'id':'call-1','ok':True,'value':'private'}, {'phone-a','phone-b'})
        self.result('phone-a',{'id':'call-1','ok':True,'value':{'chats':2}}, {'phone-a','phone-b'})
        self.assertEqual(self.updates('phone-b')['items'], [])
        self.assertEqual(self.updates('phone-a')['items'][0]['value'], {'chats':2})

    def test_untrusted_plugin_and_internal_hub_routes_are_rejected(self):
        for payload in [
            {'id':'a','kind':'invoke','command':'plugin:shell|execute','args':{}},
            {'id':'b','kind':'hub','path':'/api/mobile/host/poll','method':'GET'},
            {'id':'c','kind':'hub','path':'/api/remote/pairing','method':'POST'},
            {'id':'d','kind':'hub','path':'http://example.com','method':'GET'},
            {'id':'e','kind':'invoke','command':'../bad','args':{}},
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError): self.send('phone-a',payload)

    def test_subscribed_events_and_streamed_results_stay_in_order(self):
        self.send('a',{'id':'listen','kind':'invoke','command':'plugin:event|listen','args':{'handler':17,'event':'opencore-generation'}})
        self.bridge.take({'a'})
        self.event('a',{'handler':17,'payload':{'payload':'one'}},{'a'})
        with self.assertRaises(ValueError): self.event('b',{'handler':17,'payload':'leak'},{'a','b'})
        self.result('a',{'id':'listen','ok':True,'value':44},{'a'})
        self.send('a',{'id':'stream','kind':'hub','path':'/api/private/send','method':'POST','body':'{}'})
        self.bridge.take({'a'})
        for phase, extra in [('start',{'status':200}),('chunk',{'data':'part 1'}),('chunk',{'data':'part 2'}),('end',{})]:
            self.result('a',{'id':'stream','phase':phase,**extra},{'a'})
        items=self.updates('a')['items']
        self.assertEqual([item['type'] for item in items],['event','result','stream-start','stream-chunk','stream-chunk','stream-end'])
        self.assertEqual([item.get('data') for item in items[-3:-1]],['part 1','part 2'])

    def test_revocation_forgets_queued_data_and_pending_requests(self):
        self.send('a',{'id':'one','kind':'invoke','command':'list_conversations','args':{}})
        self.bridge.forget('a')
        self.assertEqual(self.bridge.take({'a'})['tasks'],[])
        with self.assertRaises(ValueError): self.updates('a')
        with self.assertRaises(ValueError): self.result('a',{'id':'one','value':'private'},{'a'})

    def test_reopening_a_phone_session_discards_old_events_and_commands(self):
        self.send('a',{'id':'one','kind':'invoke','command':'list_conversations','args':{}})
        old=self.sessions['a'];self.sessions['a']=self.bridge.open_session('a')['session']
        self.assertEqual(self.bridge.take({'a'})['tasks'],[])
        with self.assertRaises(ValueError): self.bridge.updates('a',old)
        with self.assertRaises(ValueError): self.bridge.enqueue('a',{'session':old,'id':'two','kind':'invoke','command':'get_snapshot','args':{}})


if __name__ == '__main__': unittest.main()
