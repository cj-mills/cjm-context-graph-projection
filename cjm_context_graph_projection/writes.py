"""THE WRITE ORDER ON A PROJECTION GRAPH: plan -> append -> apply -> advance (design detail
65f4a6c9, work item eeda5977 leg L2).

Every write a verb makes to the dev or the notes graph is an op on that graph's WRITES journal. The
verb's branch calls `begin()` -- the family's lock taken, the db healed up to its journal -- then
PLANS the op against that db (every reference resolved to a full id, every id it mints fixed,
validated, so its apply cannot refuse), and `write()` APPENDS the op and APPLIES it through the
replay's own applier (`apply_write_op`): a live write and its rebuild are one code path by
construction (ruling e4b2dfbb). The invocation is ONE unit of work on the family: the lock is held
from `begin()` until the invocation closes -- its site-link step included -- so the family's
writers apply in journal order live, exactly as replay applies them.

A record the heal applies (a crashed writer's tail) runs as its own window, at its own journaled
ts, its site-link step at its close -- as replay runs it. An invocation with no writes journal (no
--journal-path: a scratch graph, a test) applies through the same applier and appends nothing.

`write_window` is the ONE unit of work every writer takes (ruling d2d05e78 (1)): the CLI's
invocation and an app's write gesture alike -- the family's lock outermost, the live write window
inside it, so the site-link step runs before the lock is released."""

import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional

from cjm_context_graph_layer.ops import GraphCursorStore, observe_writes
from cjm_context_graph_primitives.writer import (family_name, journal_lag, JournalWriter,
                                                 stamp_cursor)

from .journal import apply_write_op
from .runtime import GraphHandle
from .sitelinks import site_link_window, step_site_links


@dataclass
class Written:
    """What one write did: the core verb's result, and whether its op was appended (False only
    with no writes journal)."""

    result: Any = None        # The core verb's result
    journaled: bool = False   # The op was appended


class InvocationWrites:
    """One invocation's unit of work on its graph's writes journal (65f4a6c9): `begin()` before
    the plan, `write()` per planned op; the family's lock is released when the block exits."""

    def __init__(
        self,
        gx: GraphHandle,
        journal_path: Optional[str],     # The graph's writes journal (None = unjournaled: the applier alone)
        emit_root: Optional[str] = None,  # The notes graph's emit root (a born post's file location derives from it)
    ):
        self.gx, self.journal_path, self.emit_root = gx, journal_path, emit_root
        self._writer: Optional[JournalWriter] = None
        self._begun = False
        self._live: Optional[Dict[str, Any]] = None  # The op this invocation is writing (its result lands here)
        self.healed = 0   # Records the heal applied at `begin()`
        self.written = 0  # Ops this invocation appended

    async def __aenter__(self) -> "InvocationWrites":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._writer is not None:
            writer, self._writer = self._writer, None
            await writer.__aexit__(*exc)

    async def begin(self) -> "InvocationWrites":
        """Take the family's lock and heal the db up to its journal, so the plan that follows reads
        a db holding the whole family. Once per invocation (a later call is a no-op)."""
        self._begun = True
        if self._writer is None and self.journal_path:
            writer = JournalWriter(self.journal_path, GraphCursorStore(self.gx.queue, self.gx.graph_id),
                                   self._apply)
            await writer.__aenter__()
            self._writer, self.healed = writer, writer.healed
            if writer.healed:
                print(f"healed: {writer.healed} journaled op(s) the db had not applied (a crashed "
                      "writer's tail), each at its own window", file=sys.stderr)
        return self

    async def _apply(
        self,
        record: Dict[str, Any],  # A journaled record (the writer has opened its ts window)
    ) -> None:
        if self._live is not None:  # this invocation's op: the invocation's window steps the site links
            self._live["result"] = (await apply_write_op(self.gx, record, self.emit_root))[1]
            return
        with observe_writes() as writes:  # a healed record: its own window, stepped at its close
            await apply_write_op(self.gx, record, self.emit_root)
        await step_site_links(self.gx, writes, force=record.get("verb") == "new-note")

    async def write(
        self,
        verb: str,              # The journal verb
        args: Dict[str, Any],   # The PLANNED args: what replay re-passes verbatim
    ) -> Written:
        """Append the planned op, apply it through the replay's applier, advance the cursor. ALWAYS
        appended -- never deduped by content (ruling 17daa86b): an A -> B -> A sequence keeps its
        last op. A write that would change nothing is the PLAN's to catch, against the healed db."""
        if not self._begun:
            raise RuntimeError(f"write({verb!r}) before begin(): the plan must read the db the "
                               "family's lock and heal leave (design detail 65f4a6c9)")
        op = {"verb": verb, "args": args}
        if self._writer is None:
            return Written(result=(await apply_write_op(self.gx, op, self.emit_root))[1])
        self._live = {}
        try:
            await self._writer.write(op, dedup=False)
            result = self._live.get("result")
        finally:
            self._live = None
        self.written += 1
        return Written(result=result, journaled=True)


@asynccontextmanager
async def write_window(
    gx: GraphHandle,
    journal_path: Optional[str],      # The graph's writes journal (None = unjournaled: the applier alone)
    emit_root: Optional[str] = None,  # The notes graph's emit root
    report: Optional[Callable[[Dict[str, Any]], None]] = None,  # Called with the site-link step's result when it ran
) -> AsyncIterator[InvocationWrites]:  # The unit's writes: `begin()` before the plan, `write()` per planned op
    """THE UNIT OF WORK one write takes (ruling d2d05e78 (1)): the invocation's writes on the
    family's journal, and inside them the live write window (design amendment 9ee4e346) whose
    site-link step runs at its close -- before the lock is released. The CLI wraps every
    invocation in one; an app's write gesture runs as ONE coroutine inside one (the lock's
    reentrancy and the write observer are context-scoped, so separate submits would be separate
    units)."""
    async with InvocationWrites(gx, journal_path, emit_root) as writes:
        async with site_link_window(gx, report=report):
            yield writes


async def cursor_report(
    gx: GraphHandle,
    journal_path: str,  # The graph's writes journal
) -> Dict[str, Any]:  # {family, cursor, lag}: the db's applied cursor and the records past it
    """A READER's view of the write order (65f4a6c9 (2d)): where the db's cursor stands and how
    many journaled records lie past it -- never applied here; the family's next writer heals."""
    store = GraphCursorStore(gx.queue, gx.graph_id)
    family = family_name(journal_path)
    held = await store.read(family)
    return {"family": family, "cursor": held.to_dict() if held else None,
            "lag": await journal_lag(journal_path, store)}


async def adopt_cursor(
    gx: GraphHandle,
    journal_path: str,  # The graph's writes journal
) -> Dict[str, Any]:  # {family, cursor}: the family's end, now the db's cursor
    """The one-time adoption of a db written before the cursor existed (65f4a6c9 (2c)): its
    family's END becomes its cursor. Truthful for a db only the apply-then-append verbs and
    rebuilds have written -- each op was applied before it was appended, so the db holds every
    journaled op."""
    end = await stamp_cursor(journal_path, GraphCursorStore(gx.queue, gx.graph_id))
    return {"family": family_name(journal_path), "cursor": end.to_dict()}
