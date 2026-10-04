import json
from dataclasses import asdict

import pytest

from compatibility.manifest import load_manifest, manifest_digest

SHA_A='a'*64
SHA_B='b'*64


def payload(**overrides):
    value={
        'format_version':1,
        'release_id':'chief-1.0.1',
        'chief_version':'1.0.1',
        'source_tree_sha256':SHA_A,
        'schema_sha256':SHA_B,
        'capability_versions':[{'id':'chief.domain_dispatch','version':'1.0.0'}],
        'changed_nodes':['capability:chief.domain_dispatch'],
        'required_tests':['tests/test_domains.py'],
        'runtime_requirements':[],
        'model_requirements':['jobs.qwen'],
        'migrations':[],
        'rollback_compatible_with':['chief-1.0.0'],
        'benchmark_requirements':[],
        'private_state_excluded':True,
        'created_at':'2026-09-24T10:00:00+00:00',
    }
    value.update(overrides);return value


def test_round_trip_and_digest_are_deterministic():
    a=load_manifest(json.dumps(payload()))
    b=load_manifest(json.dumps(payload(),indent=2))
    assert a==b
    assert manifest_digest(a)==manifest_digest(b)
    assert asdict(a)['private_state_excluded'] is True


@pytest.mark.parametrize('field,bad',[
    ('source_tree_sha256','A'*64),('schema_sha256','nope'),('chief_version','v1'),
    ('created_at','2026-09-24T10:00:00'),('release_id','bad id!'),
])
def test_rejects_invalid_identity_fields(field,bad):
    with pytest.raises(ValueError):load_manifest(payload(**{field:bad}))


def test_rejects_duplicate_json_keys():
    raw=json.dumps(payload())[:-1]+',"release_id":"other"}'
    with pytest.raises(ValueError,match='Duplicate manifest field'):load_manifest(raw)


@pytest.mark.parametrize('path',['../tests/test_x.py','/tmp/test_x.py','tests\\test_x.py','test_x.py','tests/foo.py'])
def test_rejects_unsafe_test_paths(path):
    with pytest.raises(ValueError):load_manifest(payload(required_tests=[path]))


def test_runtime_and_migration_contracts_are_strict():
    item=payload(
        runtime_requirements=[{'runtime':'openclaw','minimum':'1.0.0','before':'2.0.0','required':True}],
        migrations=[{'migration_id':'g-1','from_schema':'c'*64,'to_schema':'d'*64,'requires_backup':True,'reversible':False}],
    )
    result=load_manifest(item)
    assert result.runtime_requirements[0].required is True
    assert result.migrations[0].requires_backup is True
    bad=dict(item);bad['runtime_requirements']=[{'runtime':'openclaw','minimum':'2.0.0','before':'1.0.0','required':True}]
    with pytest.raises(ValueError):load_manifest(bad)


def test_changed_nodes_are_valid_capability_graph_keys():
    with pytest.raises(ValueError):load_manifest(payload(changed_nodes=['nonsense']))
    with pytest.raises(ValueError):load_manifest(payload(changed_nodes=['capability:Bad ID']))
