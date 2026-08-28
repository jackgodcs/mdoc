from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReleaseBuildTests(unittest.TestCase):
    def test_release_build_is_deterministic_and_manifest_matches(self):
        command = [sys.executable, str(ROOT / "scripts" / "build_release.py")]
        subprocess.run(command, check=True, capture_output=True, text=True)
        asset = ROOT / "dist" / "mdoc-1.3.3-windows-x64.zip"
        first = hashlib.sha256(asset.read_bytes()).hexdigest()
        subprocess.run(command, check=True, capture_output=True, text=True)
        second = hashlib.sha256(asset.read_bytes()).hexdigest()
        self.assertEqual(first, second)
        self.assertEqual([asset.name], sorted(path.name for path in (ROOT / "dist").iterdir()))
        with zipfile.ZipFile(asset) as package:
            names = set(package.namelist())
            manifest_bytes = package.read("PACKAGE-MANIFEST.json")
            manifest = json.loads(manifest_bytes)
            installer_script = package.read("install-mdoc.ps1")
            runtime_repair_script = package.read("repair-mdoc-runtime.ps1")
            installer_launcher = package.read("\u5b89\u88c5 mdoc.cmd")
        self.assertIn("安装 mdoc.cmd", names)
        self.assertIn("install-mdoc.ps1", names)
        self.assertIn("repair-mdoc-runtime.ps1", names)
        self.assertIn("bootstrap/toolchain-bootstrap.json", names)
        self.assertIn("runtime/requirements-v1.json", names)
        self.assertIn("runtime-bootstrap/mdoc_install_transaction.py", names)
        self.assertNotIn("skill/mdoc/tests/test_public_contract.py", names)
        self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
        self.assertTrue(installer_script.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(runtime_repair_script.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"chcp 65001 > nul", installer_launcher)
        self.assertIn(b'"path": "\\u5b89\\u88c5 mdoc.cmd"', manifest_bytes)
        self.assertEqual("2026.08.1", manifest["runtime_contract"]["toolchain_version"])
        self.assertEqual(">=3.12.0,<3.13.0", manifest["runtime_contract"]["python"])

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell installer test")
    def test_windows_powershell_installer_validates_chinese_manifest_filename(self):
        command = [sys.executable, str(ROOT / "scripts" / "build_release.py")]
        subprocess.run(command, check=True, capture_output=True, text=True)
        asset = ROOT / "dist" / "mdoc-1.3.3-windows-x64.zip"
        powershell = os.environ.get("WINDIR", r"C:\Windows")
        powershell = str(Path(powershell) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        self.assertTrue(Path(powershell).is_file())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_root = root / "package"
            runtime_root = root / "runtime"
            installation = root / "installation"
            with zipfile.ZipFile(asset) as package:
                package.extractall(package_root)
                manifest = json.loads(package.read("PACKAGE-MANIFEST.json"))
                requirements_hash = hashlib.sha256(package.read("runtime/requirements-v1.json")).hexdigest()
            state = runtime_root / "state" / "installed-runtime.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({
                "toolchain_version": manifest["runtime_contract"]["toolchain_version"],
                "python_contract": manifest["runtime_contract"]["python"],
                "profile": manifest["runtime_contract"]["profile"],
                "requirements_sha256": requirements_hash,
                "capability_probe": "ready",
                "python_source": "system-or-user",
            }), encoding="utf-8")
            result = subprocess.run([
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(package_root / "install-mdoc.ps1"),
                "-Profile",
                "Offline",
                "-SkipRuntimeRepair",
                "-Python",
                sys.executable,
                "-Destination",
                str(installation),
                "-RuntimeRoot",
                str(runtime_root),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue((installation / "SKILL.md").is_file())

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell runtime probe test")
    def test_windows_powershell_runtime_probe_skips_failed_python_candidate(self):
        powershell = os.environ.get("WINDIR", r"C:\Windows")
        powershell = Path(powershell) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        self.assertTrue(powershell.is_file())

        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "probe.ps1"
            repair = ROOT / "repair-mdoc-runtime.ps1"
            script.write_text(
                "$ErrorActionPreference = 'Stop'\n"
                f"$repair = '{str(repair).replace("'", "''")}'\n"
                "$source = Get-Content -LiteralPath $repair -Encoding UTF8 -Raw\n"
                "$start = $source.IndexOf('function Invoke-MdocProcess')\n"
                "$end = $source.IndexOf('$work = Join-Path')\n"
                ". ([ScriptBlock]::Create($source.Substring($start, $end - $start)))\n"
                "$failed = Join-Path $env:WINDIR 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
                "if (Test-MdocPython $failed) { throw 'MDOC-TEST-FAILED-CANDIDATE-ACCEPTED' }\n"
                f"$identity = Test-MdocPython '{str(Path(sys.executable)).replace("'", "''")}'\n"
                "if (-not $identity) { throw 'MDOC-TEST-VALID-PYTHON-REJECTED' }\n"
                "Write-Output 'MDOC-PYTHON-PROBE-OK'\n",
                encoding="utf-8",
            )
            result = subprocess.run([
                str(powershell),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("MDOC-PYTHON-PROBE-OK", result.stdout)

if __name__ == "__main__":
    unittest.main()
