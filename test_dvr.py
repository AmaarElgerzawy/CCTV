"""Manual smoke test: log in, read device identity, log out.

Output contains the four non-sensitive device fields plus per-call success and
failure metadata. It never includes the password, session id, or any get2DCode
value. Run with the project's virtualenv active:

    python test_dvr.py
    python test_dvr.py --verify-logout
"""

import argparse
import json
import sys

from dvr_client import DvrClient, DvrError


def _report_verification(result: bool | None) -> int:
    """Print the one-shot logout verification result and return a status."""
    if result is True:
        print("Logout verification: old session was rejected (session is closed).")
        return 0
    if result is False:
        print(
            "Logout verification: old session was NOT rejected; "
            "the session may still be open."
        )
        return 1
    print(
        "Logout verification: could not determine whether the old session "
        "was rejected."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Log in to the DVR, read device identity, then log out."
    )
    parser.add_argument(
        "--verify-logout",
        action="store_true",
        help=(
            "after logout, make one global.keepAlive call with the old session "
            "and report whether it was rejected"
        ),
    )
    args = parser.parse_args(argv)

    dvr: DvrClient | None = None
    status = 0
    logout_done = False

    try:
        dvr = DvrClient.from_env()

        # One login flow per run: login() makes at most its two protocol steps
        # and never retries automatically.
        dvr.login()
        print("Login: OK")

        info = dvr.get_device_info()
        print("Device info:")
        print(json.dumps(info, indent=2, sort_keys=True, ensure_ascii=False))

        if args.verify_logout:
            if not dvr.is_logged_in:
                print(
                    "Logout verification: no active local session to verify.",
                    file=sys.stderr,
                )
                status = 1
            else:
                verification = dvr.logout(verify=True)
                logout_done = True
                print("Logout: server-side logout succeeded")
                if _report_verification(verification):
                    status = 1
    except DvrError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        status = 1
    except Exception as exc:  # no secrets in this message
        print(f"FAILED: unexpected {type(exc).__name__}", file=sys.stderr)
        status = 1
    finally:
        if dvr is not None:
            try:
                if not logout_done and dvr.is_logged_in:
                    dvr.logout()
                    logout_done = True
                    if status == 0:
                        print("Logout: server-side logout succeeded")
            except DvrError as exc:
                print(f"Logout failed: {exc}", file=sys.stderr)
                if status == 0:
                    status = 1
            finally:
                dvr.close()

    return status


if __name__ == "__main__":
    raise SystemExit(main())
