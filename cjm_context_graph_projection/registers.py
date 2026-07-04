"""Register drift-check: each hub note's member-cache vs the active `role` assertions.

The register-hub shape (DEC-LOCK `3ef54fae`) is hybrid: membership is NEVER
hand-enumerated — the active `role=<value>` assertions ARE the register, queried
via `list --predicate role` — while the `<value>-register` hub note carries the
prose a projection cannot derive (WHY each member belongs) plus REFERENCES edges
to the members as a navigation CACHE. A cache can rot; this projector is the
reconcile-style read (worklist family: propose/confirm, never auto-fix) that
compares the two:

    missing_cache = an ACTIVE member the hub carries no REFERENCES edge for
    stale_cache   = a hub REFERENCES target that is NOT an active member but HAS
                    carried the role historically (superseded membership — genuine rot)

A hub reference that never carried the role is CONTEXTUAL (hubs cite strategy
prose, sibling registers, design notes) and is deliberately NOT drift. Role
values with no `<value>-register` note are reported hubless (counts only) — a
register that hasn't earned a hub yet is a shape choice, not rot.

Same family as `worklist` / `contradictions` / `readiness`: a derived view over
authored facts + edges; there is no write path here.
"""

from typing import Any, Dict, Set

from cjm_dev_graph_schema.identity import note_node_id
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .display import annotate_display, node_title
from .runtime import GraphHandle

ROLE_PREDICATE = "role"
HUB_SLUG_TEMPLATE = "{value}-register"


def classify_register_drift(
    active_members: Dict[str, Set[str]],    # role value -> subjects with an ACTIVE role=value assertion
    historic_members: Dict[str, Set[str]],  # role value -> subjects that EVER carried role=value (incl. superseded)
    hub_refs: Dict[str, Set[str]],          # role value -> the hub note's REFERENCES target ids (the cache)
    hubs: Dict[str, str],                   # role value -> hub note id (only values whose hub note exists)
) -> Dict[str, Any]:  # {registers: [...], hubless: [...]}
    """Pure: reconcile each register's cache against its membership ground truth.

    Proposes only — confirming a `missing_cache` row means minting the hub
    REFERENCES edge (or adding the `[[link]]` to the hub prose); confirming a
    `stale_cache` row means dropping it. Neither happens here."""
    registers, hubless = [], []
    for value in sorted(set(active_members) | set(hubs)):
        members = active_members.get(value, set())
        if value not in hubs:
            hubless.append({"value": value, "members": len(members)})
            continue
        cache = hub_refs.get(value, set())
        registers.append({
            "value": value, "hub_id": hubs[value],
            "members": len(members), "cached": len(members & cache),
            "missing_cache": sorted(members - cache),
            "stale_cache": sorted((cache - members) & historic_members.get(value, set())),
        })
    return {"registers": registers, "hubless": hubless}


async def register_drift(gx: GraphHandle) -> Dict[str, Any]:  # {registers, hubless, counts}
    """The derived register-cache reconciliation over `role` assertions + hub edges.

    Pure read: loads the role slots (active + historic membership per value),
    resolves each value's hub by the `<value>-register` slug convention
    (deterministic note id — no scan), gathers the hub's REFERENCES cache,
    classifies, then decorates with display labels."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)

    active_members: Dict[str, Set[str]] = {}
    historic_members: Dict[str, Set[str]] = {}
    for slot_assertions in F.group_by_slot(assertions).values():
        if F.prop(slot_assertions[0], "predicate") != ROLE_PREDICATE:
            continue
        subject = F.prop(slot_assertions[0], "subject_id")
        for a in slot_assertions:
            historic_members.setdefault(F.prop(a, "value", ""), set()).add(subject)
        for a in F.active_assertions(slot_assertions, supers):
            active_members.setdefault(F.prop(a, "value", ""), set()).add(subject)

    # Resolve hubs by the slug convention in ONE batched read.
    candidates = {value: note_node_id(HUB_SLUG_TEMPLATE.format(value=value))
                  for value in set(active_members) | set(historic_members)}
    hub_nodes = await F.load_nodes(gx, list(candidates.values()))
    hubs = {value: hid for value, hid in candidates.items() if hid in hub_nodes}

    ref_pairs = await F.load_edge_pairs(gx, DevRelations.REFERENCES)
    hub_ids = set(hubs.values())
    refs_by_hub: Dict[str, Set[str]] = {}
    for src, tgt in ref_pairs:
        if src in hub_ids:
            refs_by_hub.setdefault(src, set()).add(tgt)
    hub_refs = {value: refs_by_hub.get(hid, set()) for value, hid in hubs.items()}

    report = classify_register_drift(active_members, historic_members, hub_refs, hubs)

    ids: Set[str] = set()
    for r in report["registers"]:
        ids.add(r["hub_id"])
        ids.update(r["missing_cache"])
        ids.update(r["stale_cache"])
    nodes = await F.load_nodes(gx, list(ids))
    await annotate_display(gx, list(nodes.values()))
    labels = {i: (node_title(nodes[i]) if i in nodes else i) for i in ids}

    for r in report["registers"]:
        r["hub_label"] = labels.get(r["hub_id"], r["hub_id"])
        r["missing_cache"] = [{"id": i, "label": labels.get(i, i)} for i in r["missing_cache"]]
        r["stale_cache"] = [{"id": i, "label": labels.get(i, i)} for i in r["stale_cache"]]

    drifting = sum(1 for r in report["registers"] if r["missing_cache"] or r["stale_cache"])
    report["counts"] = {"registers": len(report["registers"]),
                        "in_sync": len(report["registers"]) - drifting,
                        "drifting": drifting, "hubless": len(report["hubless"])}
    return report
