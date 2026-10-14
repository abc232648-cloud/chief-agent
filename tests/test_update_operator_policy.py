import json

import pytest

from policy.contracts import ActionRisk, Outcome, PolicyRequest
from update_center.operator_policy import OperatorUpdatePolicy


def test_operator_policy_reloads_and_never_silently_allows(tmp_path):
    path = tmp_path / 'policy.json'
    value = dict(version=1, validation='ASK', selection='ASK', recovery='ASK')
    path.write_text(json.dumps(value), encoding='utf-8')
    policy = OperatorUpdatePolicy(str(path))
    request = PolicyRequest('chief.update.select_source', ActionRisk.HIGH_IMPACT)
    assert policy.evaluate(request).decision == Outcome.ASK
    value['selection'] = 'BLOCK'; path.write_text(json.dumps(value), encoding='utf-8')
    assert policy.evaluate(request).decision == Outcome.BLOCK
    assert policy.evaluate(PolicyRequest('chief.update.validate', ActionRisk.HIGH_IMPACT)).decision == Outcome.ASK


@pytest.mark.parametrize('change', [{'selection':'ALLOW'}, {'version':True}, {'extra':'ignored'}, {'validation':False}])
def test_invalid_operator_policy_fails_closed(tmp_path, change):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(dict(version=1, validation='ASK', selection='ASK', recovery='ASK') | change), encoding='utf-8')
    with pytest.raises(ValueError):
        OperatorUpdatePolicy(str(path)).evaluate(PolicyRequest('chief.update.validate', ActionRisk.HIGH_IMPACT))
