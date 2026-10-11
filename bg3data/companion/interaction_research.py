"""Developer-only bounded ordinary-interaction capture; no request submission."""
import json
from pathlib import Path

from .native import NativeBackend
from .protocol import identifier, reject


def interaction_source():
    return (Path(__file__).parent / "lua" / "interaction_probe.lua").read_text(encoding="utf-8")


class NativeInteractionResearch:
    def __init__(self, server_evaluator=None, client_evaluator=None):
        self.server = NativeBackend(server_evaluator)
        self.client_evaluator = client_evaluator

    def _query(self, spec):
        source = "local f=function(...)\n" + interaction_source() + "\nend\nreturn f(Ext.Json.Parse(" + json.dumps(json.dumps(spec)) + "))"
        if spec["side"] == "server":
            result = self.server._evaluate(source)
        else:
            if self.client_evaluator is None:
                from .. import se
                envelope = se.eval_lua(source, "client", timeout=10)
            else:
                envelope = self.client_evaluator(source)
            if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
                reject("bridge_error", "Script Extender client interaction query failed")
            result = envelope["result"]
        for name in ("records", "errors"):
            if result.get(name) == {}:
                result[name] = []
        return result

    def query(self, actor_uuid, session_id, target_uuid, operation="read", *, include_client=False):
        actor_uuid, target_uuid = identifier(actor_uuid), identifier(target_uuid)
        if actor_uuid == target_uuid:
            reject("invalid_target", "interaction research requires another supplied entity")
        if not isinstance(session_id, str) or not 1 <= len(session_id.encode("utf-8")) <= 128:
            reject("invalid_session", "expected an observed engine session ID")
        if operation not in ("install", "read", "stop") or type(include_client) is not bool:
            raise ValueError("invalid interaction research operation")
        spec = {"actor_uuid": actor_uuid, "target_uuid": target_uuid, "session_id": session_id,
                "operation": operation, "side": "server"}
        server = self._query(spec)
        client = None
        if include_client:
            try:
                client_operation = operation if server.get("active") is True or operation == "stop" else "stop"
                client = self._query({**spec, "side": "client", "operation": client_operation,
                                      "host_user": server.get("host_user")})
            except Exception:
                # Client Lua also rolls back partial subscription installs. Never leave
                # the server half running when a requested dual install failed.
                if operation == "install":
                    cleanup_failed = False
                    for side in ("client", "server"):
                        try:
                            result = self._query({**spec, "side": side, "operation": "stop"})
                            cleanup_failed |= result.get("cleanup_failed") is True
                        except Exception:
                            cleanup_failed = True
                    if cleanup_failed:
                        reject("trace_cleanup_failed", "interaction trace installation failed and cleanup could not be confirmed")
                raise
        return {"server": server, "client": client, "execution_enabled": False,
                "normal_rules_interaction_validated": False}
