---
name: claude
description: Compatibility pointer for handing bounded work to Claude from a Codex turn. Use the Möbius spawn_agent tool when available.
---

# Delegating to Claude

When `spawn_agent` is in your current tool list, read the complete `delegation`
skill and start a Claude helper with the Möbius tool (`provider: claude`). It
owns provider enablement, configured model/effort, durable identity, restart
recovery, nested work, and delivering the result to this chat.

If `spawn_agent` is absent from your current tool list, continue locally and sequentially.
Do not launch a provider CLI as a substitute; it would bypass
Möbius delegation ownership, permissions, and result delivery.
