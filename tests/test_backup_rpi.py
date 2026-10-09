"""Daily backups are reused by content, including HA's partial-labelled full scope."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from tools.backup_rpi import APP, backup_info, capture, main, newest_backup, valid_backup


class BackupPolicyTests(unittest.TestCase):
    def test_native_backup_requires_ha_hermes_and_all_common_folders(self):
        backup = {"type": "partial", "homeassistant": "2026.10.0",
                  "addons": [{"slug": APP}], "folders": ["share", "ssl", "media", "addons/local"]}
        self.assertTrue(valid_backup(backup))
        self.assertFalse(valid_backup({**backup, "folders": ["ssl"]}))
        self.assertFalse(valid_backup({**backup, "homeassistant": None}))
        self.assertFalse(valid_backup({**backup, "addons": []}))
        self.assertTrue(valid_backup({"type": "full", "addons": [{"slug": APP}]}))

    def test_selection_skips_newer_incomplete_backup_without_creating_one(self):
        listing = '{"data":{"backups":[{"slug":"older","date":"2026-10-08"},' \
                  '{"slug":"newer","date":"2026-10-09"}]}}'
        with patch("tools.backup_rpi.capture", return_value=listing) as calls, \
             patch("tools.backup_rpi.backup_info", side_effect=[
                 {"type": "partial", "addons": [{"slug": APP}]},
                 {"type": "full", "addons": [{"slug": APP}]}]):
            self.assertEqual(newest_backup("helper"), "older")
        calls.assert_called_once_with(["helper", "ha backups list --raw-json"])

    def test_no_eligible_backup_stops_without_creating_one(self):
        with patch("tools.backup_rpi.capture", return_value='{"data":{"backups":[]}}') as calls:
            with self.assertRaises(ValueError):
                newest_backup("helper")
        self.assertEqual(calls.call_count, 1)

    def test_failure_and_invalid_slug_never_echo_secrets_or_run_injected_commands(self):
        with patch("tools.backup_rpi.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 1, "SYNTHETIC_SECRET", "SYNTHETIC_SECRET")):
            with self.assertRaises(ValueError) as caught:
                capture(["helper"])
        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
        with patch("tools.backup_rpi.capture") as calls:
            with self.assertRaises(ValueError):
                backup_info("helper", "slug; bad-command")
        calls.assert_not_called()

    def test_encrypted_restore_targets_only_hermes_and_does_not_print_key(self):
        data = {"type": "full", "addons": [{"slug": APP}], "protected": True}
        output = io.StringIO()
        with patch("sys.argv", ["backup_rpi", "--backup", "safe_slug", "--restore"]), \
             patch("tools.backup_rpi.backup_info", return_value=data), \
             patch("tools.backup_rpi.capture", side_effect=[
                 '{"config":{"create_backup":{"password":"SYNTHETIC_SECRET"}}}', ""]) as calls, \
             contextlib.redirect_stdout(output):
            main()
        command = calls.call_args_list[-1].args[0][-1]
        self.assertIn(f"--app {APP} --homeassistant=false", command)
        self.assertIn("--password SYNTHETIC_SECRET", command)
        self.assertNotIn("SYNTHETIC_SECRET", output.getvalue())

    def test_missing_encryption_key_stops_before_restore(self):
        data = {"type": "full", "addons": [{"slug": APP}], "protected": True}
        with patch("sys.argv", ["backup_rpi", "--backup", "safe_slug", "--restore"]), \
             patch("tools.backup_rpi.backup_info", return_value=data), \
             patch("tools.backup_rpi.capture", return_value=
                   '{"config":{"create_backup":{"password":null}}}') as calls:
            with self.assertRaises(ValueError):
                main()
        self.assertEqual(calls.call_count, 1)
