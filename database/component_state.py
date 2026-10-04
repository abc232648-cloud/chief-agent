"""Additive mode persistence. Reads never create a schema or legacy domain rows."""
import json
from capabilities.contracts import Mode, Node
from operations.time_integrity import utc_now, utc_text
from .migrations import schema_ready


class ComponentState:
    def __init__(self, store):
        self.store = store

    def rows(self, con):
        if not schema_ready(con):
            return []
        return [dict(r) for r in con.execute('SELECT * FROM component_modes ORDER BY kind,id')]

    def mode(self, con, node):
        rows = self.rows(con)
        row = next((r for r in rows if (r['kind'], r['id']) == (node.kind, node.id)), None)
        return Mode(row['mode']) if row else Mode.ENABLED

    def put(self, con, node, mode, *, actor, reason):
        if not schema_ready(con):
            raise ValueError('Explicit isolated migration is required before component changes.')
        if mode == Mode.LEGACY_CONTROLLED:
            raise ValueError('Legacy metadata is not a persistent component mode.')
        con.execute('INSERT INTO component_modes VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET mode=excluded.mode,revision=component_modes.revision+1,changed_at=excluded.changed_at,actor=excluded.actor,reason=excluded.reason',
                    (node.kind, node.id, mode.value, 1, utc_text(utc_now()), actor, reason))
        # Same transaction as state; failed audit means failed transition.
        con.execute('INSERT INTO audit_log(event_time,category,actor,action,data_json) VALUES(?,?,?,?,?)',
                    (utc_text(utc_now()), 'component_control', actor, 'Changed desired component mode',
                     json.dumps({'node': node.key, 'mode': mode.value, 'reason': reason})))
