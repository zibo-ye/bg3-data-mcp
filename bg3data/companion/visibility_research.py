"""Developer-only LOS/concealment research, not a visibility capability.

The proposed conservative_status is diagnostic only: candidate never implies
visibility_verified. Invisibility's opaque byte has no inferred semantics.
"""
import json
from pathlib import Path

from .native import NativeBackend
from .protocol import identifier, reject

MAX_CANDIDATES = 32


def visibility_source():
    return (Path(__file__).parent / "lua" / "visibility_probe.lua").read_text(encoding="utf-8")


class NativeVisibilityResearch:
    def __init__(self, evaluator=None):
        self.backend = NativeBackend(evaluator)

    def query(self, actor_uuid, session_id, candidate_uuids):
        actor_uuid = identifier(actor_uuid)
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            reject("invalid_session", "expected an observed engine session ID")
        if not isinstance(candidate_uuids, (list, tuple)) or len(candidate_uuids) > MAX_CANDIDATES:
            reject("invalid_candidates", "expected at most 32 supplied candidate UUIDs")
        candidates = [identifier(value) for value in candidate_uuids]
        if len(set(candidates)) != len(candidates):
            reject("invalid_candidates", "candidate UUIDs must be unique")
        spec = {"actor_uuid": actor_uuid, "session_id": session_id, "candidate_uuids": candidates}
        encoded = json.dumps(json.dumps(spec, allow_nan=False))
        source = "local f=function(...)\n" + visibility_source() + "\nend\nreturn f(Ext.Json.Parse(" + encoded + "))"
        result = self.backend._evaluate(source)
        if result.get("candidates") == {}:
            result["candidates"] = []
        return result
