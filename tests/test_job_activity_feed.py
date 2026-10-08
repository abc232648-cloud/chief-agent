from database.store import Store
from database.store_extensions import init_extensions
from application.job_activity_feed import recent_job_activity


def make_store(tmp_path):
    state = Store(tmp_path / 'worker.db')
    init_extensions(state)
    from control.notifications import initialize
    initialize(state)
    return state


def ensure_domain_requests(state):
    with state._connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS domain_requests(command_id INTEGER PRIMARY KEY, domain TEXT NOT NULL)')


def test_feed_is_job_scoped_and_omits_raw_execution_payloads(tmp_path):
    state = make_store(tmp_path)
    ensure_domain_requests(state)
    state.add_job({
        'id': 'job-1',
        'title': 'SOC Analyst',
        'company': 'Example Security',
        'url': 'https://secret.example/application',
        'raw_json': {'private': 'do-not-expose'},
    })
    state.add_application('app-1', 'job-1')
    with state._connect() as con:
        con.execute(
            "INSERT INTO application_events(application_id,event_type,status,details,data_json) VALUES(?,?,?,?,?)",
            ('app-1', 'APPLICATION_REVIEW', 'WAITING_USER', 'private event detail', '{"token":"hidden"}'),
        )

    job_command = state.queue_command('private command instruction')
    state.update_command(job_command, 'COMPLETED', 'private command result')
    farm_command = state.queue_command('farm-only private instruction')
    with state._connect() as con:
        con.execute('INSERT INTO domain_requests(command_id,domain) VALUES(?,?)', (farm_command, 'farming'))

    state.add_notification('Approval required', 'Review the recorded application.', 'ACTION_REQUIRED', domain='jobs', related_page='actions')
    state.add_notification('System secret', 'cross-domain private body', 'URGENT', domain='system')

    feed = recent_job_activity(state)
    serialized = str(feed)

    assert {'notification', 'application', 'command'} <= {item['source'] for item in feed['events']}
    assert any(item['source'] == 'command' and item['summary'] == f'Command #{job_command} status: COMPLETED.' for item in feed['events'])
    assert all(not (item['source'] == 'command' and item['id'] == f'command:{farm_command}') for item in feed['events'])
    assert feed['counts']['action_required'] == 1
    assert feed['job_counts'] == {'jobs': 1, 'applications': 1, 'actions': 0}

    for forbidden in (
        'private command instruction',
        'private command result',
        'farm-only private instruction',
        'https://secret.example/application',
        'do-not-expose',
        'private event detail',
        'hidden',
        'cross-domain private body',
    ):
        assert forbidden not in serialized


def test_feed_tolerates_legacy_dev_db_without_domain_requests(tmp_path):
    state = make_store(tmp_path)
    command_id = state.queue_command('private legacy command')
    state.update_command(command_id, 'COMPLETED', 'private legacy result')
    with state._connect() as con:
        con.execute('DROP TABLE IF EXISTS domain_requests')

    feed = recent_job_activity(state)
    command = next(item for item in feed['events'] if item['id'] == f'command:{command_id}')
    assert command['summary'] == f'Command #{command_id} status: COMPLETED.'
    assert feed['job_counts']['actions'] == 0
    assert 'private legacy command' not in str(feed)
    assert 'private legacy result' not in str(feed)


def test_feed_bounds_limit_and_maps_attention_severity(tmp_path):
    state = make_store(tmp_path)
    state.add_job({'id':'job-2','title':'Cloud Security','company':'Example','url':'https://example.invalid'})
    state.add_application('app-2','job-2')
    with state._connect() as con:
        con.execute(
            "INSERT INTO application_events(application_id,event_type,status,details,data_json) VALUES(?,?,?,?,?)",
            ('app-2','SUBMISSION','FAILED','','{}'),
        )
    state.add_notification('STOP', 'Blocked safely.', 'URGENT', domain='jobs')

    feed = recent_job_activity(state, limit=999)
    assert feed['limit'] == 50
    assert feed['job_counts']['jobs'] == 1
    assert feed['job_counts']['applications'] == 1
    assert any(item['source'] == 'application' and item['severity'] == 'URGENT' for item in feed['events'])
    assert any(item['source'] == 'notification' and item['severity'] == 'URGENT' for item in feed['events'])
    assert 'raw command/application payloads are not exposed' in feed['privacy']
