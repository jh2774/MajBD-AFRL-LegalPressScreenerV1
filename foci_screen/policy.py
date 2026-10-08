"""What a tenant screens for, and what it wants to be told about.

Two questions that used to have one hard-coded answer each. Every category was
screened, and every finding at medium or above raised a notice — on every run,
whether or not anything had changed. A contractor on a nightly watchlist with a
standing medium finding queued an identical notice to the same contracting
officer every night, which is the fastest way to teach a reviewer to approve
without reading.

**What to screen for** narrows the evidence: risk categories switched off are
dropped before anything is scored or compounded, and company releases of a
switched-off kind are not read at all. Per-rule switches and weights already
exist on the Rules page; this is the coarser layer above them.

**What raises a notice** decides which findings reach the review queue. The
default keeps the old threshold but adds the thing that was missing: a piece
of evidence notifies once. After that it has been said, and saying it again
tomorrow is noise. A rejected notice counts as said — a reviewer's "no" is the
only labelled false positive this tool ever gets, and resurfacing the same
evidence would overrule it.

Nothing here touches the approval gate. A notice is still a draft that a person
reads, edits and approves; this only decides which drafts get written.

The decision functions are pure, so a policy can be tried against past
findings before it is saved — the preview on the settings page is this module
run over history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

CATEGORIES: dict[str, str] = {
    "FOCI": "Foreign ownership, control or influence",
    "STRUCTURE": "Corporate structure and registration",
    "IP_COLLATERAL": "Intellectual property pledged as collateral",
    "IP_TRANSFER": "IP transfer or financial distress",
    "SANCTIONS": "Sanctions list matches",
    "DISCLOSURE": "New company releases (legal and financial)",
}

RELEASE_KINDS: dict[str, str] = {
    "legal": "Lawsuits, settlements, subpoenas, debarment, CFIUS",
    "financial": "Results, financing, restructuring, mergers and acquisitions",
    "press": "Everything else — awards, appointments, facilities",
}

# No "info": a notice to a contracting officer about something the engine
# itself scores as informational has nothing in it to act on.
NOTICE_SEVERITIES = ("low", "medium", "high", "critical")

NOTICE_MODES: dict[str, str] = {
    "first_seen": "Each piece of evidence notifies once, whenever it is first found — "
                  "including on a contractor's first screen.",
    "changes_only": "Only evidence that appeared since the previous screen. A "
                    "contractor's first screen is a silent baseline.",
}


@dataclass(frozen=True)
class Event:
    rule_id: str
    label: str
    # A backlog is not news. The first screen of a company reads every release
    # in its feed as unseen; notifying on each would bury the reviewer in a
    # year of announcements. A sanctions match, by contrast, matters the first
    # time it is found regardless of when it started.
    requires_new: bool


EVENTS: dict[str, Event] = {
    "novation": Event("CHANGE-NOVATION-01",
                      "An award moves to a different contractor", False),
    "country_change": Event("CHANGE-COUNTRY-01",
                            "A contractor's registered country changes", False),
    "foreign_owned": Event("CHANGE-FOREIGN-OWNED-01",
                           "A contractor newly certifies as foreign owned", False),
    "sanctions_match": Event("SANCTION-01",
                             "A name matches the sanctions list", False),
    "legal_release": Event("RELEASE-LEGAL-01",
                           "A new legal disclosure is published", True),
    "financial_release": Event("RELEASE-FINANCIAL-01",
                               "A new financial disclosure is published", True),
}

# Risk categories raise notices by severity; DISCLOSURE signals are scored at
# zero on purpose, so they reach the queue only through an explicit event.
DEFAULT_NOTICE_CATEGORIES = [c for c in CATEGORIES if c != "DISCLOSURE"]


@dataclass
class ScreeningPolicy:
    categories: list[str] = field(default_factory=lambda: list(CATEGORIES))
    release_kinds: list[str] = field(default_factory=lambda: list(RELEASE_KINDS))
    notice_min_severity: str = "medium"
    notice_mode: str = "first_seen"
    notice_categories: list[str] = field(
        default_factory=lambda: list(DEFAULT_NOTICE_CATEGORIES))
    notice_always: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    # ----------------------------------------------------------- normalising
    @classmethod
    def from_dict(cls, raw: dict | None) -> tuple[ScreeningPolicy, list[str]]:
        """A valid policy from whatever was sent, plus what had to change.

        Adjustments are returned rather than raised because every one of them
        has an obvious right answer, and the person setting the policy should
        be told what it was rather than handed an error to decode.
        """
        raw = raw or {}
        notes: list[str] = []
        default = cls()

        def pick(key: str, allowed, fallback: list[str]) -> list[str]:
            if key not in raw:
                return list(fallback)
            given = [str(v).upper() if allowed is CATEGORIES else str(v)
                     for v in (raw.get(key) or [])]
            kept = [v for v in allowed if v in given]       # catalogue order
            unknown = sorted(set(given) - set(allowed))
            if unknown:
                notes.append(f"Ignored unknown {key.replace('_', ' ')}: "
                             f"{', '.join(unknown)}.")
            return kept

        categories = pick("categories", CATEGORIES, default.categories)
        kinds = pick("release_kinds", RELEASE_KINDS, default.release_kinds)
        notice_categories = pick("notice_categories", CATEGORIES,
                                 default.notice_categories)
        always = pick("notice_always", EVENTS, default.notice_always)

        severity = str(raw.get("notice_min_severity", default.notice_min_severity))
        if severity not in NOTICE_SEVERITIES:
            notes.append(f"Notice threshold '{severity}' is not one of "
                         f"{', '.join(NOTICE_SEVERITIES)}; kept "
                         f"{default.notice_min_severity}.")
            severity = default.notice_min_severity

        mode = str(raw.get("notice_mode", default.notice_mode))
        if mode not in NOTICE_MODES:
            notes.append(f"Notice mode '{mode}' is not recognised; kept "
                         f"{default.notice_mode}.")
            mode = default.notice_mode

        # You cannot be told about what you are not looking for.
        dropped = [c for c in notice_categories if c not in categories]
        if dropped:
            notes.append(f"Notices for {', '.join(dropped)} were switched off "
                         f"because that category is not screened.")
            notice_categories = [c for c in notice_categories if c in categories]

        unreachable: list[str] = []
        for name in list(always):
            rule = EVENTS[name].rule_id
            if rule.startswith("RELEASE-") and "DISCLOSURE" not in categories:
                unreachable.append(name)
            elif rule == "SANCTION-01" and "SANCTIONS" not in categories:
                unreachable.append(name)
            elif rule.startswith("CHANGE-") and "STRUCTURE" not in categories:
                unreachable.append(name)
            elif rule == "RELEASE-LEGAL-01" and "legal" not in kinds:
                unreachable.append(name)
            elif rule == "RELEASE-FINANCIAL-01" and "financial" not in kinds:
                unreachable.append(name)
        if unreachable:
            notes.append(f"'Always notify' for {', '.join(unreachable)} was switched "
                         f"off: what produces it is not being screened.")
            always = [a for a in always if a not in unreachable]

        if not categories:
            notes.append("No categories are screened, so screens will record "
                         "awards but raise no findings.")

        return cls(categories=categories, release_kinds=kinds,
                   notice_min_severity=severity, notice_mode=mode,
                   notice_categories=notice_categories,
                   notice_always=always), notes


# ----------------------------------------------------------- per watchlist

POLICY_FIELDS = tuple(ScreeningPolicy.__dataclass_fields__)


def merge(tenant: dict | None, override: dict | None) -> tuple[ScreeningPolicy, list[str]]:
    """A watchlist's policy: the tenant's, with the watchlist's own settings on top.

    A portfolio of high-priority primes can notify on "low" while a broad
    agency sweep stays at the tenant's "high", without either touching the
    other. The override holds only what it changes; everything else follows
    the tenant policy, including later changes to it. The result goes through
    the same normalising as a saved policy, so an override cannot produce a
    combination the settings page would refuse — notices for a category the
    watchlist does not screen, say.
    """
    base, _ = ScreeningPolicy.from_dict(tenant)
    override = override or {}
    unknown = sorted(set(override) - set(POLICY_FIELDS))
    merged = {**base.to_dict(),
              **{k: v for k, v in override.items() if k in POLICY_FIELDS}}
    policy, notes = ScreeningPolicy.from_dict(merged)
    if unknown:
        notes.insert(0, f"Ignored unknown setting(s): {', '.join(unknown)}.")
    return policy, notes


def clean_override(tenant: dict | None,
                   override: dict | None) -> tuple[dict, ScreeningPolicy, list[str]]:
    """What to store for a watchlist: only the settings it gives, as normalised.

    Stored as the settings named, not as a whole policy, so a watchlist that
    only lowers the notice threshold keeps following the tenant on everything
    else.
    """
    policy, notes = merge(tenant, override)
    full = policy.to_dict()
    return ({k: full[k] for k in (override or {}) if k in POLICY_FIELDS},
            policy, notes)


def policy_for(store, watchlist_id: str = "") -> ScreeningPolicy:
    """The policy a run works under: its watchlist's, or else the tenant's."""
    override = store.watchlist_policy(watchlist_id) if watchlist_id else None
    policy, _ = merge(store.screening_policy(), override)
    return policy


# ------------------------------------------------------------------ screening

def filter_signals(signals: list, policy: ScreeningPolicy) -> list:
    """Evidence in the categories this tenant screens for."""
    wanted = set(policy.categories)
    return [s for s in signals if _attr(s, "category") in wanted]


def wants_document(doc, policy: ScreeningPolicy) -> bool:
    """False for a company release of a kind this tenant does not read."""
    kind = ((getattr(doc, "meta", None) or {}).get("release_kind") or "")
    return not kind or kind in policy.release_kinds


def always_rules(policy: ScreeningPolicy) -> set[str]:
    return {EVENTS[name].rule_id for name in policy.notice_always}


def carries_event(signals: list, policy: ScreeningPolicy) -> bool:
    """Whether any signal is one this tenant always wants to hear about *and*
    is eligible to fire now — a release on a first screen is backlog, so it
    does not count, and keeping a finding for it would store noise."""
    by_rule = {EVENTS[name].rule_id: EVENTS[name] for name in policy.notice_always}
    for s in signals:
        event = by_rule.get(_attr(s, "rule_id"))
        if event and (_attr(s, "is_new", False) or not event.requires_new):
            return True
    return False


# -------------------------------------------------------------------- notices

@dataclass
class NoticeDecision:
    raise_notice: bool
    reason: str
    signal_ids: list[str] = field(default_factory=list)


def _attr(signal, name: str, default=None):
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _signal_id(signal) -> str:
    if isinstance(signal, dict):
        return signal.get("signal_id") or ""
    return signal.key()


def _at_least(severity: str, floor: str) -> bool:
    try:
        return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor)
    except ValueError:
        return False


def decide_notice(severity: str, signals: list, policy: ScreeningPolicy,
                  already_notified: set[str]) -> NoticeDecision:
    """Whether a finding should become a draft notice, and why.

    Works on Signal objects or on the dicts stored in a finding's payload, so
    the same function drives the live decision and the preview over history.
    """
    unseen = [s for s in signals if _signal_id(s) not in already_notified]
    if not unseen:
        return NoticeDecision(False, "Everything in this finding has already been "
                                     "the subject of a notice.")

    # Explicit events first. They bypass the severity threshold and the mode —
    # that is what "always" means — but not the once-only rule.
    by_rule = {EVENTS[name].rule_id: EVENTS[name] for name in policy.notice_always}
    event_hits = [s for s in unseen
                  if _attr(s, "rule_id") in by_rule
                  and (_attr(s, "is_new", False)
                       or not by_rule[_attr(s, "rule_id")].requires_new)]
    if event_hits:
        labels = sorted({by_rule[_attr(s, "rule_id")].label.lower()
                         for s in event_hits})
        return NoticeDecision(
            True, "Always notify: " + "; ".join(labels) + ".",
            [_signal_id(s) for s in event_hits])

    if not _at_least(severity, policy.notice_min_severity):
        return NoticeDecision(False, f"The finding is {severity}, below the "
                                     f"{policy.notice_min_severity} threshold for "
                                     f"notices.")

    fresh = unseen
    if policy.notice_mode == "changes_only":
        fresh = [s for s in unseen if _attr(s, "is_new", False)]
        if not fresh:
            return NoticeDecision(False, "Nothing in this finding changed since the "
                                         "previous screen.")

    allowed = set(policy.notice_categories)
    qualifying = [s for s in fresh if _attr(s, "category") in allowed]
    if not qualifying:
        return NoticeDecision(False, "The new evidence is in categories that are "
                                     "screened but set not to raise notices.")

    rules = sorted({_attr(s, "rule_id") for s in qualifying})
    kind = "new since the last screen" if policy.notice_mode == "changes_only" \
        else "not previously notified"
    return NoticeDecision(
        True, f"A {severity} finding with evidence {kind}: {', '.join(rules)}.",
        [_signal_id(s) for s in qualifying])
