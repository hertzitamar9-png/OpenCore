"""Opt-in direct TLS phone connection. No relay, cloud account or outbound service."""
from collections import OrderedDict
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
import mimetypes
import re
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import shutil
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit
from xml.sax.saxutils import escape as _xml_escape

from hf_download import atomic_json, managed_path, normalized_path
from mobile_bridge import MobileBridge


def local_addresses():
    """Find this PC's private IPv4 addresses without contacting another host."""
    try:
        result = subprocess.run(['ipconfig.exe'], capture_output=True, text=True, errors='replace', timeout=4,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: return []
    except (OSError, subprocess.TimeoutExpired): return []
    ranges = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10'))
    found, interface = [], ''
    for line in result.stdout.splitlines():
        if line.strip() and not line[0].isspace() and line.rstrip().endswith(':'):
            interface = re.sub(r'^(?:(?:Wireless LAN|Ethernet|Tunnel) adapter)\s+', '', line.strip().rstrip(':'), flags=re.I)
            continue
        match = re.search(r'\bIPv4\b[^:]*:\s*(\d{1,3}(?:\.\d{1,3}){3})', line, re.I)
        if not match: continue
        try: address = ipaddress.ip_address(match.group(1))
        except ValueError: continue
        if any(address in network for network in ranges) and not any(item['address'] == str(address) for item in found):
            found.append({'address': str(address), 'interface': interface[:100] or 'Network adapter'})
    def priority(item):
        name = item['interface']
        if re.search(r'\bwi[ -]?fi\b|wireless', name, re.I): return 0
        if re.match(r'^ethernet(?:\s+\d+)?$', name, re.I): return 1
        if re.search(r'vethernet|virtual|hyper-v', name, re.I): return 3
        return 2
    found.sort(key=priority)
    return found[:16]


def pairing_qr(text):
    import qrcode
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(text)
    code.make(fit=True)
    return [''.join('1' if bit else '0' for bit in row) for row in code.get_matrix()]


def safe_windows_name(value):
    return isinstance(value, str) and 0 < len(value) <= 120 and value not in ('.', '..') and not value.endswith(('.', ' ')) \
        and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value) \
        and not re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', value)


class UpnpError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(detail or ('UPnP error ' + str(code)))


