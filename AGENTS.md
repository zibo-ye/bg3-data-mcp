# Companion API fork instructions

- Preserve upstream MIT license and attribution. Keep additions scoped to companion observation/action APIs so they can be reviewed independently of upstream modding tools.
- Base: upstream commit `def2602cfa31fea45ce5109cc6ff459688947634`; development branch: `companion-api`.
- Target: one host-owned, recruited non-avatar companion in a three-player party. Structured game data and normal-rules action requests are the preferred backend; vision and UI input are optional adapters.
- The user deferred live testing on 2026-10-10. Do not restart/close BG3, load saves, send input, or execute Lua in the live session until the user resumes testing.
- The user subsequently reported BG3 exited and authorized an independent new save. Live testing is resumed for that test campaign; preserve existing campaigns and keep execution evidence tied to actual engine behavior.
- Do not use `Osi.UseSpell`, teleports, resource writes or spawned test encounters as proof of normal-rules gameplay.
- Reflected engine request types are research evidence, not proof of a usable Lua action API. Validate authority, submission, consumption, costs, rejection and multiplayer synchronization before advertising support.
- Keep execution unavailable for unvalidated native capabilities. Never silently fall back to scripted spells or UI input.
- Do not expose unrestricted modding tools as the autonomous companion's action surface. Keep companion tools separate and validate actor identity and request freshness.
- Preserve credentials, local configuration and game data outside Git. Do not redistribute restricted third-party mod implementations.
- Distinguish offline tests, protocol tests and live gameplay validation in reports.
