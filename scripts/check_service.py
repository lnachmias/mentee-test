"""Wait for the HTTP endpoint to report the expected app and image versions."""

import argparse
import json
import time
import urllib.error
import urllib.request


def check_service(url, app_version, image_version, timeout=120):
    deadline = time.monotonic() + timeout
    last_error = "Service did not respond"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=5) as response:
                if json.load(response) != {"status": "ok"}:
                    raise ValueError("Health endpoint did not report ok")
            with urllib.request.urlopen(url.rstrip("/") + "/version", timeout=5) as response:
                versions = json.load(response)
            if (versions.get("app_version"), versions.get("image_version")) != (app_version, image_version):
                raise ValueError(f"Unexpected live versions: {versions}")
            print(json.dumps(versions))
            return versions
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = str(error)
            time.sleep(2)
    raise RuntimeError(f"Service verification timed out: {last_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--image-version", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    check_service(args.url, args.app_version, args.image_version, args.timeout)
