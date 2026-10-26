"""Device-local TLS identity using Windows' built-in RSA implementation."""
import base64
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess

from hf_download import atomic_json, managed_path
from hf_security import _dpapi


def _der(tag, content):
    size = len(content)
    length = bytes([size]) if size < 128 else (lambda value: bytes([128 + len(value)]) + value)(size.to_bytes((size.bit_length() + 7) // 8, 'big'))
    return bytes([tag]) + length + content


def _integer(value):
    value = value.lstrip(b'\x00') or b'\x00'
    return _der(2, (b'\x00' if value[0] & 128 else b'') + value)


def _pem(label, data):
    value = base64.b64encode(data).decode('ascii')
    return ('-----BEGIN ' + label + '-----\n' + '\n'.join(value[i:i+64] for i in range(0, len(value), 64)) +
            '\n-----END ' + label + '-----\n').encode('ascii')


def create_identity():
    if os.name != 'nt': raise RuntimeError('Remote identity provisioning requires Windows')
    script = '''$ErrorActionPreference='Stop'
$rsa=[System.Security.Cryptography.RSA]::Create(3072)
$req=[System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=OpenCore paired PC',$rsa,[System.Security.Cryptography.HashAlgorithmName]::SHA256,[System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$cert=$req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddYears(5))
$p=$rsa.ExportParameters($true)
$result=@{certificate=[Convert]::ToBase64String($cert.RawData)}
foreach($n in @('Modulus','Exponent','D','P','Q','DP','DQ','InverseQ')){$result[$n]=[Convert]::ToBase64String($p.$n)}
$result|ConvertTo-Json -Compress
$cert.Dispose();$rsa.Dispose()
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise RuntimeError('Windows could not create the local TLS identity')
    value = json.loads(result.stdout)
    private = _der(48, _integer(b'\x00') + b''.join(_integer(base64.b64decode(value[name]))
                   for name in ('Modulus', 'Exponent', 'D', 'P', 'Q', 'DP', 'DQ', 'InverseQ')))
    certificate = base64.b64decode(value['certificate'])
    return _pem('CERTIFICATE', certificate), _pem('RSA PRIVATE KEY', private), hashlib.sha256(certificate).hexdigest()


def context(root):
    folder = managed_path(root, 'secrets', 'remote-access')
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / 'pc-cert.pem', folder / 'pc-key.dpapi'
    if not cert_path.exists() or not key_path.exists():
        cert, key, _ = create_identity()
        key_path.write_bytes(_dpapi(key))
        cert_path.write_bytes(cert)
    cert = cert_path.read_bytes()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.decode('ascii'))).hexdigest()
    key = _dpapi(key_path.read_bytes(), True)
    temporary = folder / ('tls-load-' + os.urandom(12).hex() + '.pem')
    try:
        with temporary.open('xb') as stream: stream.write(key)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(str(cert_path), str(temporary))
    finally:
        temporary.unlink(missing_ok=True)
    return tls, fingerprint
