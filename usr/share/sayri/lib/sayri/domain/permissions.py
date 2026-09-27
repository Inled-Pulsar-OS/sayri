"""Deciding whether an agent may do something, and who gets asked first.

The rules follow the shape OpenCode uses, because the shape is the useful part:
an ordered list of ``{action, resource, effect}`` where the *last* match wins
and the resource is matched with whole-value wildcards. That is what makes a
broad default with narrow exceptions expressible, which a flat list of
forbidden names is not.

Three layers, checked in this order:

1. The agent's own rules, from its profile. These are the user's choices made
   for that agent, and they decide anything a policy has not spoken about.
2. Saved approvals, which behave exactly like a rule that was appended.
3. Policies, which are installed by the distribution and may only *tighten*.
   A policy ``allow`` never grants access; it lifts an earlier broad policy
   ``deny`` and hands the decision back to the agent's own rules. This is what
   makes the layer safe to impose: no edit to your own configuration can widen
   it, because policies are read after everything else.

The previous behaviour was a list of four exact names (``mkfs``, ``dd``,
``shutdown``, ``reboot``) compared word by word. That missed ``rm -rf``,
``chmod``, ``mv`` and anything piped into a shell, and it could not say "ask
me", so the only available answers were run everything or nothing.

What this module is not: a parser for shell. No amount of glob matching stops
``sh -c "$(curl x)"``, so these rules are a convenience layer over the isolation
level, not a replacement for it. The ``ask`` effect is the part that does the
real work, because it puts a person in the loop for anything the globs cannot
judge.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

ALLOW = "allow"
DENY = "deny"
ASK = "ask"
EFFECTS = (ALLOW, DENY, ASK)

# Actions Sayri can gate. Kept short on purpose: a long list of actions is a
# long list of things nobody remembers to gate.
SHELL = "shell"
READ = "read"
WRITE = "write"
WEB = "web"
SKILL = "skill"
SUBAGENT = "subagent"
ACTIONS = (SHELL, READ, WRITE, WEB, SKILL, SUBAGENT)

# Where a distribution ships its policy. Read-only by convention: the point of
# the layer is that the person sitting at the desktop cannot edit it.
SYSTEM_POLICY = "/usr/share/sayri/policies.json"
# A per-installation override, for administrators rather than users. It sits in
# the same tightening-only layer, so it cannot be used to grant access either.
ADMIN_POLICY = "/etc/sayri/policies.json"

# Which isolation levels can put a question to a person at all.
#
# The rule is about what the level means, not about the command. Below level 3
# the agent is in a read-only or throwaway environment, and a question there is
# theatre: the answer would not change what the sandbox permits. Levels 3 and 4
# execute on the real machine, which is exactly where a person wants a say.
ASKING_LEVELS = ("LEVEL_3", "LEVEL_4")


# ── matching ────────────────────────────────────────────────────────


def _pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a whole-value wildcard pattern.

    ``*`` is any run of characters including none, ``?`` is exactly one, and
    every other character is literal. The match is anchored at both ends: a
    pattern describes the entire value, not a substring of it. Without the
    anchors, a rule for ``dd *`` would also match ``sudo dd if=…``, which is
    how a deny list quietly stops denying.
    """
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.DOTALL)


def _normalize_path(value: str) -> str:
    """Backslashes to slashes and a trailing slash dropped.

    So a rule written one way matches a path however it was typed.
    """
    return str(value or "").replace("\\", "/").rstrip("/") or "/"


def _normalize_shell(text: str) -> str:
    """Canonical form of a command, so spacing cannot dodge a rule.

    A shell command is a little program, not an opaque token: ``a|b``, ``a | b``
    and ``a  |  b`` are the same thing to a shell. Compared as written, a rule
    for ``curl * | sh`` is walked around with nothing but the space bar, and a
    permission list that can be evaded by typing is not a permission list. So
    runs of whitespace collapse and the spaces around command separators go,
    on both sides of the comparison.
    """
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r"\s*([|;&])\s*", r"\1", out)


def normalize_shell(text: str) -> str:
    """Public name for the command canonical form, for callers outside this module."""
    return _normalize_shell(text)


def resource_matches(pattern: str, value: str, shell: bool = False) -> bool:
    """Whether one resource value matches one pattern.

    ``shell`` selects the command normalisation. Paths and URLs are compared as
    written, apart from separators and a trailing slash.
    """
    if pattern == "*":
        return True
    if shell:
        return bool(_pattern_to_regex(_normalize_shell(pattern))
                    .match(_normalize_shell(value)))
    return bool(_pattern_to_regex(pattern).match(_normalize_path(value)))


