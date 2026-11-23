"""Release helpers using the AWS CLI; no AWS SDK dependency is required."""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hello_world import APP_VERSION
from scripts.check_service import check_service

REGION = "eu-west-2"
SERVICE = "mentee-test"
REPOSITORY = "mentee-test"
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ROOT = Path(__file__).resolve().parents[1]
PACKAGING_PATHS = (
    "Dockerfile", ".dockerignore", "requirements.txt", "hello_world.py",
    ".github/workflows/ship.yml", "scripts/release.py", "scripts/check_service.py",
)


def aws(service, operation, *args):
    command = ["aws", service, operation, *args, "--region", REGION, "--output", "json", "--no-cli-pager"]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def validate_version(version):
    if not SEMVER.fullmatch(version):
        raise ValueError("Version must be Major.Minor.Patch (for example 1.0.1)")
    return tuple(map(int, version.split(".")))


def packaging_revision():
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "HEAD", "--", *PACKAGING_PATHS],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    revision = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise RuntimeError("No packaging commit found; check out the complete Git history")
    return revision


def select_image_version(images, app_version, revision):
    requested = validate_version(app_version)
    marker = f"packaging-{revision}"
    for image in images:
        tags = image.get("imageTags", [])
        if app_version in tags:
            if marker not in tags:
                raise RuntimeError("Version is already published for different packaging. "
                                   "Bump APP_VERSION in hello_world.py; existing tags are immutable.")
            return app_version, False
    tags = [tag for image in images for tag in image.get("imageTags", [])]
    published = [validate_version(tag) for tag in tags if SEMVER.fullmatch(tag)]
    if published and requested <= max(published):
        raise RuntimeError("A new release must exceed the highest published version. "
                           "Bump APP_VERSION in hello_world.py, or use the rollback workflow.")
    return app_version, True


def repository_info():
    repository = aws("ecr", "describe-repositories", "--repository-names", REPOSITORY)["repositories"][0]
    if repository.get("imageTagMutability") != "IMMUTABLE":
        raise RuntimeError("ECR must use immutable tags for reliable releases and rollback")
    return repository


def write_outputs(values):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps(values))


def choose_version():
    repository = repository_info()
    images = aws("ecr", "describe-images", "--repository-name", REPOSITORY)["imageDetails"]
    revision = packaging_revision()
    version, build_required = select_image_version(images, APP_VERSION, revision)
    values = {
        "version": version,
        "image_uri": f"{repository['repositoryUri']}:{version}",
        "packaging_image_uri": f"{repository['repositoryUri']}:packaging-{revision}",
        "packaging_commit": revision,
        "build_required": "true" if build_required else "false",
    }
    write_outputs(values)


def resolve_image(version):
    """Resolve an existing immutable tag, independently of the checked-out version."""
    validate_version(version)
    repository = repository_info()
    detail = aws("ecr", "describe-images", "--repository-name", REPOSITORY,
                 "--image-ids", f"imageTag={version}")["imageDetails"][0]
    return {"version": version, "image_uri": f"{repository['repositoryUri']}:{version}",
            "image_digest": detail["imageDigest"]}


def wait_for_deployment(version, image, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        service = aws("lightsail", "get-container-services", "--service-name", SERVICE)["containerServices"][0]
        deployment = service.get("currentDeployment", {})
        if deployment.get("version") == version and deployment.get("state") == "ACTIVE":
            if deployment["containers"]["app"]["image"] != image:
                raise RuntimeError("The active deployment is using an unexpected image")
            return service["url"]
        history = aws("lightsail", "get-container-service-deployments", "--service-name", SERVICE)["deployments"]
        if any(item.get("version") == version and item.get("state") == "FAILED" for item in history):
            raise RuntimeError(f"Lightsail deployment {version} failed")
        time.sleep(10)
    raise RuntimeError(f"Lightsail deployment {version} did not become active within {timeout}s")


def create_deployment(config):
    result = aws("lightsail", "create-container-service-deployment", "--cli-input-json", json.dumps(config))
    service = result["containerService"]
    deployment = service.get("nextDeployment") or service.get("currentDeployment")
    if not deployment or not deployment.get("version"):
        raise RuntimeError("Lightsail did not return a deployment version")
    return deployment["version"]


def deploy(version, rollback=False):
    # Immutable ECR tags are used because Lightsail documents this URI format.
    # Record the resolved digest separately for traceability and rollback audits.
    metadata = resolve_image(version)
    image = metadata["image_uri"]
    service = aws("lightsail", "get-container-services", "--service-name", SERVICE)["containerServices"][0]
    previous = service.get("currentDeployment")
    if rollback and (not previous or previous.get("state") != "ACTIVE"):
        raise RuntimeError("Rollback requires an active deployment whose configuration can be reused")
    if previous:
        previous_config = {
            "serviceName": SERVICE,
            "containers": previous["containers"],
            "publicEndpoint": previous["publicEndpoint"],
        }
        Path("previous-deployment.json").write_text(json.dumps(previous_config, indent=2) + "\n")

    config = {
        "serviceName": SERVICE,
        "containers": {"app": {"image": image, "ports": {"8080": "HTTP"}}},
        "publicEndpoint": {
            "containerName": "app", "containerPort": 8080,
            "healthCheck": {"path": "/healthz", "successCodes": "200", "intervalSeconds": 10,
                            "timeoutSeconds": 5, "healthyThreshold": 2, "unhealthyThreshold": 3},
        },
    }
    if rollback:
        # Retain ports, environment, sidecars, and health settings; change only the app image.
        config = copy.deepcopy(previous_config)
        config["containers"]["app"]["image"] = image
    metadata["operation"] = "rollback" if rollback else "release"
    Path("release-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    deployment_version = create_deployment(config)
    url = wait_for_deployment(deployment_version, image)
    check_service(url, version)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"Live service: {url}\n\nApp/image version: `{version}`\n\n"
                         f"Digest: `{metadata['image_digest']}`\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    for command in ("image", "deploy", "rollback"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if args.command == "version":
            choose_version()
        elif args.command == "image":
            write_outputs(resolve_image(args.version))
        else:
            if args.command == "deploy" and args.version != APP_VERSION:
                raise ValueError("Release version must match APP_VERSION; use rollback for an older image")
            deploy(args.version, rollback=args.command == "rollback")
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        sys.exit(1)
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
