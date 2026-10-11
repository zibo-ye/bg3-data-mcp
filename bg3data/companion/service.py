"""Serialized companion requests with durable idempotency and uncertain-result handling."""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict

from .protocol import ACTIONS, TERMINAL, Rejected, action, digest, eligible, identifier, observation, reject, token, validate_context


class CompanionService:
    def __init__(self, backend, journal=":memory:", clock=time.monotonic, ttl=20, timeout=15, capacity=10000):
        if not 1 <= ttl <= 60 or not 1 <= timeout <= 120 or capacity < 1:
            raise ValueError("invalid service limits")
        self.backend, self.clock, self.ttl, self.timeout, self.capacity = backend, clock, ttl, timeout, capacity
        self.actor, self.armed = None, False
        self.lock = threading.RLock()
        self.observations = OrderedDict()
        self.db = sqlite3.connect(str(journal), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, client_id TEXT UNIQUE NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, intent TEXT NOT NULL DEFAULT '{}')")
        if "intent" not in {row[1] for row in self.db.execute("PRAGMA table_info(requests)")}:
            self.db.execute("ALTER TABLE requests ADD COLUMN intent TEXT NOT NULL DEFAULT '{}'")
        if "context" not in {row[1] for row in self.db.execute("PRAGMA table_info(requests)")}:
            self.db.execute("ALTER TABLE requests ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
        self.db.commit()
        self.pending_at = {}

    def close(self):
        with self.lock:
            self.armed = False
            self.db.close()

    def capabilities(self):
        capabilities = copy.deepcopy(self.backend.capabilities())
        if capabilities.get("backend") not in ("simulation", "native"):
            reject("invalid_backend", "backend type is not declared")
        capabilities.update(schema_version=1, actions={k: capabilities.get("actions", {}).get(k, False) is True for k in ACTIONS})
        versions = capabilities.get("action_contract_versions", [1])
        capabilities["action_contract_versions"] = [v for v in (1, 2) if isinstance(versions, list) and any(type(x) is int and x == v for x in versions)]
        features = capabilities.get("action_features", {})
        capabilities["action_features"] = {key: isinstance(features, dict) and features.get(key) is True
            for key in ("cast_multiple_targets", "cast_upcast_options", "throw_inventory_item", "dialogue_select")}
        feature_authority = (capabilities["backend"] == "simulation" and capabilities.get("simulation_only") is True
                             or capabilities["backend"] == "native" and capabilities.get("normal_rules_validated") is True)
        if not feature_authority:
            capabilities["action_contract_versions"] = [1]
            capabilities["action_features"] = dict.fromkeys(capabilities["action_features"], False)
        # A native adapter must be validated before any action can be exposed.
        if capabilities["backend"] == "native" and capabilities.get("normal_rules_validated") is not True:
            capabilities["actions"] = dict.fromkeys(ACTIONS, False)
        return capabilities

    def status(self):
        with self.lock:
            outstanding = [row[0] for row in self.db.execute("SELECT id FROM requests WHERE actor=? AND status IN ('pending','uncertain')", (self.actor,))]
            return {"bound_uuid": self.actor, "armed": self.armed, "outstanding": outstanding, "capabilities": self.capabilities()}

    def _snapshot(self, actor):
        return observation(self.backend.observe(actor), actor)

    def bind(self, actor_uuid):
        with self.lock:
            self.armed = False
            self.observations.clear()
            actor_uuid = identifier(actor_uuid)
            state = self._snapshot(actor_uuid)
            eligible(state["actor"])
            self.actor, self.armed = actor_uuid, False
            self.observations.clear()
            return self.status()

    def control(self, enabled):
        with self.lock:
            if type(enabled) is not bool:
                reject("invalid_control", "enabled must be a boolean")
            if enabled:
                if not self.actor:
                    reject("not_bound", "bind a companion first")
                eligible(self._snapshot(self.actor)["actor"])
                if self.status()["outstanding"]:
                    reject("outstanding_request", "resolve pending/uncertain requests before enabling")
                if not any(self.capabilities()["actions"].values()):
                    reject("backend_unavailable", "no validated action backend is available")
            self.armed = enabled
            self.observations.clear()
            return self.status()

    def observe(self):
        with self.lock:
            state = self._snapshot(self.actor)
            revision = uuid.uuid4().hex
            self.observations[revision] = (self.clock(), digest(state), copy.deepcopy(state))
            while len(self.observations) > 16:
                self.observations.popitem(last=False)
            return {**state, "observation_id": revision}

    def _validate(self, observation_id, value):
        if not self.actor:
            reject("not_bound", "bind a companion first")
        token(observation_id, "observation_id")
        if observation_id not in self.observations:
            reject("unknown_observation", "observation was consumed, evicted or belongs to another session")
        created, expected, _ = self.observations[observation_id]
        if self.clock() - created > self.ttl:
            reject("stale_observation", "observe again")
        current = self._snapshot(self.actor)
        if digest(current) != expected:
            reject("state_changed", "companion state changed; observe again")
        capabilities = self.capabilities()
        if value.get("schema_version") == 2:
            if 2 not in capabilities["action_contract_versions"]:
                reject("unsupported_action_version", "backend supports only v1 action contract")
            if value['kind']=='throw' and capabilities['action_features']['throw_inventory_item'] is not True:
                reject('unsupported_action_feature','backend cannot throw verified inventory instances')
            if value['kind']=='cast' and capabilities["action_features"]["cast_upcast_options"] is not True:
                reject("unsupported_action_feature", "backend cannot resolve observed cast choices")
            if value['kind']=='cast' and len(value["targets"]) > 1 and capabilities["action_features"]["cast_multiple_targets"] is not True:
                reject("unsupported_action_feature", "backend cannot allocate multiple selections")
        validate_context(current, self.actor, value)
        if capabilities["actions"].get(value["kind"]) is not True:
            reject("unsupported_action", "action backend is unavailable or unvalidated")
        return current

    def preview(self, observation_id, value):
        with self.lock:
            value = action(value)
            self._validate(observation_id, value)
            return {"valid": True, "action": value, "game_legality": "engine must validate path, range, costs and targets", "backend": self.capabilities()["backend"]}

    def _lookup(self, field, value):
        # `field` is only selected by this module, never a caller-supplied identifier.
        row = self.db.execute(f"SELECT id, actor, payload, status, result FROM requests WHERE {field}=?", (value,)).fetchone()
        return row

    def _save(self, request_id, result):
        result = copy.deepcopy(result)
        result["action"] = json.loads(self.db.execute("SELECT intent FROM requests WHERE id=?", (request_id,)).fetchone()[0])
        previous = ("pending",) if result["status"] == "pending" else ("pending", "uncertain")
        placeholders = ",".join("?" for _ in previous)
        self.db.execute(f"UPDATE requests SET status=?,result=? WHERE id=? AND status IN ({placeholders})", (result["status"], json.dumps(result, allow_nan=False), request_id, *previous))
        self.db.commit()
        # Another process may have already recorded correlated terminal evidence.
        result = json.loads(self._lookup("id", request_id)[4])
        if result["status"] in TERMINAL:
            self.pending_at.pop(request_id, None)
        if result["status"] in ("uncertain", "rejected"):
            self.armed = False
        return result

    def _receipt(self, request_id, actor, receipt, context=None):
        if not isinstance(receipt, dict) or receipt.get("request_id") != request_id or receipt.get("actor_uuid") != actor or receipt.get("status") not in TERMINAL | {"pending"}:
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": "backend result cannot be correlated"}
        if context and any(receipt.get(key) != context[key] for key in ("session_id", "engine_request_guid")):
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": "backend result does not match original request scope"}
        # Backend internals/raw snapshots never enter the public result.
        result = {k: copy.deepcopy(receipt[k]) for k in ("request_id", "actor_uuid", "status", "reason", "events", "changes") if k in receipt}
        result["backend"] = self.capabilities()["backend"]
        try:
            digest(result)
        except (ValueError, TypeError):
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": "backend returned invalid result data"}
        return result

    def _scoped_hooks(self):
        submit = getattr(self.backend, "scoped_submit", None)
        poll = getattr(self.backend, "scoped_poll", None)
        if callable(submit) != callable(poll):
            reject("invalid_backend", "scoped submission and polling must be provided together")
        return callable(submit)

    def _new_context(self, request_id, actor, value, current):
        if not self._scoped_hooks():
            return {}
        session = current.get("session_id")
        if not isinstance(session, str) or not 1 <= len(session) <= 128:
            reject("invalid_session", "scoped execution needs a bounded observed session ID")
        return {"scope_version": 1, "actor_uuid": actor, "session_id": session,
                "engine_request_guid": str(uuid.UUID(request_id)),
                "backend": self.capabilities()["backend"], "action_digest": digest(value)}

    def _poll_receipt(self, request_id, actor):
        """Dispatch using persisted scope, never the current observation/session."""
        def uncertain(reason):
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": reason}
        try:
            row = self.db.execute("SELECT context,intent FROM requests WHERE id=? AND actor=?", (request_id, actor)).fetchone()
            context = json.loads(row[0])
            scoped = self._scoped_hooks()
            if not isinstance(context, dict):
                return uncertain("persisted request scope invalid; no poll attempted")
            if not context:
                if scoped:
                    return uncertain("original request scope missing; no scoped poll attempted")
                return self._receipt(request_id, actor, self.backend.poll(request_id))
            if not scoped:
                return uncertain("scoped request cannot be polled by an unscoped backend")
            required = {"scope_version", "actor_uuid", "session_id", "engine_request_guid", "backend", "action_digest"}
            if (not isinstance(context, dict) or set(context) != required or type(context["scope_version"]) is not int
                or context["scope_version"] != 1 or context["actor_uuid"] != actor
                or not isinstance(context["session_id"], str) or not 1 <= len(context["session_id"]) <= 128
                or context["engine_request_guid"] != str(uuid.UUID(request_id))
                or context["backend"] != self.capabilities()["backend"]
                or context["action_digest"] != digest(json.loads(row[1]))):
                return uncertain("persisted request scope invalid; no scoped poll attempted")
            receipt = self.backend.scoped_poll(request_id, actor, copy.deepcopy(context))
            return self._receipt(request_id, actor, receipt, context)
        except Exception:
            return uncertain("result transport or stored scope failed")

    def request(self, observation_id, value, client_request_id):
        with self.lock:
            value = action(value)
            client_request_id = token(client_request_id, "client_request_id")
            payload = digest({"actor": self.actor, "action": value})
            existing = self._lookup("client_id", client_request_id)
            if existing:
                if existing[2] != payload:
                    reject("idempotency_conflict", "client_request_id was used for another actor/action")
                return copy.deepcopy(json.loads(existing[4]))
            if not self.armed:
                reject("paused", "enable companion control first")
            if self.status()["outstanding"]:
                reject("outstanding_request", "previous request must settle before another action")
            current = self._validate(observation_id, value)
            request_id = uuid.uuid4().hex
            context = self._new_context(request_id, self.actor, value, current)
            pending = {"request_id": request_id, "actor_uuid": self.actor, "status": "pending", "backend": self.capabilities()["backend"], "action": value}
            # Reserve before submitting. A crash must never cause a request to be resent.
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= self.capacity:
                    reject("journal_full", "request journal capacity reached; archive deliberately")
                if self.db.execute("SELECT 1 FROM requests WHERE actor=? AND status IN ('pending','uncertain')", (self.actor,)).fetchone():
                    reject("outstanding_request", "another session has an outstanding request")
                self.db.execute("INSERT INTO requests (id,client_id,actor,payload,status,result,intent,context) VALUES (?,?,?,?,?,?,?,?)", (request_id, client_request_id, self.actor, payload, "pending", json.dumps(pending), json.dumps(value, allow_nan=False), json.dumps(context, allow_nan=False)))
                self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback()
                existing = self._lookup("client_id", client_request_id)
                if existing and existing[2] == payload:
                    return json.loads(existing[4])
                reject("idempotency_conflict", "concurrent request ID conflict")
            except Exception:
                self.db.rollback()
                raise
            self.observations.clear()
            self.pending_at[request_id] = self.clock()
            try:
                receipt = (self.backend.scoped_submit(request_id, self.actor, value, copy.deepcopy(context))
                           if context else self.backend.submit(request_id, self.actor, value))
            except Exception:
                receipt = {"request_id": request_id, "actor_uuid": self.actor, "status": "uncertain", "reason": "transport failed after request reservation; do not retry"}
                if context:
                    receipt.update(session_id=context["session_id"], engine_request_guid=context["engine_request_guid"])
            return self._save(request_id, self._receipt(request_id, self.actor, receipt, context))

    def result(self, request_id):
        with self.lock:
            token(request_id, "request_id")
            row = self._lookup("id", request_id)
            if not row or row[1] != self.actor:
                reject("unknown_request", "request does not belong to the bound actor")
            result = json.loads(row[4])
            if row[3] != "pending":
                return result
            if request_id not in self.pending_at:
                # A restarted process cannot establish whether its predecessor submitted.
                return self._save(request_id, {"request_id": request_id, "actor_uuid": row[1], "status": "uncertain", "reason": "service restarted with an unresolved request"})
            try:
                receipt = self._poll_receipt(request_id, row[1])
            except Exception:
                receipt = {"request_id": request_id, "actor_uuid": row[1], "status": "uncertain", "reason": "result transport failed"}
            if receipt["status"] == "pending" and self.clock() - self.pending_at[request_id] >= self.timeout:
                receipt["status"], receipt["reason"] = "uncertain", "request timed out; no automatic retry"
            return self._save(request_id, receipt)

    def reconcile(self, request_id):
        """Explicitly seek late correlated evidence; never resend or manually assume success."""
        with self.lock:
            token(request_id, "request_id")
            row = self._lookup("id", request_id)
            if not row or row[1] != self.actor:
                reject("unknown_request", "request does not belong to the bound actor")
            if row[3] != "uncertain":
                return self.result(request_id)
            try:
                receipt = self._poll_receipt(request_id, row[1])
            except Exception:
                return json.loads(row[4])
            if receipt["status"] not in ("completed", "rejected"):
                return json.loads(row[4])
            # Remains paused. A human may explicitly re-enable after inspecting evidence.
            return self._save(request_id, receipt)
