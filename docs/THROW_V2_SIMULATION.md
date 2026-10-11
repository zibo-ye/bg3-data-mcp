# Throw v2 inventory simulation

This is an offline wire/software contract and deterministic fixture. It does not implement native BG3 throwing or validate its ordinary resource, inventory, trajectory or multiplayer semantics. Native action and throw feature capabilities remain false.

```json
{
  "kind": "throw",
  "schema_version": 2,
  "inventory_option_id": "observed_inventory_choice_token",
  "target": {"entity_uuid": "00000000-0000-0000-0000-000000000004"}
}
```

The target is exactly one currently known entity or finite bounded `position: {x,y,z}`. V1 shapes remain unchanged and cannot express throw. The caller cannot supply an item UUID, spell/prototype/source identity, quantity, flags, native handles, secondary targets or resource writes. A backend must advertise version 2, the throw action and `throw_inventory_item` separately. CompanionService gates these variants before submission; a v1-only or unvalidated native backend cannot accept them.

The bound actor's `inventory_options` observation contains at most 64 verified owned, throw-eligible choices. It reports opaque id, observed session/revision, exact item instance UUID, owner UUID and ownership verification, current quantity, supported target types, and optional bounded label/range/action cost. An exact own item instance UUID is permitted observation data, but selection still uses its scoped opaque option token. Unknown/remote ownership, unknown eligibility and zero quantity are withheld. Duplicated instances, invalid quantities and unbounded lists reject. Other players' inventory and raw templates/private source catalog are not exposed.

The simulation keeps a private actor inventory and immutable initial source catalog. A choice token hashes the actor and full current item record, including exact instance, private source, session, revision, owner and quantity. Changed current ownership, removal, stack state or source/revision invalidates a previously selected token. Execution checks current exact identity, original catalog source, session, ownership, eligibility, quantity, target availability, range, blocked destination and action cost immediately before any mutation. Resource failure or invalid input does not partially deduct AP, consume an item or damage the target. A transfer between durable reservation and deferred execution rejects without a second submission.

The fixture updates revision for its own throw mutations and detects changed current state. Direct external test-state writes are not an inventory event stream: a transfer out and back that restores every field without an observation or revision change cannot be detected historically. A real inventory adapter must maintain an event-backed revision for transfers/removal/stack changes rather than trusting a restored state fingerprint. No native transfer-history correctness is claimed here.

Fixture semantics are explicit: each throw costs one ActionPoint and has range 12. A single dagger deals 5 damage to a living selected entity, leaves the actor's inventory, and records that exact instance at the destination. A consumable stack deals 4 damage to a selected entity and consumes exactly one unit of the observed stack; it does not create a dropped item. A returning dagger deals 5 damage and retains ownership/quantity while changing the inventory revision. Position selection records the explicit destination without inventing an enemy list. Weapon stacks greater than one are rejected; no world-object/creature throwing, stack splitting, physics trajectory, saving throw, misses, explosive radius or native returning-weapon rule is inferred. The destination-block fixture is a coarse grid rule, not a BG3 line-of-sight or path implementation.

Receipts report the selected option, exact item UUID, quantity before/after, dropped/consumed/returned disposition and explicit destination, plus existing public resource changes. They do not expose private source records. The SQLite journal persists the canonical scoped option ID and target; durable duplicates and restart return the stored receipt without another item/resource mutation. The generic journal still does not store a resolved native item/source/throw request record. A future native adapter must durably resolve the actual original instance/source/cost and inventory revision before enqueue, recheck them in the same engine call, prove ordinary throw encoding and exact request/result correlation, and retain uncertainty without replay across reloads.

The throw tests cover strict versions/targets, native-field rejection, actor-only inventory, exact stack/owner/source identity, atomic resource/range/block rejection, hidden targets, pending transfer, returning/consumable/drop fixture changes, feature negotiation and durable duplicate restart. These are offline test results, not live throw acceptance.
