import json,os
from datetime import timedelta
import pytest
from private_secrets.service import SecretReferences,SecretUnavailable,resolve_configured
from operations.time_integrity import utc_now,utc_text


def reference(**changes):
    ref={'backend':'systemd','key':'fixture','domain':'jobs','consumers':['jobs.gateway'],'version':'1','revoked':False,'expires_at':None}
    ref.update(changes);return ref


@pytest.mark.skipif(os.name=='nt',reason='Linux systemd delivered-file contract; Windows exercises DPAPI separately')
def test_linux_secret_scope_rotation_revocation_and_private_delivery(tmp_path):
    file=tmp_path/'fixture';file.write_text('Synthetic credential one');file.chmod(0o600)
    refs={'one':reference()};service=SecretReferences(refs,credential_directory=tmp_path)
    assert bool(service.resolve('one',consumer='jobs.gateway',domain='jobs'))
    for consumer,domain in [('farm.worker','jobs'),('jobs.gateway','farming')]:
        with pytest.raises(SecretUnavailable):service.resolve('one',consumer=consumer,domain=domain)
    first=service.resolve('one',consumer='jobs.gateway',domain='jobs');file.write_text('Synthetic credential two')
    assert service.resolve('one',consumer='jobs.gateway',domain='jobs')!=first
    refs['one']['revoked']=True
    with pytest.raises(SecretUnavailable):service.resolve('one',consumer='jobs.gateway',domain='jobs')
    refs['one']=reference(expires_at=utc_text(utc_now()-timedelta(seconds=1)))
    with pytest.raises(SecretUnavailable):service.resolve('one',consumer='jobs.gateway',domain='jobs')
    refs['one']=reference();file.chmod(0o644)
    with pytest.raises(SecretUnavailable):service.resolve('one',consumer='jobs.gateway',domain='jobs')
    file.unlink();file.symlink_to(tmp_path/'missing')
    with pytest.raises(SecretUnavailable):service.resolve('one',consumer='jobs.gateway',domain='jobs')


def test_production_refuses_plaintext_and_missing_reference(monkeypatch):
    monkeypatch.setenv('CHIEF_INSTANCE_MODE','production')
    for env in ({'GROQ_API_KEY':'Synthetic credential'},{}):
        with pytest.raises(SecretUnavailable):resolve_configured(env,'GROQ_API_KEY',consumer='jobs.gateway',domain='jobs')


def test_errors_do_not_disclose_payloads_or_paths(tmp_path):
    service=SecretReferences({'one':reference(key='../synthetic-private-value')},credential_directory=tmp_path)
    with pytest.raises(SecretUnavailable) as result:service.resolve('one',consumer='jobs.gateway',domain='jobs')
    assert 'synthetic-private-value' not in str(result.value) and str(tmp_path) not in str(result.value)


def test_reference_metadata_does_not_expose_values_in_email_api():
    from notifications.email_config import public_email_config
    payload=public_email_config({'SMTP_HOST':'smtp.example.invalid','SMTP_USERNAME':'fixture@example.invalid','EMAIL_SENDER':'fixture@example.invalid','EMAIL_RECIPIENT':'other@example.invalid','SMTP_PASSWORD_REF':'synthetic-reference'})
    assert payload['password_configured'] is True
    assert 'synthetic-reference' not in json.dumps(payload)


@pytest.mark.skipif(os.name!='nt',reason='Native Windows CurrentUser DPAPI acceptance runs separately')
def test_windows_current_user_dpapi_round_trip_and_corruption(tmp_path):
    from private_secrets.windows import provision,unprotect
    root=tmp_path/'secrets';key='fixture.dpapi'
    provision(root,key,'Synthetic credential one')
    blob=(root/key).read_bytes();assert b'Synthetic credential one' not in blob
    assert bool(unprotect(blob))
    ref=reference(backend='windows-dpapi',key=key)
    service=SecretReferences({'one':ref},windows_directory=root)
    assert bool(service.resolve('one',consumer='jobs.gateway',domain='jobs'))
    (root/key).write_bytes(b'corrupt')
    with pytest.raises(SecretUnavailable):service.resolve('one',consumer='jobs.gateway',domain='jobs')
