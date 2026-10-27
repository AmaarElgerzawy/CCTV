import os
import re
import unittest


class IdCheck(unittest.TestCase):
    def test_no_duplicate_ids(self) -> None:
        root = os.path.dirname(os.path.abspath(__file__))
        for name in ("static/index.html", "static/devices.html"):
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            ids = re.findall(r'id="([^"]+)"', content)
            seen = set()
            dups = []
            for i in ids:
                if i in seen:
                    dups.append(i)
                seen.add(i)
            self.assertEqual(dups, [], f"Duplicate ids in {name}: {dups}")


if __name__ == "__main__":
    unittest.main()
