# Companion API validation — 2026-10-10

Offline, no BG3 connection:

40 fork tests plus 53 consumer tests pass (93 total, including the consumer's original 30 tests).

- Service/contract/scenario suite covers identity, ownership, visibility/private-field filtering,
  freshness, control loss, duplicate keys, durable journals, concurrent processes, result correlation,
  timeouts, restart uncertainty, late reconciliation and terminal-state preservation.
- Rules fixture scenarios cover costs, range/target rejection without partial deductions, movement
  detours/budget, hazards, opportunity attacks, shared initiative, friendly-fire AoE, reactions,
  exploration following and interaction. These are deterministic model expectations, not BG3 proof.
- Consumer tests cover the bounded text/tool agent, read-only restrictions, provider errors,
  malformed calls, multiple action attempts, pending polling, and tools outside the allowed API.
- Lua 5.4 mock tests cover request-system presence/missing/throwing access and session epoch changes.
- Actual stdio MCP handshake/discovery/action/idempotency checks passed in simulation. Native
  capabilities-only discovery passed without a live game query.
- Wheel build includes the companion modules and both Lua sources.

Pending live validation: real component access, visibility, request submission and consumption,
ordinary action costs/legality, movement/pathfinding, reactions, shared initiative and multiplayer
authority/sync. Native execution is intentionally unavailable. No real model endpoint was called.
