"""Manual smoke test: log in, read device identity, log out.

Output contains the four non-sensitive device fields plus per-call success and
failure metadata. It never includes the password, session id, or any get2DCode
value. Run with the project's virtualenv active:

    python test_dvr.py
"""

import json
import sys

from dvr_client import DvrClient, DvrError


def main() -> int:
    try:
        with DvrClient.from_env() as dvr:
            # One login flow per run: login() makes at most its two protocol
            # steps and never retries automatically.
            dvr.login()
            print("Login: OK")

            info = dvr.get_device_info()
            print("Device info:")
            print(json.dumps(info, indent=2, sort_keys=True, ensure_ascii=False))
    except DvrError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # no secrets in this message
        print(f"FAILED: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1

    print("Logout: local session cleared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
