from __future__ import annotations

import io
import unittest

from release_check import configure_utf8_console


class ReleaseCheckConsoleTests(unittest.TestCase):
    def test_configure_utf8_console_handles_chinese_diagnostics_from_cp1252_host(self):
        output = io.BytesIO()
        stream = io.TextIOWrapper(output, encoding="cp1252", errors="strict", write_through=True)

        configure_utf8_console((stream,))
        stream.write("version marker mismatch: 开始使用.txt")
        stream.flush()

        self.assertEqual("version marker mismatch: 开始使用.txt", output.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