# The shells a download can be piped into, each with and without the elevation
# that makes it matter. Enumerated rather than globbed on purpose: a pattern of
# ``*|sh`` would also match "wget x | grep sh" and refuse a harmless download,
# which is the other way a deny list stops being trusted.
_FETCHERS = ("curl", "wget")
_SHELL_TARGETS = ("sh", "bash", "dash", "zsh", "ksh",
                  "sudo sh", "sudo bash", "sudo dash", "sudo zsh", "sudo ksh")

# A download piped into a shell runs code the user never read. Said plainly,
# because it is one of the few things here that is not a matter of degree.
PIPE_TO_SHELL: Tuple[dict, ...] = tuple(
    {"action": SHELL, "resource": f"{fetch}*|{target}", "effect": DENY}
    for fetch in _FETCHERS
    for target in _SHELL_TARGETS
)

# What Sayri refuses out of the box, in code so it holds even when no policy
# file has been installed. A shipped file adds to this, never subtracts.
# Written as whole-value patterns: "dd *" matches "dd if=/dev/zero of=/dev/sda"
# and the bare "dd", which a word-by-word comparison could not do.
#
# Note what is deliberately NOT here: a rule for "rm -rf /" or "rm -rf /*". A
# wildcard spans any run of characters, so such a pattern also matches
# "rm -rf /home/jaime/build" and would refuse ordinary work while the command
# that actually matters still slipped through. Narrowing it to the root alone
# is not expressible as a pattern, so it is left to an ask rule or to the level,
# rather than shipped broken.
BASELINE_POLICY: Tuple[dict, ...] = (
    {"action": SHELL, "resource": "mkfs*", "effect": DENY},
    {"action": SHELL, "resource": "dd *", "effect": DENY},
    {"action": SHELL, "resource": "fdisk*", "effect": DENY},
    {"action": SHELL, "resource": "parted*", "effect": DENY},
    {"action": SHELL, "resource": "shutdown*", "effect": DENY},
    {"action": SHELL, "resource": "reboot*", "effect": DENY},
    {"action": SHELL, "resource": "halt*", "effect": DENY},
    {"action": SHELL, "resource": "poweroff*", "effect": DENY},
) + PIPE_TO_SHELL


# ── rules ───────────────────────────────────────────────────────────


@dataclass
class Rule:
    action: str
    resource: str
    effect: str
    # Kept so a refusal can say which rule refused. Without it, "blocked" with
    # no reason is unactionable.
    source: str = ""

    def matches(self, action: str, value: str) -> bool:
        if self.action not in ("*", action):
            return False
        return resource_matches(self.resource, value, shell=(action == SHELL))

    def to_dict(self) -> dict:
        return {"action": self.action, "resource": self.resource,
                "effect": self.effect}


@dataclass
class Decision:
    """The outcome, with enough context to explain itself."""

    effect: str
    action: str
    resource: str
    reason: str = ""
    rule: Optional[Rule] = None

    @property
    def allowed(self) -> bool:
        return self.effect == ALLOW

    @property
    def denied(self) -> bool:
        return self.effect == DENY

    @property
    def needs_ask(self) -> bool:
        return self.effect == ASK

    def message(self) -> str:
        """One line saying what was decided, then why.

        The effect leads on purpose. The reason alone names a rule but not the
        outcome, and "agent: shell true" by itself gives the reader nothing to
        act on: it has to say that this was refused, or that it was waiting for
        a person.
        """
        if self.denied:
            lead = "Blocked"
        elif self.needs_ask:
            lead = "Needs your approval"
        else:
            lead = "Allowed"
        detail = self.reason or (self.action + " " + self.resource)
        return lead + ": " + detail


def _rule_from(raw: Any, source: str) -> Optional[Rule]:
    """Build a Rule from stored data, or None if the entry is unusable.

    A malformed rule is dropped rather than guessed at. Guessing could turn a
    typo in a deny rule into something that silently allows, which is the one
    outcome a permission list must never produce.
    """
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action", "")).strip()
    resource = str(raw.get("resource", "*")).strip() or "*"
    effect = str(raw.get("effect", "")).strip().lower()
    if not action or effect not in EFFECTS:
        return None
    if action != "*" and action not in ACTIONS:
        return None
    return Rule(action=action, resource=resource, effect=effect, source=source)


