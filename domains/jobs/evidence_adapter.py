"""Job confirmation remains authoritative; shared metadata can only restrict."""
from contextlib import contextmanager
import hashlib
from evidence.contracts import Evidence
from evidence.service import EvidenceService, canonical
from security.permissions import current_context, worker_context
from operations.time_integrity import utc_now, utc_text


@contextmanager
def job_scope():
    context = current_context()
    if context is not None:
        if context.domain != 'jobs':
            raise PermissionError('Job evidence is private to Job.')
        context.require('jobs.execute')
        yield
    else:
        # Trusted local Job composition, consistent with existing unscoped Job APIs.
        from . import definition
        with worker_context(definition()[1]):
            yield


def fact_fingerprint(fact):
    # Lifecycle timestamps/status are intentionally not part of content identity.
    return hashlib.sha256(canonical({k: fact.get(k) for k in ('id','text','source_type','source_id','source_detail')}).encode()).hexdigest()


class JobEvidence:
    def __init__(self, store):
        self.store = store
        self.shared = EvidenceService(store, 'jobs', 'jobs.execute')

    def link(self, fact_id, evidence_id):
        with job_scope():
            self.shared.get(evidence_id)
            with self.store._connect() as con:
                fact = con.execute('SELECT * FROM candidate_facts WHERE id=?', (fact_id,)).fetchone()
                if fact is None:
                    raise LookupError('Unknown Job fact.')
                fingerprint = fact_fingerprint(dict(fact))
                old = con.execute('SELECT fingerprint FROM job_fact_evidence_links WHERE fact_id=? AND evidence_id=?', (fact_id, evidence_id)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise ValueError('Linked fact content changed; history cannot be rewritten.')
                    return
                con.execute('INSERT INTO job_fact_evidence_links(domain,fact_id,evidence_id,fingerprint,received_at) VALUES(?,?,?,?,?)',
                            ('jobs', fact_id, evidence_id, fingerprint, utc_text(utc_now())))

    def adapt(self, fact_id, *, truth):
        """Explicit optional reference, never infer generic verification or source time."""
        with job_scope():
            with self.store._connect() as con:
                if not con.execute('SELECT 1 FROM candidate_facts WHERE id=?', (fact_id,)).fetchone():
                    raise LookupError('Unknown Job fact.')
            evidence_id = self.shared.create(Evidence(truth, 'jobs', 'candidate_fact', str(fact_id), 'candidate_fact:' + str(fact_id)))
            self.link(fact_id, evidence_id)
            return evidence_id

    def references(self, fact_id):
        with job_scope():
            if not self.shared.ready():
                return []
            with self.store._connect() as con:
                return [r[0] for r in con.execute('SELECT evidence_id FROM job_fact_evidence_links WHERE domain=? AND fact_id=?', ('jobs', fact_id))]

    def confirmed(self):
        with job_scope():
            facts = self.store.candidate_facts(status='USER_CONFIRMED')
            if not self.shared.ready():
                return facts
            result = []
            for fact in facts:
                with self.store._connect() as con:
                    links = con.execute('SELECT evidence_id,fingerprint FROM job_fact_evidence_links WHERE domain=? AND fact_id=?', ('jobs', fact['id'])).fetchall()
                # Live assessment: a cached quality record never hides later conflict.
                if all(link[1] == fact_fingerprint(fact) and self.shared.quality(link[0]).authority_restriction == 'UNCHANGED' for link in links):
                    result.append(fact)
            return result
