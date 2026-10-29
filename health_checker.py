"""Device and channel health checks (read-only)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from cgi_client import CgiAuthError, CgiError, CgiNetworkError, DvrCgi
from dvr_client import DvrAuthError, DvrClient, DvrError, DvrLogoutError, DvrNetworkError
from registry import DeviceRegistry, ResolvedDevice


logger = logging.getLogger(__name__)


def _parse_windows() -> list[int]:
    raw = os.environ.get("LOOKBACK_WINDOWS_HOURS", "1,6,24,72")
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part)
        except Exception:
            continue
        if v > 0:
            out.append(v)
    if not out:
        out = [1, 6, 24, 72]
    return out


def _stale_default() -> int:
    try:
        v = int(os.environ.get("STALE_HOURS_DEFAULT", "24"))
    except Exception:
        v = 24
    if v < 1:
        v = 1
    if v > 720:
        v = 720
    return v


async def _get_channel_count(resolved: ResolvedDevice) -> int | None:
    client = DvrClient(resolved.base_url, resolved.username, resolved.password)
    try:
        try:
            client.login()
        except (DvrAuthError, DvrError):
            raise
        except Exception as exc:
            raise DvrNetworkError(str(exc)) from exc
        try:
            info = client.get_channels()
        finally:
            try:
                client.logout()
            except DvrLogoutError:
                pass
        # info may be dict/list
        if isinstance(info, dict):
            if isinstance(info.get("channels"), list):
                return len(info["channels"])
            if isinstance(info.get("count"), int):
                return info["count"]
        if isinstance(info, list):
            return len(info)
    finally:
        try:
            client.close()
        except Exception:
            pass
    return None


async def check_device(registry: DeviceRegistry, device_id: int, status_cache_getter=None) -> dict[str, Any]:
    # check cached tcp status if provided
    if status_cache_getter is not None:
        try:
            st = status_cache_getter(device_id)
            if st and isinstance(st, dict):
                state = st.get("state")
                if state == "offline":
                    res = {
                        "device_id": device_id,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "state": "offline",
                        "storage_state": None,
                        "storage_disks": [],
                        "storage_errors": None,
                        "device_time": None,
                        "error_class": None,
                        "channels": [],
                    }
                    try:
                        registry.save_health(device_id, res)
                    except Exception:
                        pass
                    return res
        except Exception:
            pass

    try:
        resolved = registry.resolve_device(device_id)
    except Exception:
        res = {
            "device_id": device_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "state": "auth_failed",
            "storage_state": None,
            "storage_disks": [],
            "storage_errors": None,
            "device_time": None,
            "error_class": None,
            "channels": [],
        }
        try:
            registry.save_health(device_id, res)
        except Exception:
            pass
        return res

    # get device row for unused/stale
    try:
        dev_row = registry.get_device_row(device_id)
        unused = []
        if "unused_channels" in dev_row.keys():
            try:
                v = dev_row["unused_channels"] or ""
                if isinstance(v, str) and v.strip():
                    for p in v.split(","):
                        p2 = p.strip()
                        if p2:
                            try:
                                unused.append(int(p2))
                            except Exception:
                                pass
            except Exception:
                pass
        stale_h = _stale_default()
        if "stale_hours" in dev_row.keys() and dev_row["stale_hours"] is not None:
            try:
                sh = int(dev_row["stale_hours"])
                if 1 <= sh <= 720:
                    stale_h = sh
            except Exception:
                pass
    except Exception:
        unused = []
        stale_h = _stale_default()

    # try to get channel count
    ch_count = None
    try:
        ch_count = await _get_channel_count(resolved)
    except DvrAuthError:
        res = {
            "device_id": device_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "state": "auth_failed",
            "storage_state": None,
            "storage_disks": [],
            "storage_errors": None,
            "device_time": None,
            "error_class": None,
            "channels": [],
        }
        try:
            registry.save_health(device_id, res)
        except Exception:
            pass
        return res
    except (DvrNetworkError, DvrError):
        res = {
            "device_id": device_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "state": "unreachable",
            "storage_state": None,
            "storage_disks": [],
            "storage_errors": None,
            "device_time": None,
            "error_class": None,
            "channels": [],
        }
        try:
            registry.save_health(device_id, res)
        except Exception:
            pass
        return res
    except Exception:
        res = {
            "device_id": device_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "state": "unreachable",
            "storage_state": None,
            "storage_disks": [],
            "storage_errors": None,
            "device_time": None,
            "error_class": None,
            "channels": [],
        }
        try:
            registry.save_health(device_id, res)
        except Exception:
            pass
        return res

    # setup CGI
    try:
        cgi = DvrCgi(resolved.base_url, resolved.username, resolved.password)
    except Exception:
        res = {
            "device_id": device_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "state": "unreachable",
            "storage_state": None,
            "storage_disks": [],
            "storage_errors": None,
            "device_time": None,
            "error_class": None,
            "channels": [],
        }
        try:
            registry.save_health(device_id, res)
        except Exception:
            pass
        return res

    storage_state = None
    storage_disks = []
    storage_errors = None
    try:
        st = await cgi.get_storage_health()
        storage_disks = st.get("disks") if isinstance(st.get("disks"), list) else []
        # get_storage_health returns summary; but we need detailed? task says store storage_state/storage_disks/storage_errors
        # use health
        storage_state = st.get("health")
        storage_errors = st.get("errors")
    except Exception:
        storage_state = None
        storage_disks = []
        storage_errors = None

    device_time = None
    try:
        device_time = await cgi.get_device_time()
    except Exception:
        device_time = None

    windows = _parse_windows()
    channels_res = []
    # determine channels range
    max_ch = ch_count if isinstance(ch_count, int) and ch_count > 0 else None
    # also try up to max channel from env? not specified, use count
    channels_to_check = []
    if max_ch:
        for ch in range(1, max_ch + 1):
            channels_to_check.append(ch)
    else:
        # try up to 16 as fallback
        for ch in range(1, 17):
            channels_to_check.append(ch)

    unavailable_count = 0
    stale_count = 0

    for ch in channels_to_check:
        if ch in unused:
            channels_res.append({"channel": ch, "state": "unused"})
            continue
        try:
            last = await cgi.find_last_recording(ch, device_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), windows)
        except CgiAuthError:
            res = {
                "device_id": device_id,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "state": "auth_failed",
                "storage_state": storage_state,
                "storage_disks": storage_disks,
                "storage_errors": storage_errors,
                "device_time": device_time,
                "error_class": None,
                "channels": channels_res,
            }
            try:
                registry.save_health(device_id, res)
            except Exception:
                pass
            try:
                await cgi.aclose()
            except Exception:
                pass
            return res
        except (CgiNetworkError, CgiError):
            # treat as unavailable for this channel? mark unavailable
            channels_res.append({"channel": ch, "state": "unavailable"})
            unavailable_count += 1
            continue
        except Exception:
            channels_res.append({"channel": ch, "state": "unavailable"})
            unavailable_count += 1
            continue
        if last is None:
            channels_res.append({"channel": ch, "state": "none", "window": windows[-1] if windows else None})
            # check if stale - none in lookback means stale?
            stale_count += 1
            continue
        # has last
        last_end = last.get("last_end")
        approx = bool(last.get("approximate"))
        matched_win = last.get("window")
        # check if stale by time
        is_stale = False
        try:
            if last_end:
                le = datetime.strptime(str(last_end), "%Y-%m-%d %H:%M:%S")
                # compare with device_time or now
                ref = device_time
                if ref:
                    try:
                        ref_dt = datetime.strptime(str(ref), "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        ref_dt = datetime.now()
                else:
                    ref_dt = datetime.now()
                age_hours = (ref_dt - le).total_seconds() / 3600.0
                if age_hours > stale_h + 1e-9:  # older than threshold
                    is_stale = True
        except Exception:
            pass
        if is_stale:
            channels_res.append({"channel": ch, "state": "stale", "last_end": last_end, "window": matched_win, "approximate": approx})
            stale_count += 1
        else:
            channels_res.append({"channel": ch, "state": "ok", "last_end": last_end, "window": matched_win, "approximate": approx})

    try:
        await cgi.aclose()
    except Exception:
        pass

    # classify device state with precedence
    device_state = "ok"
    # check storage problem
    if storage_state and str(storage_state).lower() != "success":
        device_state = "storage_problem"
    # check if all channels unavailable?
    if device_state not in ("storage_problem",):
        # if every checked channel is unavailable (and not unused) -> unavailable
        checked_nonunused = [c for c in channels_res if c.get("state") != "unused"]
        if checked_nonunused and all(c.get("state") == "unavailable" for c in checked_nonunused):
            device_state = "unavailable"
        elif stale_count > 0 or any(c.get("state") in ("stale", "none") for c in checked_nonunused):
            if device_state != "storage_problem":
                device_state = "stale"
    else:
        pass

    res = {
        "device_id": device_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "state": device_state,
        "storage_state": storage_state,
        "storage_disks": storage_disks,
        "storage_errors": storage_errors,
        "device_time": device_time,
        "error_class": None,
        "channels": channels_res,
    }
    try:
        registry.save_health(device_id, res)
    except Exception:
        pass
    return res
