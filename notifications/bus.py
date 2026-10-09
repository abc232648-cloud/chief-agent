"""Durable Chief notification-event bus with presentation-only audience routing.

The bus persists sanitized versioned envelopes in existing ``domain_records`` and,
when an Owner or companion surface is declared, writes the existing ``notifications``
row as a compatibility projection. No event or audience match grants API authority.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import json

from agents.interfaces import InterfaceRegistry
from agents.manifest import INTERFACE_KINDS
from agents.staff_roles import staff_role
from notifications.events import (
    NotificationAudience,
    NotificationEvent,
    NotificationLink,
    new_event_id,
)

EVENT_RECORD_KIND = 'chief_notification_event_v1'
EVENT_RECORD_VERSION = 1
_MAX_SCAN = 1000


@dataclass(frozen=True)
class NotificationContext:
    """Trusted presentation context supplied after normal Chief authentication."""

    surface: str
    principal_id: str
    identity_role: str
    domains: tuple[str, ...]
    staff_role: str | None = None

    def __post_init__(self):
        if self.surface not in INTERFACE_KINDS:
            raise ValueError('Unknown notification presentation surface.')
        if not isinstance(self.principal_id, str) or not self.principal_id.strip() or len(self.principal_id) > 128:
            raise ValueError('Notification presentation identity is required.')
        if not isinstance(self.identity_role, str) or not self.identity_role.strip() or len(self.identity_role) > 80:
            raise ValueError('Notification presentation identity role is required.')
        domains = tuple(self.domains)
        if not domains or len(domains) != len(set(domains)) or any(not isinstance(domain, str) or not domain for domain in domains):
            raise ValueError('Notification presentation domains must be explicit and unique.')
        object.__setattr__(self, 'domains', domains)
        if self.surface == 'staff':
            object.__setattr__(self, 'staff_role', staff_role(self.staff_role).name)
        elif self.staff_role is not None:
            raise ValueError('Staff role metadata is only valid on the Staff surface.')


class NotificationBus:
    """Validate, persist and project sanitized events without granting authority."""

    def __init__(self, store, interfaces: InterfaceRegistry):
        if not isinstance(interfaces, InterfaceRegistry):
            raise TypeError('NotificationBus requires the authoritative InterfaceRegistry.')
        self.store = store
        self.interfaces = interfaces

    def _initialize(self):
        from domains.storage import initialize as initialize_domains
        from control.notifications import initialize as initialize_notifications
        initialize_domains(self.store)
        initialize_notifications(self.store)

    def _validate_event_contract(self, event: NotificationEvent) -> NotificationEvent:
        if not isinstance(event, NotificationEvent):
            raise ValueError('NotificationBus accepts only validated NotificationEvent values.')
        if event.source_agent == 'system':
            if event.audience.surfaces != ('owner',):
                raise ValueError('System notifications are restricted to the Owner surface.')
            if event.audience.staff_roles or event.audience.principal_ids:
                raise ValueError('System notifications cannot infer Staff or identity recipients.')
            if any(link.surface != 'owner' or link.module != 'system' for link in event.links):
                raise ValueError('System notification links must remain inside the Owner system module.')
            return event

        if not self.interfaces.notifications_declared(event.source_agent):
            raise ValueError(f'Agent {event.source_agent!r} does not declare notifications.')
        for surface in event.audience.surfaces:
            registration = self.interfaces.require(event.source_agent, surface)
            if surface == 'staff':
                for role in event.audience.staff_roles:
                    self.interfaces.staff_role(event.source_agent, role)
            for link in (item for item in event.links if item.surface == surface):
                if link.module != registration.module:
                    raise ValueError('Notification deep link module must match the registered interface module.')
        return event

    @staticmethod
    def _decode_record(row):
        try:
            payload = json.loads(row['data_json'])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError('Stored notification event record is invalid.') from None
        if not isinstance(payload, dict) or set(payload) != {'version', 'event', 'legacy_notification_id'} or payload['version'] != EVENT_RECORD_VERSION:
            raise ValueError('Stored notification event record is invalid.')
        event = NotificationEvent.from_dict(payload['event'])
        if event.source_agent != row['domain']:
            raise ValueError('Stored notification event source does not match its domain namespace.')
        legacy_id = payload['legacy_notification_id']
        if legacy_id is not None and (type(legacy_id) is not int or legacy_id <= 0):
            raise ValueError('Stored notification compatibility reference is invalid.')
        return event, legacy_id

    def _existing(self, con, event: NotificationEvent):
        rows = con.execute(
            'SELECT id,domain,data_json FROM domain_records WHERE domain=? AND kind=? ORDER BY id DESC',
            (event.source_agent, EVENT_RECORD_KIND),
        ).fetchall()
        for row in rows:
            stored, legacy_id = self._decode_record(row)
            if stored.event_id == event.event_id:
                if stored != event:
                    raise ValueError('Notification event identifier conflict.')
                return {'record_id': int(row['id']), 'legacy_notification_id': legacy_id}
        return None

    def publish(self, event: NotificationEvent, *, connection=None):
        """Atomically append one event and its legacy Owner/companion projection.

        Replaying the same immutable event id is idempotent. Reusing an event id with
        different content fails closed. A supplied connection lets an existing Chief
        transaction include the event without opening a nested write transaction;
        that caller is responsible for having initialized the accepted schema first.
        """
        self._validate_event_contract(event)
        if connection is None:
            self._initialize()
        manager = nullcontext(connection) if connection is not None else self.store._connect()
        with manager as con:
            if connection is None:
                con.execute('BEGIN IMMEDIATE')
            existing = self._existing(con, event)
            if existing is not None:
                return {'status': 'ALREADY_RECORDED', 'event_id': event.event_id, **existing}

            legacy_id = None
            if {'owner', 'companion'} & set(event.audience.surfaces):
                related = event.link_for('companion') or event.link_for('owner')
                cursor = con.execute(
                    '''INSERT INTO notifications(title,body,severity,domain,related_page,presented,created_at)
                       VALUES(?,?,?,?,?,0,?)''',
                    (
                        event.title,
                        event.body,
                        event.severity.value,
                        event.source_agent,
                        related.route if related else '',
                        event.created_at,
                    ),
                )
                legacy_id = int(cursor.lastrowid)

            wrapper = {
                'version': EVENT_RECORD_VERSION,
                'event': event.as_dict(),
                'legacy_notification_id': legacy_id,
            }
            created_epoch = datetime.fromisoformat(event.created_at).timestamp()
            cursor = con.execute(
                'INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                (event.source_agent, EVENT_RECORD_KIND, json.dumps(wrapper, sort_keys=True, separators=(',', ':')), created_epoch),
            )
            return {
                'status': 'RECORDED',
                'event_id': event.event_id,
                'record_id': int(cursor.lastrowid),
                'legacy_notification_id': legacy_id,
            }

    def visible(self, event: NotificationEvent, context: NotificationContext) -> bool:
        """Presentation filter only; callers must still authorize their API request."""
        self._validate_event_contract(event)
        if not isinstance(context, NotificationContext):
            raise ValueError('A validated NotificationContext is required.')
        if context.surface not in event.audience.surfaces:
            return False
        if event.audience.principal_ids and context.principal_id not in event.audience.principal_ids:
            return False

        if event.source_agent == 'system':
            return (
                context.surface == 'owner'
                and context.identity_role in {'Owner', 'Administrator'}
                and '*' in context.domains
            )
        if event.source_agent not in context.domains and '*' not in context.domains:
            return False
        registration = self.interfaces.require(event.source_agent, context.surface)
        if context.surface == 'staff':
            return (
                context.staff_role in event.audience.staff_roles
                and context.staff_role in registration.roles
            )
        return context.identity_role in registration.roles

    def project(self, event: NotificationEvent, context: NotificationContext):
        if not self.visible(event, context):
            return None
        link = event.link_for(context.surface)
        return {
            'schema_version': 1,
            'event_id': event.event_id,
            'source_agent': event.source_agent,
            'event_type': event.event_type,
            'severity': event.severity.value,
            'priority': event.priority,
            'title': event.title,
            'body': event.body,
            'object': None if event.object_type is None else {'type': event.object_type, 'id': event.object_id},
            'deep_link': link.as_dict() if link else None,
            'created_at': event.created_at,
            'authority': 'PRESENTATION_ONLY',
        }

    def list_for(self, context: NotificationContext, limit=100):
        """Return recent valid events visible to one already-authenticated surface."""
        if not isinstance(context, NotificationContext):
            raise ValueError('A validated NotificationContext is required.')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Notification event limit must be from 1 to 200.')
        self._initialize()
        with self.store._connect() as con:
            rows = con.execute(
                'SELECT id,domain,data_json FROM domain_records WHERE kind=? ORDER BY id DESC LIMIT ?',
                (EVENT_RECORD_KIND, _MAX_SCAN),
            ).fetchall()
        result = []
        for row in rows:
            try:
                event, _ = self._decode_record(row)
                projected = self.project(event, context)
            except ValueError:
                # Malformed/unregistered presentation records are omitted rather than
                # becoming an authorization or data-leak bypass.
                continue
            if projected is not None:
                result.append({'record_id': int(row['id']), **projected})
                if len(result) >= limit:
                    break
        return result


def publish_legacy_notification(store, interfaces: InterfaceRegistry, title, body,
                                severity='INFO', domain='system', related_page='', *, connection=None):
    """Compatibility helper for callers that already own composition.

    Core never imports the application composition layer. Callers must inject the
    authoritative InterfaceRegistry explicitly. Existing Job behavior is retained:
    Job events project to Owner + companion and still feed the legacy notifications
    table/Web Push cursor. Staff delivery is never inferred by this helper.
    """
    if not isinstance(interfaces, InterfaceRegistry):
        raise TypeError('Legacy notification publishing requires an InterfaceRegistry.')
    if domain == 'system':
        surfaces = ('owner',)
        links = (NotificationLink('owner', 'system', related_page),) if related_page else ()
    else:
        interfaces.manifests.get(domain)  # fail closed on an unknown source namespace
        if not interfaces.notifications_declared(domain):
            raise ValueError(f'Agent {domain!r} does not declare notifications.')
        surfaces = tuple(
            surface for surface in ('owner', 'companion')
            if interfaces.available(domain, surface)
        )
        if not surfaces:
            raise ValueError('Legacy notifications require an Owner or companion interface.')
        links = ()
        if related_page:
            links = tuple(
                NotificationLink(surface, interfaces.require(domain, surface).module, related_page)
                for surface in surfaces
            )
    event = NotificationEvent(
        event_id=new_event_id(),
        source_agent=domain,
        event_type='legacy.notification',
        title=title,
        body=body,
        severity=severity,
        audience=NotificationAudience(surfaces),
        links=links,
    )
    return NotificationBus(store, interfaces).publish(event, connection=connection)
