"""Release guards reject missing access without exposing credential-bearing errors."""

import subprocess
import unittest
from unittest.mock import patch
import urllib.error

from tools.check_access import AccessError, capture, github_get, validate_github


class ReleaseAccessTests(unittest.TestCase):
    def test_missing_workflow_blocks_automation_publish_but_not_normal_publish(self):
        args = ({"login": "nriacr"}, {"permissions": {"push": True}}, {"enabled": True}, {"repo"})
        validate_github(*args, require_workflow=False)
        with self.assertRaisesRegex(AccessError, "workflow"):
            validate_github(*args, require_workflow=True)
        validate_github(*args[:-1], {"repo", "workflow"}, require_workflow=True)

    def test_account_and_repository_access_are_not_interchangeable(self):
        with self.assertRaises(AccessError):
            validate_github({"login": "other-account"}, {"permissions": {"push": True}},
                            {"enabled": True}, {"repo", "workflow"}, True)
        with self.assertRaises(AccessError):
            validate_github({"login": "nriacr"}, {"permissions": {"pull": True}},
                            {"enabled": True}, {"repo", "workflow"}, True)

    def test_failed_credential_helper_does_not_echo_secret_stdout_or_stderr(self):
        failure = subprocess.CompletedProcess([], 1, "password=SYNTHETIC_SECRET", "https://SYNTHETIC_SECRET@github.com")
        with patch("tools.check_access.subprocess.run", return_value=failure):
            with self.assertRaises(AccessError) as caught:
                capture(["git", "credential", "fill"])
        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))

    def test_github_rejection_does_not_echo_response_or_credential_url(self):
        failure = urllib.error.HTTPError("https://SYNTHETIC_SECRET@github.com", 401,
                                        "SYNTHETIC_SECRET", {}, None)
        with patch("tools.check_access.urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(AccessError) as caught:
                github_get("/user", "SYNTHETIC_SECRET")
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
