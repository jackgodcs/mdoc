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
        asset = ROOT / "dist" / "mdoc-1.3.0-windows-x64.zip"
        first = hashlib.sha256(asset.read_bytes()).hexdigest()
        subprocess.run(command, check=True, capture_output=True, text=True)
        second = hashlib.sha256(asset.read_bytes()).hexdigest()
        self.assertEqual(first, second)
        self.assertEqual([asset.name], sorted(path.name for path in (ROOT / "dist").iterdir()))
        import zipfile
        with zipfile.ZipFile(asset) as package:
            names = set(package.namelist())
            manifest = json.loads(package.read("PACKAGE-MANIFEST.json"))
        self.assertIn("安装 mdoc.cmd", names)
        self.assertIn("install-mdoc.ps1", names)
        self.assertIn("repair-mdoc-runtime.ps1", names)
        self.assertIn("bootstrap/toolchain-bootstrap.json", names)
        self.assertIn("runtime/requirements-v1.json", names)
        self.assertIn("runtime-bootstrap/mdoc_install_transaction.py", names)
        self.assertNotIn("skill/mdoc/tests/test_public_contract.py", names)
        self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
        self.assertEqual("2026.08.1", manifest["runtime_contract"]["toolchain_version"])
        self.assertEqual(">=3.12.0,<3.13.0", manifest["runtime_contract"]["python"])

if __name__ == "__main__":
    unittest.main()
