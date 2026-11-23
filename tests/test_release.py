import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import release
from scripts.check_service import check_service


class VersionTests(unittest.TestCase):
    def test_source_version_is_used_exactly_including_patch(self):
        self.assertEqual(release.select_image_version([], "1.0.9", "abc"), ("1.0.9", True))
        self.assertEqual(release.select_image_version([], "2.0.0", "abc"), ("2.0.0", True))

    def test_invalid_versions_are_rejected(self):
        for version in ("01.0.0", "v1.0.0", "latest", "1.0", "1.0.0; echo bad", "1.0.0\n"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.validate_version(version)

    def test_new_release_must_exceed_highest_published_version(self):
        for version in ("1.0.9", "1.0.2"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "highest published"):
                release.select_image_version([{"imageTags": ["1.0.10", "latest"]}], version, "new")

    def test_patch_has_no_artificial_cap_or_rollover(self):
        images = [{"imageTags": ["1.0.3", "packaging-old"]}]
        self.assertEqual(release.select_image_version(images, "1.0.4", "new"), ("1.0.4", True))


class PackagingCommitTests(unittest.TestCase):
    def test_git_history_ignores_documentation_and_detects_packaging_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=root, check=True, capture_output=True, text=True,
                ).stdout.strip()

            git("init", "--initial-branch=main")
            (root / "Dockerfile").write_text("FROM scratch\n")
            (root / "README.md").write_text("Initial documentation\n")
            git("add", "Dockerfile", "README.md")
            git("commit", "-m", "Initial packaging")
            first = git("rev-parse", "HEAD")
            with patch.object(release, "ROOT", root):
                self.assertEqual(release.packaging_revision(), first)
                (root / "README.md").write_text("Documentation only\n")
                git("add", "README.md")
                git("commit", "-m", "Update documentation")
                self.assertNotEqual(git("rev-parse", "HEAD"), first)
                self.assertEqual(release.packaging_revision(), first)
                (root / "Dockerfile").write_text("FROM scratch\nLABEL updated=true\n")
                git("add", "Dockerfile")
                git("commit", "-m", "Change packaging")
                self.assertEqual(release.packaging_revision(), git("rev-parse", "HEAD"))
                self.assertNotEqual(release.packaging_revision(), first)

    def test_existing_packaging_revision_reuses_the_source_version(self):
        images = [{"imageTags": ["1.0.2", "packaging-abc"]}, {"imageTags": ["1.0.3", "packaging-other"]}]
        self.assertEqual(release.select_image_version(images, "1.0.2", "abc"), ("1.0.2", False))

    def test_packaging_change_without_version_bump_is_rejected(self):
        images = [{"imageTags": ["1.0.2", "packaging-abc"]}]
        with self.assertRaisesRegex(RuntimeError, "Bump APP_VERSION"):
            release.select_image_version(images, "1.0.2", "new")

    def test_existing_images_without_a_marker_cannot_be_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            release.select_image_version([{"imageTags": ["1.0.1"]}], "1.0.1", "new")


