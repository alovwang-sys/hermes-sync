"""Small in-memory WebDAV server for backend conformance tests."""

from __future__ import annotations

import contextlib
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterator


class FakeWebDavServer:
    def __init__(self):
        self._objects: Dict[str, bytes] = {}
        self._collections: set[str] = {""}
        self._requests: list[dict[str, str]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.objects = self._objects  # type: ignore[attr-defined]
        self._server.collections = self._collections  # type: ignore[attr-defined]
        self._server.requests = self._requests  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeWebDavServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)

    @contextlib.contextmanager
    def running(self) -> Iterator["FakeWebDavServer"]:
        with self:
            yield self

    def assert_protocol_covered(self) -> None:
        operations = {request["operation"] for request in self._requests}
        required = {"PROPFIND", "MKCOL", "PUT", "GET", "DELETE"}
        missing = sorted(required - operations)
        if missing:
            raise AssertionError(f"fake WebDAV protocol coverage missing: {missing}")

    @staticmethod
    def _handler_class():
        class Handler(BaseHTTPRequestHandler):
            server_version = "FakeWebDavHarness/1.0"

            def do_MKCOL(self) -> None:
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                self._record("MKCOL", key)
                collections = self.server.collections  # type: ignore[attr-defined]
                if key in collections:
                    self._send_status(405)
                    return
                parent = self._parent_key(key)
                if parent not in collections:
                    self._send_status(409)
                    return
                collections.add(key)
                self._send_status(201)

            def do_PROPFIND(self) -> None:
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                if self.headers.get("Depth") != "infinity":
                    self._send_status(400)
                    return
                collections = self.server.collections  # type: ignore[attr-defined]
                if key not in collections:
                    self._send_status(404)
                    return
                self._record("PROPFIND", key)
                self._send_propfind(key)

            def do_PUT(self) -> None:
                key = self._object_key()
                if key is None or not key:
                    self._send_status(400)
                    return
                parent = self._parent_key(key)
                if parent not in self.server.collections:  # type: ignore[attr-defined]
                    self._send_status(409)
                    return
                length = int(self.headers.get("Content-Length") or "0")
                self.server.objects[key] = self.rfile.read(length)  # type: ignore[attr-defined]
                self._record("PUT", key)
                self._send_status(201)

            def do_GET(self) -> None:
                key = self._object_key()
                if key is None:
                    self._send_status(400)
                    return
                objects = self.server.objects  # type: ignore[attr-defined]
                if key not in objects:
                    self._send_status(404)
                    return
                self._record("GET", key)
                self.send_response(200)
                self.send_header("Content-Length", str(len(objects[key])))
                self.end_headers()
                self.wfile.write(objects[key])

            def do_DELETE(self) -> None:
                key = self._object_key()
                if key is None or not key:
                    self._send_status(400)
                    return
                self._record("DELETE", key)
                objects = self.server.objects  # type: ignore[attr-defined]
                collections = self.server.collections  # type: ignore[attr-defined]
                if key in objects:
                    del objects[key]
                    self._send_status(204)
                    return
                if key in collections:
                    for path in list(objects):
                        if path.startswith(key + "/"):
                            del objects[path]
                    for path in sorted(list(collections), reverse=True):
                        if path == key or path.startswith(key + "/"):
                            collections.remove(path)
                    self._send_status(204)
                    return
                self._send_status(404)

            def log_message(self, format: str, *args) -> None:
                return

            def _object_key(self) -> str | None:
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.query:
                    return None
                return urllib.parse.unquote(parsed.path.strip("/"))

            @staticmethod
            def _parent_key(key: str) -> str:
                parts = [part for part in key.split("/") if part]
                return "/".join(parts[:-1])

            def _record(self, operation: str, key: str) -> None:
                self.server.requests.append(  # type: ignore[attr-defined]
                    {
                        "method": self.command,
                        "operation": operation,
                        "key": key,
                    }
                )

            def _send_propfind(self, key: str) -> None:
                ET.register_namespace("D", "DAV:")
                root = ET.Element("{DAV:}multistatus")
                objects = self.server.objects  # type: ignore[attr-defined]
                prefix = key.rstrip("/")
                for object_key in sorted(objects):
                    if prefix and not object_key.startswith(prefix + "/"):
                        continue
                    response = ET.SubElement(root, "{DAV:}response")
                    href = "/" + urllib.parse.quote(object_key, safe="/-_.~")
                    ET.SubElement(response, "{DAV:}href").text = href
                body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                self.send_response(207)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_status(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

        return Handler
