"""Local bootstrap only. Never creates or migrates the F schema."""
import argparse,getpass
from database.store import Store
from .service import IdentityService

def main():
    parser=argparse.ArgumentParser(description='Bootstrap the first local Owner after a separately verified migration.')
    parser.add_argument('--database',required=True);parser.add_argument('--username',required=True)
    parser.add_argument('--recover',action='store_true',help='Administrative recovery of an existing Owner; never unlocks a restore.')
    parser.add_argument('--expected-owner-id')
    args=parser.parse_args()
    if args.recover:
        from pathlib import Path
        from .recovery import is_os_administrator,recover_owner
        if not is_os_administrator():raise PermissionError('Run this recovery command with explicit OS administrator authorization.')
        if not args.expected_owner_id:raise ValueError('Supply the exact existing Owner ID.')
        target=str(Path(args.database).resolve(strict=True))
        print('Stop Chief services first. Recovery revokes this Owner\'s sessions and does not unlock quarantined restores.')
        if input('Confirm exact database path: ')!=target:raise ValueError('Target confirmation did not match.')
        password=getpass.getpass('New Owner password: ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords did not match.')
        import json
        print(json.dumps(recover_owner(target,args.username,args.expected_owner_id,password)))
        return
    service=IdentityService(Store(args.database));service.require_ready()
    password=getpass.getpass('New Owner password: ')
    if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords did not match.')
    service.bootstrap(args.username,password)
    print('Owner created. Open the loopback dashboard sign-in page.')

if __name__=='__main__':main()