class InternetGateway:
    """Best-effort UPnP IGD mapping for the direct TLS phone server."""
    SSDP = ('239.255.255.250', 1900)
    TYPES = (
        'urn:schemas-upnp-org:service:WANIPConnection:2',
        'urn:schemas-upnp-org:service:WANIPConnection:1',
        'urn:schemas-upnp-org:service:WANPPPConnection:2',
        'urn:schemas-upnp-org:service:WANPPPConnection:1',
    )

    def __init__(self):
        self.lock = threading.RLock()
        self.control_url = ''
        self.service_type = ''
        self.local_ip = ''
        self.external_ip = ''
        self.external_port = None
        self.internal_port = None
        self.mapped = False
        self.last_error = ''
        self.checked_at = 0.0

    def status(self):
        with self.lock:
            return {'available': bool(self.control_url and self.external_ip), 'mapped': self.mapped,
                    'externalAddress': self.external_ip, 'externalPort': self.external_port,
                    'internalAddress': self.local_ip, 'internalPort': self.internal_port,
                    'error': self.last_error, 'checkedAt': self.checked_at}

    @staticmethod
    def _ssdp_locations(local_ip, timeout=1.5):
        message = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                   'MX: 1\r\nST: ssdp:all\r\n\r\n').encode('ascii')
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((local_ip, 0))
            sock.settimeout(timeout)
            sock.sendto(message, InternetGateway.SSDP)
            deadline = time.monotonic() + timeout
            locations = []
            while time.monotonic() < deadline:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break
                text = data.decode('utf-8', 'replace')
                match = re.search(r'(?im)^LOCATION:\s*(\S+)', text)
                if match and match.group(1) not in locations:
                    locations.append(match.group(1))
            return locations
        except OSError:
            return []
        finally:
            sock.close()

    @staticmethod
    def _fetch(url):
        request = urllib.request.Request(url, headers={'User-Agent': 'OpenCore/0.3.170 UPnP'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=6) as response:
            return response.read(512 * 1024)

    def _discover(self, candidates):
        for address in candidates:
            if not isinstance(address, str):
                continue
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip.version != 4 or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            for location in self._ssdp_locations(address):
                try:
                    root = ET.fromstring(self._fetch(location))
                except (OSError, ET.ParseError, ValueError):
                    continue
                services = []
                for service in root.iter():
                    if not service.tag.endswith('service'):
                        continue
                    values = {child.tag.rsplit('}', 1)[-1]: (child.text or '').strip() for child in service}
                    stype = values.get('serviceType', '')
                    control = values.get('controlURL', '')
                    if stype in self.TYPES and control:
                        services.append((stype, urljoin(location, control)))
                if not services:
                    continue
                services.sort(key=lambda item: self.TYPES.index(item[0]))
                self.service_type, self.control_url = services[0]
                self.local_ip = address
                self.last_error = ''
                return True
        return False

    def _soap(self, action, arguments):
        if not self.control_url or not self.service_type:
            raise UpnpError(0, 'No UPnP internet gateway was found')
        body = ('<?xml version="1.0"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                '<s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>') % (
                    action, self.service_type,
                    ''.join('<%s>%s</%s>' % (key, _xml_escape(str(value)), key) for key, value in arguments.items()),
                    action)
        request = urllib.request.Request(self.control_url, data=body.encode('utf-8'), headers={
            'Content-Type': 'text/xml; charset="utf-8"',
            'SOAPAction': '"%s#%s"' % (self.service_type, action),
            'User-Agent': 'OpenCore/0.3.170 UPnP'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=8) as response:
                return response.read(512 * 1024)
        except urllib.error.HTTPError as error:
            raw = error.read(512 * 1024)
            code, detail = 0, ''
            try:
                root = ET.fromstring(raw)
                for element in root.iter():
                    name = element.tag.rsplit('}', 1)[-1]
                    if name == 'errorCode':
                        code = int((element.text or '0').strip())
                    elif name == 'errorDescription':
                        detail = (element.text or '').strip()
            except (ET.ParseError, ValueError):
                pass
            raise UpnpError(code or error.code, detail or ('UPnP request failed (%s)' % error.code)) from None
        except OSError:
            raise UpnpError(0, 'The router did not answer UPnP requests') from None

    @staticmethod
    def _value(raw, name):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return ''
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1] == name:
                return (element.text or '').strip()
        return ''

    def _external_ip(self):
        value = self._value(self._soap('GetExternalIPAddress', {}), 'NewExternalIPAddress')
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return ''
        if address.version != 4 or address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
            return ''
        return str(address)

    def _existing_mapping(self, port):
        try:
            raw = self._soap('GetSpecificPortMappingEntry', {'NewRemoteHost': '', 'NewExternalPort': str(port), 'NewProtocol': 'TCP'})
            return self._value(raw, 'NewInternalClient'), self._value(raw, 'NewInternalPort'), self._value(raw, 'NewPortMappingDescription')
        except UpnpError:
            return '', '', ''

    def _map(self, port):
        arguments = {'NewRemoteHost': '', 'NewExternalPort': str(port), 'NewProtocol': 'TCP',
                     'NewInternalPort': str(self.internal_port), 'NewInternalClient': self.local_ip,
                     'NewEnabled': '1', 'NewPortMappingDescription': 'OpenCore Remote', 'NewLeaseDuration': '0'}
        for lease in ('0', '604800'):
            arguments['NewLeaseDuration'] = lease
            try:
                self._soap('AddPortMapping', arguments)
            except UpnpError as error:
                if error.code == 725 and lease == '0':
                    continue
                if error.code == 718:
                    client, internal, description = self._existing_mapping(port)
                    if client == self.local_ip and internal == str(self.internal_port):
                        return True
                    if description.startswith('OpenCore Remote'):
                        try:
                            self._soap('DeletePortMapping', {'NewRemoteHost': '', 'NewExternalPort': str(port), 'NewProtocol': 'TCP'})
                            self._soap('AddPortMapping', arguments)
                            return self._existing_mapping(port)[0] == self.local_ip
                        except UpnpError:
                            pass
                self.last_error = str(error)
                return False
            client, internal, _ = self._existing_mapping(port)
            return client == self.local_ip and internal == str(self.internal_port)
        return False

    def refresh(self, candidates, internal_port, preferred_port):
        with self.lock:
            self.internal_port = internal_port
            if not self.control_url or not self.external_ip:
                if not self._discover(candidates):
                    self.mapped = False
                    self.last_error = 'No UPnP internet gateway was found on the router'
                    self.checked_at = time.time()
                    return self.status()
            try:
                self.external_ip = self._external_ip()
            except UpnpError as error:
                self.external_ip = ''
                self.mapped = False
                self.last_error = str(error)
                self.checked_at = time.time()
                return self.status()
            if not self.external_ip:
                self.mapped = False
                self.last_error = 'The internet provider uses a shared address, so a direct internet port cannot be opened'
                self.checked_at = time.time()
                return self.status()
            start = preferred_port if isinstance(preferred_port, int) and 1024 <= preferred_port <= 65535 else internal_port
            ports = [start] + [port for port in range(start + 1, min(65535, start + 12) + 1)]
            for port in ports:
                if self._map(port):
                    self.external_port = port
                    self.mapped = True
                    self.last_error = ''
                    self.checked_at = time.time()
                    return self.status()
            self.mapped = False
            self.checked_at = time.time()
            return self.status()

    def remove(self):
        with self.lock:
            port = self.external_port
            if self.control_url and port:
                try:
                    self._soap('DeletePortMapping', {'NewRemoteHost': '', 'NewExternalPort': str(port), 'NewProtocol': 'TCP'})
                except UpnpError:
                    pass
            self.mapped = False
            self.external_port = None
            self.checked_at = time.time()


class DuckDNS:
    """Keep a free DuckDNS name pointed at this PC's current public IPv4 address."""
    API = 'https://www.duckdns.org/update'

    def __init__(self, domain='', token=''):
        self.domain, self.token = '', ''
        self.host, self.ip, self.updated_at, self.error, self.ok = '', '', 0.0, '', False
        if domain or token:
            try: self.configure(domain, token)
            except ValueError: self.error = 'Saved DuckDNS settings are invalid'

    @property
    def configured(self):
        return bool(self.domain and self.token)

    @staticmethod
    def clean_domain(value):
        if not isinstance(value, str): raise ValueError('Enter a DuckDNS name')
        value = value.strip().lower()
        if '://' in value: value = urlsplit(value).hostname or ''
        if value.endswith('.duckdns.org'): value = value[:-len('.duckdns.org')]
        value = value.strip('.')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
            raise ValueError('Use letters, numbers and hyphens for the DuckDNS name')
        return value

    @staticmethod
    def clean_token(value):
        if not isinstance(value, str): raise ValueError('Enter the DuckDNS token')
        value = value.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value):
            raise ValueError('That does not look like a DuckDNS token')
        return value

    def configure(self, domain, token):
        self.domain = self.clean_domain(domain)
        self.token = self.clean_token(token)
        self.host = self.domain + '.duckdns.org'
        self.error, self.ok = '', False

    def clear(self):
        self.domain, self.token, self.host, self.ip = '', '', '', ''
        self.updated_at, self.error, self.ok = 0.0, '', False

    def update(self, ip):
        if not self.configured: raise ValueError('DuckDNS is not configured')
        try: ipaddress.ip_address(ip)
        except (ValueError, TypeError): raise ValueError('No public IPv4 address is available yet') from None
        query = urlencode({'domains': self.domain, 'token': self.token, 'ip': ip})
        request = urllib.request.Request(self.API + '?' + query, headers={'User-Agent': 'OpenCore remote access'})
        with urllib.request.urlopen(request, timeout=12) as response:
            answer = response.read(64).decode('ascii', 'replace').strip()
        if answer != 'OK': raise ValueError('DuckDNS rejected the update')
        self.ip, self.updated_at, self.error, self.ok = ip, time.time(), '', True

    def status(self):
        return {'configured': self.configured, 'domain': self.domain, 'host': self.host,
                'ip': self.ip, 'updatedAt': int(self.updated_at), 'error': self.error, 'ok': self.ok}


