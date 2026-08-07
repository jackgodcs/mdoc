from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReleaseBuildTests(unittest.TestCase):
    def test_release_build_is_deterministic_and_manifest_matches(self):
        command = [sys.executable, str(ROOT / "scripts" / "build_release.py")]
        subprocess.run(command, check=True, capture_output=True, text=True)
        asset = ROOT / "dist" / "mdoc-1.0.0.zip"
        first = hashlib.sha256(asset.read_bytes()).hexdigest()
        subprocess.run(command, check=True, capture_output=True, text=True)
        second = hashlib.sha256(asset.read_bytes()).hexdigest()
        self.assertEqual(first, second)
        manifest = json.loads((ROOT / "dist" / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(first, manifest["sha256"])
        self.assertEqual("Apache-2.0", manifest["license"])

if __name__ == "__main__":
    unittest.main()
