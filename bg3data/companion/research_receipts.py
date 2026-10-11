"""Bounded developer evidence reduction; no action submission or service receipt.

The equality adapter is restricted to the two self-Shout prototypes observed in
the independent live test. It is not a generic request/cast GUID guarantee.
"""
from .protocol import identifier, token


def reduce_shout_trace(trace, *, actor_uuid, session_id, request_guid, spell_id):
    actor_uuid, request_guid = identifier(actor_uuid), identifier(request_guid)
    spell_id = token(spell_id, "spell_id")
    receipt = {"status": "uncertain", "request_guid": request_guid,
               "actor_uuid": actor_uuid, "spell_id": spell_id,
               "execution_enabled": False, "retry_allowed": False,
               "costs_verified": False, "correlation_scope": "observed_self_shout"}

    def uncertain(reason):
        return dict(receipt, reason=reason)

    if spell_id not in ("Shout_Hide", "Shout_Dash"):
        return uncertain("unsupported_correlation_scope")
    if not isinstance(trace, dict) or trace.get("actor_uuid") != actor_uuid or trace.get("session_id") != session_id:
        return uncertain("trace_identity_mismatch")
    if not isinstance(session_id, str) or not session_id:
        return uncertain("session_unknown")
    if any(trace.get(key) is not False for key in ("invalidated", "truncated", "expired")) or trace.get("errors") != []:
        return uncertain("trace_incomplete")
    records = trace.get("records")
    if not isinstance(records, list) or len(records) > 96:
        return uncertain("trace_records_invalid")
    last_seq = 0
    matching = []
    for row in records:
        if not isinstance(row, dict) or type(row.get("seq")) is not int or row["seq"] <= last_seq:
            return uncertain("trace_sequence_invalid")
        last_seq = row["seq"]
        if row.get("request_guid") == request_guid or row.get("cast_guid") == request_guid:
            if row.get("actor_uuid") != actor_uuid:
                return uncertain("matching_guid_actor_mismatch")
            if row.get("kind") != "queue_departed" and row.get("spell") != spell_id:
                return uncertain("matching_guid_spell_mismatch")
            matching.append(row)
    events = {}
    for row in matching:
        kind = row.get("kind")
        if kind not in ("queued", "started", "queue_departed", "finished", "outcome") or kind in events:
            return uncertain("matching_events_ambiguous")
        events[kind] = row
    if not all(kind in events for kind in ("queued", "started", "finished", "outcome")):
        return uncertain("correlated_lifecycle_missing")
    queued, started = events["queued"], events["started"]
    if queued.get("queue") != "NetworkStartRequests" or queued.get("request_guid") != request_guid:
        return uncertain("unsupported_queue")
    if not queued["seq"] < started["seq"] < min(events["finished"]["seq"], events["outcome"]["seq"]):
        return uncertain("lifecycle_order_invalid")
    cast_entity = started.get("cast_entity")
    if not isinstance(cast_entity, str) or not cast_entity or any(
        events[kind].get("cast_entity") != cast_entity or events[kind].get("cast_guid") != request_guid
        for kind in ("started", "finished", "outcome")
    ):
        return uncertain("cast_identity_mismatch")
    result = events["outcome"].get("result")
    if result != events["finished"].get("result"):
        return uncertain("terminal_results_disagree")
    if result not in ("None", "CantCast"):
        return uncertain("unvalidated_terminal_result")
    return dict(receipt, status="completed" if result == "None" else "rejected",
                reason="correlated_engine_result", engine_result=result,
                cast_guid=request_guid, cast_entity=cast_entity)
