"""Read-only Dahua DVR JSON-RPC client.

All API behavior here is based on api_spec.md. Details the spec does not
document are marked with TODO(spec) and kept in one place so they can be
corrected once verified against the device.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx
from dotenv import load_dotenv

_RPC2_PATH = "/RPC2"
_LOGIN_PATH = "/RPC2_Login"

# TODO(spec): api_spec.md confirms POST /RPC2_Login rpc=global.login and its
# response fields, but not the request-side credential fields or their values.
# The fields and challenge values below come from the known Dahua two-step
# login flow and still need confirmation against this DVR.
_LOGIN_CHALLENGE_CODE = 268632079
_CLIENT_TYPE = "Web3.0"
_AUTHORITY_TYPE = "Default"
_PASSWORD_TYPE = "Default"

# Device identity calls: (method, request params, merged field, response key).
# getProductDefinition's request name is documented by api_spec.md and
# har_summary.txt. The other calls show no parameters in either source, so an
# empty params object is the smallest assumption.
# TODO(spec): confirm that getDeviceType, getDeviceClass and getSoftwareVersion
# accept an empty params object. get2DCode is intentionally not called here.
_DEVICE_INFO_CALLS: tuple[tuple[str, dict[str, Any], str, str], ...] = (
    ("magicBox.getDeviceType", {}, "device_type", "type"),
    ("magicBox.getDeviceClass", {}, "device_class", "type"),
    ("magicBox.getSoftwareVersion", {}, "software_version", "version"),
    (
        "magicBox.getProductDefinition",
        {"name": "BuildDate"},
        "build_date",
        "definition",
    ),
)


class DvrError(Exception):
    """Base class for DVR errors.

    Messages never contain the password or the session id.
    """


class DvrConfigError(DvrError):
    """Missing or invalid local configuration."""


class DvrAuthError(DvrError):
    """The DVR rejected the login."""


class DvrSessionExpired(DvrError):
    """The session is missing, rejected, or no longer usable."""


class DvrRpcError(DvrError):
    """The DVR answered an RPC with an error object."""

    def __init__(
        self, method: str, status: int, payload: dict[str, Any]
    ) -> None:
        self.method = method
        self.status = status
        self.code = _error_code(payload)
        super().__init__(_rpc_diagnostic(method, status, payload))


class _DvrHttpError(DvrError):
    def __init__(
        self, status_code: int, payload: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"DVR returned HTTP {status_code}")


def _normalize_base_url(host: str) -> str:
    value = host.strip()
    if not value:
        raise DvrConfigError("DVR_HOST is empty")

    if "://" not in value:
        value = f"http://{value}"

    url = httpx.URL(value)
    if url.scheme not in ("http", "https") or not url.host:
        raise DvrConfigError("DVR_HOST must be a hostname or an http(s) URL")

    # Keep scheme/host/port only; copy_with avoids depending on URL.netloc's type.
    base = url.copy_with(path="/", query=None, fragment=None)
    return str(base).rstrip("/")


def _error_code(payload: dict[str, Any]) -> Any:
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _error_message(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else None


def _response_key_names(payload: dict[str, Any]) -> str:
    top_level = ", ".join(sorted(str(key) for key in payload)) or "(none)"
    params = payload.get("params")
    if isinstance(params, dict) and params:
        nested = ", ".join(sorted(str(key) for key in params))
        return f"{top_level}; params: {nested}"
    return top_level


def _result_type(payload: dict[str, Any]) -> str:
    """Describe the result value's type without exposing non-boolean values."""
    result = payload.get("result")
    if isinstance(result, bool):
        return f"bool {result}"
    return "other"


def _info_type(payload: dict[str, Any]) -> str:
    """Describe params.info's container type, never its contents."""
    params = payload.get("params")
    if not isinstance(params, dict) or "info" not in params:
        return "missing"
    info = params.get("info")
    if isinstance(info, dict):
        return "dict"
    if isinstance(info, list):
        return "list"
    return "other"


def _rpc_diagnostic(method: str, status: int, payload: dict[str, Any]) -> str:
    """Describe an RPC failure using names and types only, never values."""
    code = _error_code(payload)
    message = _error_message(payload)
    keys = _response_key_names(payload)

    parts = [f"DVR RPC {method} failed", f"HTTP {status}"]
    if code is not None:
        parts.append(f"code {code}")
    text = f"{parts[0]} ({', '.join(parts[1:])})"
    if message:
        text += f": {message}"
    return (
        f"{text}; result: {_result_type(payload)}; "
        f"params.info: {_info_type(payload)}; response keys: {keys}"
    )


# TODO(spec): api_spec.md does not mark which methods return data. The methods
# this client calls all return a params object, so a successful call to them
# must include params.
_DATA_RETURNING_METHODS = frozenset(
    {
        "global.keepAlive",
        "magicBox.getDeviceType",
        "magicBox.getDeviceClass",
        "magicBox.getSoftwareVersion",
        "magicBox.getProductDefinition",
    }
)


