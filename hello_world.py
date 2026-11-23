"""Print release information, or serve it over HTTP with Gunicorn."""

import json
from pathlib import Path


APP_VERSION = "1.0.0"
# Written by Docker at build time; a local Python run has no image version.
_image_version_file = Path(__file__).with_name("IMAGE_VERSION")
IMAGE_VERSION = (
    _image_version_file.read_text(encoding="utf-8").strip()
    if _image_version_file.exists()
    else "local"
)


def version_info():
    return {
        "message": "Hello, world!",
        "app_version": APP_VERSION,
        "image_version": IMAGE_VERSION,
    }


def app(environ, start_response):
    """A small WSGI application; Gunicorn handles the HTTP server."""
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")
    headers = []

    if method not in ("GET", "HEAD"):
        status, payload = "405 Method Not Allowed", {"error": "Method not allowed"}
        headers.append(("Allow", "GET, HEAD"))
    elif path in ("/", "/version"):
        status, payload = "200 OK", version_info()
    elif path == "/healthz":
        status, payload = "200 OK", {"status": "ok"}
    else:
        status, payload = "404 Not Found", {"error": "Not found"}

    body = (json.dumps(payload) + "\n").encode("utf-8")
    headers.extend([
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ])
    start_response(status, headers)
    return [b"" if method == "HEAD" else body]


if __name__ == "__main__":
    print(json.dumps(version_info()))