def load_rules(raw: Iterable[Any], source: str) -> List[Rule]:
    """Turn stored entries into rules, dropping the unusable ones."""
    out: List[Rule] = []
    for entry in raw or ():
        rule = _rule_from(entry, source)
        if rule is not None:
            out.append(rule)
    return out


# ── policies ────────────────────────────────────────────────────────


def _read_policy_file(path: str) -> List[Rule]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        # A missing or broken policy file must not decide anything. The in-code
        # baseline still applies, so failing closed here would mean a typo in
        # JSON took the sandbox away entirely.
        return []
    entries = data.get("policies") if isinstance(data, dict) else data
    return load_rules(entries or (), path)


def load_policies() -> List[Rule]:
    """The tightening-only layer: baseline, then the shipped and admin files."""
    rules = load_rules(BASELINE_POLICY, "baseline")
    for path in (SYSTEM_POLICY, ADMIN_POLICY):
        if os.path.isfile(path):
            rules.extend(_read_policy_file(path))
    return rules


# ── deciding ────────────────────────────────────────────────────────


def _why(rule: Rule, action: str, value: str) -> str:
    if rule.source and rule.source != "baseline":
        return rule.source.split("/")[-1] + ": " + rule.action + " " + rule.resource
    return rule.action + " " + rule.resource


def evaluate_agent_rules(
    rules: Sequence[Rule],
    action: str,
    value: str,
) -> Optional[Decision]:
    """The last matching agent rule decides. None when nothing matched."""
    hit: Optional[Rule] = None
    for rule in rules or ():
        if rule.matches(action, value):
            hit = rule
    if hit is None:
        return None
    return Decision(effect=hit.effect, action=action, resource=value,
                    rule=hit, reason=_why(hit, action, value))


def apply_policies(
    policies: Sequence[Rule],
    agent: Optional[Decision],
    action: str,
    value: str,
) -> Optional[Decision]:
    """Overlay the tightening-only layer on top of what the agent decided.

    Only ``deny`` overrides here. A policy ``allow`` is not a grant: it cancels
    an earlier broad deny for that exact value and the agent's own rules are
    then what decide. That asymmetry is the whole reason the layer is safe to
    impose on someone: nothing in it can widen access, and the worst a mistake
    does is refuse.
    """
    if not policies:
        return agent
    hit: Optional[Rule] = None
    for rule in policies:
        if rule.matches(action, value):
            hit = rule
    if hit is None:
        return agent
    if hit.effect == DENY:
        return Decision(effect=DENY, action=action, resource=value, rule=hit,
                        reason="Blocked by system policy: "
                               + _why(hit, action, value))
    if hit.effect == ASK:
        # A policy that asks is carried through, not dropped. Falling through
        # here would return the agent's own decision, which for an agent with
        # no matching rule is the default — so a policy written to ask would
        # quietly allow, and the one thing the policy layer must never do is
        # widen.
        return Decision(effect=ASK, action=action, resource=value, rule=hit,
                        reason="Needs your approval: "
                               + _why(hit, action, value))
    # A policy allow cancels a broad deny. With nothing to cancel, or when the
    # agent had already decided, the agent's own answer stands.
    if agent is not None and agent.denied and not agent.rule:
        return Decision(effect=ALLOW, action=action, resource=value, rule=hit,
                        reason="Allowed by system policy: "
                               + _why(hit, action, value))
    return agent


def check(
    action: str,
    value: str,
    rules: Sequence[Rule] = (),
    default: str = ALLOW,
    policies: Optional[Sequence[Rule]] = None,
    level: Any = None,
    ask_enabled: bool = True,
) -> Decision:
    """The whole decision, in one call.

    ``default`` is what happens when no agent rule matches, which the caller
    derives from the sandbox level: execution levels allow, no-execution levels
    deny. Policies are applied last whichever way the agent went.

    ``ask_enabled`` is the user's own switch for being asked at all, and the
    default here is True. That is the opposite of what the product does, on
    purpose: the product's default lives in ``SandboxConfig.ask_before_run``,
    which is False and which both real call sites pass explicitly. What this
    default has to be is the safe one, because the mistake being guarded against
    is a caller that forgets to pass the switch. Forgetting it should mean the
    agent asks, not that it quietly runs what somebody wrote a rule to question.
    """
    agent = evaluate_agent_rules(rules, action, value)
    if agent is None:
        agent = Decision(effect=default, action=action, resource=value)
    decided = apply_policies(policies if policies is not None else (),
                             agent, action, value)
    return apply_bypass(decided, level, ask_enabled)