def _rpc_succeeded(method: str, payload: dict[str, Any]) -> bool:
    """Apply the DVR's success rules.

    A call succeeds when result is boolean true and, for a data-returning
    method, params is present. A benign error object, including one with a
    non-zero code, does not fail a call whose result is true.
    """
    if payload.get("result") is not True:
        return False
    if method in _DATA_RETURNING_METHODS and "params" not in payload:
        return False
    return True


def _is_challenge_code(code: Any) -> bool:
    return code == _LOGIN_CHALLENGE_CODE or code == str(_LOGIN_CHALLENGE_CODE)


def _login_failure_message(
    step: int, status: int, payload: dict[str, Any]
) -> str:
    """Describe a login failure without exposing secret values."""
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message") if isinstance(error, dict) else None

    params = payload.get("params")
    keys = (
        ", ".join(sorted(str(key) for key in params))
        if isinstance(params, dict)
        else ""
    )

    parts = [f"Login step {step} failed", f"HTTP {status}"]
    if code is not None:
        parts.append(f"code {code}")
    text = f"{parts[0]} ({', '.join(parts[1:])})"
    if isinstance(message, str) and message:
        text += f": {message}"
    text += f"; params keys: {keys or '(none)'}"
    return text


def _hash_password(username: str, password: str, realm: str, nonce: str) -> str:
    # TODO(spec): the spec names realm/random but not the digest algorithm.
    ha1 = hashlib.md5(
        f"{username}:{realm}:{password}".encode("utf-8")
    ).hexdigest().upper()
    return hashlib.md5(
        f"{username}:{nonce}:{ha1}".encode("utf-8")
    ).hexdigest().upper()


