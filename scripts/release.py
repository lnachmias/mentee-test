"""Release helpers using the AWS CLI; no AWS SDK dependency is required."""

import argparse
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


def aws(service, operation, *args):
    command = ["aws", service, operation, *args, "--region", REGION, "--output", "json", "--no-cli-pager"]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def next_image_version(tags, app_version):
    if not SEMVER.fullmatch(app_version):
        raise ValueError("APP_VERSION must be Major.Minor.Patch")
    versions = [tuple(map(int, tag.split("."))) for tag in tags if SEMVER.fullmatch(tag)]
    baseline = tuple(map(int, app_version.split(".")))
    if versions:
        major, minor, patch = max(versions)
        baseline = max(baseline, (major, minor, patch + 1))
    return ".".join(map(str, baseline))


def choose_version():
    repository = aws("ecr", "describe-repositories", "--repository-names", REPOSITORY)["repositories"][0]
    images = aws("ecr", "describe-images", "--repository-name", REPOSITORY)["imageDetails"]
    tags = [tag for image in images for tag in image.get("imageTags", [])]
    version = next_image_version(tags, APP_VERSION)
    values = {
        "app_version": APP_VERSION,
        "image_version": version,
        "image_uri": f"{repository['repositoryUri']}:{version}",
    }
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps(values))


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


def deploy(image, image_version):
    # Immutable ECR tags are used because Lightsail documents this URI format.
    # Record the resolved digest separately for traceability and rollback audits.
    if not SEMVER.fullmatch(image_version) or not image.endswith(":" + image_version):
        raise ValueError("The image URI tag must match IMAGE_VERSION")
    detail = aws("ecr", "describe-images", "--repository-name", REPOSITORY,
                 "--image-ids", f"imageTag={image_version}")["imageDetails"][0]
    service = aws("lightsail", "get-container-services", "--service-name", SERVICE)["containerServices"][0]
    previous = service.get("currentDeployment")
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
    metadata = {"app_version": APP_VERSION, "image_version": image_version,
                "image_uri": image, "image_digest": detail["imageDigest"]}
    Path("release-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    version = create_deployment(config)
    url = wait_for_deployment(version, image)
    check_service(url, APP_VERSION, image_version)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"Live service: {url}\n\nApp: `{APP_VERSION}` · Image: `{image_version}`\n\n"
                         f"Digest: `{detail['imageDigest']}`\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--image", required=True)
    deploy_parser.add_argument("--image-version", required=True)
    args = parser.parse_args()
    try:
        if args.command == "version":
            choose_version()
        else:
            deploy(args.image, args.image_version)
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        sys.exit(1)