class DeploymentTests(unittest.TestCase):
    @patch("scripts.release.Path.write_text")
    @patch("scripts.release.check_service")
    @patch("scripts.release.wait_for_deployment", return_value="https://example.test/")
    @patch("scripts.release.create_deployment", return_value=2)
    @patch("scripts.release.resolve_image")
    @patch("scripts.release.aws")
    def test_release_preserves_rollback_and_verifies_the_requested_deployment(self, aws, resolve, create, wait, verify, write):
        previous = {
            "version": 1, "state": "ACTIVE", "createdAt": "earlier",
            "containers": {"app": {"image": "repo:1.0.0", "ports": {"8080": "HTTP"}}},
            "publicEndpoint": {"containerName": "app", "containerPort": 8080},
        }
        resolve.return_value = {"version": "1.0.1", "image_uri": "repo:1.0.1", "image_digest": "sha256:example"}
        aws.return_value = {"containerServices": [{"currentDeployment": previous}]}
        release.deploy("1.0.1")
        config = create.call_args.args[0]
        self.assertEqual(config["publicEndpoint"]["healthCheck"]["path"], "/healthz")
        self.assertEqual(config["publicEndpoint"]["healthCheck"]["successCodes"], "200")
        self.assertEqual(config["containers"]["app"]["image"], "repo:1.0.1")
        rollback = json.loads(write.call_args_list[0].args[0])
        self.assertEqual(set(rollback), {"serviceName", "containers", "publicEndpoint"})
        self.assertEqual(rollback["containers"], previous["containers"])
        wait.assert_called_once_with(2, "repo:1.0.1")
        verify.assert_called_once_with("https://example.test/", "1.0.1")

    @patch("scripts.release.time.sleep")
    @patch("scripts.release.aws")
    def test_wait_does_not_accept_the_previous_active_release(self, aws, sleep):
        aws.side_effect = [
            {"containerServices": [{"currentDeployment": {"version": 1, "state": "ACTIVE"}}]},
            {"deployments": [{"version": 2, "state": "ACTIVATING"}]},
            {"containerServices": [{"url": "https://example.test/", "currentDeployment": {
                "version": 2, "state": "ACTIVE", "containers": {"app": {"image": "repo:1.0.1"}}}}]},
        ]
        self.assertEqual(release.wait_for_deployment(2, "repo:1.0.1"), "https://example.test/")
        sleep.assert_called_once()

    @patch("scripts.release.aws")
    def test_failed_candidate_fails_the_release(self, aws):
        aws.side_effect = [
            {"containerServices": [{"currentDeployment": {"version": 1, "state": "ACTIVE"}}]},
            {"deployments": [{"version": 2, "state": "FAILED"}]},
        ]
        with self.assertRaisesRegex(RuntimeError, "deployment 2 failed"):
            release.wait_for_deployment(2, "repo:1.0.1")

    @patch("scripts.release.aws")
    def test_active_candidate_with_wrong_image_is_rejected(self, aws):
        aws.return_value = {"containerServices": [{"currentDeployment": {
            "version": 2, "state": "ACTIVE", "containers": {"app": {"image": "repo:1.0.0"}}}}]}
        with self.assertRaisesRegex(RuntimeError, "unexpected image"):
            release.wait_for_deployment(2, "repo:1.0.1")

    @patch("scripts.release.create_deployment")
    @patch("scripts.release.aws")
    def test_invalid_version_never_deploys(self, aws, create):
        with self.assertRaises(ValueError):
            release.deploy("latest", rollback=True)
        aws.assert_not_called()
        create.assert_not_called()

    @patch("scripts.release.create_deployment")
    @patch("scripts.release.aws")
    def test_missing_ecr_image_never_creates_a_deployment(self, aws, create):
        aws.side_effect = [
            {"repositories": [{"repositoryUri": "repo", "imageTagMutability": "IMMUTABLE"}]},
            subprocess.CalledProcessError(254, ["aws"], stderr="ImageNotFoundException"),
        ]
        with self.assertRaises(subprocess.CalledProcessError):
            release.deploy("1.0.0", rollback=True)
        create.assert_not_called()

    @patch("scripts.release.aws")
    def test_mutable_repository_is_rejected(self, aws):
        aws.return_value = {"repositories": [{"repositoryUri": "repo", "imageTagMutability": "MUTABLE"}]}
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            release.resolve_image("1.0.0")
        self.assertEqual(aws.call_count, 1)

    @patch("scripts.release.Path.write_text")
    @patch("scripts.release.check_service")
    @patch("scripts.release.wait_for_deployment", return_value="https://example.test/")
    @patch("scripts.release.create_deployment", return_value=3)
    @patch("scripts.release.aws")
    def test_rollback_uses_selected_version_and_preserves_service_configuration(self, aws, create, wait, verify, write):
        previous = {
            "version": 2, "state": "ACTIVE",
            "containers": {
                "app": {"image": "repo:2.0.0", "ports": {"8080": "HTTP"}, "environment": {"MODE": "demo"}},
                "sidecar": {"image": "sidecar:stable"},
            },
            "publicEndpoint": {"containerName": "app", "containerPort": 8080,
                               "healthCheck": {"path": "/healthz", "successCodes": "200"}},
        }
        aws.side_effect = [
            {"repositories": [{"repositoryUri": "repo", "imageTagMutability": "IMMUTABLE"}]},
            {"imageDetails": [{"imageDigest": "sha256:old"}]},
            {"containerServices": [{"currentDeployment": previous}]},
        ]
        # Current source can be newer; verification must use the selected image's version.
        with patch.object(release, "APP_VERSION", "2.0.0"):
            release.deploy("1.0.0", rollback=True)
        config = create.call_args.args[0]
        self.assertEqual(config["containers"]["app"]["image"], "repo:1.0.0")
        self.assertEqual(config["containers"]["app"]["environment"], {"MODE": "demo"})
        self.assertEqual(config["containers"]["sidecar"], previous["containers"]["sidecar"])
        self.assertEqual(config["publicEndpoint"], previous["publicEndpoint"])
        self.assertEqual(previous["containers"]["app"]["image"], "repo:2.0.0")
        metadata = json.loads(write.call_args_list[1].args[0])
        self.assertEqual(metadata["image_digest"], "sha256:old")
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertEqual(metadata["operation"], "rollback")
        wait.assert_called_once_with(3, "repo:1.0.0")
        verify.assert_called_once_with("https://example.test/", "1.0.0")

    @patch("scripts.release.Path.write_text")
    @patch("scripts.release.create_deployment")
    @patch("scripts.release.resolve_image", return_value={"image_uri": "repo:1.0.0"})
    @patch("scripts.release.aws")
    def test_rollback_without_an_active_deployment_is_rejected(self, aws, resolve, create, write):
        for service in ({}, {"currentDeployment": {"state": "FAILED"}}):
            aws.return_value = {"containerServices": [service]}
            with self.assertRaisesRegex(RuntimeError, "active deployment"):
                release.deploy("1.0.0", rollback=True)
        create.assert_not_called()
        write.assert_not_called()

    @patch("scripts.release.time.monotonic", side_effect=[0, 901])
    def test_deployment_timeout_fails(self, monotonic):
        with self.assertRaisesRegex(RuntimeError, "did not become active"):
            release.wait_for_deployment(2, "repo:1.0.1")


class VerificationTests(unittest.TestCase):
    @patch("scripts.check_service.urllib.request.urlopen")
    def test_expected_live_version_succeeds(self, urlopen):
        versions = {"version": "1.0.1"}
        urlopen.side_effect = [io.BytesIO(b'{"status":"ok"}'), io.BytesIO(json.dumps(versions).encode())]
        self.assertEqual(check_service("https://example.test", "1.0.1"), versions)

    @patch("scripts.check_service.time.sleep")
    @patch("scripts.check_service.time.monotonic", side_effect=[0, 0, 121])
    @patch("scripts.check_service.urllib.request.urlopen")
    def test_healthy_old_version_is_not_a_successful_release(self, urlopen, monotonic, sleep):
        urlopen.side_effect = [io.BytesIO(b'{"status":"ok"}'),
                               io.BytesIO(b'{"version":"1.0.0"}')]
        with self.assertRaisesRegex(RuntimeError, "Unexpected live version"):
            check_service("https://example.test", "1.0.1")
