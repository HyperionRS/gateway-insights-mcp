# SPDX-License-Identifier: Apache-2.0
"""The release gate's scan: current files, every historic version, messages and authors."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan_forbidden.py"
MARK = "marker" + "-" + "word"


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def scan(repo):
    env = {**os.environ, "FORBIDDEN_PATTERNS": MARK}
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        Path(self.repo, "a.txt").write_text("clean\n")

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, message="change"):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)

    def test_clean_history_passes(self):
        self.commit()
        self.assertEqual(scan(self.repo).returncode, 0)

    def test_a_line_removed_by_a_later_commit_is_still_found(self):
        Path(self.repo, "b.txt").write_text(f"{MARK}\n")
        self.commit()
        Path(self.repo, "b.txt").unlink()
        self.commit()
        out = scan(self.repo)
        self.assertEqual(out.returncode, 1)
        self.assertIn("history: blob", out.stdout)
        self.assertNotIn(MARK, out.stdout)   # the log is public: the index, never the pattern

    def test_a_path_and_a_message_are_scanned(self):
        Path(self.repo, f"{MARK}.txt").write_text("clean\n")
        self.commit(f"add {MARK}")
        out = scan(self.repo)
        self.assertEqual(out.returncode, 1)
        self.assertIn("committed file name", out.stdout)
        self.assertIn("message or author", out.stdout)
        self.assertNotIn(MARK, out.stdout)


if __name__ == "__main__":
    unittest.main()
