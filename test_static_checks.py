import os
import subprocess
import sys
import unittest


class StaticChecks(unittest.TestCase):
    def test_pyflakes_no_undefined_name(self) -> None:
        try:
            import pyflakes  # noqa: F401
        except Exception:
            self.skipTest("pyflakes not installed")
        root = os.path.dirname(os.path.abspath(__file__))
        py_files = []
        for name in os.listdir(root):
            if name.endswith(".py"):
                py_files.append(os.path.join(root, name))
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes"] + py_files,
            cwd=root,
            capture_output=True,
            text=True,
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        for line in combined.splitlines():
            if not line.strip():
                continue
            if "undefined name" in line.lower():
                self.fail(f"pyflakes found undefined name: {line}")
            # Allow only unused imports/variables
            if "imported but unused" in line.lower():
                continue
            if "assigned to but never used" in line.lower():
                continue
            # Any other message is a failure
            self.fail(f"pyflakes reported disallowed issue: {line}")


if __name__ == "__main__":
    unittest.main()