class InstantDNS:
    """Account-free dynamic DNS: create one hostname, keep the token locally and update it forever."""
    API = 'api.instantdns.io'

    def __init__(self, host='', token=''):
        self.host, self.token = '', ''
        self.ip, self.updated_at, self.error, self.ok = '', 0.0, '', False
        if host or token:
            try: self.configure(host, token)
            except ValueError: self.error = 'Saved permanent address is invalid'

    @property
    def configured(self):
        return bool(self.host and self.token)

    @staticmethod
    def clean_host(value):
        if not isinstance(value, str): raise ValueError('Permanent address is missing')
        value = value.strip().lower()
        if '://' in value: value = urlsplit(value).hostname or ''
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.instantdns\.io', value):
            raise ValueError('Invalid permanent address')
        return value

    @staticmethod
    def clean_token(value):
        if not isinstance(value, str): raise ValueError('Permanent address token is missing')
        value = value.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value):
            raise ValueError('Invalid permanent address token')
        return value

    def configure(self, host, token):
        self.host = self.clean_host(host)
        self.token = self.clean_token(token)
        self.error, self.ok = '', False

    def clear(self):
        self.host, self.token, self.ip = '', '', ''
        self.updated_at, self.error, self.ok = 0.0, '', False

    @classmethod
    def _request(cls, path, token='', timeout=12):
        context = ssl.create_default_context()
        class Connection(http.client.HTTPSConnection):
            def connect(self):
                infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
                sock = socket.create_connection(infos[0][4][:2], self.timeout, self.source_address)
                self.sock = context.wrap_socket(sock, server_hostname=self.host)
        connection = Connection(cls.API, 443, timeout=timeout)
        headers = {'User-Agent': 'OpenCore remote access', 'Accept': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        response, raw = None, b''
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            raw = response.read(8192)
        finally:
            connection.close()
        if response is None:
            raise ValueError('Permanent address service did not respond')
        if response.status >= 400:
            try: detail = json.loads(raw).get('error') or ''
            except (ValueError, AttributeError): detail = ''
            raise ValueError(detail or 'Permanent address service returned %s' % response.status)
        try:
            value = json.loads(raw)
        except ValueError:
            raise ValueError('Permanent address service returned an invalid response') from None
        if not isinstance(value, dict): raise ValueError('Permanent address service returned an invalid response')
        return value

    def create(self):
        value = self._request('/new')
        self.configure(value.get('hostname'), value.get('token'))
        ip = value.get('ip') or ''
        if value.get('record_type') != 'A' or not ip:
            raise ValueError('Permanent address service did not return an IPv4 address')
        self.ip, self.updated_at, self.error, self.ok = ip, time.time(), '', True
        return value

    def update(self):
        if not self.configured: raise ValueError('Permanent address is not configured')
        value = self._request('/update', self.token)
        ip = value.get('ip') or ''
        if value.get('record_type') != 'A' or not ip:
            raise ValueError('Permanent address service did not update the IPv4 record')
        self.ip, self.updated_at, self.error, self.ok = ip, time.time(), '', True

    def status(self):
        return {'configured': self.configured, 'provider': 'instantdns', 'host': self.host,
                'ip': self.ip, 'updatedAt': int(self.updated_at), 'error': self.error, 'ok': self.ok}


class RemoteAccess:
    def __init__(self, root, resources, owner=None, *, surface=None, tls_factory=None):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.owner, self.surface, self.tls_factory = owner, surface, tls_factory
        self.lock, self.server, self.thread = threading.RLock(), None, None
        self.internet = InternetGateway()
        self.internet_thread, self.internet_stop = None, threading.Event()
        self.external_port = None
        self.pair_code, self.pair_until, self.fingerprint = None, 0, ''
        self.attempts, self.last_input, self.last_seen = {}, {}, {}
        self.mobile = MobileBridge(self.root/'runtime-setup'/'remote-ui-preferences.json')
        appdata = os.environ.get('APPDATA')
        upload_base = Path(appdata).resolve() / 'ai.opencore.control-center' if appdata else self.root / 'runtime-setup'
        self.artifact_root = upload_base / 'artifacts'
        self.mobile_upload_root = managed_path(upload_base, 'mobile-uploads')
        self.folder_uploads, self.folder_upload_lock = {}, threading.RLock()
        self.addresses, self.addresses_checked = [], 0
        self.devices_path = managed_path(self.root, 'secrets', 'remote-device-identities.json')
        self.config_path = managed_path(self.root, 'runtime-setup', 'remote-access.json')
        configured = self.config()
        external_port = configured.get('externalPort')
        if type(external_port) is int and 1024 <= external_port <= 65535:
            self.external_port = external_port
        self.ddns = DuckDNS(configured.get('ddnsDomain', ''), configured.get('ddnsToken', ''))
        self.ddns_lock = threading.RLock()
        self.instant = InstantDNS(configured.get('permanentHost', ''), configured.get('permanentToken', ''))
        self.instant_lock = threading.RLock()
        self.instant_last_attempt = 0.0
        self.pc_name = configured.get('name') or os.environ.get('COMPUTERNAME') or socket.gethostname() or 'OpenCore PC'
        if not isinstance(self.pc_name, str) or len(self.pc_name) > 80 or any(ord(c) < 32 for c in self.pc_name):
            self.pc_name = 'OpenCore PC'
        try:
            value = json.loads(self.devices_path.read_text(encoding='utf-8'))
            self.devices = {item['id']: item for item in value if isinstance(item, dict) and
                            isinstance(item.get('hash'), str) and len(item['hash']) == 64} if isinstance(value, list) else {}
        except (OSError, ValueError, TypeError, KeyError): self.devices = {}
        # Only app-created outputs are exposed. Models, user-selected files,
        # credentials, databases and arbitrary absolute paths are never roots.
        self.media_roots = {name: self.root / name for name in ('artifacts', 'generated', 'studio/outputs')}
        if appdata:
            app_root = Path(appdata).resolve() / 'ai.opencore.control-center'
            self.media_roots.update({'app-artifacts': app_root / 'artifacts', 'app-studio': app_root / 'studio'})
        self.output_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.webm', '.html', '.pdf', '.txt', '.glb', '.zip', '.wav', '.mp3', '.flac', '.ogg', '.gltf', '.obj', '.stl', '.csv', '.md'}

    def config(self):
        try:
            value = json.loads(self.config_path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError): return {}

    def rename(self, value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
            raise ValueError('Choose a PC name from 1 to 80 characters')
        with self.lock:
            self.pc_name = value.strip()
            options = self.config()
            options['name'] = self.pc_name
            atomic_json(self.config_path, options)
            return self.status()

    def _visible_devices(self):
        """Show one entry per phone, preferring the identity that sent a device key."""
        ordered = sorted(self.devices.values(), key=lambda item: self.last_seen.get(item['id'], -100), reverse=True)
        chosen = {}
        for item in ordered:
            name = str(item.get('name', 'Phone')).strip().casefold()
            key = item.get('deviceKey') or 'legacy:' + name
            chosen.setdefault(key, item)
        keyed_names = {str(item.get('name', 'Phone')).strip().casefold() for item in chosen.values() if item.get('deviceKey')}
        return [item for item in chosen.values()
                if item.get('deviceKey') or str(item.get('name', 'Phone')).strip().casefold() not in keyed_names]

    def status(self):
        now = time.monotonic()
        if now - self.addresses_checked > 60:
            self.addresses = local_addresses()
            self.addresses_checked = now
        with self.lock:
            internet = self.internet.status()
            addresses = list(self.addresses)
            public = ''
            if self.server and internet.get('mapped') and internet.get('externalAddress') and internet.get('externalPort'):
                public = '%s:%s' % (internet['externalAddress'], internet['externalPort'])
                addresses.insert(0, {'address': public, 'interface': 'Internet (works anywhere)', 'kind': 'internet'})
            ddns = self.ddns.status()
            permanent_dns = self.instant.status()
            permanent = ''
            permanent_host = permanent_dns['host'] if permanent_dns['ok'] else (ddns['host'] if ddns['ok'] else '')
            if permanent_host and internet.get('mapped') and internet.get('externalPort'):
                permanent = '%s:%s' % (permanent_host, internet['externalPort'])
                addresses.insert(0, {'address': permanent, 'interface': 'Permanent address', 'kind': 'internet'})
            return {'enabled': self.server is not None, 'port': self.server.server_port if self.server else None, 'pcName': self.pc_name,
                    'fingerprint': self.fingerprint, 'pairingExpiresIn': max(0, int(self.pair_until - time.monotonic())),
                    'addresses': addresses, 'publicAddress': permanent or public, 'internet': internet,
                    'ddns': ddns, 'permanent': permanent_dns,
                    'devices': [{'id': d['id'], 'name': d.get('name', 'Phone'),
                                 'connected': now-self.last_seen.get(d['id'], -100) < 10} for d in self._visible_devices()],
                    'network': 'internet' if (public or permanent) else 'direct', 'relay': False}

    def start(self, options):
        with self.lock:
            if self.server:
                if options.get('refresh'):
                    self._refresh_internet()
                    if self.ddns.configured:
                        self._update_ddns(force=True)
                return self.status()
            saved = self.config()
            if isinstance(saved.get('name'), str) and 1 <= len(saved['name'].strip()) <= 80:
                self.pc_name = saved['name'].strip()
            if not self.instant.configured and saved.get('permanentHost') and saved.get('permanentToken'):
                try: self.instant.configure(saved['permanentHost'], saved['permanentToken'])
                except ValueError: pass
            if not self.ddns.configured and saved.get('ddnsDomain') and saved.get('ddnsToken'):
                try: self.ddns.configure(saved['ddnsDomain'], saved['ddnsToken'])
                except ValueError: pass
            host = options.get('bind', saved.get('bind', '0.0.0.0'))
            try: ipaddress.ip_address(host)
            except (ValueError, TypeError): raise ValueError('Bind to a local IP address') from None
            port = options.get('port', saved.get('port', 0))
            if type(port) is not int or not (port == 0 or 1024 <= port <= 65535):
                raise ValueError('Choose a port between 1024 and 65535')
            if self.tls_factory: tls, fingerprint = self.tls_factory()
            else:
                from tls_identity import context
                tls, fingerprint = context(self.root)
            server = RemoteServer((host, port), self, tls)
            self.server, self.fingerprint = server, fingerprint
            self.thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.thread.start()
            options = self.config()
            options.update(enabled=True, bind=host, port=server.server_port, name=self.pc_name,
                           externalPort=self.external_port or server.server_port)
            atomic_json(self.config_path, options)
            self._refresh_internet()
            self._start_internet_thread()
            if self.instant.configured:
                threading.Thread(target=self._update_permanent, kwargs={'force': True}, daemon=True).start()
            else:
                threading.Thread(target=self._create_permanent, daemon=True).start()
            if self.ddns.configured:
                threading.Thread(target=self._update_ddns, kwargs={'force': True}, daemon=True).start()
            return self.status()

    def restore(self):
        try:
            options = json.loads(self.config_path.read_text(encoding='utf-8'))
            if options.get('enabled') is True: self.start(options)
        except Exception:
            pass

    def _refresh_internet(self):
        if not self.server:
            return
        try:
            if not self.addresses:
                self.addresses = local_addresses()
                self.addresses_checked = time.monotonic()
            candidates = [item['address'] if isinstance(item, dict) else item for item in self.addresses]
            preferred = self.external_port or self.server.server_port
            result = self.internet.refresh(candidates, self.server.server_port, preferred)
            if result.get('externalPort'):
                self.external_port = result['externalPort']
                options = self.config()
                options.update(externalPort=self.external_port)
                atomic_json(self.config_path, options)
        except Exception:
            self.internet.mapped = False
            self.internet.last_error = 'Internet address setup failed'
            self.internet.checked_at = time.time()

    def _start_internet_thread(self):
        if self.internet_thread and self.internet_thread.is_alive():
            return
        self.internet_stop.clear()
        def refresh():
            while not self.internet_stop.wait(300):
                if self.server:
                    self._refresh_internet()
                    if self.instant.configured:
                        self._update_permanent()
                    elif time.time() - self.instant_last_attempt >= 900:
                        self._create_permanent()
                    if self.ddns.configured:
                        self._update_ddns()
        self.internet_thread = threading.Thread(target=refresh, daemon=True)
        self.internet_thread.start()

    def configure_ddns(self, domain, token):
        with self.ddns_lock:
            options = self.config()
            if not isinstance(domain, str) or not domain.strip():
                self.ddns.clear()
                options.pop('ddnsDomain', None)
                options.pop('ddnsToken', None)
                atomic_json(self.config_path, options)
                return self.status()
            if not isinstance(token, str) or not token.strip():
                if not self.ddns.token: raise ValueError('Enter the DuckDNS token')
                token = self.ddns.token
            self.ddns.configure(domain, token)
            options['ddnsDomain'] = self.ddns.domain
            options['ddnsToken'] = self.ddns.token
            atomic_json(self.config_path, options)
            self._update_ddns(force=True)
            return self.status()

    def _update_ddns(self, force=False):
        with self.ddns_lock:
            if not self.ddns.configured or not self.server:
                return
            internet = self.internet.status()
            ip = internet.get('externalAddress') or ''
            if not ip:
                self.ddns.ok, self.ddns.error = False, 'Waiting for the router to report the internet address'
                return
            if not force and self.ddns.ok and self.ddns.ip == ip and time.time() - self.ddns.updated_at < 300:
                return
            try:
                self.ddns.update(ip)
            except Exception as error:
                self.ddns.ok, self.ddns.error = False, str(error)

    def _create_permanent(self):
        with self.instant_lock:
            if self.instant.configured or not self.server:
                return
            if time.time() - self.instant_last_attempt < 900:
                return
            self.instant_last_attempt = time.time()
            try:
                self.instant.create()
                options = self.config()
                options['permanentHost'] = self.instant.host
                options['permanentToken'] = self.instant.token
                atomic_json(self.config_path, options)
            except Exception as error:
                self.instant.ok, self.instant.error = False, str(error)

    def _update_permanent(self, force=False):
        with self.instant_lock:
            if not self.instant.configured or not self.server:
                return
            if not force and self.instant.ok and time.time() - self.instant.updated_at < 300:
                return
            try:
                self.instant.update()
            except Exception as error:
                self.instant.ok, self.instant.error = False, str(error)

    def ensure_permanent(self):
        with self.instant_lock:
            if not self.server:
                raise ValueError('Enable remote access first')
            if not self.instant.configured:
                try:
                    self.instant.create()
                except Exception as error:
                    self.instant.ok, self.instant.error = False, str(error)
                    raise ValueError(str(error)) from None
                options = self.config()
                options['permanentHost'] = self.instant.host
                options['permanentToken'] = self.instant.token
                atomic_json(self.config_path, options)
            self._update_permanent(force=True)
            return self.status()

    def pairing(self, address):
        with self.lock:
            if not self.server: raise ValueError('Enable remote access first')
            if not isinstance(address, str): raise ValueError('Enter the PC’s reachable IP address or VPN name')
            parsed = urlsplit(address if '://' in address else 'https://' + address)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError('Use a direct HTTPS PC address without a path')
            if len(parsed.hostname) > 253 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in parsed.hostname):
                raise ValueError('Invalid PC address')
            host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
            port = parsed.port
            if port is None:
                port = self.external_port if self.internet.mapped and parsed.hostname == self.internet.external_ip else self.server.server_port
            endpoint = 'https://' + host + ':' + str(port)
            self.pair_code, self.pair_until = secrets.token_urlsafe(32), time.monotonic() + 120
            self.attempts.clear()
            text = 'opencore://pair?' + urlencode({'url': endpoint, 'pin': self.fingerprint, 'code': self.pair_code, 'name': self.pc_name})
            return {'pairingText': text, 'qrRows': pairing_qr(text), 'expiresIn': 120, 'fingerprint': self.fingerprint}

    def pair(self, code, name, address, device_key=None):
        with self.lock:
            now = time.monotonic()
            self.attempts = {key: value for key, value in self.attempts.items() if now - value[0] < 120}
            stamp, attempts = self.attempts.get(address, (now, 0))
            if len(self.attempts) > 128 or attempts >= 10: raise PermissionError('Pairing attempt limit reached')
            self.attempts[address] = (stamp, attempts + 1)
            if not isinstance(code, str) or not self.pair_code or now > self.pair_until or not hmac.compare_digest(code, self.pair_code):
                raise PermissionError('Pairing code is invalid or expired')
            if not isinstance(name, str) or not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name): raise ValueError('Invalid phone name')
            if device_key is not None and (not isinstance(device_key, str) or not re.fullmatch(r'[a-f0-9]{64}', device_key)):
                raise ValueError('Invalid phone identity')
            previous = [item['id'] for item in self.devices.values() if device_key and item.get('deviceKey') == device_key]
            if device_key:
                previous += [item['id'] for item in self.devices.values()
                             if not item.get('deviceKey') and str(item.get('name', 'Phone')).strip().casefold() == name.strip().casefold()]
            previous = list(dict.fromkeys(previous))
            if len(self.devices) - len(previous) >= 8: raise ValueError('Revoke an old phone before pairing another')
            for identity in previous:
                del self.devices[identity]
                self.last_seen.pop(identity, None)
                self.mobile.forget(identity)
            token, identity = secrets.token_urlsafe(48), secrets.token_hex(12)
            self.devices[identity] = {'id': identity, 'name': name, 'hash': hashlib.sha256(token.encode()).hexdigest()}
            if device_key: self.devices[identity]['deviceKey'] = device_key
            atomic_json(self.devices_path, list(self.devices.values()))
            self.pair_code, self.pair_until = None, 0
            return {'deviceId': identity, 'token': token, 'name': self.pc_name}

    def authorize(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization) > 256: return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.lock:
            identity = next((item['id'] for item in self.devices.values() if hmac.compare_digest(item['hash'], digest)), None)
            if identity: self.last_seen[identity] = time.monotonic()
            return identity

    def revoke(self, identity):
        with self.lock:
            if identity not in self.devices: raise ValueError('Phone is not paired')
            del self.devices[identity]
            self.last_seen.pop(identity, None)
            self.mobile.forget(identity)
            atomic_json(self.devices_path, list(self.devices.values()))
            return self.status()

    def revoke_disconnected(self):
        with self.lock:
            now = time.monotonic()
            disconnected = [identity for identity in self.devices if now-self.last_seen.get(identity, -100) >= 10]
            for identity in disconnected:
                del self.devices[identity]
                self.last_seen.pop(identity, None)
                self.mobile.forget(identity)
            if disconnected: atomic_json(self.devices_path, list(self.devices.values()))
            return self.status()

    def shutdown(self):
        self.internet_stop.set()
        with self.lock:
            server, self.server = self.server, None
            self.pair_code, self.pair_until = None, 0
        self.internet.remove()
        if server: server.shutdown(); server.server_close()
        self.last_seen.clear()
        self.mobile.close()
        return self.status()

    def close(self):
        if self.config_path.exists():
            saved = self.config()
            saved.update(enabled=False, name=self.pc_name)
            atomic_json(self.config_path, saved)
        return self.shutdown()

    def media_path(self, root, relative):
        base = self.media_roots.get(root)
        if base is None or not isinstance(relative, str) or not relative or '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
            raise ValueError('Invalid generated media path')
        expected = base
        base = base.resolve()
        # A redirected output root is not an implicit share of another directory.
        if base != expected: raise ValueError('Generated media root is redirected')
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file(): raise ValueError('Generated file is unavailable')
        if path.suffix.lower() not in self.output_extensions or path.name.startswith('.'):
            raise ValueError('This file is not a generated media output')
        return path

    def media_reference(self, absolute):
        if not isinstance(absolute, str) or len(absolute) > 4096: raise ValueError('Generated file is unavailable')
        try: path = normalized_path(absolute).resolve(strict=True)
        except (OSError, ValueError): raise ValueError('Generated file is unavailable') from None
        for name, expected in self.media_roots.items():
            try: base = expected.resolve(strict=True)
            except OSError: continue
            if base != expected.resolve(): continue
            if path.is_relative_to(base) and path.is_file():
                relative = path.relative_to(base).as_posix()
                self.media_path(name, relative)
                return {'root': name, 'path': relative}
        raise ValueError('Only OpenCore generated files can be opened on the phone')

    def artifact(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', identity):
            raise ValueError('This OpenCore file is unavailable')
        identity = identity.lower()
        try:
            base = self.artifact_root.resolve(strict=True)
            if base != self.artifact_root.resolve(): raise ValueError('The OpenCore file directory is redirected')
            metadata_path = (base / (identity + '.json')).resolve(strict=True)
            content_path = (base / (identity + '.bin')).resolve(strict=True)
            if not metadata_path.is_relative_to(base) or not content_path.is_relative_to(base):
                raise ValueError('This OpenCore file is unavailable')
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            if not isinstance(metadata, dict): raise ValueError('This OpenCore file is unavailable')
            mime, name, size = metadata.get('mime'), metadata.get('name'), metadata.get('size')
            allowed = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'application/pdf',
                       'text/plain', 'text/markdown', 'application/json', 'text/javascript', 'text/csv',
                       'text/typescript', 'text/x-python', 'text/x-rust', 'application/xml', 'text/html'}
            if metadata.get('id') != identity or mime not in allowed or not safe_windows_name(name):
                raise ValueError('This OpenCore file is unavailable')
            if type(size) is not int or not 0 < size <= 16 * 1024 * 1024:
                raise ValueError('This OpenCore file is unavailable')
            data = content_path.read_bytes()
            if len(data) != size: raise ValueError('This OpenCore file is unavailable')
            return data, mime, name
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise ValueError('This OpenCore file is unavailable') from None

    def ui_revision(self):
        try:
            data=(self.resources/'runtime-setup/mobile-ui/manifest.json').read_bytes()
            return hashlib.sha256(data).hexdigest() if len(data)<=65536 else ''
        except OSError:return ''

    def mobile_asset(self, relative):
        if not isinstance(relative, str) or len(relative) > 160 or '\\' in relative:
            raise ValueError('Invalid mobile interface asset')
        base = (self.resources/'runtime-setup/mobile-ui').resolve()
        if not base.is_relative_to(self.resources): raise ValueError('Mobile interface is unavailable')
        try:
            manifest = json.loads((base/'manifest.json').read_text(encoding='utf-8'))
            item = manifest['files'][relative]
            path = (base/relative).resolve(strict=True)
            if not path.is_relative_to(base) or not path.is_file() or item['bytes'] > 8 * 1024 * 1024:
                raise ValueError('Invalid mobile interface asset')
            data = path.read_bytes()
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Mobile interface asset failed verification')
            if item['mime'] not in ('text/html; charset=utf-8','text/javascript; charset=utf-8','text/css; charset=utf-8','image/png'):
                raise ValueError('Invalid mobile interface content type')
            return data, item['mime']
        except (KeyError, OSError, TypeError, json.JSONDecodeError):
            raise ValueError('Update OpenCore on the PC to use the phone interface') from None

    def folder_upload(self, device, body):
        if not isinstance(body, dict): raise ValueError('Invalid folder upload')
        action, identity = body.get('action'), body.get('uploadId')
        with self.folder_upload_lock:
            now = time.monotonic()
            for old_id, old in list(self.folder_uploads.items()):
                if now - old['created'] > 3600:
                    shutil.rmtree(old['root'], ignore_errors=True)
                    del self.folder_uploads[old_id]
            if action == 'begin':
                name = body.get('name')
                if not isinstance(name, str) or not safe_windows_name(name.strip()):
                    raise ValueError('The selected folder name is invalid')
                if len(self.folder_uploads) >= 8: raise ValueError('Finish or cancel another folder transfer first')
                self.mobile_upload_root.mkdir(parents=True, exist_ok=True)
                if self.mobile_upload_root.resolve() != self.mobile_upload_root: raise ValueError('The folder upload directory is redirected')
                identity = secrets.token_hex(16)
                root = self.mobile_upload_root / identity
                root.mkdir()
                destination = root / name.strip()
                destination.mkdir()
                (root / '.upload-created').write_text(str(time.time()), encoding='ascii')
                self.folder_uploads[identity] = {'device': device, 'root': root, 'path': destination,
                                                 'created': now, 'bytes': 0, 'files': set()}
                return {'uploadId': identity}
            upload = self.folder_uploads.get(identity) if isinstance(identity, str) and re.fullmatch('[a-f0-9]{32}', identity) else None
            if not upload or upload['device'] != device: raise ValueError('Folder transfer expired or unavailable')
            if action == 'cancel':
                shutil.rmtree(upload['root']); del self.folder_uploads[identity]
                return {'cancelled': True}
            if action == 'finish':
                if not upload['files']: raise ValueError('The selected folder contains no files')
                path = upload['path'].resolve()
                if not path.is_relative_to(upload['root'].resolve()): raise ValueError('Invalid staged folder path')
                del self.folder_uploads[identity]
                return {'path': str(path)}
            if action != 'file': raise ValueError('Invalid folder upload action')
            relative, encoded = body.get('path'), body.get('dataBase64')
            if not isinstance(relative, str) or not relative or len(relative) > 512 or '\\' in relative or ':' in relative:
                raise ValueError('Invalid folder file path')
            parts = relative.split('/')
            if len(parts) > 24 or any(not safe_windows_name(part) for part in parts):
                raise ValueError('Invalid folder file path')
            if not isinstance(encoded, str) or len(encoded) > 11 * 1024 * 1024: raise ValueError('Folder file exceeds the 8 MiB transfer limit')
            try: data = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error): raise ValueError('Invalid folder file data') from None
            if not data or len(data) > 8 * 1024 * 1024 or upload['bytes'] + len(data) > 64 * 1024 * 1024:
                raise ValueError('Folder transfer exceeds the 64 MiB limit')
            if relative in upload['files'] or len(upload['files']) >= 512: raise ValueError('Folder has too many or duplicate files')
            target = upload['path'].joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.resolve().is_relative_to(upload['path'].resolve()) or target.is_symlink(): raise ValueError('Invalid folder file path')
            with target.open('xb') as output: output.write(data)
            upload['bytes'] += len(data); upload['files'].add(relative)
            return {'accepted': True, 'files': len(upload['files']), 'bytes': upload['bytes']}

    def media(self):
        items = []
        for root, base in self.media_roots.items():
            if not base.exists() or base.resolve() != base: continue
            # Bounded scan, no following symlink directories.
            import os
            visited = 0
            for folder, directories, files in os.walk(base, followlinks=False):
                directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()]
                for name in files:
                    visited += 1
                    if visited > 5000: break
                    path = Path(folder) / name
                    if path.suffix.lower() not in self.output_extensions: continue
                    relative = path.relative_to(base).as_posix()
                    try: stat = self.media_path(root, relative).stat()
                    except (ValueError, OSError): continue
                    items.append({'root': root, 'path': relative, 'bytes': stat.st_size, 'modified': stat.st_mtime})
                if visited > 5000: break
        return {'files': sorted(items, key=lambda item: item['modified'], reverse=True)[:200]}

