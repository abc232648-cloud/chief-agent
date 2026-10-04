"""Private, bounded PNG attachments to authorized staff work. No file paths.

Bytes live in the existing private database, so its consistent backup includes
attachments. Only simple RGB/RGBA PNGs are accepted; metadata is not retained.
"""
import base64
import binascii
import hashlib
import json
import struct
import time
import zlib
from . import setup, staff
from .journal import identifier
from identity.service import IdentityService
from operations.time_integrity import utc_now, utc_text

KIND = 'farm_photo_v1'
MAX_BYTES = 2 * 1024 * 1024


def normalize_png(encoded):
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES * 4 // 3 + 8):
        raise ValueError('Photo exceeds the 2 MiB limit.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Invalid photo encoding.') from exc
    if len(raw) > MAX_BYTES or raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Use a PNG photo within the size limit.')
    pos = 8; chunks = []; compressed = bytearray(); header = None; ended = False
    while pos < len(raw):
        if pos + 12 > len(raw):
            raise ValueError('Truncated PNG.')
        size = struct.unpack('>I', raw[pos:pos+4])[0]
        kind = raw[pos+4:pos+8]
        end = pos + 12 + size
        if end > len(raw):
            raise ValueError('Truncated PNG chunk.')
        data = raw[pos+8:pos+8+size]
        if zlib.crc32(kind + data) & 0xffffffff != struct.unpack('>I', raw[pos+8+size:end])[0]:
            raise ValueError('PNG integrity check failed.')
        if header is None:
            if kind != b'IHDR' or size != 13:
                raise ValueError('PNG header required.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
            if not (1 <= width <= 1536 and 1 <= height <= 1536) or depth != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
                raise ValueError('Use a non-interlaced RGB/RGBA PNG, at most 1536 pixels per side.')
            header = (width, height, 3 if color == 2 else 4)
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            if size != 0 or end != len(raw):
                raise ValueError('Invalid PNG end.')
            ended = True
        elif kind not in {b'sRGB', b'gAMA', b'pHYs'}:
            raise ValueError('Unsupported photo chunk; use the upload form to remove metadata.')
        if kind in {b'IHDR', b'IDAT', b'IEND'}:
            chunks.append(raw[pos:end])
        pos = end
    if not ended or not compressed or header is None:
        raise ValueError('Incomplete PNG.')
    width, height, channels = header
    expected = height * (1 + width * channels)
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise ValueError('Invalid PNG pixels.') from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid or oversized PNG image data.')
    if any(pixels[y * (1 + width * channels)] > 4 for y in range(height)):
        raise ValueError('Invalid PNG scanline filter.')
    return b'\x89PNG\r\n\x1a\n' + b''.join(chunks)


def authorized_item(store, con, principal, work_id):
    principal = setup.authorize(store, con, principal, 'read')
    item = staff.projection(staff.rows(con)).get(work_id)
    if not item:
        raise PermissionError('Work item is unavailable.')
    manager = setup.role(con, principal) in {'OWNER', 'GENERAL_MANAGER', 'SUPERVISOR'}
    if not manager and principal.id not in {item['reporter'], item['assignee']}:
        raise PermissionError('Attachment is outside your work scope.')
    return principal


def records(con, *, work_id=None, photo_id=None, metadata_only=False):
    if (work_id is None)==(photo_id is None):
        raise ValueError('A scoped photo lookup is required.')
    field='work_id' if work_id is not None else 'id'
    value=work_id if work_id is not None else photo_id
    selection="json_remove(data_json,'$.image_base64')" if metadata_only else 'data_json'
    return [json.loads(r[0]) for r in con.execute('SELECT '+selection+" FROM domain_records WHERE domain='farming' AND kind='farm_photo_v1' AND json_extract(data_json,'$."+field+"')=? ORDER BY id LIMIT 11",(value,))]


def metadata(record):
    return {k: v for k, v in record.items() if k != 'image_base64'}


def append(store, principal, payload):
    if not isinstance(payload, dict) or set(payload) != {'event_id', 'work_id', 'image_base64'}:
        raise ValueError('Supply exactly the photo fields.')
    photo_id = identifier(payload['event_id']); work_id = identifier(payload['work_id'])
    raw = normalize_png(payload['image_base64']); digest = hashlib.sha256(raw).hexdigest()
    with store._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        principal = authorized_item(store, con, principal, work_id)
        setup.authorize(store, con, principal, 'report'); setup.available(store, con)
        existing = records(con, photo_id=photo_id, metadata_only=True)
        prior = existing[0] if existing else None
        if prior:
            if prior['actor_id'] != principal.id or prior['work_id'] != work_id or prior['sha256'] != digest:
                raise ValueError('Photo submission identifier conflict.')
            return {'status': 'ALREADY_RECORDED', 'record': metadata(prior)}
        if len(records(con, work_id=work_id, metadata_only=True)) >= 10:
            raise ValueError('This work item has reached its 10-photo development limit.')
        record = {'id': photo_id, 'work_id': work_id, 'actor_id': principal.id, 'received_at': utc_text(utc_now()),
                  'sha256': digest, 'bytes': len(raw), 'image_base64': base64.b64encode(raw).decode('ascii')}
        con.execute('INSERT INTO domain_records(domain,kind,data_json,created_at) VALUES(?,?,?,?)',
                    ('farming', KIND, json.dumps(record, sort_keys=True), time.time()))
        IdentityService(store)._event(con, principal, 'FARM_PHOTO_RECORDED', 'farming', photo_id, 'RECORDED')
    return {'status': 'RECORDED', 'record': metadata(record)}


def listing(store, principal, work_id):
    identifier(work_id)
    with store._connect() as con:
        authorized_item(store, con, principal, work_id)
        return records(con, work_id=work_id, metadata_only=True)


def image(store, principal, photo_id):
    identifier(photo_id)
    with store._connect() as con:
        matches = records(con, photo_id=photo_id)
        record = matches[0] if matches else None
        if not record:
            raise PermissionError('Photo unavailable.')
        authorized_item(store, con, principal, record['work_id'])
        raw = base64.b64decode(record['image_base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('Stored photo integrity check failed.')
        return raw
