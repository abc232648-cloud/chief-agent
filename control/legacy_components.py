"""Existing domain rows are authoritative; never dual-write generalized modes."""
from capabilities.contracts import Mode
from .component_contracts import DesiredState


def domain_state(con, node):
    row = con.execute('SELECT enabled,autostart,running FROM agent_controls WHERE domain=?', (node.id,)).fetchone()
    if row is None:
        raise ValueError('Legacy controls have not been initialized for this domain.')
    return DesiredState(node, Mode.ENABLED if row['enabled'] else Mode.DISABLED, 0,
                        'agent_controls', (), tuple((key, row[key]) for key in row.keys()))
