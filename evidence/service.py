from dataclasses import asdict
import hashlib
import json
import uuid
from evidence.contracts import Evidence, Verification, token
from security.permissions import current_context
from operations.time_integrity import utc_now, utc_text
from database.evidence_migrations import schema_ready
from data_quality.engine import assess


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class EvidenceService:
    def __init__(self, store, domain, capability):
        self.store, self.domain, self.capability = store, token(domain), token(capability)

    def guard(self):
        context = current_context()
        if context is None or context.domain != self.domain:
            raise PermissionError('An active matching domain context is required.')
        context.require(self.capability)

    def ready(self):
        self.guard()
        with self.store._connect() as con:
            return schema_ready(con)

    def _get(self, con, evidence_id):
        row = con.execute('SELECT record FROM shared_evidence WHERE domain=? AND id=?', (self.domain, evidence_id)).fetchone()
        if row is None:
            raise LookupError('Evidence reference is unavailable in this domain.')
        record = json.loads(row[0])
        record.update(domain=self.domain, id=evidence_id, verification_event_id=None)
        event = con.execute('SELECT state,basis,id FROM evidence_verifications WHERE domain=? AND evidence_id=? ORDER BY id DESC LIMIT 1', (self.domain, evidence_id)).fetchone()
        if event:
            record.update(verification=event[0], verification_basis=event[1], verification_event_id=event[2])
        return record

    def get(self, evidence_id):
        self.guard()
        with self.store._connect() as con:
            return self._get(con, evidence_id)

    def create(self, evidence):
        self.guard()
        if not isinstance(evidence, Evidence):
            raise TypeError('Validated Evidence is required.')
        evidence_id = uuid.uuid4().hex
        with self.store._connect() as con:
            if not schema_ready(con):
                raise RuntimeError('Explicit isolated D migration is required.')
            con.execute('INSERT INTO shared_evidence VALUES(?,?,?)', (self.domain, evidence_id, canonical(asdict(evidence))))
        return evidence_id

    def verify(self, evidence_id, state, basis):
        self.guard()
        state, basis = Verification(state), token(basis)
        with self.store._connect() as con:
            self._get(con, evidence_id)
            cur = con.execute('INSERT INTO evidence_verifications(domain,evidence_id,state,basis,actor,received_at) VALUES(?,?,?,?,?,?)',
                              (self.domain, evidence_id, state.value, basis, token(current_context().agent_id), utc_text(utc_now())))
            return cur.lastrowid

    def contradict(self, left, right, reason, *, conflict_id=None, resolved=False):
        self.guard()
        if left == right:
            raise ValueError('A contradiction needs two distinct records.')
        conflict_id = token(conflict_id) if conflict_id else uuid.uuid4().hex
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._get(con, left)
            self._get(con, right)
            previous = con.execute('SELECT left_id,right_id FROM evidence_contradictions WHERE domain=? AND conflict_id=? ORDER BY id DESC LIMIT 1', (self.domain, conflict_id)).fetchone()
            if (resolved and not previous) or (previous and tuple(previous) != (left, right)):
                raise ValueError('Conflict resolution must reference its original pair.')
            con.execute('INSERT INTO evidence_contradictions(domain,conflict_id,left_id,right_id,state,reason,received_at) VALUES(?,?,?,?,?,?,?)',
                        (self.domain, conflict_id, left, right, 'RESOLVED' if resolved else 'OPEN', token(reason), utc_text(utc_now())))
        return conflict_id

    def quality(self, evidence_id, *, now=None, persist=False):
        self.guard()
        with self.store._connect() as con:
            # One read snapshot covers metadata, latest verification and contradictions.
            con.execute('BEGIN IMMEDIATE' if persist else 'BEGIN')
            record = self._get(con, evidence_id)
            conflicts = con.execute('SELECT c.id,c.conflict_id,c.state FROM evidence_contradictions c WHERE c.domain=? AND (c.left_id=? OR c.right_id=?) AND c.id=(SELECT MAX(x.id) FROM evidence_contradictions x WHERE x.domain=c.domain AND x.conflict_id=c.conflict_id) ORDER BY c.id', (self.domain, evidence_id, evidence_id)).fetchall()
            result = assess(record, now=now, contradictions=any(r[2] == 'OPEN' for r in conflicts))
            if persist:
                fingerprint = hashlib.sha256(canonical({'record': record, 'conflicts': [tuple(r) for r in conflicts]}).encode()).hexdigest()
                con.execute('INSERT INTO evidence_quality_assessments(domain,evidence_id,fingerprint,result) VALUES(?,?,?,?)',
                            (self.domain, evidence_id, fingerprint, canonical(asdict(result))))
            return result

    def history(self, evidence_id):
        self.guard()
        with self.store._connect() as con:
            original = con.execute('SELECT record FROM shared_evidence WHERE domain=? AND id=?', (self.domain, evidence_id)).fetchone()
            self._get(con, evidence_id)
            return {'original': json.loads(original[0]),
                    'verification': [dict(r) for r in con.execute('SELECT * FROM evidence_verifications WHERE domain=? AND evidence_id=? ORDER BY id', (self.domain, evidence_id))],
                    'contradictions': [dict(r) for r in con.execute('SELECT * FROM evidence_contradictions WHERE domain=? AND (left_id=? OR right_id=?) ORDER BY id', (self.domain, evidence_id, evidence_id))],
                    'quality': [dict(r) for r in con.execute('SELECT * FROM evidence_quality_assessments WHERE domain=? AND evidence_id=? ORDER BY id', (self.domain, evidence_id))]}
