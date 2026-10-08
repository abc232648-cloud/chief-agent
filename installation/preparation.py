"""Managed installation preparation, without dependency execution or activation.

Verified source is extracted into an exclusive private slot. A receipt never
grants update approval, proves publisher identity or certifies compatibility.
No operational database, active pointer, scheduler or service is touched.
"""
import io
import json
import uuid
import zipfile

from compatibility.manifest import _unique_object
from operations.private_tree import private_tree
from operations.time_integrity import utc_now, utc_text
from update_center.staging import inspect_package


def prepare_source(data, root, *, expected_archive, expected_source, private_storage_confirmed=False):
    if private_storage_confirmed is not True:
        raise ValueError('Confirm private operator-controlled installation storage.')
    verified=inspect_package(data,expected_archive=expected_archive,expected_source=expected_source)
    identity='prepare-'+uuid.uuid4().hex
    receipt={**verified,'format_version':1,'preparation_id':identity,
             'created_at':utc_text(utc_now()),'status':'SOURCE_PREPARED_NOT_INSTALLED',
             'publisher_authentication':'NOT_ESTABLISHED','environment':'NOT_PREPARED',
             'compatibility':'NOT_EVALUATED','activation':'BLOCKED',
             'data_migration':False,'external_actions_enabled':False}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest=json.loads(archive.read('SOURCE_MANIFEST.json'),object_pairs_hook=_unique_object)
        prefix='source/' if 'hash_method' in manifest else 'chief-agent/'
        receipt['source_identity_method']=manifest.get('hash_method','sorted path-NUL-digest lines SHA256')
        with private_tree(root,identity) as (_,write):
            for name in sorted(manifest['files']):
                write('source/'+name,archive.read(prefix+name))
            write('SOURCE_MANIFEST.json',archive.read('SOURCE_MANIFEST.json'))
            # Last file is the completion marker. Consumers must still verify
            # every source byte and prerequisite before any future execution.
            write('preparation.json',json.dumps(receipt,sort_keys=True).encode())
    return receipt