class DvrClient:
    """One DVR session. Create one instance per device session.

    No module-level state is used, so callers can later manage many devices and
    connect only when needed.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
        verify: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not username:
            raise DvrConfigError("DVR_USER is empty")
        if not password:
            raise DvrConfigError("DVR_PASS is empty")

        self._username = username
        self._password = password
        self._session_id: str | None = None
        self._rpc_id = 0
        self._http = httpx.Client(
            base_url=_normalize_base_url(host),
            timeout=timeout,
            verify=verify,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    @classmethod
    def from_env(cls, **kwargs: Any) -> "DvrClient":
        """Build a client from DVR_HOST, DVR_USER, and DVR_PASS.

        python-dotenv reads .env; this code never prints or returns its contents.
        """
        load_dotenv()
        names = ("DVR_HOST", "DVR_USER", "DVR_PASS")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise DvrConfigError(
                "Missing environment variable(s): " + ", ".join(missing)
            )
        return cls(
            os.environ["DVR_HOST"],
            os.environ["DVR_USER"],
            os.environ["DVR_PASS"],
            **kwargs,
        )

    def __enter__(self) -> "DvrClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            self.logout()
        finally:
            self.close()
        return False

    def __repr__(self) -> str:
        return f"DvrClient(base_url={str(self._http.base_url).rstrip('/')!r})"

    @property
    def is_logged_in(self) -> bool:
        return self._session_id is not None

    def login(self) -> None:
        """Log in using the two-step Dahua challenge flow.

        api_spec.md confirms the endpoint, the method, and that a successful
        response contains a top-level "session". Everything request-side that
        the spec does not name is marked TODO(spec).
        """
        if self._session_id:
            return

        # TODO(spec): userName, password, clientType, authorityType and
        # passwordType are not named in api_spec.md. The spec lists
        # params.authorization and params.encryption, which this flow neither
        # sends nor reads. There is exactly one attempt per step and no retry.

        # Step 1: request the realm/random challenge. No session is sent.
        status, challenge = self._login_post(
            1,
            {
                "userName": self._username,
                "password": "",
                "clientType": _CLIENT_TYPE,
            },
            session=None,
            request_id=1,
        )

        session_one = challenge.get("session")
        challenge_params = challenge.get("params")
        challenge_params = (
            challenge_params if isinstance(challenge_params, dict) else {}
        )
        realm = challenge_params.get("realm")
        nonce = challenge_params.get("random")

        # A direct success is also accepted; in that case no second call is made.
        if (
            challenge.get("error") is None
            and challenge.get("result") is True
            and isinstance(session_one, str)
            and session_one
        ):
            self._session_id = session_one
            return

        # The expected challenge is error 268632079 plus realm, random and a
        # top-level session.
        if not (
            _is_challenge_code(_error_code(challenge))
            and isinstance(realm, str)
            and isinstance(nonce, str)
            and isinstance(session_one, str)
            and session_one
        ):
            raise DvrAuthError(_login_failure_message(1, status, challenge))

        # Step 2: answer the challenge, sending the step-1 session.
        digest = _hash_password(self._username, self._password, realm, nonce)
        status, answer = self._login_post(
            2,
            {
                "userName": self._username,
                "password": digest,
                "clientType": _CLIENT_TYPE,
                "authorityType": _AUTHORITY_TYPE,
                "passwordType": _PASSWORD_TYPE,
            },
            session=session_one,
            request_id=2,
        )

        if answer.get("error") is None and answer.get("result") is True:
            returned_session = answer.get("session")
            self._session_id = (
                returned_session
                if isinstance(returned_session, str) and returned_session
                else session_one
            )
            return

        self._session_id = None
        raise DvrAuthError(_login_failure_message(2, status, answer))

    def keepalive(self) -> int | None:
        """Send global.keepAlive and return the DVR's timeout value if present."""
        payload = self._rpc("global.keepAlive")
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}
        timeout = params.get("timeout")
        return timeout if isinstance(timeout, int) else None

    def get_device_info(self) -> dict[str, Any]:
        """Read the four documented device identity fields.

        Each RPC is isolated: a failure is recorded and the remaining calls
        still run. The returned dict contains the merged fields plus
        succeeded_calls and failed_calls. Failed entries contain only the method
        name and error code -- never an error message or response value.
        """
        info: dict[str, Any] = {
            "device_type": None,
            "device_class": None,
            "software_version": None,
            "build_date": None,
            "succeeded_calls": [],
            "failed_calls": [],
        }

        for method, params, field, response_key in _DEVICE_INFO_CALLS:
            try:
                payload = self._rpc(method, params)
            except DvrError as exc:
                code = exc.code if isinstance(exc, DvrRpcError) else None
                info["failed_calls"].append(
                    {"method": method, "error_code": code}
                )
                continue

            call_params = payload.get("params")
            if isinstance(call_params, dict):
                info[field] = call_params.get(response_key)
            info["succeeded_calls"].append(method)

        return info

    def logout(self) -> None:
        """Forget the local session.

        TODO(spec): api_spec.md states that a logout request was not observed
        and that the logout method/endpoint is unknown. No server-side logout
        call is made here, because guessing a method name would violate the
        spec. The DVR will expire the session after its keepAlive timeout.
        """
        self._session_id = None

    def close(self) -> None:
        self._http.close()

    def _login_post(
        self,
        step: int,
        params: dict[str, str],
        *,
        session: str | None,
        request_id: int,
    ) -> tuple[int, dict[str, Any]]:
        try:
            return self._post(
                _LOGIN_PATH,
                "global.login",
                params,
                session=session,
                request_id=request_id,
            )
        except _DvrHttpError as exc:
            if exc.payload is not None:
                raise DvrAuthError(
                    _login_failure_message(step, exc.status_code, exc.payload)
                ) from None
            raise DvrAuthError(
                f"Login step {step} failed (HTTP {exc.status_code})"
            ) from None
        except DvrError as exc:
            raise DvrAuthError(f"Login step {step} failed: {exc}") from None

    def _require_session(self) -> None:
        if not self._session_id:
            raise DvrSessionExpired("No active DVR session; call login() first")

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_session()
        try:
            status, payload = self._post(
                _RPC2_PATH,
                method,
                params or {},
                session=self._session_id,
            )
        except _DvrHttpError as exc:
            raise DvrRpcError(method, exc.status_code, exc.payload or {}) from None

        session = payload.get("session")
        if isinstance(session, str) and session:
            self._session_id = session

        if not _rpc_succeeded(method, payload):
            # TODO(spec): api_spec.md does not document an invalid-session error
            # code. A failed response that omits the otherwise-common "session"
            # field is the only expiry signal available, so it is treated as
            # one. This check does not depend on whether an "error" key exists.
            if "session" not in payload:
                self._session_id = None
                raise DvrSessionExpired(_rpc_diagnostic(method, status, payload))
            raise DvrRpcError(method, status, payload)

        return payload

    def _post(
        self,
        path: str,
        method: str,
        params: dict[str, Any],
        *,
        session: str | None = None,
        request_id: int | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if request_id is None:
            self._rpc_id += 1
            request_id = self._rpc_id
        else:
            self._rpc_id = max(self._rpc_id, request_id)

        body: dict[str, Any] = {
            "method": method,
            "params": params,
            "id": request_id,
        }
        if session is not None:
            body["session"] = session

        try:
            response = self._http.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise DvrError("Timed out while contacting the DVR") from exc
        except httpx.HTTPError as exc:
            raise DvrError("Could not contact the DVR") from exc

        if not response.is_success:
            error_payload = None
            try:
                candidate = response.json()
                if isinstance(candidate, dict):
                    error_payload = candidate
            except ValueError:
                pass
            raise _DvrHttpError(response.status_code, error_payload)

        try:
            payload = response.json()
        except ValueError as exc:
            raise DvrError("DVR returned a response that was not JSON") from exc

        if not isinstance(payload, dict):
            raise DvrError("DVR returned an unexpected response shape")

        return response.status_code, payload
