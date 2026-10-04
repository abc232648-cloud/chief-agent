"""Shared ownership/recovery boundary for every supported scheduling entry."""
import json
import uuid


class SchedulerReviewRequired(RuntimeError):pass


def run_guarded_cycle(store, operation):
    # This lock also covers direct callers, not only the service entry point.
    from worker.ownership import ComponentOwnership
    from operations.time_integrity import utc_now, utc_text
    with ComponentOwnership(store.path,'scheduler-cycle'):
        with store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute("SELECT value FROM control_state WHERE key='scheduler_cycle_v1'").fetchone()
            if row:
                prior=json.loads(row[0])
                if not isinstance(prior,dict) or prior.get('version')!=1 or prior.get('state')!='COMPLETED':
                    raise SchedulerReviewRequired('Interrupted scheduler cycle requires review; no automatic replay.')
            record={'version':1,'id':uuid.uuid4().hex,'state':'IN_PROGRESS','started_at':utc_text(utc_now())}
            con.execute("INSERT OR REPLACE INTO control_state VALUES('scheduler_cycle_v1',?)",(json.dumps(record),))
        # Any interruption leaves IN_PROGRESS, even after an external side effect.
        result=operation()
        record.update(state='COMPLETED',completed_at=utc_text(utc_now()))
        with store._connect() as con:
            con.execute("UPDATE control_state SET value=? WHERE key='scheduler_cycle_v1'",(json.dumps(record),))
        return result
