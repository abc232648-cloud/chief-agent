from application.ui_api import owner_activity
from database.store import Store
from database.store_extensions import add_audit, init_extensions


def test_owner_activity_projection_omits_private_audit_payload(tmp_path):
    store=Store(tmp_path/'owner-activity.db')
    init_extensions(store)
    add_audit(
        store,
        'jobs',
        'Synthetic Owner projection check',
        status='FAILED',
        details='private-detail-must-not-leave-chief',
        data={'private_token':'private-value-must-not-leave-chief'},
    )

    body=owner_activity(store)
    assert body['availability']=='LIVE'
    assert body['items']
    item=body['items'][0]
    assert set(item)=={'id','event_time','category','actor','action','status'}
    serialized=str(body)
    assert 'private-detail-must-not-leave-chief' not in serialized
    assert 'private-value-must-not-leave-chief' not in serialized
