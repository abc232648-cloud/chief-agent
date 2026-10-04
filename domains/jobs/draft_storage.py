"""Job-owned draft persistence, with immutable files and atomic DB history."""
import json
from operations.draft_files import draft_files, validate_application_id
from operations.correlation import references


def persist_draft(store, root, application_id, job, draft, selected_cv=None):
    validate_application_id(application_id)
    cv = selected_cv or {}
    # Reject a missing job or existing application before creating any output.
    # Keep the writer reservation through files and all three history inserts.
    con = store._connect()
    try:
        con.execute('BEGIN IMMEDIATE')
        if not con.execute('SELECT 1 FROM jobs WHERE id=?', (job['id'],)).fetchone():
            raise ValueError('Application drafting requires a stored job.')
        if con.execute('SELECT 1 FROM applications WHERE id=?', (application_id,)).fetchone():
            raise ValueError('Application already exists; draft history cannot be overwritten.')
        with draft_files(root, application_id, draft) as paths:
            con.execute('INSERT INTO applications(id,job_id,status,notes,draft_json,cv_path,cover_letter_path) VALUES(?,?,?,?,?,?,?)',
                        (application_id, job['id'], 'DRAFT', 'AI-generated draft passed evidence validation.',
                         json.dumps(draft, ensure_ascii=False), paths['cv_path'], paths['cover_letter_path']))
            con.execute('''INSERT INTO application_snapshots(application_id,stage,cv_id,cv_name,cv_variant,cv_path,cv_snapshot_json,cover_letter_text,form_fields_json,source_url)
                           VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (application_id, 'DRAFT', str(cv.get('id', '')), str(cv.get('name', '')), str(cv.get('variant', '')),
                         str(cv.get('file_path', paths['cv_path'])), json.dumps(draft['cv'], ensure_ascii=False),
                         draft['cover_letter'], '[]', str(job.get('url', ''))))
            from database.store_extensions import redact
            data = redact({**references(), 'cv_id': cv.get('id'), 'cv_name': cv.get('name'), 'cv_variant': cv.get('variant')})
            con.execute('INSERT INTO application_events(application_id,event_type,status,details,data_json) VALUES(?,?,?,?,?)',
                        (application_id, 'DRAFT_CREATED', 'RECORDED', 'Application draft created and CV provenance recorded.', json.dumps(data, ensure_ascii=False)))
            con.commit()
        return paths
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
