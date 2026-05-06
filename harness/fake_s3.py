"""Small in-memory S3-compatible server for backend conformance tests."""

from __future__ import annotations

import contextlib
import hashlib
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterator


class FakeS3Server:
    def __init__(self, *, bucket: str = "hermes-sync-harness"):
        self.bucket = bucket
        self._objects: Dict[str, bytes] = {}
        self._requests: list[dict[str, str]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.objects = self._objects  # type: ignore[attr-defined]
        self._server.bucket = self.bucket  # type: ignore[attr-defined]
        self._server.requests = self._requests  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeS3Server":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)

    @contextlib.contextmanager
    def running(self) -> Iterator["FakeS3Server"]:
        with self:
            yield self

    def assert_protocol_covered(self) -> None:
        operations = {request["operation"] for request in self._requests}
        required = {"LIST", "PUT_OBJECT", "GET_OBJECT", "DELETE_OBJECT"}
        missing = sorted(required - operations)
        if missing:
            raise AssertionError(f"fake S3 protocol coverage missing: {missing}")

    @staticmethod
    def _handler_class():
        class Handler(BaseHTTPRequestHandler):
            server_version = "FakeS3Harness/1.0"

            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length)
                if not self._validate_unsigned_subset(body):
                    return
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                self._record("PUT_OBJECT", key)
                self.server.objects[key] = body  # type: ignore[attr-defined]
                self._send_status(200)

            def do_GET(self) -> None:
                if not self._validate_unsigned_subset(b""):
                    return
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                if query.get("list-type") == ["2"]:
                    if not self._is_bucket_root():
                        self._send_status(400)
                        return
                    prefix = query.get("prefix", [""])[0]
                    self._record("LIST", prefix)
                    self._send_list(prefix)
                    return
                if "list-type" in query:
                    self._send_status(400)
                    return
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                objects = self.server.objects  # type: ignore[attr-defined]
                if key not in objects:
                    self._send_status(404)
                    return
                self._record("GET_OBJECT", key)
                self.send_response(200)
                self.send_header("Content-Length", str(len(objects[key])))
                self.end_headers()
                self.wfile.write(objects[key])

            def do_DELETE(self) -> None:
                if not self._validate_unsigned_subset(b""):
                    return
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                self._record("DELETE_OBJECT", key)
                self.server.objects.pop(key, None)  # type: ignore[attr-defined]
                self._send_status(204)

            def log_message(self, format: str, *args) -> None:
                return

            def _object_key(self) -> str | None:
                parsed = urllib.parse.urlsplit(self.path)
                parts = [part for part in parsed.path.split("/") if part]
                bucket = self.server.bucket  # type: ignore[attr-defined]
                if len(parts) < 2 or parts[0] != bucket:
                    return None
                return urllib.parse.unquote("/".join(parts[1:]))

            def _is_bucket_root(self) -> bool:
                parsed = urllib.parse.urlsplit(self.path)
                parts = [part for part in parsed.path.split("/") if part]
                return parts == [self.server.bucket]  # type: ignore[attr-defined]

            def _validate_unsigned_subset(self, body: bytes) -> bool:
                if self.headers.get("Authorization") or self.headers.get("x-amz-security-token"):
                    self._send_status(400)
                    return False
                if self.headers.get("x-oss-s3-compat"):
                    self._send_status(400)
                    return False
                expected_hash = hashlib.sha256(body).hexdigest()
                if self.headers.get("x-amz-content-sha256") != expected_hash:
                    self._send_status(400)
                    return False
                return True

            def _record(self, operation: str, key: str) -> None:
                self.server.requests.append(  # type: ignore[attr-defined]
                    {
                        "method": self.command,
                        "operation": operation,
                        "key": key,
                    }
                )

            def _send_list(self, prefix: str) -> None:
                root = ET.Element("ListBucketResult")
                ET.SubElement(root, "Name").text = self.server.bucket  # type: ignore[attr-defined]
                ET.SubElement(root, "Prefix").text = prefix
                ET.SubElement(root, "KeyCount").text = "0"
                objects = self.server.objects  # type: ignore[attr-defined]
                count = 0
                for key in sorted(objects):
                    if not key.startswith(prefix):
                        continue
                    contents = ET.SubElement(root, "Contents")
                    ET.SubElement(contents, "Key").text = key
                    ET.SubElement(contents, "Size").text = str(len(objects[key]))
                    count += 1
                for element in root.iter("KeyCount"):
                    element.text = str(count)
                ET.SubElement(root, "IsTruncated").text = "false"
                body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_status(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        return Handler
