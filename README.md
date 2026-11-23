# mentee-test

A small Python service that reports independent app and container image versions.

Print the version locally (Python 3.13, no dependencies required):

```sh
python3 hello_world.py
```

Without a built image, `image_version` is `local`. The app version is `1.0.0`.

Build and run the container:

```sh
docker build --build-arg IMAGE_VERSION=1.0.0 -t mentee-test:1.0.0 .
docker run --rm --name mentee-test -p 8080:8080 --read-only --cap-drop=ALL --security-opt=no-new-privileges mentee-test:1.0.0
```

In another terminal, inspect the live version and health:

```sh
curl http://localhost:8080/version
curl http://localhost:8080/healthz
```

`/` and `/version` return:

```json
{"message": "Hello, world!", "app_version": "1.0.0", "image_version": "1.0.0"}
```

`/healthz` returns `{"status": "ok"}`. The service listens on port 8080 and
writes access and error logs to stdout/stderr. For local HTTP development:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:8080 hello_world:app
```

## Versioning

- **App version:** `APP_VERSION` in `hello_world.py`, initially `1.0.0`.
- **Image version:** an independent `Major.Minor.Patch` value passed as
  `IMAGE_VERSION` during the Docker build. It is baked into `/app/IMAGE_VERSION`
  and the OCI image version label. Python reads the baked file.
- A container cannot automatically discover the registry tag it was pulled with.
  The release pipeline must use the same value for the build argument and image tag,
  and publish immutable tags. Adding another tag does not change the baked version.
- For example, a base image security fix can keep app `1.0.0` and produce image
  `1.0.1`. Update the pinned base digest, rebuild with `IMAGE_VERSION=1.0.1`,
  and publish the image as `mentee-test:1.0.1`.

## Container choices

The Dockerfile uses an official slim Python image pinned by digest, a hash-verified
Gunicorn dependency, a non-root user, root-owned application files, a health check,
and graceful termination. Only explicitly allowed files enter the build context.
Dependency installation is cached separately from source and image version changes.
There are no compiler or OS package installation steps and no extra build stage
because this application needs only a pure Python wheel.

AWS deployment and CI/CD are the next stages; nothing is provisioned yet.
