"""Explicit local update restrictions, reloaded at every authority check."""
import json

from update_center.local_inputs import read_bounded
from compatibility.manifest import _unique_object
from policy.contracts import Outcome, PolicyPack, PolicyRule, Precedence
from policy.engine import PolicyEngine


class OperatorUpdatePolicy:
    def __init__(self, filename):
        self.filename = filename

    def evaluate(self, request):
        value = json.loads(read_bounded(self.filename, 4096), object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or set(value) != {'version', 'validation', 'selection', 'recovery'} or
            type(value['version']) is not int or value['version'] != 1 or
            any(value[k] not in ('ASK', 'BLOCK') for k in ('validation', 'selection', 'recovery'))):
            raise ValueError('Explicit bounded operator update policy required.')
        actions = {'chief.update.validate':'validation', 'chief.update.review':'selection',
                   'chief.update.select_source':'selection', 'chief.update.recover_previous':'recovery'}
        rules = tuple(PolicyRule(action, Outcome(value[field]), 'Local update restriction; explicit human review remains required.',
                                 value[field] == 'BLOCK') for action, field in actions.items())
        return PolicyEngine((PolicyPack('installation-update-controls', '1.0.0', Precedence.INSTALLATION_OWNER, rules),)).evaluate(request)
