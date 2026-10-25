"""Bounded Hub HTTP and resumable, checksum verified managed downloads."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from hf_catalog import HUB, HEX40, HEX64, parse_repository, safe_filename


class Paused(RuntimeError):
    pass


class HubError(RuntimeError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def normalized_path(path):
    value=str(path)
    if os.name=='nt':
        if value.startswith('\\\\?\\UNC\\'):value='\\\\'+value[8:]
        elif value.startswith('\\\\?\\'):value=value[4:]
    return Path(value)


def managed_path(base, *parts):
    base = normalized_path(Path(base).resolve())
    target = base.joinpath(*parts)
    resolved = normalized_path(target.resolve())
    if not resolved.is_relative_to(base) or resolved == base:
        raise ValueError(f"Path is outside its managed directory: {base!s} -> {resolved!s}")
    return resolved


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, allow_test_http=False):
        self.allow_test_http = allow_test_http

    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        host = (parsed.hostname or "").lower()
        trusted = host == "huggingface.co" or host.endswith((".huggingface.co", ".hf.co"))
        test = self.allow_test_http and host in ("127.0.0.1", "localhost") and parsed.scheme == "http"
        if parsed.username or parsed.password or not ((parsed.scheme == "https" and trusted) or test):
            raise HubError("The Hub redirected to an untrusted download host")
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected is not None and urlsplit(request.full_url).netloc.lower() != parsed.netloc.lower():
            redirected.remove_header("Authorization")
        return redirected


class HubClient:
    def __init__(self, token_provider=lambda: None, *, endpoint=HUB, allow_test_http=False, timeout=20):
        parsed = urlsplit(endpoint)
        if endpoint != HUB and not (allow_test_http and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
            raise ValueError("Only the official Hugging Face Hub endpoint is supported")
        self.endpoint, self.token_provider = endpoint.rstrip("/"), token_provider
        self.timeout = min(max(timeout, 1), 30)
        self.opener = build_opener(SafeRedirect(allow_test_http))

    def _request(self, path, *, headers=None, token=None):
        values = {"User-Agent": "OpenCore-Managed-Hub/1", "Accept-Encoding": "identity", **(headers or {})}
        if token:
            values["Authorization"] = "Bearer " + token
        return self.opener.open(Request(self.endpoint + path, headers=values), timeout=self.timeout)

    def open(self, path, *, headers=None, token=None, anonymous_first=True):
        try:
            return self._request(path, headers=headers, token=None if anonymous_first else token)
        except HTTPError as error:
            status = error.code
            error.close()
            saved = token if token is not None else self.token_provider()
            if anonymous_first and status in (401, 403, 404) and saved:
                try:
                    return self._request(path, headers=headers, token=saved)
                except HTTPError as retried:
                    status = retried.code
                    retried.close()
            message = "Repository or file requires a valid Hugging Face token and publisher access" if status in (401, 403, 404) else "The Hugging Face Hub rejected the request"
            raise HubError(message, status) from None
        except (URLError, OSError, TimeoutError):
            raise HubError("The Hugging Face request timed out or could not connect") from None

    def json(self, path, *, token=None, anonymous_first=True, limit=16 * 1024 * 1024, expected_type=dict):
        try:
            with self.open(path, token=token, anonymous_first=anonymous_first) as response:
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > limit):
                    raise HubError("Hub metadata exceeds the supported size limit")
                body = response.read(limit + 1)
            if len(body) > limit:
                raise HubError("Hub metadata exceeds the supported size limit")
            result = json.loads(body)
            if not isinstance(result, expected_type):
                raise ValueError()
            return result
        except (ValueError, UnicodeError):
            raise HubError("The Hub returned invalid JSON metadata") from None

    def metadata(self, repository):
        repository = parse_repository(repository)
        return self.json("/api/models/" + quote(repository, safe="/") + "/revision/main?blobs=true")

    def search_quantizations(self, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 192:
            raise ValueError('Invalid model search')
        return self.json('/api/models?filter=gguf&search=' + quote(query, safe='') +
                         '&limit=40&sort=downloads&direction=-1', expected_type=list, limit=2 * 1024 * 1024)

    def quantization_derivatives(self, repository):
        repository = parse_repository(repository)
        return self.json('/api/models?filter=' + quote('base_model:quantized:' + repository, safe='') +
                         '&limit=100&sort=downloads&direction=-1', expected_type=list, limit=2 * 1024 * 1024)

    def validate_token(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"hf_[A-Za-z0-9_]{5,250}", token):
            raise ValueError("Enter a valid Hugging Face access token")
        identity = self.json("/api/whoami-v2", token=token, anonymous_first=False, limit=256 * 1024)
        if not identity.get("name"):
            raise ValueError("Hugging Face did not validate this token")
        return {"valid": True}

    def open_file(self, pin, offset=0):
        path = "/" + quote(pin["repository"], safe="/") + "/resolve/" + pin["revision"] + "/" + quote(pin["filename"], safe="/")
        return self.open(path, headers={"Range": "bytes=" + str(offset) + "-"} if offset else {})


def validate_entry(entry):
    if not isinstance(entry, dict) or not re.fullmatch(r"hf-[0-9a-f]{24}", str(entry.get("id", ""))):
        raise ValueError("Invalid managed model ID")
    repo = parse_repository(entry.get("repository", ""))
    subdirectory=entry.get('subdirectory','')
    if subdirectory:
        safe_filename(subdirectory)
    revision = entry.get("revision", "")
    if not isinstance(revision, str) or not HEX40.fullmatch(revision):
        raise ValueError("Model revision must be an immutable commit")
    pins = entry.get("files")
    if entry.get("installable") is not True or not isinstance(pins, list) or not 1 <= len(pins) <= 20000:
        raise ValueError("This model variant has no complete pinned installation plan")
    seen, total = set(), 0
    for pin in pins:
        name = safe_filename(pin.get("filename"))
        if name.casefold() in seen or pin.get("repository") != repo or pin.get("revision") != revision:
            raise ValueError("File pins do not match the selected checkpoint")
        seen.add(name.casefold())
        if type(pin.get("bytes")) is not int or not 0 <= pin["bytes"] <= 100 * 1024 ** 4:
            raise ValueError("File pin size is invalid")
        if not (HEX64.fullmatch(str(pin.get("sha256", ""))) or HEX40.fullmatch(str(pin.get("gitBlobSha1", "")))):
            raise ValueError("Every file requires an LFS SHA-256 or Git blob SHA-1 pin")
        total += pin["bytes"]
    if entry.get("bytes") != total:
        raise ValueError("Checkpoint byte count does not match its file pins")
    return entry


class Downloader:
    def __init__(self, root, client):
        self.root, self.client = Path(root).resolve(), client

    def directory(self, entry):
        validate_entry(entry)
        hub = managed_path(self.root, "models", "hub")
        return managed_path(hub, entry["id"])

    @staticmethod
    def _pause(pause):
        if pause.is_set():
            raise Paused("Download paused; verified files and pinned partial downloads are retained")

    def _verify(self, path, pin, pause):
        if not path.is_file() or path.stat().st_size != pin["bytes"]:
            return None
        sha256, blob = hashlib.sha256(), hashlib.sha1()
        blob.update(b"blob " + str(pin["bytes"]).encode("ascii") + b"\0")
        with path.open("rb") as source:
            while True:
                self._pause(pause)
                block = source.read(1024 * 1024)
                if not block:
                    break
                sha256.update(block)
                blob.update(block)
        actual = sha256.hexdigest()
        if (pin.get("sha256") and actual != pin["sha256"]) or (pin.get("gitBlobSha1") and blob.hexdigest() != pin["gitBlobSha1"]):
            return None
        return actual

    def install(self, entry, pause, report):
        validate_entry(entry)
        directory = self.directory(entry)
        directory.mkdir(parents=True, exist_ok=True)
        total, completed, verified = entry["bytes"], 0, []
        for pin in entry["files"]:
            self._pause(pause)
            target = managed_path(directory, safe_filename(pin["filename"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = managed_path(directory, pin["filename"] + ".partial")
            marker = managed_path(directory, pin["filename"] + ".partial.json")
            def progress(stage, current=0):
                report({"stage": stage, "bytesDone": completed + current, "bytesTotal": total, "filename": pin["filename"]})
            progress("verifying", 0)
            digest = self._verify(target, pin, pause)
            if digest is None:
                try:
                    partial_pin = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    partial_pin = None
                if partial_pin != pin or not partial.is_file() or partial.stat().st_size > pin["bytes"]:
                    partial.unlink(missing_ok=True)
                    marker.unlink(missing_ok=True)
                offset = partial.stat().st_size if partial.is_file() else 0
                atomic_json(marker, pin)
                if offset < pin["bytes"]:
                    self._pause(pause)
                    with self.client.open_file(pin, offset) as response:
                        status = response.status
                        if offset and status == 200:
                            offset = 0  # Ignoring Range is legal; overwrite rather than append.
                        elif status == 206:
                            value = response.headers.get("Content-Range", "")
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
                            if not match or tuple(map(int, match.groups())) != (offset, pin["bytes"] - 1, pin["bytes"]):
                                raise HubError("The Hub returned an inconsistent byte range")
                        elif status != 200:
                            raise HubError("The Hub returned an unexpected download response")
                        length = response.headers.get("Content-Length")
                        if length and (not length.isdigit() or int(length) != pin["bytes"] - offset):
                            raise HubError("The download size differs from its pinned metadata")
                        current = offset
                        progress("downloading", current)
                        with partial.open("ab" if offset else "wb") as output:
                            while current < pin["bytes"]:
                                self._pause(pause)
                                block = response.read(min(65536, pin["bytes"] - current))
                                if not block:
                                    raise HubError("The download ended before its pinned byte count")
                                output.write(block)
                                current += len(block)
                                progress("downloading", current)
                            if response.read(1):
                                raise HubError("The download exceeds its pinned byte count")
                            output.flush()
                            os.fsync(output.fileno())
                progress("verifying", pin["bytes"])
                digest = self._verify(partial, pin, pause)
                if digest is None:
                    partial.unlink(missing_ok=True)
                    marker.unlink(missing_ok=True)
                    raise ValueError("Downloaded file checksum does not match the immutable Hub pin")
                self._pause(pause)
                os.replace(partial, target)
                marker.unlink(missing_ok=True)
            completed += pin["bytes"]
            verified.append({**pin, "localPath": str(target), "actualSha256": digest})
            progress("verified", 0)
        self._pause(pause)
        receipt_path = managed_path(directory, "install-receipt.json")
        receipt = {"schema": 1, "id": entry["id"], "repository": entry["repository"], "revision": entry["revision"],
                   "files": verified, "bytes": total, "installedAt": time.time(), "inferenceVerified": False,
                   "localDirectory": str(directory), "receiptPath": str(receipt_path)}
        atomic_json(receipt_path, receipt)
        report({"stage": "installed", "bytesDone": total, "bytesTotal": total, "filename": None})
        return receipt
