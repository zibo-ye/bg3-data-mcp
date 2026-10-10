# Companion API development plan

## Goal and ownership

An external agent should control the fourth recruited companion in a three-player BG3 party,
using normal game rules. Human-like behavior means following party intent, moving, selecting
skills and cooperating; it does not require imitating mouse input.

This fork owns game-facing data and action transport. The consuming companion project owns
agent prompts, planning, policy, configuration and the bounded decision loop. Avoid moving
model-provider code into the data server. Upstream lookup, indexing and SE transport remain reusable.

The fork currently changes documentation only. There are no new callable companion tools or
validated native actions. Existing SE eval and development/test tools can alter the game and
must not be treated as a restricted autonomous companion interface.

## Proposed tool boundary (not implemented)

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

Requests must be limited to the bound host-owned companion, require fresh observations and respect
the game's authority and turn state. Native actions must not require selecting the companion in
the host's HUD. UUIDs, resource types and action lifecycle must come from actual game behavior,
not invented assumptions.

## Native backend research

The pinned SE source exposes `CastStartRequest`, `CastRequestsComponent.StartRequests` and
`CastRequestSystem` queues including `NetworkStartRequests` and `CommandProtocolStartRequests`:
[source](https://github.com/Norbyte/bg3se/blob/45f32c1a1fc10b08a962f0463bb5f2cd0cb9cc76/BG3Extender/GameDefinitions/Components/SpellCast.h).
These are reflected C++ structures, not a demonstrated callable Lua action API. Determine which
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
