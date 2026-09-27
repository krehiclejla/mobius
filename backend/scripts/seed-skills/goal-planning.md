# Goal planning

Finish the read before material work on a new or resumed Goal. Bounded one-turn
work stays standard. Only the owner promotes; honor opt-outs.

## The execution loop — read this first

A Goal is durable intent; execution attempts are separate. Resume its saved
record after interruption instead of replacing unfinished scope.

1. Promote, then record a small nested route. Parents verify; leaves work.
2. Run ready independent sibling leaves concurrently only when authorized and
   isolation avoids repeated input. Parallelism itself is not the saving.
3. Serialize dependencies, shared writes, plan revisions, and final integration.
4. Add discoveries beneath their owner; preserve unfinished siblings.
5. Work in-run. After verifying the outcome, call `update_goal` with
   `complete`.

## Route and promote

Promote an observable outcome when durability helps across stages, turns,
discovery, parallel work, long operations, or restart risk and work can begin
now. This is judgment, not a keyword trigger. Recheck after owner choices,
implementation approval, or expanded scope.

Use the first-class `promote_goal` tool. The helper is resilience, not an
equivalent convenience path: use it only when the tool is absent or an attempted
tool call returns a failure:

```bash
python3 /data/platform/backend/scripts/mobius_control_mcp.py call promote_goal --args-json '{"objective":"Outcome and completion condition"}'
```

## Plan and work

Pass the plan to `promote_goal` as `tasks`, or send it later with
`update_goal`. Each task is `{id, title, depends_on?, parent_id?}`; a nested
task names its `parent_id`.

`update_goal` `tasks` edits the plan as one revision: a known id changes only
the fields you give, a new id adds a task. Finish one task and start the next
in the same call — `[{id: "inspect", status: "completed", result: "..."},
{id: "build", status: "running"}]`. Each call returns the revision and the
running and ready tasks, so there is no separate read. With no arguments it
returns the full plan.

Statuses are pending, running, completed, blocked, failed, and cancelled; a
note or result holds up to 1000 characters. Work deepest leaves.
Children inherit ancestor dependencies and make a parent **Ready to verify**,
not complete. Verify upward; cancelled prerequisites are settled. Plans may
change; outcomes may not. Settled tasks do not close the Goal. To work on a
retained unfinished Goal other than the one shown, pass its `goal_id`; that
attaches this attempt and cannot reopen closed work.

### When your turn ends

A Goal never continues by itself. When your turn ends, the chat is the
owner's turn unless something you armed will wake it: a durable Wait (read
`waiting.md`) for an observable condition, or a helper whose result returns
here. A decision or approval only the owner can give goes on its saved card; a
`blocked` task only records that gate.

Work in-run; turns are not a budget. Do not end a run merely to refresh context
or select the next task, and never promise to continue later without arming
what will wake you. Otherwise end with an honest, plain status.

After verifying the original outcome, call `update_goal` with `complete:
'Verified evidence'` (plus `finished_claims` for exact claimed actions this Goal
performed). It validates and records completion and is refused while tasks or
helpers are unfinished; a green plan or ended attempt leaves the Goal open.
After an owner Stop, summarize, then honor it last with
`mapi -X POST /api/chat/stop -d "{\"chat_id\":\"$CHAT_ID\"}"`.
