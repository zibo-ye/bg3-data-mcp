"""Developer-only immediate path preview. Never submits movement or enables actions."""
import json
from pathlib import Path

from .native import NativeBackend
from .protocol import identifier, position, reject


def path_source():
    # Separate developer entry point; deliberately absent from native.lua_source.
    return (Path(__file__).parent / "lua" / "path_probe.lua").read_text(encoding="utf-8")


class NativePathResearch:
    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def preview(self, actor_uuid, session_id, destination):
        actor_uuid = identifier(actor_uuid)
        if not isinstance(session_id, str) or not 1 <= len(session_id.encode("utf-8")) <= 128:
            reject("invalid_session", "expected an observed engine session ID")
        spec = {"actor_uuid": actor_uuid, "session_id": session_id, "position": position(destination)}
        encoded = json.dumps(json.dumps(spec, allow_nan=False))
        source = "local f=function(...)\n" + path_source() + "\nend\nreturn f(Ext.Json.Parse(" + encoded + "))"
        result = self.backend._evaluate(source)
        if result.get("cleanup_release_succeeded") is not True:
            reject("path_cleanup_failed", "path release failed; allocation state is unknown")
        if result.get("preview_valid") is not True:
            reject("path_preview_failed", "native path preview could not be copied safely")
        if result.get("nodes") == {}:
            result["nodes"] = []
        return result
