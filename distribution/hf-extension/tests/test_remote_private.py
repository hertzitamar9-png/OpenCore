import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remote_access import RemoteAccess
from private_chat import PrivateChats
from pc_window import png

ROOT = Path(__file__).resolve().parents[3]


class Surface:
    def frame(self): return png(1, 1, b'\0\0\xff\0'), 1, 1
    def input(self, value): self.last = value


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = RemoteAccess(self.root, self.root, surface=Surface())

    def tearDown(self): self.remote.close(); self.temp.cleanup()

    def test_address_discovery_only_returns_private_pc_addresses(self):
        from remote_access import local_addresses
        sample = ('Ethernet adapter vEthernet:\n'
                  '   IPv4 Address. . . . . . . . . . . : 172.20.1.5(Preferred)\n'
                  'Ethernet adapter Ethernet:\n'
                  '   IPv4 Address. . . . . . . . . . . : 192.168.1.33(Preferred)\n'
                  'Default Gateway . . . . . . . . . : 192.168.1.1\n'
                  'Wireless LAN adapter Wi-Fi:\n'
                  '   IPv4 Address. . . . . . . . . . . : 10.10.0.4\n'
                  'Tunnel adapter Private VPN:\n'
                  '   IPv4 Address. . . . . . . . . . . : 100.101.102.103\n'
                  'IPv4 Address. . . . . . . . . . . : 127.0.0.1\n'
                  'IPv4 Address. . . . . . . . . . . : 8.8.8.8\n')
        with patch('remote_access.subprocess.run', return_value=type('Result',(),{'returncode':0,'stdout':sample})()):
            self.assertEqual(local_addresses(), [{'address':'10.10.0.4','interface':'Wi-Fi'}, {'address':'192.168.1.33','interface':'Ethernet'}, {'address':'100.101.102.103','interface':'Private VPN'}, {'address':'172.20.1.5','interface':'vEthernet'}])

    def test_pairing_qr_contains_the_exact_one_time_code_and_no_extra_data(self):
        self.remote.server = type('Server',(),{'server_port':12345})()
        self.remote.fingerprint = 'ab' * 32
        value = self.remote.pairing('192.168.1.33')
        self.assertIn('pairingText', value)
        self.assertGreater(len(value['qrRows']), 20)
        self.assertTrue(all(set(row) <= {'0','1'} and len(row)==len(value['qrRows']) for row in value['qrRows']))
        # Real offline QR decode on developer machines when OpenCV is installed.
        try: import cv2, numpy as np
        except ImportError: pass
        else:
            pixels = np.array([[0 if bit=='1' else 255 for bit in row] for row in value['qrRows']], dtype=np.uint8)
            decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(np.kron(pixels, np.ones((8,8), dtype=np.uint8)))
            self.assertEqual(decoded, value['pairingText'])
        self.remote.server = None

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK is needed to exercise the Android QR decoder')
    def test_phone_decoder_reads_the_pc_pairing_code(self):
        from remote_access import pairing_qr
        expected = 'opencore://pair?url=https%3A%2F%2F192.168.1.33%3A12345&pin=' + 'ab'*32 + '&code=fixture-one-use-code'
        rows = pairing_qr(expected)
        scale = 6
        width = len(rows)*scale
        pixels = bytearray()
        for row in rows:
            expanded = bytes(color for bit in row for color in ([0 if bit == '1' else 255]*scale))
            for _ in range(scale): pixels.extend(expanded)
        raw = self.root/'qr.raw'; raw.write_bytes(pixels)
        source = self.root/'DecodeFixture.java'
        source.write_text('''import java.nio.file.*;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
public class DecodeFixture {
  public static void main(String[] args) throws Exception {
    int size = Integer.parseInt(args[1]);
    byte[] pixels = Files.readAllBytes(Paths.get(args[0]));
    LuminanceSource image = new PlanarYUVLuminanceSource(pixels,size,size,0,0,size,size,false);
    System.out.print(new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(image))).getText());
  }
}''', encoding='utf-8')
        jar = ROOT/'android-remote/vendor/zxing-core-3.5.3.jar'
        subprocess.run(['javac','-encoding','UTF-8','-cp',str(jar),str(source)],check=True,capture_output=True)
        decoded = subprocess.run(['java','-cp',str(self.root)+os.pathsep+str(jar),'DecodeFixture',str(raw),str(width)],check=True,capture_output=True,text=True)
        self.assertEqual(decoded.stdout, expected)

    def test_phone_connection_status_is_ephemeral_and_follows_authorized_activity(self):
        self.remote.pair_code, self.remote.pair_until = 'one-time', time.monotonic()+120
        phone = self.remote.pair('one-time','Test phone','127.0.0.1')
        self.assertFalse(self.remote.status()['devices'][0]['connected'])
        self.assertEqual(self.remote.authorize('Bearer '+phone['token']),phone['deviceId'])
        self.assertTrue(self.remote.status()['devices'][0]['connected'])
        self.remote.last_seen[phone['deviceId']] = time.monotonic()-20
        self.assertFalse(self.remote.status()['devices'][0]['connected'])
        self.assertNotIn('last_seen', self.remote.devices_path.read_text())

    @unittest.skipUnless(os.name == 'nt', 'Windows owns the local TLS key')
    def test_pairing_name_and_port_survive_restart_and_disabling_access(self):
        with patch.dict(os.environ, {'COMPUTERNAME': 'Original PC'}):
            first = RemoteAccess(self.root, self.root, surface=Surface())
            second = None
            try:
                self.assertEqual(first.status()['pcName'], 'Original PC')
                port = first.start({'bind':'127.0.0.1'})['port']
                first.pair_code, first.pair_until = 'one-time', time.monotonic()+120
                paired = first.pair('one-time', 'Test phone', '127.0.0.1')
                self.assertEqual(paired['name'], 'Original PC')
                first.rename('Studio PC')
                first.shutdown()
                second = RemoteAccess(self.root, self.root, surface=Surface())
                second.restore()
                self.assertTrue(second.status()['enabled'])
                self.assertEqual(second.status()['port'], port)
                self.assertEqual(second.status()['pcName'], 'Studio PC')
                self.assertEqual(second.authorize('Bearer '+paired['token']), paired['deviceId'])
                second.close()
                self.assertFalse(second.status()['enabled'])
                self.assertEqual(second.start({})['port'], port)
                self.assertEqual(second.authorize('Bearer '+paired['token']), paired['deviceId'])
                second.revoke(paired['deviceId'])
                self.assertIsNone(second.authorize('Bearer '+paired['token']))
                for invalid in ('', 'x'*81, 'bad\nname'):
                    with self.assertRaises(ValueError): second.rename(invalid)
            finally:
                first.shutdown()
                if second: second.shutdown()

    def test_default_off_single_use_expiring_pairing_and_revocation(self):
        self.assertFalse(self.remote.status()['enabled'])
        self.remote.pair_code, self.remote.pair_until = 'one-time-code', time.monotonic() + 120
        paired = self.remote.pair('one-time-code', 'Test phone', '127.0.0.1')
        header = 'Bearer ' + paired['token']
        self.assertEqual(self.remote.authorize(header), paired['deviceId'])
        self.assertNotIn(paired['token'], self.remote.devices_path.read_text())
        with self.assertRaises(PermissionError): self.remote.pair('one-time-code', 'Replay', '127.0.0.1')
        self.remote.revoke(paired['deviceId'])
        self.assertIsNone(self.remote.authorize(header))
        self.remote.pair_code, self.remote.pair_until = 'expired', time.monotonic() - 1
        with self.assertRaises(PermissionError): self.remote.pair('expired', 'Phone', '127.0.0.1')

    def test_media_cannot_escape_generated_roots_or_share_model_and_secret_directories(self):
        (self.root / 'artifacts').mkdir()
        (self.root / 'artifacts/image.png').write_bytes(b'image')
        self.assertEqual(self.remote.media_path('artifacts', 'image.png').read_bytes(), b'image')
        for root, name in [('secrets', 'anything'), ('artifacts', '../secret'), ('artifacts', 'C:\\secret'), ('artifacts', '/absolute')]:
            with self.subTest(root=root, name=name), self.assertRaises(ValueError): self.remote.media_path(root, name)

    @unittest.skipUnless(os.name == 'nt', 'Windows owns the local TLS key')
    def test_actual_tls_pair_native_bridge_and_origin_rejection(self):
        status = self.remote.start({'bind': '127.0.0.1'})
        port = status['port']
        self.assertNotIn(port, (8840, 8841, 8842, 8811, 8812, 8813, 8850, 8860))
        self.assertTrue(status['enabled'])
        from urllib.parse import urlsplit, parse_qs
        pairing = self.remote.pairing('127.0.0.1')
        code = parse_qs(urlsplit(pairing['pairingText']).query)['code'][0]
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); tls.check_hostname = False; tls.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.connect()
        self.assertEqual(hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest(), status['fingerprint'])
        connection.request('POST', '/pair', json.dumps({'code': code, 'name': 'Fixture phone'}), {'Content-Type': 'application/json'})
        response = connection.getresponse(); self.assertEqual(response.status, 200); paired = json.loads(response.read()); connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('POST','/session','{}',{'Authorization':'Bearer '+paired['token'],'Content-Type':'application/json'})
        response = connection.getresponse(); self.assertEqual(response.status,200); session=json.loads(response.read())['session']; connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        command={'session':session,'id':'snapshot','kind':'invoke','command':'get_snapshot','args':{}}
        connection.request('POST','/request',json.dumps(command),{'Authorization':'Bearer '+paired['token'],'Content-Type':'application/json'})
        response=connection.getresponse(); self.assertEqual(response.status,200);response.read();connection.close()
        tasks=self.remote.mobile.take({paired['deviceId']})['tasks'];self.assertEqual(tasks[0]['command'],'get_snapshot')
        self.remote.mobile.result({'device':paired['deviceId'],'session':session,'id':'snapshot','ok':True,'value':{'samePc':True}},{paired['deviceId']})
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('POST','/updates',json.dumps({'session':session}),{'Authorization':'Bearer '+paired['token'],'Content-Type':'application/json'})
        response=connection.getresponse();self.assertEqual(response.status,200);self.assertTrue(json.loads(response.read())['items'][0]['value']['samePc']);connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('GET', '/info', headers={'Authorization': 'Bearer ' + paired['token']})
        response = connection.getresponse(); self.assertEqual(response.status, 200); self.assertEqual(json.loads(response.read())['name'], self.remote.pc_name); connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('GET', '/info')
        response = connection.getresponse(); self.assertEqual(response.status, 403); response.read(); connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('POST', '/rename', json.dumps({'name':'New PC name'}), {'Authorization':'Bearer '+paired['token'], 'Content-Type':'application/json'})
        response = connection.getresponse(); self.assertEqual(response.status, 200); self.assertEqual(json.loads(response.read())['name'],'New PC name'); connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('GET', '/info', headers={'Authorization': 'Bearer ' + paired['token'], 'Origin': 'https://evil.invalid'})
        response = connection.getresponse(); self.assertEqual(response.status, 403); response.read(); connection.close()
        connection = http.client.HTTPSConnection('127.0.0.1', port, context=tls)
        connection.request('POST', '/unpair', '{}', {'Authorization':'Bearer '+paired['token'], 'Content-Type':'application/json'})
        response = connection.getresponse(); self.assertEqual(response.status, 200); self.assertTrue(json.loads(response.read())['removed']); connection.close()
        self.assertIsNone(self.remote.authorize('Bearer '+paired['token']))


