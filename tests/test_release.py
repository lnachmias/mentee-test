import io
import json
import unittest
from unittest.mock import patch

from scripts import release
from scripts.check_service import check_service


class VersionTests(unittest.TestCase):
    def test_first_image_uses_app_version(self):
        self.assertEqual(release.next_image_version([], "1.0.0"), "1.0.0")

    def test_patch_is_numeric_and_ignores_non_release_tags(self):
        self.assertEqual(release.next_image_version(["latest", "1.0.9", "1.0.10", "bad"], "1.0.0"), "1.0.11")

    def test_versions_never_go_backwards(self):
        self.assertEqual(release.next_image_version(["1.2.3"], "1.0.0"), "1.2.4")
        self.assertEqual(release.next_image_version(["1.0.8"], "2.0.0"), "2.0.0")

    def test_invalid_app_version_is_rejected(self):
        with self.assertRaises(ValueError):
            release.next_image_version([], "01.0.0")


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
