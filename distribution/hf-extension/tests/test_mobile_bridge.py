import unittest
import tempfile
import concurrent.futures
import threading
from unittest.mock import patch
from pathlib import Path
from mobile_bridge import MobileBridge


class MobileBridgeTests(unittest.TestCase):
    def test_idle_host_waits_and_a_phone_command_wakes_it_immediately(self):
        bridge=MobileBridge();devices=set();started=threading.Event()
        def wait():
            started.set();return bridge.take(lambda:devices.copy(),wait=1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(wait);self.assertTrue(started.wait(.5))
            devices.add('phone');session=bridge.open_session('phone')['session']
            bridge.enqueue('phone',{'session':session,'id':'read','kind':'invoke','command':'get_snapshot','args':{}})
            result=future.result(timeout=1)
        self.assertTrue(result['longPoll'])
        self.assertEqual(result['tasks'][0]['command'],'get_snapshot')

    def test_paired_but_offline_phones_do_not_keep_the_desktop_polling_rapidly(self):
        with patch('mobile_bridge.time.monotonic',return_value=100):
            bridge=MobileBridge(); session=bridge.open_session('phone')['session']
            self.assertTrue(bridge.take({'phone'})['active'])
        with patch('mobile_bridge.time.monotonic',return_value=140):
            self.assertFalse(bridge.take({'phone'})['active'])
            bridge.updates('phone',session)
            self.assertTrue(bridge.take({'phone'})['active'])
            bridge.forget('phone')
            self.assertFalse(bridge.take({'phone'})['active'])

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

    def test_phone_navigation_is_private_while_model_settings_remain_shared(self):
        key='opencore.ui.v1.navigation.view'
        self.bridge.publish_preferences({key:'"models"','opencore.reasoning-effort.v1':'high'})
        self.send('a',{'id':'page','kind':'preference','action':'set','key':key,'value':'"conversations"'})
        self.assertEqual(self.bridge.take({'a'})['tasks'],[])
        self.assertEqual(self.bridge.preferences.get(key),None)
        self.assertEqual(self.bridge.phone_preferences('a',self.sessions['a'])['values'][key],'"conversations"')
        self.assertNotIn(key,self.bridge.phone_preferences('b',self.sessions['b'])['values'])
        self.assertEqual(self.bridge.phone_preferences('a',self.sessions['a'])['values']['opencore.reasoning-effort.v1'],'high')

    def test_phone_presentation_survives_reconnection_and_pc_restart_but_not_revocation(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'remote-ui.json';bridge=MobileBridge(file)
            session=bridge.open_session('a')['session'];key='opencore.ui.v1.navigation.view'
            bridge.enqueue('a',{'session':session,'id':'page','kind':'preference','action':'set','key':key,'value':'"models"'})
            session=bridge.open_session('a')['session']
            self.assertEqual(bridge.phone_preferences('a',session)['values'][key],'"models"')
            restarted=MobileBridge(file);session=restarted.open_session('a')['session']
            self.assertEqual(restarted.phone_preferences('a',session)['values'][key],'"models"')
            restarted.forget('a')
            self.assertEqual(MobileBridge(file).device_preferences,{})


if __name__ == '__main__': unittest.main()
