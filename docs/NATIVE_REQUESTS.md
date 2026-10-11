# Native request investigation

Evidence is from pinned BG3SE source `45f32c1a1fc10b08a962f0463bb5f2cd0cb9cc76`.
The source audit is supplemented by the limited live observations below; request submission remains unvalidated.

## Live inspection — 2026-10-10

Independent balanced tutorial campaign, BG3 DX11 4.1.1.7631656 / SE 32. Protected system lookups confirm ServerCastRequest, ServerTurnOrder and ServerSightViewshed plus network/command cast and EndTurn arrays. Idle actor StartRequests was absent. A read-only OnSystemUpdate hook captured an ordinary UI Hide cast by recruited host-owned, non-avatar Lae'zel in NetworkStartRequests. The action resource decreased 1 to 0 and CastedSpell was observed.

The request used Shout_Hide in Prototype and OriginatorPrototype; SourceType SpellSet; Source and ProgressionSource zero GUIDs; caster self-target with TargetingType Shout; Immediate and FromClient flags; empty NetGuid; generated RequestGuid; default originator metadata. The initial capture did not modify a queue. This example does not establish the proper metadata for other spells, submission legality or multiplayer authority. Local runtime evidence is retained by the consuming project's ignored artifacts/live directory, with findings in docs/LIVE_SESSION.md.

Subsequently, a typed Lua table using actual companion SpellBook metadata was appended to NetworkStartRequests while the companion had zero action points. Conversion and append succeeded (0→1); the engine consumed it, but no additional cast event or resource change occurred. No explicit rejection reason was captured, so this is not proof of an engine cost-check rejection. A separate EndTurn entity append was consumed and changed only that companion's RequestedEndTurn, leaving the allied player active in the same initiative group. No production action capability is enabled by these experiments.

## Bounded developer trace

`bg3data.companion.research.NativeRequestTrace` installs observation hooks only. It is intentionally absent from the restricted companion MCP. Use it during an authorized live test after obtaining a snapshot:

```python
from bg3data.companion.native import NativeBackend
from bg3data.companion.research import NativeRequestTrace

NativeBackend().observe(actor_uuid)
trace = NativeRequestTrace()
try:
    trace.query(actor_uuid, "install")
    # Perform a separately authorized test; the trace itself executes no action.
    evidence = trace.query(actor_uuid, "read")
finally:
    evidence = trace.query(actor_uuid, "stop")
```

It rejects avatars, remote-owned and unrecruited actors; filters queue and cast records to the bound companion; records request and cast GUIDs separately; stops on session epoch changes, after 3600 system ticks or three errors; and retains at most 96 records. Partial installation rolls back subscriptions. Calling read/stop does not reinstall. Stopping preserves collected evidence. Offline Lua tests cover these invariants; actual component creation timing and metadata initialization remain unvalidated. A matching spell name, empty queue or unrelated CastedSpell event cannot by itself establish request completion.

## Confirmed exposure in source

| Source | Finding |
|---|---|
| `Lua/Libs/LuaSharedLibs.cpp`, `RegisterSystemProxy` | Sets `Ext.System` to the system proxy (singular). |
| `Lua/Shared/Proxies/LuaSystemMap.inl` | Index resolves `ExtSystemType` and uses `ExposedSystemTypes.inl` to return system references. |
| `GameDefinitions/Components/ExposedSystemTypes.inl` | Exposes `esv::spell_cast::CastRequestSystem`, `esv::TurnOrderSystem` and `esv::sight::ViewshedSystem`. |
| `GameDefinitions/Components/SpellCast.h` | `ServerCastRequest` includes `NetworkStartRequests` and other origin-specific queues; `ServerSpellCastRequests` has `StartRequests`. |
| `Lua/Shared/Proxies/LuaArrayProxy.h`, `SetElement` | For default-constructible elements, assigning index `size+1` calls `PushBack(get<T>(...))`. This says nothing about whether a particular Lua table converts safely or is a valid game request. |
| `GameDefinitions/Components/Combat.h` | `ServerTurnOrder` exposes `EndTurn` entity array and `TurnCompleted` requests. |
| `GameDefinitions/Controllers/Movement.h` | Movement state machine/controller structures exist; this does not identify an exposed normal player movement submission method. |

