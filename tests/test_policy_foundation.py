from itertools import combinations
import pytest
from policy.contracts import ActionRisk as R,Outcome as O,Precedence as P,PolicyRule,PolicyPack,PolicyRequest
from policy.engine import PolicyEngine


def pack(identity,level,decision,hard=False,version='1.0.0'):
    return PolicyPack(identity,version,level,(PolicyRule('synthetic',decision,'Synthetic rule',hard),))


@pytest.mark.parametrize('higher,lower',list(combinations(P,2)))
def test_lower_priority_cannot_relax_higher_obligation(higher,lower):
    engine=PolicyEngine((pack('higher',higher,O.ASK),pack('lower',lower,O.ALLOW)))
    result=engine.evaluate(PolicyRequest('synthetic',R.READ))
    assert result.decision==O.ASK and result.conflict
    assert result.governing_packs==( ('higher','1.0.0'), )


@pytest.mark.parametrize('level',list(P))
def test_hard_prohibition_cannot_be_overridden(level):
    engine=PolicyEngine((pack('hard',P.HARD_SAFETY_SECURITY,O.BLOCK,True),pack('allow',level,O.ALLOW)))
    assert engine.evaluate(PolicyRequest('synthetic',R.HIGH_IMPACT)).decision==O.BLOCK


@pytest.mark.parametrize('risk',list(R))
def test_risk_class_alone_does_not_authorize_unknown(risk):
    assert PolicyEngine(()).evaluate(PolicyRequest('unknown',risk)).decision==O.ASK


def test_same_priority_uses_stricter_decision_and_pins_versions():
    engine=PolicyEngine((pack('first',P.CHIEF,O.ALLOW,version='1.2.3'),pack('second',P.CHIEF,O.BLOCK)))
    result=engine.evaluate(PolicyRequest('synthetic',R.RECORD))
    assert result.decision==O.BLOCK and ('first','1.2.3') in result.matched_packs
    with pytest.raises(ValueError):PolicyEngine((pack('same',P.CHIEF,O.ASK),pack('same',P.CHIEF,O.ASK,version='2.0.0')))


def test_invalid_version_or_hard_allow_rejected():
    with pytest.raises(ValueError):pack('bad',P.CHIEF,O.ALLOW,True)
    with pytest.raises(ValueError):pack('bad',P.CHIEF,O.ALLOW,version='latest')
