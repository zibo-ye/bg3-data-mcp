"""Scoped dialog participation research; never exports graphs or selects answers."""
import json
from pathlib import Path

from .native import NativeBackend
from .protocol import identifier, reject


def dialogue_source():
    return (Path(__file__).parent / "lua" / "dialogue_probe.lua").read_text(encoding="utf-8")


class NativeDialogueResearch:
    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def inspect(self, actor_uuid, session_id):
        actor_uuid = identifier(actor_uuid)
        if not isinstance(session_id, str) or not 1 <= len(session_id.encode("utf-8")) <= 128:
            reject("invalid_session", "expected an observed engine session ID")
        spec = {"actor_uuid": actor_uuid, "session_id": session_id}
        encoded = json.dumps(json.dumps(spec))
        source = "local f=function(...)\n" + dialogue_source() + "\nend\nreturn f(Ext.Json.Parse(" + encoded + "))"
        return self.backend._evaluate(source)
