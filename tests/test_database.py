from database.store import Store

def test_store_tracks_core_state(tmp_path):
    s=Store(tmp_path/'worker.db')
    s.add_job({'id':'j1','title':'SOC Analyst','company':'Acme','url':'https://example.com/j1','platform':'Example','remote':True,'fit_score':88,'scam_status':'NO_OBVIOUS_SCAM','confidence':0.9})
    aid=s.add_action('Review application','submit_application','Needs user approval',priority=90)
    cid=s.queue_command('Find remote SOC jobs')
    assert s.counts()['jobs']==1 and s.counts()['actions']==1 and s.counts()['commands']==1
    assert s.actions()[0]['id']==aid and s.commands()[0]['id']==cid
    s.resolve_action(aid,'APPROVED')
    assert s.counts()['actions']==0


def test_v17_job_ranking_fields_and_duplicate_lookup(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    job = {
        'id': 'a', 'title': 'SOC Analyst', 'company': 'Acme', 'url': 'https://acme.example/jobs/1',
        'dedupe_key': 'url:https://acme.example/jobs/1', 'canonical_url': 'https://acme.example/jobs/1',
        'rank_score': 88.5, 'status': 'ELIGIBLE'
    }
    store.add_job(job)
    found = store.find_job_by_dedupe_key(job['dedupe_key'])
    assert found['id'] == 'a'
    assert found['rank_score'] == 88.5
    assert store.jobs(include_duplicates=False)[0]['id'] == 'a'


def test_old_style_jobs_table_is_migrated(tmp_path):
    import sqlite3
    db = tmp_path / 'old.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
          platform TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', remote INTEGER NOT NULL DEFAULT 0,
          fit_score REAL, scam_status TEXT, confidence REAL, status TEXT NOT NULL DEFAULT 'NEW',
          raw_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    con.commit(); con.close()
    store = Store(db)
    columns = {r['name'] for r in store._connect().execute('PRAGMA table_info(jobs)').fetchall()}
    assert {'canonical_url','dedupe_key','rank_score','duplicate_of'} <= columns
