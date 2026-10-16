"""HTTPS device authorization and explicitly approved collection transport."""

import json
import platform
import re
import socket
import ssl
import time
from urllib import error, parse, request
import uuid

from . import __version__

CLIENT_ID = "hoomy-collector"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_SERVER = "https://my.hoomy.dev"


class AgentError(Exception):
    pass


class APIError(AgentError):
    def __init__(self, status, code):
        self.status = status
        self.code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_:-]{1,64}", code) else "request_failed"
        super().__init__(f"Hoomy request refused ({status}, {self.code}).")


def origin(value):
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parsed = parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise AgentError("Select an HTTPS server origin, without a path or credentials.")
    try:
        port = parsed.port
    except ValueError:
        raise AgentError("Invalid server port.") from None
    hostname = parsed.hostname.encode("idna").decode().lower()
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    return "https://" + hostname + (":" + str(port) if port and port != 443 else "")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, server, ca_file=None):
        self.server = origin(server)
        context = ssl.create_default_context(cafile=ca_file)
        if not ca_file:
            try:
                import certifi
                context.load_verify_locations(certifi.where())
            except ImportError:
                pass
        self.opener = request.build_opener(NoRedirect(), request.HTTPSHandler(context=context))

    def trusted_url(self, url):
        absolute = parse.urljoin(self.server + "/", url)
        parsed = parse.urlsplit(absolute)
        if origin(parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))) != self.server or parsed.username or parsed.password or parsed.fragment:
            raise AgentError("Server discovery refers to an untrusted origin.")
        return absolute

    def call(self, method, url, payload=None, token=None, form=False, idempotency_key=None):
        headers = {"Accept": "application/json", "X-Request-ID": str(uuid.uuid4())}
        data = None
        if payload is not None:
            data = (parse.urlencode(payload) if form else json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = request.Request(self.trusted_url(url), data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as response:
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise AgentError("Hoomy returned an oversized response.")
                value = json.loads(body) if body else {}
        except error.HTTPError as exc:
            try:
                body = json.loads(exc.read(65536))
                code = body.get("error") or body.get("code")
                if isinstance(code, dict):
                    code = code.get("code")
            except (ValueError, AttributeError):
                code = "request_failed"
            finally:
                exc.close()
            raise APIError(exc.code, code) from None
        except (error.URLError, TimeoutError, OSError):
            raise AgentError("Cannot reach Hoomy securely; no authorization state was assumed.") from None
        except (ValueError, UnicodeError):
            raise AgentError("Hoomy returned an invalid response.") from None
        if not isinstance(value, dict):
            raise AgentError("Hoomy returned an unsupported response.")
        return value


class Agent:
    def __init__(self, state, http=None, clock=time.time, sleep=time.sleep):
        self.state = state
        self.http = http or HTTP(state.server)
        self.clock = clock
        self.sleep = sleep

    def discover(self):
        result = self.http.call("GET", "/.well-known/hoomy")
        if origin(result.get("canonical_server_url", "")) != self.state.server:
            raise AgentError("The server changed its origin; explicitly select that server before login.")
        if origin(result.get("issuer", self.state.server)) != self.state.server:
            raise AgentError("This release requires authentication hosted on the selected Hoomy origin.")
        try:
            resource = result["resources"]["collector"]
            self.http.trusted_url(resource)
            endpoints = {name: self.http.trusted_url(result["endpoints"][name]) for name in ("enrolments", "device_authorization", "token")}
        except (KeyError, TypeError):
            raise AgentError("Hoomy does not advertise supported device authentication.") from None
        for name in ("revoke", "policy_proposals", "events", "receipts"):
            if name in result.get("endpoints", {}):
                endpoints[name] = self.http.trusted_url(result["endpoints"][name])
        return {"resource": resource, "endpoints": endpoints}

    def login(self, show_verification, label=None, collection_policy=None):
        with self.state.locked():
            config = self.state.config()
            existing = self.state.credentials()
            if collection_policy is None and existing and existing.get("state") == "authorized":
                self._access_token_locked()
                return self._activate_locked()
            discovery = self.discover()
            scopes = {"device:self"}
            if collection_policy is not None:
                from .policy import INGEST_SCOPES, approved_policy
                collection_policy = approved_policy(collection_policy)
                if not existing or existing.get("state") != "authorized":
                    raise AgentError("Connect this machine before proposing conversation collection.")
                if not all(name in discovery["endpoints"] for name in ("policy_proposals", "events", "receipts")):
                    raise AgentError("This server does not advertise approved conversation ingestion.")
                proposal = self._authorized_locked("POST", discovery["endpoints"]["policy_proposals"], {"proposed_policy": collection_policy})
                scopes = INGEST_SCOPES
            else:
                proposal = self.http.call("POST", discovery["endpoints"]["enrolments"], {
                "installation_id": self.state.installation_id(),
                "label": label or socket.gethostname(),
                "platform": platform.system().lower(),
                "architecture": platform.machine(),
                "agent_version": __version__,
                "proposed_policy": {"content_tier": "metadata", "roots": [], "exclusions": [], "collection_enabled": False},
                })
            try:
                authorization = self.http.call("POST", discovery["endpoints"]["device_authorization"], {
                    "client_id": CLIENT_ID, "resource": discovery["resource"], "scope": " ".join(sorted(scopes)),
                    "enrolment_ticket": proposal["enrolment_ticket"],
                }, form=True)
                device_code = authorization["device_code"]
                verification_url = self.http.trusted_url(authorization.get("verification_uri_complete") or authorization["verification_uri"])
                verification_uri = self.http.trusted_url(authorization["verification_uri"])
                user_code = authorization["user_code"]
                duration = min(1800, max(1, int(authorization["expires_in"])))
                interval = max(1, int(authorization.get("interval", 5)))
            except (KeyError, ValueError, TypeError):
                raise AgentError("Hoomy returned an incomplete device authorization.") from None
            if not isinstance(device_code, str) or not device_code or device_code in verification_url or proposal["enrolment_ticket"] in verification_url:
                raise AgentError("Hoomy returned an unsafe verification URL.")
            if not isinstance(user_code, str) or not re.fullmatch(r"[A-Za-z0-9 -]{1,32}", user_code):
                raise AgentError("Hoomy returned an invalid public confirmation code.")
            deadline = self.clock() + duration
            show_verification(verification_url, verification_uri, user_code, duration)
            while self.clock() < deadline:
                self.sleep(min(interval, max(0, deadline - self.clock())))
                if self.clock() >= deadline:
                    break
                try:
                    tokens = self.http.call("POST", discovery["endpoints"]["token"], {
                        "grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": CLIENT_ID,
                        "resource": discovery["resource"],
                    }, form=True)
                except APIError as exc:
                    if exc.status in (502, 503, 504):
                        interval = min(interval * 2, 60)
                        continue
                    if exc.code == "authorization_pending":
                        continue
                    if exc.code == "slow_down":
                        interval += 5
                        continue
                    if exc.code in ("access_denied", "expired_token"):
                        raise AgentError("Machine approval was denied or expired; run login again.") from None
                    raise
                except AgentError:
                    interval = min(interval * 2, 60)
                    continue
                config.update({"server": self.state.server, "discovery": discovery, "enrolment_id": proposal["enrolment_id"]})
                self.state.save_config(config)
                self._save_tokens(tokens, scopes)
                if collection_policy is not None and self._device_locked().get("policy") != collection_policy:
                    raise AgentError("The approved collection policy differs from the reviewed proposal; collection is stopped.")
                return self._activate_locked()
            raise AgentError("Machine approval expired; run login again for a new QR code.")

    def _activate_locked(self):
        device = self._device_locked()
        revision = device.get("policy_revision")
        if revision is None:
            raise AgentError("Hoomy returned no approved device policy revision.")
        from .policy import INGEST_SCOPES, approved_policy
        credentials = self.state.credentials()
        enabled = set(credentials.get("scope", "device:self").split()) == INGEST_SCOPES and device.get("collection_available") is True
        policy = approved_policy(device.get("policy")) if enabled else None
        self._authorized_locked("POST", "/v1/devices/self/activate", {
            "policy_revision": revision, "capabilities": ["heartbeat", "conversation.projected", "conversation.source_excluded"] if enabled else ["heartbeat"],
        })
        config = self.state.config()
        config.update({"device_id": device.get("device_id"), "policy_revision": revision, "collection_enabled": enabled, "approved_policy": policy})
        self.state.save_config(config)
        return device

    def _save_tokens(self, tokens, expected_scope=None):
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"] or str(tokens.get("token_type", "")).lower() != "bearer" or not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]:
            raise AgentError("Hoomy did not provide a renewable device authorization.")
        try:
            expires_in = max(1, int(tokens["expires_in"]))
        except (ValueError, TypeError, KeyError):
            raise AgentError("Hoomy returned an invalid token lifetime.") from None
        expected_scope = expected_scope or set((self.state.credentials() or {}).get("scope", "device:self").split())
        if not isinstance(tokens.get("scope", "device:self"), str):
            raise AgentError("Hoomy returned invalid device permissions.")
        scope = set(tokens.get("scope", "device:self").split())
        if scope != expected_scope or scope not in ({"device:self"}, {"device:self", "evidence:ingest"}):
            raise AgentError("Hoomy returned permissions outside the explicitly approved grant.")
        self.state.save_credentials({"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"], "expires_at": self.clock() + expires_in, "state": "authorized", "scope": " ".join(sorted(scope))})

    def _access_token_locked(self):
        credentials = self.state.credentials()
        if not credentials or credentials.get("state") != "authorized":
            raise AgentError("Machine requires login; remote work is stopped.")
        if credentials["expires_at"] > self.clock() + 30:
            return credentials["access_token"]
        config = self.state.config()
        # Never replay a rotating refresh after an interrupted or ambiguous exchange.
        pending = dict(credentials, state="refresh_in_progress")
        self.state.save_credentials(pending)
        try:
            tokens = self.http.call("POST", config["discovery"]["endpoints"]["token"], {
                "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"],
                "client_id": CLIENT_ID, "resource": config["discovery"]["resource"],
            }, form=True)
            self._save_tokens(tokens)
        except (AgentError, KeyError):
            self.state.save_credentials(dict(pending, state="needs_login"))
            raise AgentError("Credential renewal was refused or interrupted; run login again.") from None
        return self.state.credentials()["access_token"]

    def _authorized_locked(self, method, url, payload=None, idempotency_key=None):
        token = self._access_token_locked()
        try:
            arguments = {"idempotency_key": idempotency_key} if idempotency_key else {}
            return self.http.call(method, url, payload, token=token, **arguments)
        except APIError as exc:
            if exc.status == 401:
                credentials = self.state.credentials()
                if exc.code == "device_revoked":
                    self.state.save_credentials(dict(credentials, state="revoked"))
                    raise AgentError("Machine authorization was revoked; remote work is stopped.") from None
                self.state.save_credentials(dict(credentials, expires_at=0))
                token = self._access_token_locked()
                try:
                    return self.http.call(method, url, payload, token=token, **arguments)
                except APIError as retry:
                    if retry.status != 401:
                        raise
                credentials = self.state.credentials()
                self.state.save_credentials(dict(credentials, state="needs_login"))
                raise AgentError("Machine authorization is no longer accepted; run login again.") from None
            if exc.status == 403 and exc.code in ("device_revoked", "revoked", "membership_revoked"):
                credentials = self.state.credentials()
                self.state.save_credentials(dict(credentials, state="revoked"))
            raise

    def _device_locked(self):
        return self._authorized_locked("GET", "/v1/devices/self")

    def status(self):
        with self.state.locked():
            return self._device_locked()

    def heartbeat(self, collection=False, outbox_count=0, coverage=None):
        with self.state.locked():
            return self._authorized_locked("POST", "/v1/devices/self/heartbeat", {
                "agent_version": __version__, "outbox_count": outbox_count, "collection_enabled": collection,
                "capabilities": ["heartbeat", "conversation.projected", "conversation.source_excluded"] if collection else ["heartbeat"], "service_state": "running",
            })

    def cycle(self):
        from .collector import reconcile
        from .outbox import Backpressure, Outbox
        from .policy import INGEST_SCOPES, approved_policy, project_for_cwd, within, absolute_path
        with self.state.locked():
            device = self._device_locked()
            credentials = self.state.credentials()
            scopes = set(credentials.get("scope", "device:self").split())
            if scopes != INGEST_SCOPES or device.get("collection_available") is not True:
                return self._authorized_locked("POST", "/v1/devices/self/heartbeat", {
                    "agent_version": __version__, "outbox_count": 0, "collection_enabled": False,
                    "capabilities": ["heartbeat"], "service_state": "running"})
            policy = approved_policy(device.get("policy"))
            revision = device.get("policy_revision")
            if type(revision) is not int or revision < 1:
                raise AgentError("Collection requires a current approved policy revision.")
            config = self.state.config()
            # A changed policy requires a reviewed service/configuration update.
            if config.get("approved_policy") != policy or config.get("policy_revision") != revision or not config.get("collection_enabled"):
                raise AgentError("Collection policy changed; review and activate the machine again.")
            endpoints = config.get("discovery", {}).get("endpoints", {})
            if not all(name in endpoints for name in ("events", "receipts")):
                raise AgentError("Ingestion endpoints are missing; collection is stopped.")
            outbox = Outbox(self.state.path / "outbox")
            try:
                try:
                    coverage = reconcile(outbox, policy, revision, self.clock())
                except Backpressure:
                    coverage = {"failures": {"outbox_capacity": 1}, "scan_complete": False}
                config["collection_coverage"] = coverage
                self.state.save_config(config)
                for event, digest in outbox.pending(revision, self.clock()):
                    location = event["source_location"]
                    source_path = absolute_path(location["path"])
                    if (project_for_cwd(policy, location["cwd"]) != event["project_id"]
                            or not any(within(source_path, absolute_path(root)) for root in policy["roots"])
                            or any(within(source_path, absolute_path(root)) for root in policy["exclusions"])):
                        raise AgentError("Queued evidence is outside the current approved policy.")
                    try:
                        try:
                            receipt = self._authorized_locked("GET", endpoints["receipts"].rstrip("/") + "/" + event["event_id"])
                        except APIError as exc:
                            if exc.status != 404:
                                raise
                            receipt = self._authorized_locked("POST", endpoints["events"], event, idempotency_key=event["event_id"])
                        outbox.accepted(event, digest, receipt)
                    except AgentError:
                        outbox.failed(event["event_id"], self.clock())
                        raise
                counts = outbox.counts()
                return self._authorized_locked("POST", "/v1/devices/self/heartbeat", {
                    "agent_version": __version__, "outbox_count": counts["outbox_count"], "collection_enabled": True,
                    "capabilities": ["heartbeat", "conversation.projected", "conversation.source_excluded"], "service_state": "running"})
            finally:
                outbox.close()

    def logout(self):
        with self.state.locked():
            credentials = self.state.credentials()
            config = self.state.config()
            confirmed = False
            if credentials and config.get("discovery", {}).get("endpoints", {}).get("revoke"):
                try:
                    self.http.call("POST", config["discovery"]["endpoints"]["revoke"], {
                        "client_id": CLIENT_ID, "token": credentials["refresh_token"], "token_type_hint": "refresh_token",
                    }, form=True)
                    confirmed = True
                except AgentError:
                    pass
            self.state.clear_credentials()
            return confirmed