def is_policy_rule(rule: Optional[Rule]) -> bool:
    """Whether a rule came from the tightening-only layer.

    Decided by what the loader actually put there rather than by "anything that
    is not the agent". Naming the two sources the agent uses would work until
    somebody passed a third label, and then every such rule would silently
    become un-bypassable — the wrong way round for a convenience check.
    """
    if rule is None:
        return False
    return rule.source == "baseline" or rule.source in (SYSTEM_POLICY, ADMIN_POLICY)


def ask_allowed(level: Any, ask_enabled: bool = True) -> bool:
    """Whether this level can put a question to a person.

    Two conditions: the user turned asking on, and the level is one where the
    answer could change the outcome.

    An unknown level is treated as able to ask. The gate exists to stop asking
    in a sandbox where the answer cannot matter, and a caller that did not say
    which sandbox it is in has not claimed to be in one of those. Both real
    call sites pass the level, so this only decides how the function behaves
    when called without one — and there, "ask" is the answer that keeps its
    meaning instead of turning into a refusal nobody wrote.
    """
    if not ask_enabled:
        return False
    if level is None:
        return True
    name = getattr(level, "name", str(level))
    return any(mark in name for mark in ASKING_LEVELS)


def apply_bypass(decision: Decision, level: Any, ask_enabled: bool = True) -> Decision:
    """Settle an "ask" that is never going to be asked.

    Three cases, and the third is the one that matters:

    * A rule from the system or the administrator says ask. It stays a no. The
      user's switch applies to the rules in their own agents, and nowhere else,
      or the switch would be a way to talk the system layer out of a question.
    * The level cannot ask. Then it is a no, not a yes: a question that cannot
      be answered is not consent.
    * Otherwise the user has asking switched off, and the command goes ahead.
      This is the bypass, and it is the default.

    A denial is never touched. Turning "ask" into "allow" is a decision to
    change, and only somebody entitled to the rule can make it.
    """
    if not decision.needs_ask:
        return decision
    rule = decision.rule
    from_system = is_policy_rule(rule)
    if from_system:
        return Decision(
            effect=DENY, action=decision.action, resource=decision.resource,
            rule=rule,
            reason="This needs your approval, and asking is switched off. "
                   + _why(rule, decision.action, decision.resource))
    if not ask_enabled:
        # The bypass. The user's own rule asked, and the user has said they do
        # not want to be interrupted about it.
        return Decision(
            effect=ALLOW, action=decision.action, resource=decision.resource,
            rule=rule,
            reason="Allowed without asking: " + (decision.reason or "no rule asked"))
    if not ask_allowed(level, True):
        # Asking is on, but this level cannot put the question, so there is no
        # version of this where the answer arrives. A question nobody can
        # answer is not consent.
        return Decision(
            effect=DENY, action=decision.action, resource=decision.resource,
            rule=rule,
            reason="This needs your approval, but this isolation level "
                   "(%s) does not ask. " % getattr(level, "name", str(level))
                   + "Allow it in the agent's rules, or lower the level.")
    # Asking is on and possible here: leave the question standing.
    return decision


# ── what the level means on its own ─────────────────────────────────
#
# The isolation level stays the coarse boundary it always was: below this line
# nothing executes at all. The rules decide what happens inside a level that is
# allowed to execute, which is the distinction the old flat deny list could not
# express.


def default_effect_for_level(level: Any) -> str:
    """ALLOW for a level that may execute, DENY for one that may not."""
    name = getattr(level, "name", str(level))
    return DENY if "LEVEL_0" in name else ALLOW


def describe_effects() -> str:
    """Plain-English summary, for the prompt and for the panel."""
    return (
        "PERMISSION EFFECTS:\n"
        "- allow: go ahead, no question asked.\n"
        "- ask: stop and wait for the user to approve it in Sayri.\n"
        "- deny: refuse, and say why.\n"
        "Rules are checked newest first and the last one that matches decides, "
        "so put the broad rule first and the exceptions after it.\n"
        "Asking is off unless the user turns it on in Settings, and it only "
        "works at isolation levels 3 and 4. So an ask rule is a request for a "
        "checkpoint rather than a guarantee of one: with asking off, your own "
        "ask rule lets the command through, and a system policy that asks "
        "refuses it. Write a deny when you mean it.\n"
    )
