"""Run a callback after a committed session write touches watched tables.

In-process caches and signals (open-stream access, the public service index)
need to know when rows they depend on changed, without polling the database.
``on_committed_change`` marks a session when a flush or a bulk statement
writes one of the tables, and calls the callback once that session's
outermost transaction commits. A rolled-back transaction calls nothing.

Writes from another process cannot reach these hooks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


def on_committed_change(
  name: str,
  tables: Iterable[str],
  callback: Callable[[], None],
  *,
  inserts: bool,
) -> None:
  """Call ``callback`` after each commit that wrote one of ``tables``.

  ``inserts`` chooses whether a new row counts; updates (of a column that
  actually changed) and deletes always do. ``name`` keys the per-session mark,
  so it must be unique per watcher.
  """
  watched = frozenset(tables)
  key = f"mobius_committed_change:{name}"

  def touches(instance) -> bool:
    return getattr(inspect(instance).mapper.local_table, "name", None) in watched

  @event.listens_for(Session, "after_flush")
  def _mark_flush(session, _flush_context) -> None:
    if (
      any(touches(obj) for obj in session.deleted)
      or (inserts and any(touches(obj) for obj in session.new))
      or any(
        touches(obj) and session.is_modified(obj, include_collections=False)
        for obj in session.dirty
      )
    ):
      session.info[key] = True

  @event.listens_for(Session, "do_orm_execute")
  def _mark_statement(state) -> None:
    if not (state.is_update or state.is_delete or (inserts and state.is_insert)):
      return
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", None) in watched:
      state.session.info[key] = True

  @event.listens_for(Session, "after_commit")
  def _publish(session) -> None:
    # SQLAlchemy also fires this when a savepoint is released; the change is
    # not visible to other sessions until the outermost transaction commits.
    if session.in_nested_transaction():
      return
    if session.info.pop(key, False):
      callback()

  @event.listens_for(Session, "after_transaction_end")
  def _forget(session, transaction) -> None:
    # Only the outermost transaction ends the unit of work; a rolled-back
    # savepoint must not drop a change its committed parent still carries.
    if transaction.parent is None:
      session.info.pop(key, None)
