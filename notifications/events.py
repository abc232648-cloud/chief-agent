"""Versioned presentation-event contract for Chief notification surfaces.

The envelope intentionally contains only bounded presentation metadata. It is not an
authorization grant and must never contain raw agent payloads, credentials, form data
or provider secrets. Runtime APIs must still apply Chief identity/domain authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import uuid

from agents.manifest import INTERFACE_KINDS
from agents.staff_roles import staff_role, validate_staff_roles
from notifications.models import NotificationSeverity

EVENT_SCHEMA_VERSION = 1
SURFACE_ORDER = ('owner', 'staff', 'companion')
_TOKEN_RE = re.compile(r'^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$')
_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
_ROUTE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~-]{0,79}$')


def _text(value, label, limit):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text.')
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise ValueError(f'{label} must contain 1-{limit} characters.')
    return value


def _token(value, label):
    value = _text(value, label, 80)
    if not _TOKEN_RE.fullmatch(value):
        raise ValueError(f'{label} must be a lowercase stable identifier.')
    return value


def _identity(value, label='Notification recipient identity'):
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f'{label} is invalid.')
    return value


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError('Notification event time must be a timezone-aware timestamp.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Notification event time must be a timezone-aware timestamp.') from None
    if parsed.tzinfo is None:
        raise ValueError('Notification event time must be timezone-aware.')
    return parsed.astimezone(timezone.utc).isoformat()


def new_event_id():
    return uuid.uuid4().hex


@dataclass(frozen=True)
class NotificationAudience:
    """Presentation recipients. Assignment authority is still checked elsewhere."""

    surfaces: tuple[str, ...]
    staff_roles: tuple[str, ...] = ()
    principal_ids: tuple[str, ...] = ()

    def __post_init__(self):
        surfaces = tuple(self.surfaces)
        if not surfaces or len(surfaces) != len(set(surfaces)):
            raise ValueError('Notification surfaces must be non-empty and unique.')
        if any(surface not in INTERFACE_KINDS for surface in surfaces):
            raise ValueError('Unknown notification surface.')
        object.__setattr__(self, 'surfaces', surfaces)

        principals = tuple(_identity(value) for value in self.principal_ids)
        if len(principals) != len(set(principals)):
            raise ValueError('Notification recipient identities must be unique.')
        object.__setattr__(self, 'principal_ids', principals)

        if 'staff' in surfaces:
            roles = validate_staff_roles(self.staff_roles)
            if any(staff_role(role).scope != 'DOMAIN' for role in roles) and not principals:
                raise ValueError('Assignment-scoped Staff notifications require explicit recipient identities.')
            object.__setattr__(self, 'staff_roles', roles)
        elif self.staff_roles:
            raise ValueError('Staff roles require the Staff notification surface.')
        else:
            object.__setattr__(self, 'staff_roles', ())

    def as_dict(self):
        return {
            'surfaces': list(self.surfaces),
            'staff_roles': list(self.staff_roles),
            'principal_ids': list(self.principal_ids),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {'surfaces', 'staff_roles', 'principal_ids'}:
            raise ValueError('Invalid notification audience record.')
        return cls(tuple(value['surfaces']), tuple(value['staff_roles']), tuple(value['principal_ids']))


@dataclass(frozen=True)
class NotificationLink:
    """Structured internal deep link; never accepts an arbitrary URL."""

    surface: str
    module: str
    route: str

    def __post_init__(self):
        if self.surface not in INTERFACE_KINDS:
            raise ValueError('Unknown notification link surface.')
        object.__setattr__(self, 'module', _token(self.module, 'Notification link module'))
        route = _text(self.route, 'Notification link route', 240)
        if route.startswith('/') or route.endswith('/') or '\\' in route or '?' in route or '#' in route or '://' in route:
            raise ValueError('Notification deep links must use a safe relative route.')
        parts = route.split('/')
        if any(part in {'.', '..'} or not _ROUTE_SEGMENT_RE.fullmatch(part) for part in parts):
            raise ValueError('Notification deep links must use safe route segments.')
        object.__setattr__(self, 'route', route)

    @property
    def path(self):
        if self.surface == 'owner':
            return f'/owner/{self.module}/{self.route}'
        if self.surface == 'staff':
            return f'/staff/{self.module}/{self.route}'
        return f'/{self.module}/{self.route}'

    def as_dict(self):
        return {'surface': self.surface, 'module': self.module, 'route': self.route, 'path': self.path}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) not in ({'surface', 'module', 'route'}, {'surface', 'module', 'route', 'path'}):
            raise ValueError('Invalid notification deep link record.')
        result = cls(value['surface'], value['module'], value['route'])
        if 'path' in value and value['path'] != result.path:
            raise ValueError('Notification deep link path does not match its structured route.')
        return result


@dataclass(frozen=True)
class NotificationEvent:
    """Immutable sanitized event routed to one or more Chief presentation surfaces."""

    event_id: str
    source_agent: str
    event_type: str
    title: str
    body: str
    severity: NotificationSeverity
    audience: NotificationAudience
    links: tuple[NotificationLink, ...] = ()
    object_type: str | None = None
    object_id: str | None = None
    priority: int = 50
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        object.__setattr__(self, 'event_id', _identity(self.event_id, 'Notification event id'))
        object.__setattr__(self, 'source_agent', _token(self.source_agent, 'Notification source agent'))
        object.__setattr__(self, 'event_type', _token(self.event_type, 'Notification event type'))
        object.__setattr__(self, 'title', _text(self.title, 'Notification title', 160))
        object.__setattr__(self, 'body', _text(self.body, 'Notification body', 500))
        try:
            severity = self.severity if isinstance(self.severity, NotificationSeverity) else NotificationSeverity(self.severity)
        except (TypeError, ValueError):
            raise ValueError('Unknown notification severity.') from None
        object.__setattr__(self, 'severity', severity)
        if not isinstance(self.audience, NotificationAudience):
            raise ValueError('Notification audience must be a validated NotificationAudience.')

        links = tuple(self.links)
        if any(not isinstance(link, NotificationLink) for link in links):
            raise ValueError('Notification links must be validated NotificationLink values.')
        surfaces = [link.surface for link in links]
        if len(surfaces) != len(set(surfaces)):
            raise ValueError('Only one notification deep link may be declared per surface.')
        if any(link.surface not in self.audience.surfaces for link in links):
            raise ValueError('Notification deep links must belong to declared audience surfaces.')
        object.__setattr__(self, 'links', links)

        if (self.object_type is None) != (self.object_id is None):
            raise ValueError('Notification object type and id must be supplied together.')
        if self.object_type is not None:
            object.__setattr__(self, 'object_type', _token(self.object_type, 'Notification object type'))
            object.__setattr__(self, 'object_id', _identity(self.object_id, 'Notification object id'))
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError('Notification priority must be an integer from 0 to 100.')
        object.__setattr__(self, 'created_at', _timestamp(self.created_at))

    def link_for(self, surface):
        if surface not in INTERFACE_KINDS:
            raise ValueError('Unknown notification surface.')
        return next((link for link in self.links if link.surface == surface), None)

    def as_dict(self):
        return {
            'schema_version': EVENT_SCHEMA_VERSION,
            'event_id': self.event_id,
            'source_agent': self.source_agent,
            'event_type': self.event_type,
            'title': self.title,
            'body': self.body,
            'severity': self.severity.value,
            'audience': self.audience.as_dict(),
            'links': [link.as_dict() for link in self.links],
            'object_type': self.object_type,
            'object_id': self.object_id,
            'priority': self.priority,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, value):
        expected = {
            'schema_version', 'event_id', 'source_agent', 'event_type', 'title', 'body', 'severity',
            'audience', 'links', 'object_type', 'object_id', 'priority', 'created_at',
        }
        if not isinstance(value, dict) or set(value) != expected or value.get('schema_version') != EVENT_SCHEMA_VERSION:
            raise ValueError('Unsupported notification event record.')
        return cls(
            event_id=value['event_id'],
            source_agent=value['source_agent'],
            event_type=value['event_type'],
            title=value['title'],
            body=value['body'],
            severity=value['severity'],
            audience=NotificationAudience.from_dict(value['audience']),
            links=tuple(NotificationLink.from_dict(link) for link in value['links']),
            object_type=value['object_type'],
            object_id=value['object_id'],
            priority=value['priority'],
            created_at=value['created_at'],
        )
