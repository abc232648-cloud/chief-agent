"""Explicit Farm unit conversion; stock always stores eggs or kilograms."""
from decimal import Decimal
import re
from . import costing_policy, setup
from .journal import identifier


def overview(store,principal):
    with store._connect() as con:
        setup.authorize(store,con,principal,'read')
        history=costing_policy.rows(con)
        if not history:return {'revision':None,'eggs_per_crate':None,'kg_per_bag':None}
        p=history[-1]['payload']
        # Workers receive conversion sizes only, never prices or financial reasons.
        return {'revision':p['event_id'],'eggs_per_crate':p['eggs_per_crate'],'kg_per_bag':p['kg_per_bag']}


def check(con,payload):
    conversion=payload.get('conversion')
    if conversion is None:return
    if not isinstance(conversion,dict) or set(conversion)!={'unit','quantity','policy_revision'}:
        raise ValueError('Supply the entered unit, quantity and saved conversion version.')
    identifier(conversion['policy_revision'])
    value=conversion['quantity']
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,3})?',value):
        raise ValueError('Use a bounded exact quantity with at most three decimal places.')
    unit=conversion['unit']
    if unit not in {'crates','bags'} or not payload['kind'].startswith('eggs_' if unit=='crates' else 'feed_'):
        raise ValueError('Crates apply to eggs; bags apply to feed.')
    settings=next((r['payload'] for r in costing_policy.rows(con) if r['payload']['event_id']==conversion['policy_revision']),None)
    factor=settings.get('eggs_per_crate' if unit=='crates' else 'kg_per_bag') if settings else None
    if factor is None:raise ValueError('This conversion size is unknown. Enter individual eggs or kilograms.')
    result=Decimal(value)*Decimal(str(factor))
    if result!=Decimal(payload['quantity']):raise ValueError('Converted quantity does not match the saved conversion size.')