class LocalModel(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        self.server.payloads.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        value = b'data: {"choices":[{"delta":{"content":"PRIVATE_TEST_REPLY"}}]}\n\ndata: [DONE]\n\n'
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(value))); self.end_headers(); self.wfile.write(value)


class PrivateTests(unittest.TestCase):
    def test_native_backend_uses_the_actual_running_profile_port(self):
        from hf_runtime import RuntimeManager
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeManager(directory, ROOT/'src-tauri/resources')
            for profile,port in [('doucode',8840),('nanbeige-bf16',8860),('dualcore-kv',8850),('echo',8811),('attached-local',59173)]:
                with self.subTest(profile=profile):
                    self.assertEqual(manager.private_backend({'profile':profile,'status':'running','backendPort':port}), ('http://127.0.0.1:'+str(port),None))
            for snapshot in [None,{'profile':'api:cloud','status':'running','backendPort':443},{'status':'running','backendPort':8813},{'status':'off','backendPort':8811}]:
                with self.assertRaises(ValueError): manager.private_backend(snapshot)

    def test_close_clears_an_unstarted_stream_reservation_and_archive(self):
        private = PrivateChats(ROOT/'src-tauri/resources')
        try:
            identity = private.open()['sessionId']; session = private.sessions[identity]
            stream = private.send(identity,'never sent to the model')
            stream.close()
            self.assertFalse(session['busy'])
            private.close(identity)
            self.assertIsNone(session['archive']._db)
            self.assertTrue(private.generation.acquire(blocking=False));private.generation.release()
        finally: private.shutdown()

    def test_close_aborts_a_real_request_before_response_headers(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(LocalModel):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));entered.set();release.wait(5)
        server = ThreadingHTTPServer(('127.0.0.1',0),Slow)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        private=PrivateChats(ROOT/'src-tauri/resources',backend=lambda:('http://127.0.0.1:'+str(server.server_port),None))
        try:
            identity=private.open()['sessionId'];session=private.sessions[identity]
            worker=threading.Thread(target=lambda:list(private.send(identity,'cancel during prefill')),daemon=True);worker.start()
            self.assertTrue(entered.wait(2));private.close(identity);worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(session['archive']._db)
            self.assertTrue(private.generation.acquire(blocking=False));private.generation.release()
        finally: release.set();private.shutdown();server.shutdown();server.server_close()

    def test_real_echo_retrieval_is_in_memory_and_close_removes_the_session(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), LocalModel); server.payloads = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        private = PrivateChats(ROOT / 'src-tauri/resources', backend=lambda: ('http://127.0.0.1:' + str(server.server_port), None))
        try:
            identity = private.open()['sessionId']
            archive = private.sessions[identity]['archive']
            self.assertEqual(archive.db.execute('PRAGMA database_list').fetchone()[2], '')
            with patch.dict(os.environ, {'HTTP_PROXY':'http://outside.invalid:9999', 'http_proxy':'http://outside.invalid:9999', 'NO_PROXY':'', 'no_proxy':''}):
                self.assertIn(b'PRIVATE_TEST_REPLY', b''.join(private.send(identity, 'Secret marker octopus-731')))
            private.sessions[identity]['messages'].clear()
            b''.join(private.send(identity, 'What was the octopus-731 marker?'))
            self.assertIn('Secret marker octopus-731', json.dumps(server.payloads[-1]))
            self.assertFalse(server.payloads[-1]['cache_prompt'])
            private.close(identity)
            self.assertNotIn(identity, private.sessions)
            with self.assertRaises(ValueError): private.send(identity, 'cannot reuse')
        finally: private.shutdown(); server.shutdown(); server.server_close()

    def test_external_backend_is_rejected_before_any_request(self):
        private = PrivateChats(ROOT / 'src-tauri/resources', backend=lambda: ('https://outside.invalid', 'not-to-be-sent'))
        try:
            identity = private.open()['sessionId']
            self.assertIn(b'"error"', b''.join(private.send(identity, 'private prompt')))
            self.assertEqual(private.sessions[identity]['bytes'], 0)
        finally: private.shutdown()


if __name__ == '__main__': unittest.main()
