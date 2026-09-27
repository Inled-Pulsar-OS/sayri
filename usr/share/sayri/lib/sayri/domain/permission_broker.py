"""Asking a person before an agent does something.

An ``ask`` rule is only worth having if there is someone to answer it. This
module is the part that makes that true: the agent's thread parks, the request
is shown, and the answer comes back on the same object.

Three decisions worth stating, because each is a way this could be wrong:

* **Waiting is bounded.** A request nobody answers must not hold a turn open
  for ever, so every request carries a deadline. On expiry the answer is "no".
  Failing towards "no" matters: the alternative is a prompt that silently
  becomes consent, which is the failure mode of every permission system built
  out of a dialog box.

* **Interrupting cancels.** If the user stops the turn while a question is on
  screen, the answer is "no" as well. Otherwise a stopped turn would leave a
  thread parked, and the next one would queue behind it.

* **Answers are matched by id, never by order.** Two requests can be open at
  once (a sub-agent and its parent), and answering the wrong one because it
  came first would run a command the user never looked at.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# How long a question stays open. Long enough to read a command and decide,
# short enough that a forgotten dialog is not still waiting tomorrow.
DEFAULT_TIMEOUT = 120.0


@dataclass
class PermissionRequest:
    id: str
    action: str
    resource: str
    reason: str = ""
    agent_id: str = ""
    agent_name: str = ""
    session_id: str = ""
    # Set when the rule that asked came from a policy file rather than the
    # agent's own configuration, so the UI can say the user cannot widen it.
    policy: bool = False
    deadline: float = 0.0
    created: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "resource": self.resource,
            "reason": self.reason,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "policy": self.policy,
            "expires_in": max(0.0, round(self.deadline - time.monotonic(), 1))
            if self.deadline else 0.0,
        }

    def expired(self) -> bool:
        return bool(self.deadline) and time.monotonic() >= self.deadline


class PermissionBroker:
    """Holds the open questions and hands back the answers.

    A plain lock around a dict is the whole mechanism. The interesting part is
    not the concurrency, it is that every path out of :meth:`wait` returns a
    definite answer, so no caller can end up treating a timeout as a yes.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = float(timeout)
        self._lock = threading.Lock()
        self._open: Dict[str, dict] = {}
        # Per instance, not per class: two brokers are two separate people
        # answering, and sharing one bucket would let one agent's "always" grant
        # access to another agent's commands.
        self._approvals: Dict[str, List[dict]] = {}
        # Called with each request dict as it opens, so a front-end can show it.
        self.on_request: Optional[Callable[[dict], None]] = None
        # Called with the request id once it is settled, so a front-end can
        # take the card off the screen. Without this a card outlives its
        # question and the user answers something already decided.
        self.on_resolved: Optional[Callable[[str], None]] = None

    # ── the agent's side ────────────────────────────────────────────

    def ask(
        self,
        action: str,
        resource: str,
        reason: str = "",
        agent_id: str = "",
        agent_name: str = "",
        session_id: str = "",
        policy: bool = False,
    ) -> bool:
        """Park until someone answers. True means yes.

        Call this from the agent's own thread; it blocks that thread, not the
        one answering.
        """
        request = PermissionRequest(
            id=uuid.uuid4().hex[:12],
            action=action,
            resource=resource,
            reason=reason,
            agent_id=agent_id,
            agent_name=agent_name,
            session_id=session_id,
            policy=policy,
            deadline=time.monotonic() + self._timeout,
        )
        event = threading.Event()
        box: Dict[str, Any] = {"allow": False}
        with self._lock:
            self._open[request.id] = {"event": event, "box": box, "request": request}

        self._notify(self.on_request, request.to_dict())
        try:
            if not event.wait(timeout=self._timeout):
                # No answer in time: no. Not "assume yes", and not a hang.
                self.answer(request.id, False, resolved="timeout")
        finally:
            with self._lock:
                self._open.pop(request.id, None)
        self._notify(self.on_resolved, request.id)
        return bool(box["allow"])

    def cancel_session(self, session_id: str) -> int:
        """Refuse everything open for one session. Used when a turn is stopped."""
        with self._lock:
            ids = [rid for rid, entry in self._open.items()
                   if entry["request"].session_id == session_id]
        for rid in ids:
            self.answer(rid, False, resolved="cancelled")
        return len(ids)

    def cancel_all(self, reason: str = "cancelled") -> int:
        """Refuse everything open, whoever asked."""
        with self._lock:
            ids = list(self._open)
        for rid in ids:
            self.answer(rid, False, resolved=reason)
        return len(ids)

    # ── the answer's side ───────────────────────────────────────────

    def answer(self, request_id: str, allow: bool, resolved: str = "answered",
               remember: bool = False) -> bool:
        """Deliver an answer. Returns False if the id is unknown or already done.

        An unknown id is a normal thing to get (a stale panel answering a
        question that timed out a second ago), so it is reported, not raised.

        ``remember`` saves the approval as a rule for next time, and it happens
        here rather than at the call site so it can only ever be recorded for a
        question that was really open. Answering an id that already timed out
        must not leave a rule behind for something the user never saw.
        """
        with self._lock:
            entry = self._open.get(request_id)
            if entry is None:
                return False
            request = entry["request"]
            if remember and allow:
                self._store(request)
            entry["box"]["allow"] = bool(allow)
            entry["event"].set()
        return True

    def pending(self) -> List[dict]:
        """What is on screen right now."""
        with self._lock:
            return [entry["request"].to_dict() for entry in self._open.values()]

    def has(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._open

    def _notify(self, hook: Optional[Callable], payload: Any) -> None:
        """Call a front-end hook without letting it break the agent.

        These hooks are UI code reached from the agent's own thread. A
        front-end that raises here would otherwise turn a cosmetic problem into
        a failed turn, and the user would be left with a question that never
        gets answered because the thread that asked it is already dead.
        """
        if hook is None:
            return
        try:
            hook(payload)
        except Exception:
            pass

    # ── remembering ─────────────────────────────────────────────────

    def approvals_for(self, action: str, resource: str) -> List[dict]:
        """Saved approvals that would cover this action and value.

        Kept here rather than in the profile so "allow always" does not have to
        rewrite the agent's configuration behind the user's back, and so a
        policy can still overrule it.
        """
        from sayri.domain import permissions

        out = []
        for entry in self._approvals.get(action, []):
            if permissions.resource_matches(entry["resource"], resource,
                                           shell=(action == permissions.SHELL)):
                out.append(entry)
        return out

    def remember(self, action: str, resource: str, agent_id: str = "") -> dict:
        """Save an approval as a rule that would match this and similar values.

        The pattern is derived from the command that was approved, so
        "allow always" for ``git status --short`` covers ``git status`` next
        time without covering ``git push``. Narrowing the approval to the shape
        of the command is the difference between a useful memory and a blanket
        licence the user did not intend to give.
        """
        return self._store(
            PermissionRequest(id="", action=action, resource=resource,
                              agent_id=agent_id))

    def _store(self, request: "PermissionRequest") -> dict:
        """The one place an approval is written. Call with the lock held."""
        from sayri.domain import permissions

        pattern = (derive_pattern(request.resource)
                   if request.action == permissions.SHELL else request.resource)
        entry = {"action": request.action, "resource": pattern,
                 "agent_id": request.agent_id}
        bucket = self._approvals.setdefault(request.action, [])
        # Re-approving the same thing replaces the old entry rather than
        # stacking a second copy, so the list cannot grow without bound as a
        # user clicks "always" over and over.
        bucket[:] = [e for e in bucket
                     if e["resource"] != pattern or e["agent_id"] != request.agent_id]
        bucket.append(entry)
        return entry

    def forget(self, action: Optional[str] = None, agent_id: str = "") -> int:
        """Drop saved approvals. Returns how many went."""
        with self._lock:
            if action is None:
                n = sum(len(v) for v in self._approvals.values())
                self._approvals.clear()
                return n
            bucket = self._approvals.get(action, [])
            keep = [e for e in bucket if e["agent_id"] != agent_id]
            removed = len(bucket) - len(keep)
            self._approvals[action] = keep
            return removed

    def saved_approvals(self) -> Dict[str, List[dict]]:
        """Every saved approval, for a panel that lists them.

        A copy, because the caller is on the answering thread and the lists are
        mutated under a lock by the agent's thread.
        """
        with self._lock:
            return {action: list(entries)
                    for action, entries in self._approvals.items()
                    if entries}

    def approval_rules(self, action: str, agent_id: str = "") -> List[Any]:
        """Saved approvals as Rule objects, to hand to the permission check."""
        from sayri.domain import permissions

        with self._lock:
            entries = list(self._approvals.get(action, []))
        return [
            permissions.Rule(action=action, resource=e["resource"],
                             effect=permissions.ALLOW, source="approval")
            for e in entries
            if not agent_id or e["agent_id"] in ("", agent_id)
        ]


def derive_pattern(command: str) -> str:
    """A pattern that matches this command and its arguments, but not a cousin.

    The program is kept literal, and so is the subcommand when the second word
    is not a flag. Everything after that becomes one ``*``.

    Keeping the subcommand is the part that matters. Widening only the first
    word gives ``git *`` for "git push", and then a single "always" on one push
    quietly authorises ``git reset --hard`` for ever. ``git push *`` covers the
    arguments of the same command and still asks about the neighbouring one,
    which is the difference between a memory and a licence nobody meant to
    give.

    A second word that is a flag is treated as an argument, not a subcommand, so
    ``rm -rf /tmp/build`` becomes ``rm *`` rather than ``rm -rf *``.
    """
    from sayri.domain import permissions

    text = permissions.normalize_shell(command)
    if not text:
        return "*"
    words = text.split(" ")
    if len(words) == 1:
        return words[0]
    if not words[1].startswith("-"):
        return words[0] + " " + words[1] + " *"
    return words[0] + " *"
