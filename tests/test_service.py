import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

import hello_world


def request(path, method="GET"):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(PATH_INFO=path, REQUEST_METHOD=method, QUERY_STRING="", **{"wsgi.input": io.BytesIO()})
    response = {}

    def start_response(status, headers, exc_info=None):
        response.update(status=status, headers=dict(headers))

    body = validator(hello_world.app)(environ, start_response)
    try:
        response["body"] = b"".join(body)
    finally:
        if hasattr(body, "close"):
            body.close()
    return response


class ServiceTests(unittest.TestCase):
    def test_version_endpoints(self):
        for path in ("/", "/version"):
            with self.subTest(path=path):
                response = request(path)
                self.assertEqual(response["status"], "200 OK")
                self.assertEqual(json.loads(response["body"]),
                                 {"message": "Hello, world!", "version": hello_world.APP_VERSION})
                self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    def test_health_and_http_errors(self):
        self.assertEqual(json.loads(request("/healthz")["body"]), {"status": "ok"})
        self.assertEqual(request("/missing")["status"], "404 Not Found")
        self.assertEqual(request("/version", "POST")["status"], "405 Method Not Allowed")
        self.assertEqual(request("/version", "HEAD")["body"], b"")
        self.assertEqual(request("/version", "HEAD")["headers"]["Content-Length"],
                         request("/version")["headers"]["Content-Length"])

    def test_source_version_is_the_only_version_in_local_and_container_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source = Path(hello_world.__file__).read_text()
            source = source.replace(f'APP_VERSION = "{hello_world.APP_VERSION}"', 'APP_VERSION = "2.0.0"')
            (target / "hello_world.py").write_text(source)
            output = subprocess.check_output([sys.executable, str(target / "hello_world.py")], text=True)
            self.assertEqual(json.loads(output), {"message": "Hello, world!", "version": "2.0.0"})
