"""Owner chat archiving: filing a chat away without touching its history.

An archived chat stays a complete, working chat. Archiving never stops a run,
cancels a wait, releases a claim, or expires anything; it only moves the chat
from the drawer's Recents to its Archived section and out of the recent-chat
continuity new sessions receive. That is the difference from deletion, which
ends the chat's work and starts a recovery window.

Only the owner's own actions change the flag: the explicit archive/restore
routes, and the owner sending a message into an archived chat (which restores
it, the way replying to an archived conversation brings it back elsewhere).
Agent, helper, wait, and app activity deliberately leave the chat archived.
"""

from app import drawer_pins, models
from app.broadcast import get_system_broadcast
from app.timeutil import now_naive_utc
from sqlalchemy.orm import Session


def publish_archive_changed(chat: models.Chat) -> None:
  """Tell every open shell to re-read this one chat's row."""
  get_system_broadcast().publish(
    {"type": "chat_archive_changed", "chatId": str(chat.id)}
  )


def archive_chat(db: Session, chat: models.Chat) -> None:
  """Archive ``chat`` and unpin it; already-archived chats keep their stamp.

  Pinned is a promise to keep something in view, which contradicts filing it
  away, so archiving clears the pin inside the drawer's shared pin boundary.
  Restoring does not re-pin.
  """
  if chat.archived_at is not None:
    return
  with drawer_pins.serialized_write():
    chat.archived_at = now_naive_utc()
    chat.pinned_at = None
    db.commit()
  publish_archive_changed(chat)


def unarchive_chat(db: Session, chat: models.Chat) -> None:
  """Return an archived chat to Recents at its existing activity position."""
  if chat.archived_at is None:
    return
  chat.archived_at = None
  db.commit()
  publish_archive_changed(chat)


def restore_on_accepted_input(chat: models.Chat, requested: bool) -> None:
  """Join an accepted owner input's writer transaction; never commit here.

  The writer calls this only after its command's duplicate and validation
  gates. The route publishes the drawer change after the writer ack.
  """
  if requested and chat.archived_at is not None:
    chat.archived_at = None
