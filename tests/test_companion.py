"""Offline contract and independent scenario tests; never contact BG3."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path

from bg3data.companion.native import NativeBackend
from bg3data.companion.protocol import Rejected, action, observation
from bg3data.companion.service import CompanionService
from bg3data.companion.simulation import ACTOR, CHEST, ENEMY, FRIEND, HIDDEN, HOST, SimulationBackend, fixture


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.backend = SimulationBackend()
        self.now = 0
        self.service = CompanionService(self.backend, clock=lambda: self.now)
        self.addCleanup(self.service.close)
        self.service.bind(ACTOR)
        self.service.control(True)

    def submit(self, value, client="request_1"):
        return self.service.request(self.service.observe()["observation_id"], value, client)

    def rejects(self, code, function, *args):
        with self.assertRaises(Rejected) as caught:
            function(*args)
        self.assertEqual(code, caught.exception.code)

    def test_host_avatar_and_remote_player_rejected(self):
        self.rejects("avatar_forbidden", self.service.bind, HOST)
        self.rejects("identity_unknown", self.service.bind, FRIEND)

    def test_failed_rebind_pauses_existing_control(self):
        self.assertTrue(self.service.armed)
        self.rejects("invalid_uuid", self.service.bind, "bad")
        self.assertFalse(self.service.armed)
        self.assertEqual({}, self.service.observations)

    def test_unknown_avatar_identity_and_dead_actor_rejected(self):
        self.backend.state["actor"]["avatar_verified"] = False
        self.rejects("identity_unknown", self.service.bind, ACTOR)
        self.backend.state["actor"].update(avatar_verified=True, dead=True)
        self.rejects("actor_unavailable", self.service.bind, ACTOR)

    def test_hidden_enemy_and_private_fields_never_leave_backend(self):
        self.backend.state["future_story"] = "secret"
        self.backend.state["actor"]["inventory_secrets"] = ["secret"]
        state = self.service.observe()
        self.assertNotIn(HIDDEN, str(state))
        self.assertNotIn("secret", str(state))
        self.rejects("unknown_target", self.submit, {"kind": "cast", "spell_id": "Target_MainHandAttack", "target_uuid": HIDDEN})
        self.assertEqual([], self.backend.submissions)

    def test_visibility_unknown_hides_enemy(self):
        next(u for u in self.backend.state["combatants"] if u["uuid"] == ENEMY)["visibility_verified"] = False
        self.assertNotIn(ENEMY, str(self.service.observe()))

    def test_world_api_does_not_depend_on_host_selected_character(self):
        observed = self.service.observe()
        self.backend.state["selected_uuid"] = FRIEND
        result = self.service.request(observed["observation_id"], {"kind": "move", "position": {"x": 2, "y": 0, "z": 0}}, "move")
        self.assertEqual("completed", result["status"])
        self.assertEqual(7, self.backend.state["actor"]["resources"]["Movement"]["0"]["amount"])

    def test_changed_turn_resource_position_and_session_invalidate_observation(self):
        for change in (lambda s: s["actor"].update(active_turn=False),
                       lambda s: s["actor"]["resources"]["ActionPoint"]["0"].update(amount=0),
                       lambda s: s["actor"]["position"].update(x=1),
                       lambda s: s.update(session_id="reloaded-world")):
            self.backend.state = fixture()
            oid = self.service.observe()["observation_id"]
            change(self.backend.state)
            self.rejects("state_changed", self.service.preview, oid, {"kind": "end_turn"})

    def test_stale_unknown_and_evicted_observation_rejected(self):
        oid = self.service.observe()["observation_id"]
        self.now = 21
        self.rejects("stale_observation", self.service.preview, oid, {"kind": "end_turn"})
        self.rejects("unknown_observation", self.service.preview, "bogus", {"kind": "end_turn"})
        self.now = 0
        for _ in range(17):
            self.service.observe()
        self.rejects("unknown_observation", self.service.preview, oid, {"kind": "end_turn"})

    def test_preview_does_not_submit_or_consume(self):
        oid = self.service.observe()["observation_id"]
        self.assertTrue(self.service.preview(oid, {"kind": "end_turn"})["valid"])
        self.assertEqual([], self.backend.submissions)
        self.assertEqual("completed", self.service.request(oid, {"kind": "end_turn"}, "end")["status"])

    def test_wrong_turn_rejected_before_backend_submission(self):
        self.backend.state["actor"]["active_turn"] = False
        self.rejects("wrong_turn", self.submit, {"kind": "move", "position": {"x": 1, "y": 0, "z": 0}})
        self.assertEqual([], self.backend.submissions)

    def test_pause_and_rebind_consume_observations(self):
        oid = self.service.observe()["observation_id"]
        self.service.control(False)
        self.rejects("paused", self.service.request, oid, {"kind": "end_turn"}, "paused")
        self.service.control(True)
        self.rejects("unknown_observation", self.service.request, oid, {"kind": "end_turn"}, "old")
        self.service.bind(ACTOR)
        self.assertFalse(self.service.armed)

    def test_duplicate_client_id_returns_receipt_without_double_cast(self):
        value = {"kind": "cast", "spell_id": "Target_HealingWord", "target_uuid": FRIEND}
        oid = self.service.observe()["observation_id"]
        first = self.service.request(oid, value, "heal")
        second = self.service.request("consumed", value, "heal")
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.backend.submissions))
        self.assertEqual(15, next(u for u in self.backend.state["party"] if u["uuid"] == FRIEND)["hp"])
        resources = self.backend.state["actor"]["resources"]
        self.assertEqual(0, resources["BonusActionPoint"]["0"]["amount"])
        self.assertEqual(1, resources["SpellSlot"]["1"]["amount"])
        self.rejects("idempotency_conflict", self.service.request, oid, {"kind": "end_turn"}, "heal")

    def test_all_observations_consumed_after_submission(self):
        oid = self.service.observe()["observation_id"]
        self.submit({"kind": "move", "position": {"x": 1, "y": 0, "z": 0}})
        self.rejects("unknown_observation", self.service.preview, oid, {"kind": "end_turn"})

    def test_spell_range_rejection_does_not_charge_resources_and_pauses(self):
        before = copy.deepcopy(self.backend.state)
        result = self.submit({"kind": "cast", "spell_id": "Target_MainHandAttack", "target_uuid": ENEMY})
        self.assertEqual(("rejected", "out_of_range"), (result["status"], result["reason"]))
        self.assertEqual(before, self.backend.state)
        self.assertFalse(self.service.armed)

    def test_unpaid_cost_rejection_is_atomic(self):
        self.backend.state["actor"]["resources"]["SpellSlot"]["1"]["amount"] = 0
        before = copy.deepcopy(self.backend.state)
        result = self.submit({"kind": "cast", "spell_id": "Target_HealingWord", "target_uuid": FRIEND})
        self.assertEqual("insufficient_resources", result["reason"])
        self.assertEqual(before, self.backend.state)

    def test_move_attack_end_turn_scenario_and_shared_initiative(self):
        self.assertEqual("completed", self.submit({"kind": "move", "position": {"x": 3, "y": 0, "z": 0}}, "move")["status"])
        self.assertEqual("completed", self.submit({"kind": "cast", "spell_id": "Target_MainHandAttack", "target_uuid": ENEMY}, "attack")["status"])
        enemy = next(u for u in self.backend.state["combatants"] if u["uuid"] == ENEMY)
        self.assertEqual(14, enemy["hp"])
        self.assertEqual(0, self.backend.state["actor"]["resources"]["ActionPoint"]["0"]["amount"])
        self.assertEqual("completed", self.submit({"kind": "end_turn"}, "end")["status"])
        host = next(u for u in self.backend.state["party"] if u["uuid"] == HOST)
        self.assertTrue(host["active_turn"])
        self.assertFalse(host["turn_ended"])
        self.rejects("wrong_turn", self.submit, {"kind": "end_turn"}, "again")

    def test_path_cost_uses_detour_and_blocked_destinations_fail(self):
        self.backend.blocked.add((1, 0))
        result = self.submit({"kind": "move", "position": {"x": 2, "y": 0, "z": 0}})
        self.assertEqual("completed", result["status"])
        self.assertEqual(5, self.backend.state["actor"]["resources"]["Movement"]["0"]["amount"])
        self.assertEqual("path_unavailable", self.submit({"kind": "move", "position": {"x": 1, "y": 0, "z": 0}}, "blocked")["reason"])

    def test_insufficient_movement_no_partial_move(self):
        before = copy.deepcopy(self.backend.state)
        result = self.submit({"kind": "move", "position": {"x": 10, "y": 0, "z": 0}})
        self.assertEqual("insufficient_movement", result["reason"])
        self.assertEqual(before, self.backend.state)

    def test_opportunity_attack_and_hazards_have_cost(self):
        self.submit({"kind": "move", "position": {"x": 3, "y": 0, "z": 0}}, "approach")
        self.backend.hazards.add((2, 0))
        result = self.submit({"kind": "move", "position": {"x": 1, "y": 0, "z": 0}}, "retreat")
        self.assertEqual(15, self.backend.state["actor"]["hp"])
        self.assertTrue(any(e["kind"] == "opportunity_attack" for e in result["events"]))

    def test_aoe_world_target_costs_and_friendly_fire(self):
        result = self.submit({"kind": "cast", "spell_id": "Projectile_Fireball", "position": {"x": 4, "y": 0, "z": 0}})
        self.assertEqual("completed", result["status"])
        units = self.backend._units()
        self.assertTrue(units[ENEMY]["dead"])
        self.assertTrue(units[FRIEND]["dead"])
        self.assertEqual(0, units[ACTOR]["resources"]["SpellSlot"]["3"]["amount"])

    def test_reaction_can_be_decided_outside_active_turn(self):
        self.backend.state["actor"]["active_turn"] = False
        self.backend.state["reactions"] = [{"id": "reaction1", "actor_uuid": ACTOR, "expires_at": 3}]
        result = self.submit({"kind": "react", "reaction_id": "reaction1", "accept": True})
        self.assertEqual("completed", result["status"])
        self.assertEqual(0, self.backend.state["actor"]["resources"]["ReactionActionPoint"]["0"]["amount"])

    def test_declining_reaction_preserves_resource(self):
        self.backend.state["reactions"] = [{"id": "reaction1", "actor_uuid": ACTOR}]
        self.submit({"kind": "react", "reaction_id": "reaction1", "accept": False})
        self.assertEqual(1, self.backend.state["actor"]["resources"]["ReactionActionPoint"]["0"]["amount"])

    def test_expired_and_other_actors_reactions_rejected(self):
        self.backend.state["reactions"] = [{"id": "reaction1", "actor_uuid": ACTOR, "expires_at": 0}]
        self.assertEqual("reaction_expired", self.submit({"kind": "react", "reaction_id": "reaction1", "accept": True})["reason"])
        self.service.control(True)
        self.backend.state["reactions"] = [{"id": "other", "actor_uuid": HOST}]
        self.rejects("reaction_unavailable", self.submit, {"kind": "react", "reaction_id": "other", "accept": True}, "other")

    def test_exploration_follow_and_interaction(self):
        self.backend.state = fixture(combat=False)
        host = self.backend._units()[HOST]
        host["position"]["x"] = 5
        result = self.submit({"kind": "follow", "leader_uuid": HOST, "distance": 2}, "follow")
        self.assertEqual("completed", result["status"])
        self.assertEqual(3, self.backend.state["actor"]["position"]["x"])
        self.assertEqual(9, self.backend.state["actor"]["resources"]["Movement"]["0"]["amount"])
        self.assertEqual("completed", self.submit({"kind": "interact", "target_uuid": CHEST}, "interact")["status"])
        self.rejects("wrong_turn", self.submit, {"kind": "end_turn"}, "end")

    def test_pending_action_blocks_other_requests_and_correlates_completion(self):
        self.backend.deferred = True
        first = self.submit({"kind": "end_turn"})
        self.assertEqual("pending", first["status"])
        self.rejects("outstanding_request", self.submit, {"kind": "end_turn"}, "second")
        result = self.service.result(first["request_id"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(1, len(self.backend.submissions))

    def test_wrong_request_id_actor_and_invalid_status_mark_uncertain(self):
        for receipt in ({"request_id": "other", "actor_uuid": ACTOR, "status": "completed"},
                        {"request_id": "expected", "actor_uuid": HOST, "status": "completed"},
                        {"request_id": "expected", "actor_uuid": ACTOR, "status": "success"}):
            result = self.service._receipt("expected", ACTOR, receipt)
            self.assertEqual("uncertain", result["status"])

    def test_transport_failure_uncertain_survives_rebind(self):
        def fail(*args):
            raise RuntimeError("lost response")
        self.backend.submit = fail
        result = self.submit({"kind": "end_turn"})
        self.assertEqual("uncertain", result["status"])
        self.assertFalse(self.service.armed)
        self.service.bind(ACTOR)
        self.rejects("outstanding_request", self.service.control, True)

    def test_timeout_does_not_retry(self):
        self.backend.deferred = True
        result = self.submit({"kind": "end_turn"})
        self.backend.poll = lambda rid: {"request_id": rid, "actor_uuid": ACTOR, "status": "pending"}
        self.now = 16
        result = self.service.result(result["request_id"])
        self.assertEqual("uncertain", result["status"])
        self.assertEqual(1, len(self.backend.submissions))

    def test_durable_idempotency_and_pending_request_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.db"
            first = CompanionService(self.backend, path)
            first.bind(ACTOR)
            first.control(True)
            self.backend.deferred = True
            value = {"kind": "end_turn"}
            receipt = first.request(first.observe()["observation_id"], value, "persistent")
            first.close()
            second = CompanionService(self.backend, path)
            try:
                second.bind(ACTOR)
                duplicate = second.request("consumed", value, "persistent")
                self.assertEqual(receipt, duplicate)
                self.assertEqual("uncertain", second.result(receipt["request_id"])["status"])
                self.rejects("outstanding_request", second.control, True)
                self.assertEqual(1, len(self.backend.submissions))
            finally:
                second.close()

    def test_concurrent_services_only_submit_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "requests.db"
            services = [CompanionService(self.backend, path) for _ in range(2)]
            results = []
            barrier = threading.Barrier(2)
            def worker(service):
                try:
                    service.bind(ACTOR)
                    service.control(True)
                    oid = service.observe()["observation_id"]
                    barrier.wait(timeout=5)
                    results.append(service.request(oid, {"kind": "end_turn"}, "concurrent"))
                except Rejected as exc:
                    results.append({"error": exc.code})
            self.backend.deferred = True
            threads = [threading.Thread(target=worker, args=(s,)) for s in services]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(2, len(results))
                self.assertEqual(1, len(self.backend.submissions))
            finally:
                for service in services:
                    service.close()

    def test_native_backend_cannot_be_armed_and_never_evaluates_for_capabilities(self):
        calls = []
        backend = NativeBackend(lambda code: calls.append(code))
        service = CompanionService(backend)
        try:
            self.assertFalse(any(service.capabilities()["actions"].values()))
            self.assertEqual([], calls)
            self.rejects("backend_unavailable", backend.submit, "id", ACTOR, {"kind": "end_turn"})
            self.assertEqual([], calls)
        finally:
            service.close()

    def test_unvalidated_native_adapter_cannot_advertise_execution(self):
        self.backend.capabilities = lambda: {"backend": "native", "normal_rules_validated": False, "actions": {"cast": True}}
        self.assertFalse(self.service.capabilities()["actions"]["cast"])
        self.rejects("unsupported_action", self.submit, {"kind": "cast", "spell_id": "Target_HealingWord", "target_uuid": FRIEND})

    def test_native_observation_filters_nonparty_without_visibility_evidence(self):
        native = NativeBackend(lambda code: {"ok": True, "result": fixture()})
        state = observation(native.observe(ACTOR), ACTOR)
        self.assertNotIn(ENEMY, str(state))
        self.assertIn(HOST, str(state))

    def test_journal_capacity_rejects_without_submission(self):
        self.service.capacity = 1
        self.submit({"kind": "move", "position": {"x": 1, "y": 0, "z": 0}})
        self.rejects("journal_full", self.submit, {"kind": "end_turn"}, "second")
        self.assertEqual(1, len(self.backend.submissions))

    def test_late_evidence_reconciles_uncertainty_without_resubmitting(self):
        self.backend.deferred = True
        result = self.submit({"kind": "end_turn"})
        request_id = result["request_id"]
        poll = self.backend.poll
        self.backend.poll = lambda rid: {"request_id": rid, "actor_uuid": ACTOR, "status": "pending"}
        self.now = 16
        self.assertEqual("uncertain", self.service.result(request_id)["status"])
        self.backend.poll = poll
        self.assertEqual("completed", self.service.reconcile(request_id)["status"])
        self.assertEqual(1, len(self.backend.submissions))
        self.assertFalse(self.service.armed)
        self.service.control(True)

    def test_receipts_are_copied_and_completed_results_cannot_be_overwritten(self):
        result = self.submit({"kind": "end_turn"})
        request_id = result["request_id"]
        result["status"] = "uncertain"
        self.assertEqual("completed", self.service.result(request_id)["status"])
        self.assertEqual("completed", self.service._save(request_id, {"request_id": request_id, "actor_uuid": ACTOR, "status": "uncertain"})["status"])

    def test_enemy_private_resources_and_spellbook_are_not_public(self):
        enemy = next(u for u in self.service.observe()["combatants"] if u["uuid"] == ENEMY)
        self.assertNotIn("resources", enemy)
        self.assertNotIn("spells", enemy)

    def test_actor_control_loss_before_submit_has_no_effect(self):
        oid = self.service.observe()["observation_id"]
        self.backend.state["actor"]["host_owned"] = False
        self.rejects("state_changed", self.service.request, oid, {"kind": "end_turn"}, "lost")
        self.assertEqual([], self.backend.submissions)

    def test_malformed_backend_result_pauses_even_if_action_may_have_happened(self):
        def submit(rid, actor, value):
            return {"request_id": rid, "actor_uuid": actor, "status": "completed", "changes": {"hp": float("nan")}}
        self.backend.submit = submit
        result = self.submit({"kind": "end_turn"})
        self.assertEqual("uncertain", result["status"])
        self.assertFalse(self.service.armed)


class ProtocolTests(unittest.TestCase):
    def test_malformed_actions_rejected(self):
        values = [None, [], {}, {"kind": "teleport"}, {"kind": "end_turn", "x": 1},
                  {"kind": "cast", "spell_id": "Attack", "target_uuid": ENEMY, "position": {"x": 1, "y": 0, "z": 0}},
                  {"kind": "cast", "spell_id": "a;inject", "target_uuid": ENEMY},
                  {"kind": "move", "position": {"x": float("nan"), "y": 0, "z": 0}},
                  {"kind": "move", "position": {"x": True, "y": 0, "z": 0}},
                  {"kind": "move", "position": {"x": 0, "y": 0, "z": float("inf")}},
                  {"kind": "move", "position": {"x": 0, "y": 0}},
                  {"kind": "follow", "leader_uuid": HOST, "distance": -1},
                  {"kind": "react", "reaction_id": "r", "accept": 1},
                  {"kind": "interact", "target_uuid": "not-a-uuid"}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(Rejected):
                action(value)


if __name__ == "__main__":
    unittest.main()
