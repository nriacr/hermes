"""tools/prepare_code_rollback.sh on a throwaway repository: no live system, no real version reservation."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "prepare_code_rollback.sh"
VERSION_STUB = """#!/bin/sh
# Test stand-in for bin/ha-version: bump the patch number in config.yaml only.
repo="$2"
current="$(sed -n 's/^version: "\\([^"]*\\)"/\\1/p' "$repo/ha-addon/config.yaml")"
next="${current%.*}.$(( ${current##*.} + 1 ))"
sed -i.bak "s/^version: \\"$current\\"/version: \\"$next\\"/" "$repo/ha-addon/config.yaml" && rm "$repo/ha-addon/config.yaml.bak"
printf '%s\\n' "$next"
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class RollbackToolTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.ha = root / "ha"
        origin = root / "origin.git"
        self.repo = self.ha / "hermes"
        (self.ha / "bin").mkdir(parents=True)
        (self.ha / "bin" / "ha-version").write_text(VERSION_STUB)
        (self.ha / "bin" / "ha-version").chmod(0o755)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "remote", "add", "origin", str(origin))
        (self.repo / "tools").mkdir()
        shutil.copy(TOOL, self.repo / "tools" / TOOL.name)
        (self.repo / "tools" / "check.sh").write_text("#!/bin/sh\nexit ${FAKE_CHECK_EXIT:-0}\n")
        (self.repo / "ha-addon" / "app" / "hermes").mkdir(parents=True)
        (self.repo / "ha-addon" / "config.yaml").write_text('name: Hermes\nversion: "1.0.0"\n')
        (self.repo / "ha-addon" / "CHANGELOG.md").write_text("# Değişiklik günlüğü\n\n## 1.0.0\n\n- ilk\n")
        (self.repo / "ha-addon" / "app" / "hermes" / "old.py").write_text("OLD = 1\n")
        for name in ("Dockerfile", "run.sh", ".dockerignore"):
            (self.repo / "ha-addon" / name).write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "release 1.0.0")
        self.good = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "ha-addon" / "app" / "hermes" / "old.py").write_text("OLD = 2\n")
        (self.repo / "ha-addon" / "app" / "hermes" / "brand_new.py").write_text("NEW = 1\n")
        (self.repo / "ha-addon" / "config.yaml").write_text('name: Hermes\nversion: "1.1.0"\n')
        (self.repo / "tools" / "later_tool.sh").write_text("echo tool\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "release 1.1.0")
        git(self.repo, "push", "-q", "origin", "main")

    def run_tool(self, commit, check_exit="0"):
        env = dict(os.environ, HA_DEPLOY_LOCK_PID="1", FAKE_CHECK_EXIT=check_exit)
        return subprocess.run(["sh", "tools/prepare_code_rollback.sh", commit], cwd=self.repo, env=env, capture_output=True, text=True)

    def test_files_added_after_the_good_release_are_removed_and_tools_stay(self):
        result = self.run_tool(self.good)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.repo / "ha-addon" / "app" / "hermes"
        self.assertEqual((app / "old.py").read_text(), "OLD = 1\n")
        self.assertFalse((app / "brand_new.py").exists())
        self.assertTrue((self.repo / "tools" / "later_tool.sh").exists())
        self.assertIn('version: "1.0.1"', (self.repo / "ha-addon" / "config.yaml").read_text())
        self.assertIn("1 dosya kaldırıldı", result.stdout)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertIn("Roll the add-on code back to 1.0.0 as 1.0.1", git(self.repo, "log", "-1", "--format=%s"))

    def test_a_failing_check_stops_with_clear_instructions_and_no_commit(self):
        head = git(self.repo, "rev-parse", "HEAD")
        result = self.run_tool(self.good, check_exit="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HATA: testler geri dönen kodla geçmedi", result.stderr)
        self.assertIn(f"reset --hard {head}", result.stderr)
        self.assertIn("Canlıya hiçbir şey kurulmadı", result.stderr)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_a_commit_outside_main_history_is_refused(self):
        result = self.run_tool("0" * 40)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
