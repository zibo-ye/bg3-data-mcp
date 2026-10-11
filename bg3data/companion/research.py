"""Developer-only bounded native trace; never enables or submits companion actions."""
import json

from .native import NativeBackend, lua_source
from .protocol import action, identifier, reject


class NativeRequestTrace:
    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def query(self, actor_uuid, operation="read"):
        actor_uuid = identifier(actor_uuid)
        if operation not in ("install", "read", "stop"):
            raise ValueError("invalid trace operation")
        source = "local f=function(...)\n" + lua_source("trace.lua") + "\nend\nreturn f(" + json.dumps(actor_uuid) + "," + json.dumps(operation) + ")"
        result = self.backend._evaluate(source)
        if result.get("records") == {}:
            result["records"] = []
        if result.get("errors") == {}:
            result["errors"] = []
        return result


class NativeRequestPreparation:
    """Resolve live metadata without queue writes or an execution receipt.

    A prepared plan is not a ticket to submit later: identity, session and turn
    must be checked again in the same engine call that eventually enqueues it.
    """

    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def prepare(self, actor_uuid, session_id, value, request_guid):
        actor_uuid, request_guid = identifier(actor_uuid), identifier(request_guid)
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            reject("invalid_session", "expected an observed engine session ID")
        value = action(value)
        if value["kind"] not in ("cast", "end_turn"):
            reject("unsupported_action", "native request preparation is not implemented for this action")
        if value["kind"] == "cast" and "position" in value:
            reject("unsupported_target", "native position targeting adapter is not validated")
        spec = {"actor_uuid": actor_uuid, "session_id": session_id,
                "action": value, "request_guid": request_guid}
        encoded = json.dumps(json.dumps(spec, allow_nan=False))
        source = "local f=function(...)\n" + lua_source("prepare.lua") + "\nend\nreturn f(Ext.Json.Parse(" + encoded + "))"
        return self.backend._evaluate(source)