Read-only `lua/probe.lua` checks systems, queue presence, optional reflected type and length.
It neither serializes whole pending player requests nor appends anything. Unsupported fields are
handled with `pcall`. Native visibility is withheld; `SightEntityViewshed` and sight systems need
runtime interpretation before being used as proof of perception.

## Casting fields needing real examples

`NativeRequestPreparation` in the same developer-only module resolves a read-only plan using actual SpellBook metadata. It rechecks the expected observer session, recruited non-avatar identity, a positive reserved host user ID, active turn and an idle destination queue. A cast requires a single unambiguous SpellBook source and a self-target Shout stats entry; other targeting adapters remain unavailable. Missing source fields fail rather than being replaced by zero GUIDs. The ordinary Immediate/FromClient flag proposal is retained, with no bypass flags. End-turn plans do not require a spell.

```python
from bg3data.companion.research import NativeRequestPreparation
plan = NativeRequestPreparation().prepare(
    actor_uuid, observed_session_id,
    {"kind": "cast", "spell_id": "Shout_Hide", "target_uuid": actor_uuid},
    request_guid,
)
```

Plans neither append queues nor change resources; they are not completed/rejected receipts and confer no execution authority. Legality stays unvalidated, including a zero-resource plan. An eventual submitter must repeat all preflight checks and resolve live handles in the same engine call that enqueues the action, rather than replaying an old plan. Production NativeBackend submission remains disabled. Offline tests exercise real Lua source with mocked engine objects; source conversion and stats targeting still require live verification for each supported action.

`CastStartRequest` contains Spell, CastOptions, Caster, Targets, Originator, Item, unknown handles,
optional CastPosition, StoryActionId, NetGuid, an unknown byte and RequestGuid.
`SpellId` includes prototype, originator prototype, source type, source and progression-source GUIDs.
A spell name alone is insufficient to reconstruct this metadata reliably.
`InitialTarget` extends BaseTarget with targeting type and optional second target, and BaseTarget
contains target handle, optional target proxy and position.

The flag enum contains bypass options such as IgnoreHasSpell, IgnoreCastChecks, IgnoreTargetChecks
and Forced. These must not be used for normal companion actions. The enum also has FromClient,
but its name alone is not evidence that setting it creates an authorized player request.

Next live inspection must identify, without altering a request:

1. Which queue receives an ordinary player cast, and when the engine consumes it.
2. Exact SpellId/target/originator/net identity and request GUID representation.
3. Whether preparation/confirmation and reactions require additional request stages.
4. How rejection and completion correlate with a request, including interrupted/countered casts.
5. How host authority, actor ownership and multiplayer synchronization are checked.

Only after that can a submission adapter be implemented and tested for resource payment and normal
legality. The prepared probe deliberately supplies no enqueue template with guessed fields.

## Movement and turn completion

Appending an entity to an EndTurn array is a research candidate; it is not verified to complete only
that character's shared initiative turn. Similarly, Osiris movement and movement-machine field
writes cannot be assumed to reproduce a player path request, movement budget or opportunity attacks.
No native action is currently exposed by this fork. If a sufficient SE API is unavailable, evaluate
a minimal native entry point against these same requirements; do not emulate legality by editing resources.


## Queue trace sampling

A request held in a queue is recorded once per continuous presence, keyed by queue and request GUID. A later complete sample may record `queue_departed`; it proves only observed absence, never consumption, rejection or success. Sampling is bounded to 96 companion entries and skips departure inference for incomplete samples. Queue and cast records preserve their separate network identity fields for inspection; those fields do not establish a validated mapping. Cast callbacks recheck companion ownership before recording. Fourteen mocked trace tests cover these limits; real engine callback timing and request-to-cast correlation remain unverified.
