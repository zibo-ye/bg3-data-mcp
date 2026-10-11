# Companion API development plan

## Goal and ownership

An external agent should control the fourth recruited companion in a three-player BG3 party,
using normal game rules. Human-like behavior means following party intent, moving, selecting
skills and cooperating; it does not require imitating mouse input.

This fork owns game-facing data and action transport. The consuming companion project owns
agent prompts, planning, policy, configuration and the bounded decision loop. Avoid moving
model-provider code into the data server. Upstream lookup, indexing and SE transport remain reusable.

The fork implements the contracts, service, SQLite journal, restricted MCP server and offline
simulation under `bg3data/companion/`. Native execution is unavailable; a read-only SE adapter
and presence probe are prepared but not validated in game. Existing SE eval and development/test
tools can alter the game and must not be treated as a restricted autonomous companion interface.

## Implemented tool boundary

- `bg3_companion_capabilities`: report implemented actions, backend and validation status;
  distinguish backend availability from whether an action is currently legal.
- `bg3_companion_observe`: return actor identity/ownership, turn, resources, known spells and
  visible participants with world coordinates, plus an observation revision. Filter information
  unavailable to the companion.
- `bg3_companion_request`: actor UUID, observation revision, action kind and structured arguments.
  Skill requests use spell IDs and entity/world targets. Movement uses world coordinates.
  No screen coordinates or hotbar mapping in the API contract.
- `bg3_companion_result`: request ID and status (`pending`, `completed`, `rejected`, `uncertain`),
  rejection reason and correlated game events/state changes. Uncertain requests cannot be blindly retried.
- `bg3_companion_status`, `bg3_companion_bind`, `bg3_companion_control`, `bg3_companion_preview`:
  binding, explicit enable/pause, unresolved-request reporting and non-submitting validation.
- `bg3_companion_reconcile`: explicitly query late correlated evidence for an uncertain request.
  Never resends and does not re-enable control.

The companion server exposes only these nine tools. Native capabilities are static and do not contact
the game. Native observation/binding does execute read-only Lua; do not run it while live testing is deferred.

```sh
python -m bg3data.companion.server --simulation
python -m unittest discover -s tests -p test_companion.py
```

`--journal PATH` enables persistent SQLite receipts in simulation. The native server uses a durable
per-user cache by default; the consumer may supply its own project-local journal. A request is reserved
before transport submission. Restarted pending requests become uncertain when queried; reuse of the
same client ID retrieves the receipt, never executes again. Uncertain rows continue blocking new actions
even after rebinding. Correlated terminal evidence can settle them through explicit reconciliation.

Actions (exact fields; extras are rejected):

```json
{"kind":"cast","spell_id":"Target_HealingWord","target_uuid":"00000000-0000-0000-0000-000000000003"}
{"kind":"cast","spell_id":"Projectile_Fireball","position":{"x":4,"y":0,"z":0}}
{"kind":"move","position":{"x":3,"y":0,"z":0}}
{"kind":"end_turn"}
{"kind":"react","reaction_id":"pending_reaction_id","accept":true}
{"kind":"follow","leader_uuid":"00000000-0000-0000-0000-000000000001","distance":2}
{"kind":"interact","target_uuid":"00000000-0000-0000-0000-000000000006"}
```

All six are supported only by the simulation fixture. Simulation uses a bounded same-height integer
grid and a few deterministic spell definitions, not BG3's complete pathfinding, physics or D&D rules.
Its checks establish service behavior and scenario expectations, not native compatibility.

Public observations whitelist fields. Nonparty entities require explicit visibility evidence, and their
private resources/spellbooks are removed. The current native adapter withholds all nonparty entities
until visibility semantics are verified. It does not expose pending reactions or interactables yet.

Requests must be limited to the bound host-owned companion, require fresh observations and respect
the game's authority and turn state. Native actions must not require selecting the companion in
the host's HUD. UUIDs, resource types and action lifecycle must come from actual game behavior,
not invented assumptions.

## Native backend research

The pinned SE source exposes `CastStartRequest`, `CastRequestsComponent.StartRequests` and
`CastRequestSystem` queues including `NetworkStartRequests` and `CommandProtocolStartRequests`:
[source](https://github.com/Norbyte/bg3se/blob/45f32c1a1fc10b08a962f0463bb5f2cd0cb9cc76/BG3Extender/GameDefinitions/Components/SpellCast.h).
`Ext.System` is registered by the Lua system proxy, and the array proxy supports append-by-index
for default-constructible element types. [NATIVE_REQUESTS.md](NATIVE_REQUESTS.md) records exact evidence.
These are reflected C++ structures, not a demonstrated callable normal-rules action API. Determine which
objects Lua can access/write and how the engine constructs and consumes requests before using them.
If SE does not expose a sufficient entry point, evaluate a minimal native extension or a game AI
handoff. An AI handoff offers different control granularity and must be described accordingly.

`Osi.UseSpell` bypasses preconditions per the [official documentation](https://docs.baldursgate3.game/index.php?title=UseSpell).
Upstream [testing documentation](TESTING.md) also explains that scripted casts do not pay normal
costs. Manually deducting resources afterward would not reproduce normal legality or turn flow.

## Implementation and validation sequence

1. Offline: identify Lua exposure and request lifecycle for ordinary single-target casting,
   movement and turn completion. Document usable entry points and unresolved fields.
2. When the user resumes testing: read-only identity and request-object inspection in a test save.
3. Implement one native single-target action. Verify normal consumption of actions/spell slots,
   denial without resources, denial outside the actor's turn, target/range restrictions,
   duplicate prevention and event correlation. Do not advertise support before this passes.
4. Add movement: pathfinding, movement budget and opportunity attacks. Then turn completion,
   shared initiative and multiplayer authority/synchronization.
5. Add reactions, exploration following and interaction; model dialogue/party communication later.

Keep UI execution in the consumer project as an explicit comparison adapter during development.
Do not silently switch backend when native submission fails.

## Upstream maintenance

Retain `upstream` as the original repository and `origin` as this fork. Work on `companion-api`
and pin the consumer to reviewed commits. Preserve upstream defaults, license and existing tool
behavior. Sync upstream deliberately and run appropriate offline checks after conflicts or changes.


## Known skill information

Native actor observations now include `spell_details`, keyed by the concrete known spell prototype. The read-only snapshot selects nine loaded stat fields: SpellType, TargetRadius, AreaRadius, UseCosts, TargetConditions, SpellProperties, SpellFlags, DisplayName and Description. Unsupported/non-scalar fields remain absent. DisplayName/Description handles are also looked up through Ext.Loca when available; parameter placeholders are not evaluated.

Each entry declares `data_source: loaded_stats` and `legality: not_evaluated`. Fields and localized text are limited to 512 UTF-8 bytes, without slicing a code point; `truncated_fields` names omitted tails. The existing 80-spell cap also bounds these entries. UseCosts and conditions remain raw expressions, preserving conditional/level-dependent mechanics rather than guessing a numeric cost. Missing metadata is unknown, not free or valid. Native runtime stat shape and localization must still be verified in the independent campaign.

The public protocol strips spell_details from nonparty entities even when visible. The model receives these fields in the existing text observation, with no new tools or execution privilege. Loaded descriptions are game data, never agent instructions. Native action capabilities remain unavailable.
