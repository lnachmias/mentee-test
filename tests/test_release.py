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
    def test_first_image_starts_at_zero_patch(self):
        self.assertEqual(release.next_image_version([], "1.0.0"), "1.0.0")
        self.assertEqual(release.next_image_version([], "1.0.9"), "1.0.0")

    def test_patch_is_numeric_and_ignores_non_release_tags(self):
        self.assertEqual(release.next_image_version(["latest", "1.0.1", "1.0.2", "bad"], "1.0.0"), "1.0.3")

    def test_patch_rolls_over_after_three(self):
        tags = []
        for expected in ("1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.1.0", "1.1.1"):
            actual = release.next_image_version(tags, "1.0.0")
            self.assertEqual(actual, expected)
            tags.append(actual)

    def test_old_large_patch_versions_roll_forward(self):
        self.assertEqual(release.next_image_version(["1.0.9", "1.0.10"], "1.0.0"), "1.1.0")

    def test_versions_never_go_backwards(self):
        self.assertEqual(release.next_image_version(["1.2.3"], "1.0.0"), "1.3.0")
        self.assertEqual(release.next_image_version(["1.0.8"], "2.0.0"), "2.0.0")

    def test_invalid_app_version_is_rejected(self):
        with self.assertRaises(ValueError):
            release.next_image_version([], "01.0.0")


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

    def test_existing_packaging_revision_reuses_the_same_version(self):
        images = [{"imageTags": ["1.0.2", "packaging-abc"]}, {"imageTags": ["1.0.3", "packaging-other"]}]
        self.assertEqual(release.select_image_version(images, "1.0.0", "abc"), ("1.0.2", False))

    def test_new_packaging_commit_bumps_the_image(self):
        images = [{"imageTags": ["1.0.2", "packaging-abc"]}]
        self.assertEqual(release.select_image_version(images, "1.0.0", "new"), ("1.0.3", True))

    def test_new_packaging_commit_after_patch_three_increments_minor(self):
        images = [{"imageTags": ["1.0.3", "packaging-abc"]}]
        self.assertEqual(release.select_image_version(images, "1.0.0", "new"), ("1.1.0", True))

    def test_existing_images_without_a_marker_get_one_new_release(self):
        self.assertEqual(release.select_image_version([{"imageTags": ["1.0.1"]}], "1.0.0", "new"), ("1.0.2", True))

    def test_ambiguous_marker_never_selects_a_possibly_incorrect_baked_version(self):
        images = [{"imageTags": ["1.0.0", "1.0.1", "packaging-abc"]}]
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            release.select_image_version(images, "1.0.0", "abc")


class DeploymentTests(unittest.TestCase):
    @patch("scripts.release.Path.write_text")
    @patch("scripts.release.check_service")
    @patch("scripts.release.wait_for_deployment", return_value="https://example.test/")
    @patch("scripts.release.create_deployment", return_value=2)
    @patch("scripts.release.aws")
    def test_release_preserves_rollback_and_verifies_the_requested_deployment(self, aws, create, wait, verify, write):
        previous = {
            "version": 1, "state": "ACTIVE", "createdAt": "earlier",
            "containers": {"app": {"image": "repo:1.0.0", "ports": {"8080": "HTTP"}}},
            "publicEndpoint": {"containerName": "app", "containerPort": 8080},
        }
        aws.side_effect = [
            {"imageDetails": [{"imageDigest": "sha256:example"}]},
            {"containerServices": [{"currentDeployment": previous}]},
        ]
        release.deploy("repo:1.0.1", "1.0.1")
        config = create.call_args.args[0]
        self.assertEqual(config["publicEndpoint"]["healthCheck"]["path"], "/healthz")
        self.assertEqual(config["publicEndpoint"]["healthCheck"]["successCodes"], "200")
        self.assertEqual(config["containers"]["app"]["image"], "repo:1.0.1")
        rollback = json.loads(write.call_args_list[0].args[0])
        self.assertEqual(set(rollback), {"serviceName", "containers", "publicEndpoint"})
        self.assertEqual(rollback["containers"], previous["containers"])
        wait.assert_called_once_with(2, "repo:1.0.1")
        verify.assert_called_once_with("https://example.test/", "1.0.0", "1.0.1")

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
    def test_tag_and_baked_version_mismatch_never_deploys(self, aws, create):
        with self.assertRaises(ValueError):
            release.deploy("repo:1.0.0", "1.0.1")
        aws.assert_not_called()
        create.assert_not_called()


class VerificationTests(unittest.TestCase):
    @patch("scripts.check_service.urllib.request.urlopen")
    def test_expected_live_versions_succeed(self, urlopen):
        versions = {"app_version": "1.0.0", "image_version": "1.0.1"}
        urlopen.side_effect = [io.BytesIO(b'{"status":"ok"}'), io.BytesIO(json.dumps(versions).encode())]
        self.assertEqual(check_service("https://example.test", "1.0.0", "1.0.1"), versions)

    @patch("scripts.check_service.time.sleep")
    @patch("scripts.check_service.time.monotonic", side_effect=[0, 0, 121])
    @patch("scripts.check_service.urllib.request.urlopen")
    def test_healthy_old_version_is_not_a_successful_release(self, urlopen, monotonic, sleep):
        urlopen.side_effect = [io.BytesIO(b'{"status":"ok"}'),
                               io.BytesIO(b'{"app_version":"1.0.0","image_version":"1.0.0"}')]
        with self.assertRaisesRegex(RuntimeError, "Unexpected live versions"):
            check_service("https://example.test", "1.0.0", "1.0.1")
