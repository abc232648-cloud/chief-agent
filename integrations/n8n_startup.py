"""Explicit synthetic startup gate; never dispatches or retries business work.

Run before accepting integration work, with the reviewed handshake installed.
A receipt is an observation of this instant, never an authorization or durable
health claim. Runtime failures still require the usual UNKNOWN/no-resend rules.
"""
import argparse
import json
import time
import uuid

from . import n8n_handoff


def qualify(*, confirmed=False):
    if confirmed is not True:
        raise ValueError('Explicit synthetic-only startup qualification is required.')
    parsed, token = n8n_handoff._configuration()
    attempts = []
    consecutive = 0
    started = time.monotonic()
    # Distinct synthetic probes only. Never repeat an uncertain operation ID.
    # Two consecutive receipts prevent a lone late startup response passing.
    for _ in range(3):
        payload = {'contract':n8n_handoff.CONTRACT, 'operation_id':uuid.uuid4().hex,
                   'domain':'chief', 'deadline':time.time()+15,
                   'purpose':'synthetic-handshake-only'}
        stamp = time.monotonic()
        verified = n8n_handoff._exchange(parsed, token, payload)
        elapsed = time.monotonic()-stamp
        verified = (verified is True and time.time() < payload['deadline']
                    and elapsed < n8n_handoff.TIMEOUT)
        attempts.append({'verified':verified, 'seconds':round(elapsed, 3)})
        consecutive = consecutive+1 if verified else 0
        if consecutive == 2:
            break
        if len(attempts) < 3:
            time.sleep(1)
    return {'status':'STARTUP_READY' if consecutive == 2 else 'NOT_READY',
            'scope':'SYNTHETIC_HANDSHAKE_ONLY', 'attempts':attempts,
            'seconds':round(time.monotonic()-started, 3),
            'business_execution_authorized':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-synthetic-only', action='store_true', required=True)
    parser.parse_args()
    try:
        result = qualify(confirmed=True)
    except (ValueError, OSError):
        # Never expose exception text containing private configuration.
        print(json.dumps({'status':'NOT_READY','reason':'Private startup configuration unavailable.'}))
        return 1
    print(json.dumps(result))
    return 0 if result['status']=='STARTUP_READY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
