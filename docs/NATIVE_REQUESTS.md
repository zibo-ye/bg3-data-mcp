# Native request investigation

Evidence is from pinned BG3SE source `45f32c1a1fc10b08a962f0463bb5f2cd0cb9cc76`.
This is an offline code audit, not evidence of runtime compatibility with the installed loader.

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