class RemoteServer(ThreadingHTTPServer):
    daemon_threads, allow_reuse_address, request_queue_size = True, True, 8
    def __init__(self, address, state, tls):
        self.state, self.tls, self.slots = state, tls, threading.BoundedSemaphore(8)
        super().__init__(address, RemoteHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        try: return self.tls.wrap_socket(connection, server_side=True), address
        except Exception: connection.close(); raise

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False): request.close(); return
        try: super().process_request(request, address)
        except Exception: self.slots.release(); raise

    def process_request_thread(self, request, address):
        try: super().process_request_thread(request, address)
        finally: self.slots.release()

    def handle_error(self, *_): pass


class RemoteHandler(BaseHTTPRequestHandler):
    server_version, sys_version = 'OpenCore', ''
    def log_message(self, *_): pass
    def send(self, status, value, content_type='application/json', headers=None):
        raw = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(status)
        for key, val in {'Content-Type': content_type, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', **(headers or {})}.items(): self.send_header(key, val)
        self.end_headers(); self.wfile.write(raw)

    def body(self, limit=128 * 1024):
        length = self.headers.get('Content-Length', '')
        if not length.isdigit() or not 0 < int(length) <= limit or self.headers.get('Transfer-Encoding'): raise ValueError('Invalid remote request size')
        value = json.loads(self.rfile.read(int(length)))
        if not isinstance(value, dict): raise ValueError('Expected a request object')
        return value

    def handle_api(self):
        if self.headers.get('Origin') or len(self.path) > 4096: raise PermissionError('Native paired clients only')
        state, parsed = self.server.state, urlsplit(self.path)
        if parsed.query or parsed.fragment: raise ValueError('Query parameters are not accepted')
        if self.command == 'POST' and parsed.path == '/pair':
            body = self.body(); return self.send(200, state.pair(body.get('code'), body.get('name', 'Android phone'), self.client_address[0], body.get('deviceKey')))
        device = state.authorize(self.headers.get('Authorization'))
        if not device: raise PermissionError('Pair this phone from the PC app')
        if self.command == 'POST' and parsed.path == '/session':
            self.body(); return self.send(200, {**state.mobile.open_session(device),'uiRevision':state.ui_revision()})
        if self.command == 'POST' and parsed.path == '/request':
            return self.send(200, state.mobile.enqueue(device, self.body(48 * 1024 * 1024)))
        if self.command == 'POST' and parsed.path == '/updates':
            return self.send(200, state.mobile.updates(device, self.body().get('session')))
        if self.command == 'POST' and parsed.path == '/preferences':
            return self.send(200, state.mobile.phone_preferences(device, self.body().get('session')))
        if self.command == 'POST' and parsed.path == '/folder':
            return self.send(200, state.folder_upload(device, self.body(12 * 1024 * 1024)))
        if self.command == 'POST' and parsed.path == '/ui':
            data, mime = state.mobile_asset(self.body().get('path'))
            return self.send(200, data, mime)
        if self.command == 'GET' and parsed.path == '/info': return self.send(200, {'name': state.pc_name,'uiRevision':state.ui_revision()})
        if self.command == 'POST' and parsed.path == '/rename':
            state.rename(self.body().get('name')); return self.send(200, {'name': state.pc_name})
        if self.command == 'POST' and parsed.path == '/unpair':
            state.revoke(device); return self.send(200, {'removed': True})
        if self.command == 'GET' and parsed.path == '/media': return self.send(200, state.media())
        if self.command == 'POST' and parsed.path == '/file':
            body = self.body(); path = state.media_path(body.get('root'), body.get('path'))
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            with path.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
                self.end_headers()
                for chunk in iter(lambda: source.read(65536), b''): self.wfile.write(chunk)
            return
        if self.command == 'POST' and parsed.path == '/artifact':
            data, kind, name = state.artifact(self.body().get('id'))
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Disposition', "inline; filename*=UTF-8''" + quote(name))
            self.end_headers(); self.wfile.write(data)
            return
        if self.command == 'POST' and parsed.path == '/media-path':
            return self.send(200, state.media_reference(self.body().get('path')))
        return self.send(404, {'error': 'Unknown remote endpoint'})

    def _handle(self):
        try: self.handle_api()
        except PermissionError as error: self.send(403, {'error': str(error)})
        except (ValueError, KeyError, OSError) as error:
            if isinstance(error, (BrokenPipeError, ConnectionError, socket.timeout)): return
            self.send(400, {'error': str(error) if isinstance(error, ValueError) else 'The generated file is unavailable'})
        except Exception: self.send(500, {'error': 'Remote request failed on the PC'})
    do_GET = do_POST = _handle
