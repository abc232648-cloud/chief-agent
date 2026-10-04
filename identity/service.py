from dataclasses import asdict
from datetime import timedelta
import hashlib,hmac,json,secrets,uuid
from operations.time_integrity import utc_now,utc_text,aware_utc
from database.identity_migrations import schema_ready
from evidence.contracts import token
from evidence.service import canonical
from .contracts import Principal,AuthenticationRequired,ReauthenticationRequired,SetupRequired,roles,SENSITIVE
from .passwords import password_hash,check_password,needs_upgrade,DUMMY


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


class IdentityService:
    def __init__(self,store,*,now=utc_now,extra_roles=None,target_guard=None):
        self.store,self.now,self.roles=store,now,roles(extra_roles)
        self.target_guard=target_guard

    def ready(self):
        with self.store._connect() as con:return schema_ready(con)

    def require_ready(self):
        if not self.ready():raise SetupRequired('Verified isolated F migration and local Owner bootstrap are required.')

    def _event(self,con,principal,operation,domain,resource,outcome,authority=None):
        con.execute('INSERT INTO human_security_events(human_id,session_id,operation,domain,resource,outcome,authority,received_at) VALUES(?,?,?,?,?,?,?,?)',
                    (principal.id if principal else None,principal.session_id if principal else None,operation,domain,resource,outcome,authority,utc_text(self.now())))

    def event(self,principal,operation,domain=None,resource=None,outcome='ALLOWED',authority=None):
        with self.store._connect() as con:self._event(con,principal,operation,domain,resource,outcome,authority)

    def _insert_user(self,con,username,password,role,domains):
        username=token(username.lower());domains=tuple(domains)
        if role not in self.roles or not domains or len(set(domains))!=len(domains):raise ValueError('Known role and explicit unique scopes required.')
        if '*' in domains and role not in {'Owner','Administrator'}:raise ValueError('Only installation administrators may have global scope.')
        for domain in domains:
            if domain!='*':token(domain)
        identity=uuid.uuid4().hex
        con.execute('INSERT INTO human_identities VALUES(?,?,?,?,?,1,?)',(identity,username,password_hash(password),role,canonical(domains),utc_text(self.now())))
        return identity

    def bootstrap(self,username,password):
        self.require_ready()
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM human_identities').fetchone():raise PermissionError('Owner bootstrap is already complete.')
            identity=self._insert_user(con,username,password,'Owner',('*',))
            self._event(con,None,'OWNER_BOOTSTRAP',None,identity,'RECORDED')
            return identity

    def user_management(self,principal,*,sensitive=False):
        current=self.refresh(principal)
        if current.role=='Owner' and '*' in current.domains:
            allowed=tuple(self.roles)
        elif current.role=='Administrator':allowed=('Manager','Worker')
        elif current.role=='Manager':allowed=('Worker',)
        else:raise PermissionError('This role cannot manage users.')
        permission='identity.workers.manage' if current.role=='Manager' else 'identity.manage'
        for domain in current.domains:
            self.authorize(current,permission,None if domain=='*' else domain,sensitive=sensitive)
        return {'roles':allowed,'domains':current.domains,'permission':permission}

    def _authorize_user_target(self,principal,role,domains,*,sensitive):
        spec=self.user_management(principal,sensitive=sensitive)
        if role not in spec['roles']:raise PermissionError('This role cannot manage the requested user role.')
        if not domains or ('*' not in spec['domains'] and not set(domains)<=set(spec['domains'])):
            raise PermissionError('The entire user account must be within your domain scope.')
        return spec

    def create_user(self,principal,username,password,role,domains):
        domains=tuple(domains)
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._authorize_user_target(principal,role,domains,sensitive=True)
            if self.target_guard:self.target_guard(con,self.refresh(principal),domains)
            normalized=token(username.lower())
            if con.execute('SELECT 1 FROM human_identities WHERE username=?',(normalized,)).fetchone():
                raise ValueError('That username is unavailable.')
            identity=self._insert_user(con,normalized,password,role,domains)
            self._event(con,principal,'IDENTITY_CREATED',None,identity,'RECORDED')
            return identity

    def list_users(self,principal):
        spec=self.user_management(principal)
        with self.store._connect() as con:
            rows=con.execute('SELECT id,username,role,domains,enabled,created_at FROM human_identities ORDER BY username').fetchall()
            result=[]
            for row in rows:
                domains=json.loads(row['domains'])
                if row['role'] not in spec['roles'] or ('*' not in spec['domains'] and not set(domains)<=set(spec['domains'])):continue
                result.append({**dict(row),'domains':domains,'enabled':bool(row['enabled']),'is_current_user':row['id']==principal.id})
            return result

    def _verify_login(self,con,row,password,now):
        # Unknown names share one bounded bucket instead of growing a row per
        # attacker-supplied username. Known accounts keep their own lockout.
        key=digest(row['username']) if row else digest('chief:unknown-identity')
        limit=con.execute('SELECT * FROM human_login_limits WHERE username_hash=?',(key,)).fetchone()
        if limit and limit['locked_until'] and aware_utc(limit['locked_until'])>now:
            raise AuthenticationRequired('Sign-in failed or temporarily limited.')
        verified=check_password(password,row['password_hash'] if row else DUMMY)
        valid=bool(row is not None and row['enabled'] and verified)
        if not valid:
            failures=(limit['failures'] if limit and not limit['locked_until'] else 0)+1
            locked=utc_text(now+timedelta(minutes=5)) if failures>=5 else None
            con.execute('INSERT INTO human_login_limits VALUES(?,?,?) ON CONFLICT(username_hash) DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until',(key,failures,locked))
            self._event(con,None,'LOGIN',None,None,'DENIED')
        else:
            con.execute('DELETE FROM human_login_limits WHERE username_hash=?',(key,))
            if needs_upgrade(row['password_hash']):
                con.execute('UPDATE human_identities SET password_hash=? WHERE id=?',(password_hash(password),row['id']))
                self._event(con,None,'PASSWORD_HASH_UPGRADED',None,row['id'],'RECORDED')
        return valid

    def login(self,username,password):
        self.require_ready();now=self.now()
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM human_identities WHERE username=?',(username.lower(),)).fetchone() if isinstance(username,str) and len(username)<=256 else None
            valid=self._verify_login(con,row,password,now)
            if valid:
                raw=secrets.token_urlsafe(32);session_id=uuid.uuid4().hex;timestamp=utc_text(now)
                con.execute('INSERT INTO human_sessions VALUES(?,?,?,?,?,?,?,0)',(session_id,digest(raw),row['id'],timestamp,timestamp,utc_text(now+timedelta(hours=8)),timestamp))
                principal=Principal(row['id'],session_id,row['role'],tuple(json.loads(row['domains'])),timestamp)
                self._event(con,principal,'LOGIN',None,None,'ALLOWED')
        if not valid:raise AuthenticationRequired('Sign-in failed or temporarily limited.')
        return raw,principal

    @staticmethod
    def csrf(raw):return hmac.new(raw.encode(),b'chief-local-csrf-v1',hashlib.sha256).hexdigest()

    def _principal(self,con,session_id):
        row=con.execute('SELECT s.*,u.role,u.domains,u.enabled FROM human_sessions s JOIN human_identities u ON u.id=s.human_id WHERE s.id=?',(session_id,)).fetchone()
        now=self.now()
        if not row or row['revoked'] or not row['enabled'] or row['role'] not in self.roles or aware_utc(row['expires_at'])<=now or aware_utc(row['last_seen'])+timedelta(minutes=10)<=now or any(aware_utc(row[k])>now+timedelta(seconds=5) for k in ('created_at','last_seen','reauth_at')):
            raise AuthenticationRequired('Sign in is required.')
        return Principal(row['human_id'],row['id'],row['role'],tuple(json.loads(row['domains'])),row['reauth_at'])

    def authenticate(self,raw):
        self.require_ready()
        if not isinstance(raw,str) or not 32<=len(raw)<=128:raise AuthenticationRequired('Sign in is required.')
        with self.store._connect() as con:
            row=con.execute('SELECT id FROM human_sessions WHERE token_hash=?',(digest(raw),)).fetchone()
            if not row:raise AuthenticationRequired('Sign in is required.')
            principal=self._principal(con,row[0])
        return principal

    def activity(self,raw):
        """Explicit interaction receipt, never called by read authentication."""
        if not isinstance(raw,str) or not 32<=len(raw)<=128:raise AuthenticationRequired('Sign in is required.')
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT id FROM human_sessions WHERE token_hash=?',(digest(raw),)).fetchone()
            if not row:raise AuthenticationRequired('Sign in is required.')
            principal=self._principal(con,row[0])
            con.execute('UPDATE human_sessions SET last_seen=? WHERE id=?',(utc_text(self.now()),principal.session_id))
        return principal

    def refresh(self,principal):
        with self.store._connect() as con:current=self._principal(con,principal.session_id)
        if current.id!=principal.id:raise AuthenticationRequired('Identity mismatch.')
        return current

    def reauthenticate(self,principal,password,*,raw):
        # Stable session ID preserves audit/approval linkage. Only a caller with
        # the current secret may rotate it; serialize races with logout/revocation.
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=self._principal(con,principal.session_id)
            token_row=con.execute('SELECT token_hash FROM human_sessions WHERE id=?',(current.session_id,)).fetchone()
            if current.id!=principal.id or not isinstance(raw,str) or not hmac.compare_digest(token_row[0],digest(raw)):
                raise AuthenticationRequired('Session changed; sign in again.')
            row=con.execute('SELECT * FROM human_identities WHERE id=?',(current.id,)).fetchone()
            valid=self._verify_login(con,row,password,self.now())
            if valid:
                replacement=secrets.token_urlsafe(32);stamp=utc_text(self.now())
                con.execute('UPDATE human_sessions SET token_hash=?,reauth_at=?,last_seen=? WHERE id=?',(digest(replacement),stamp,stamp,current.session_id))
                current=self._principal(con,current.session_id)
                self._event(con,current,'REAUTHENTICATED',None,None,'ALLOWED')
        if not valid:raise AuthenticationRequired('Sign-in failed or temporarily limited.')
        return replacement,current

    def revoke(self,principal):
        with self.store._connect() as con:
            self._principal(con,principal.session_id)
            con.execute('UPDATE human_sessions SET revoked=1 WHERE id=?',(principal.session_id,))
            self._event(con,principal,'SESSION_REVOKED',None,None,'RECORDED')

    def disable_user(self,principal,identity):
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.user_management(principal,sensitive=True)
            target=con.execute('SELECT role,domains FROM human_identities WHERE id=?',(identity,)).fetchone()
            if not target:raise PermissionError('User unavailable in your management scope.')
            self._authorize_user_target(principal,target['role'],json.loads(target['domains']),sensitive=True)
            if self.target_guard:self.target_guard(con,self.refresh(principal),json.loads(target['domains']))
            if target['role']=='Owner' and con.execute("SELECT COUNT(*) FROM human_identities WHERE role='Owner' AND enabled=1").fetchone()[0]<=1:raise PermissionError('Cannot disable the last Owner.')
            if target['role']=='Owner' and '*' in json.loads(target['domains']):
                installation_owners=sum('*' in json.loads(row['domains']) for row in con.execute("SELECT domains FROM human_identities WHERE role='Owner' AND enabled=1"))
                if installation_owners<=1:raise PermissionError('Cannot disable the last installation-wide Owner.')
            con.execute('UPDATE human_identities SET enabled=0 WHERE id=?',(identity,));con.execute('UPDATE human_sessions SET revoked=1 WHERE human_id=?',(identity,))
            self._event(con,principal,'IDENTITY_DISABLED',None,identity,'RECORDED')

    def _grant_valid(self,con,row,kind):
        issuer=con.execute('SELECT role,domains,enabled FROM human_identities WHERE id=?',(row['issuer'],)).fetchone()
        permitted={'Owner'} if kind=='delegation' else {'Owner','Administrator'}
        return bool(not row['revoked'] and aware_utc(row['expires_at'])>self.now() and issuer and issuer['enabled'] and issuer['role'] in permitted and ('*' in json.loads(issuer['domains']) or row['domain'] in json.loads(issuer['domains'])))

    def authorize(self,principal,permission,domain=None,resource=None,*,sensitive=None):
        with self.store._connect() as con:
            current=self._principal(con,principal.session_id)
            if current.id!=principal.id:raise AuthenticationRequired('Identity mismatch.')
            scoped=(domain in current.domains or '*' in current.domains) if domain else '*' in current.domains
            authority='ROLE'
            allowed=scoped and permission in self.roles[current.role]
            if scoped and not allowed and permission=='work.approve':
                for row in con.execute('SELECT * FROM human_delegations WHERE recipient=? AND domain=? AND resource=?',(current.id,domain,resource)):
                    if self._grant_valid(con,row,'delegation'):allowed=True;authority='DELEGATION:'+row['id'];break
            if scoped and not allowed and permission=='safety.pause':
                for row in con.execute('SELECT * FROM human_emergencies WHERE recipient=? AND domain=?',(current.id,domain)):
                    if self._grant_valid(con,row,'emergency'):allowed=True;authority='EMERGENCY:'+row['id'];break
            if permission=='safety.pause' and scoped and current.role in {'Owner','Administrator'}:allowed=True
            if not allowed:raise PermissionError('This identity is not authorized for this operation/scope.')
            if (permission in SENSITIVE if sensitive is None else sensitive) and not timedelta(0)<=self.now()-aware_utc(current.reauth_at)<=timedelta(minutes=5):raise ReauthenticationRequired('Reauthenticate before this sensitive operation.')
        return authority

    def grant(self,principal,recipient,domain,*,resource=None,seconds=900,emergency=False,reason_ref='operator-review'):
        principal=self.refresh(principal)
        self.authorize(principal,'emergency.manage' if emergency else 'delegation.manage')
        if type(seconds) is not int or not 1<=seconds<=(900 if emergency else 3600):raise ValueError('Authority duration exceeds its bound.')
        token(domain);token(reason_ref)
        if not emergency and (not isinstance(resource,str) or not resource.startswith('action:') or not resource[7:].isdigit()):raise ValueError('Delegation requires one exact action reference.')
        with self.store._connect() as con:
            target=con.execute('SELECT domains,enabled FROM human_identities WHERE id=?',(recipient,)).fetchone()
            if not target or not target['enabled'] or (domain not in json.loads(target['domains']) and '*' not in json.loads(target['domains'])):raise PermissionError('Delegation cannot add cross-domain scope.')
            if '*' not in principal.domains and domain not in principal.domains:raise PermissionError('Issuer domain scope exceeded.')
            identity=uuid.uuid4().hex;expiry=utc_text(self.now()+timedelta(seconds=seconds))
            if emergency:con.execute('INSERT INTO human_emergencies VALUES(?,?,?,?,?,?,0)',(identity,principal.id,recipient,domain,expiry,reason_ref))
            else:con.execute('INSERT INTO human_delegations VALUES(?,?,?,?,?,?,0)',(identity,principal.id,recipient,domain,resource,expiry))
            self._event(con,principal,'EMERGENCY_GRANTED' if emergency else 'DELEGATION_GRANTED',domain,identity,'RECORDED')
        return identity

    def revoke_grant(self,principal,identity,*,emergency=False):
        principal=self.refresh(principal)
        self.authorize(principal,'emergency.manage' if emergency else 'delegation.manage')
        table='human_emergencies' if emergency else 'human_delegations'
        with self.store._connect() as con:
            row=con.execute('SELECT domain FROM '+table+' WHERE id=?',(identity,)).fetchone()
            if not row or ('*' not in principal.domains and row[0] not in principal.domains):raise PermissionError('Grant outside scope.')
            con.execute('UPDATE '+table+' SET revoked=1 WHERE id=?',(identity,))
            self._event(con,principal,'AUTHORITY_REVOKED',row[0],identity,'RECORDED')

    @staticmethod
    def action_digest(row):return digest(canonical({k:row[k] for k in ('id','action','payload_json')}))

    def approve_action(self,principal,action_id,domain):
        from operations.correlation import references
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            # Serialize the authority check with the approval write and revocations.
            authority=self.authorize(principal,'work.approve',domain,'action:'+str(action_id))
            row=con.execute('SELECT * FROM actions WHERE id=?',(action_id,)).fetchone()
            if not row or row['status'] not in {'PENDING','APPROVED'}:raise ValueError('Action is missing or no longer awaiting human approval.')
            payload=json.loads(row['payload_json'] or '{}')
            actual_domain='jobs'
            if 'command_id' in payload:
                scope=con.execute('SELECT domain FROM domain_requests WHERE command_id=?',(payload['command_id'],)).fetchone()
                if scope:actual_domain=scope[0]
            if actual_domain!=domain:raise PermissionError('Approval domain mismatch.')
            if row['status']=='APPROVED' and con.execute('SELECT 1 FROM human_action_approvals WHERE action_id=?',(action_id,)).fetchone():raise ValueError('Identified approval is already recorded.')
            con.execute('INSERT INTO human_action_approvals(action_id,human_id,session_id,action_digest,authority,approved_at) VALUES(?,?,?,?,?,?)',
                        (action_id,principal.id,principal.session_id,self.action_digest(row),authority,utc_text(self.now())))
            con.execute("UPDATE actions SET status='APPROVED',resolved_at=CURRENT_TIMESTAMP WHERE id=?",(action_id,))
            con.execute('INSERT INTO audit_log(event_time,category,actor,action,status,data_json) VALUES(?,?,?,?,?,?)',
                        (utc_text(self.now()),'policy',principal.id,'User approved action','COMPLETED',canonical({**references(),'action_id':action_id,'human_id':principal.id,'human_session_id':principal.session_id})))
            self._event(con,principal,'ACTION_APPROVED',domain,'action:'+str(action_id),'RECORDED',authority)

    def approval_valid(self,action_id,domain):
        if not self.ready():return True # Accepted pre-F worker behavior; F HTTP still fails closed.
        try:
            with self.store._connect() as con:
                row=con.execute('SELECT * FROM human_action_approvals WHERE action_id=? ORDER BY id DESC LIMIT 1',(action_id,)).fetchone()
                action=con.execute('SELECT * FROM actions WHERE id=?',(action_id,)).fetchone()
                if not row or not action or self.action_digest(action)!=row['action_digest'] or not timedelta(0)<=self.now()-aware_utc(row['approved_at'])<=timedelta(minutes=30):return False
                payload=json.loads(action['payload_json'] or '{}')
                actual_domain='jobs'
                if 'command_id' in payload:
                    scope=con.execute('SELECT domain FROM domain_requests WHERE command_id=?',(payload['command_id'],)).fetchone()
                    if scope:actual_domain=scope[0]
                if domain!=actual_domain:return False
                principal=self._principal(con,row['session_id'])
            return self.authorize(principal,'work.approve',domain,'action:'+str(action_id),sensitive=False)==row['authority']
        except (PermissionError,ValueError):return False
