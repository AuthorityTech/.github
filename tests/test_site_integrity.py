import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


class SiteIntegrityShell(unittest.TestCase):
    def test_every_command_block_fails_at_the_first_failed_gate(self):
        source = Path(".github/actions/site-integrity/action.yml").read_text()
        blocks = re.findall(r"      run: \|\n((?:        .*\n|\n)+)", source)
        exercised = 0
        for block in blocks:
            names = re.findall(r"\$(SITE_INTEGRITY_(?:INSTALL_COMMAND|SCHEMA_COMMAND|EXTRA_COMMANDS))", block)
            if not names:
                continue
            exercised += 1
            for command in ["false", "false | true", "echo \"$ABSENT_GATE_VARIABLE\""]:
                with self.subTest(owner=names[0], command=command), tempfile.TemporaryDirectory() as tmp:
                    marker = Path(tmp) / "should-not-run"
                    env = {**os.environ, "RUNNER_TEMP": tmp, names[0]: command + f"\ntouch '{marker}'\n"}
                    env.pop("ABSENT_GATE_VARIABLE", None)
                    result = subprocess.run(["bash", "-c", textwrap.dedent(block)], env=env, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(marker.exists())
            with tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(["bash", "-c", textwrap.dedent(block)], env={**os.environ, "RUNNER_TEMP": tmp, names[0]: "true\n"}, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(exercised, 3)
