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
        validate_context(current, self.actor, value)
        if self.capabilities()["actions"].get(value["kind"]) is not True:
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

    def _receipt(self, request_id, actor, receipt):
        if not isinstance(receipt, dict) or receipt.get("request_id") != request_id or receipt.get("actor_uuid") != actor or receipt.get("status") not in TERMINAL | {"pending"}:
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": "backend result cannot be correlated"}
        # Backend internals/raw snapshots never enter the public result.
        result = {k: copy.deepcopy(receipt[k]) for k in ("request_id", "actor_uuid", "status", "reason", "events", "changes") if k in receipt}
        result["backend"] = self.capabilities()["backend"]
        try:
            digest(result)
        except (ValueError, TypeError):
            return {"request_id": request_id, "actor_uuid": actor, "status": "uncertain", "reason": "backend returned invalid result data"}
        return result

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
            self._validate(observation_id, value)
            request_id = uuid.uuid4().hex
            pending = {"request_id": request_id, "actor_uuid": self.actor, "status": "pending", "backend": self.capabilities()["backend"], "action": value}
            # Reserve before submitting. A crash must never cause a request to be resent.
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= self.capacity:
                    reject("journal_full", "request journal capacity reached; archive deliberately")
                if self.db.execute("SELECT 1 FROM requests WHERE actor=? AND status IN ('pending','uncertain')", (self.actor,)).fetchone():
                    reject("outstanding_request", "another session has an outstanding request")
                self.db.execute("INSERT INTO requests (id,client_id,actor,payload,status,result,intent) VALUES (?,?,?,?,?,?,?)", (request_id, client_request_id, self.actor, payload, "pending", json.dumps(pending), json.dumps(value, allow_nan=False)))
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
                receipt = self.backend.submit(request_id, self.actor, value)
            except Exception:
                receipt = {"request_id": request_id, "actor_uuid": self.actor, "status": "uncertain", "reason": "transport failed after request reservation; do not retry"}
            return self._save(request_id, self._receipt(request_id, self.actor, receipt))

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
                receipt = self._receipt(request_id, row[1], self.backend.poll(request_id))
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
                receipt = self._receipt(request_id, row[1], self.backend.poll(request_id))
            except Exception:
                return json.loads(row[4])
            if receipt["status"] not in ("completed", "rejected"):
                return json.loads(row[4])
            # Remains paused. A human may explicitly re-enable after inspecting evidence.
            return self._save(request_id, receipt)
