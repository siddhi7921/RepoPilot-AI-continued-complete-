"""Offline SQLite preparation and authenticated, resumable administrator import.

No passwords or sessions are exported. Diagnostics contain counts and row indexes only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse

REPORT_FIELDS = {
    'job_id', 'repo_url', 'revision', 'created_at', 'updated_at', 'summary',
    'root_cause', 'patch', 'source_files', 'evidence', 'events', 'steps',
    'indexing_status', 'summary_citations', 'root_cause_citation', 'hypotheses',
    'expected_behavior', 'reproduction_steps', 'legacy_history',
}


def private_json(path, data):
    # Never overwrite an existing export or mapping file.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(data, output, ensure_ascii=True)


def prepare(args):
    original = Path(args.sqlite).resolve(strict=True)
    backup = Path(args.backup).resolve()
    if original == backup or backup.exists():
        raise ValueError('Backup must be a new file')
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    # SQLite online backup includes committed WAL data and leaves the live application operational.
    with sqlite3.connect(original.as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(backup) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
    with sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        accounts = [dict(row) for row in db.execute('SELECT username, created_at FROM users ORDER BY username')]
        account_dates = {row['username']: row['created_at'] for row in accounts}
        records, skipped = [], []
        total = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        for index, row in enumerate(db.execute('SELECT * FROM jobs ORDER BY id')):
            try:
                row = dict(row)
                details = json.loads(row.get('details_json') or '{}')
                # Ownership must be present in the original record, never inferred from an email.
                owner = details.get('owner')
                if not isinstance(owner, str) or owner not in account_dates:
                    skipped.append({'index': index, 'reason': 'missing_verified_legacy_owner'})
                    continue
                report = {key: value for key, value in details.items() if key in REPORT_FIELDS}
                for key in ['repo_url', 'revision', 'created_at', 'updated_at', 'summary', 'root_cause', 'patch', 'expected_behavior', 'reproduction_steps', 'indexing_status']:
                    report.setdefault(key, row.get(key))
                report['job_id'] = row['id']
                for key in ['evidence', 'steps']:
                    report.setdefault(key, json.loads(row.get(key) or '[]'))
                report.setdefault('events', json.loads(row.get('events_json') or '[]'))
                historical_verification = details.get('verification', {})
                report['legacy_history'] = {
                    'status': str(details.get('status', row.get('status', 'unknown')))[:100],
                    'verification_status': str(historical_verification.get('status', 'unknown'))[:100],
                    'patch_was_approved': details.get('patch_approved') is True,
                }
                records.append({'id': row['id'], 'username': owner, 'account_created_at': account_dates[owner], 'report': report})
            except (ValueError, TypeError, AttributeError):
                skipped.append({'index': index, 'reason': 'malformed_legacy_record'})
    manifest = {'source_id': hashlib.sha256(backup.read_bytes()).hexdigest(), 'accounts': accounts, 'records': records, 'skipped': skipped, 'original_count': total}
    if len(records) + len(skipped) != total:
        raise ValueError('Source record count mismatch')
    private_json(args.export, manifest)
    print(json.dumps({'backup_verified': True, 'accounts': len(accounts), 'reports': len(records), 'skipped': skipped, 'original_count': total}))


def send(site, payload):
    token = os.environ.get('REPOPILOT_ADMIN_TOKEN')
    if not token:
        raise ValueError('Administrator session is required in REPOPILOT_ADMIN_TOKEN')
    data = json.dumps(payload).encode()
    if len(data) > 4400000:
        raise ValueError('Batch exceeds request size; reduce batch size')
    req = urllib.request.Request(site + '/api/admin/migration', data=data, headers={'Cookie': 'nf_jwt=' + token, 'Content-Type': 'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(req, timeout=60) as response:
        return json.loads(response.read(200000))


def batches(manifest, mappings, size):
    common = {'source_id': manifest['source_id'], 'mappings': mappings}
    accounts = manifest['accounts']
    records = manifest['records']
    for offset in range(0, len(accounts), size):
        yield {**common, 'accounts': accounts[offset:offset + size], 'records': []}
    for offset in range(0, len(records), size):
        yield {**common, 'records': records[offset:offset + size]}


def run_import(args):
    url = urlparse(args.site)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('Use an HTTPS site origin without credentials')
    site = args.site.rstrip('/')
    manifest = json.loads(Path(args.export).read_text())
    mappings = json.loads(Path(args.mapping).read_text())
    if len(manifest['records']) + len(manifest['skipped']) != manifest['original_count']:
        raise ValueError('Manifest record count mismatch')
    plans = []
    for index, payload in enumerate(batches(manifest, mappings, args.batch_size)):
        result = send(site, payload)
        plans.append((payload, result))
        print(json.dumps({'batch': index, **{key: result[key] for key in ['accounts', 'total', 'eligible', 'already_imported', 'skipped']}}))
    plan_digest = hashlib.sha256(json.dumps({'site': site, 'plans': [result['plan_digest'] for _, result in plans]}, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'dry_run': True, 'batches': len(plans), 'source_skipped': manifest['skipped'], 'commit_digest': plan_digest}))
    if not args.commit:
        return
    if args.confirm_digest != plan_digest:
        raise ValueError('Commit requires the exact digest from the full dry-run summary')
    verified, skipped = 0, len(manifest['skipped'])
    for index, (payload, plan) in enumerate(plans):
        result = send(site, {**payload, 'commit': True, 'plan_digest': plan['plan_digest']})
        if not result.get('verified'):
            raise ValueError('Server did not verify the batch')
        verified += result['eligible']
        skipped += len(result['skipped'])
        print(json.dumps({'batch': index, 'verified': True, 'committed': result['committed'], 'already_imported': result['already_imported']}))
    if verified + skipped != manifest['original_count']:
        raise ValueError('Post-import report count mismatch')
    print(json.dumps({'verified_reports': verified, 'skipped_reports': skipped, 'original_count': manifest['original_count'], 'all_records_migrated': skipped == 0, 'legacy_database_retained': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    for name in ['sqlite', 'backup', 'export']:
        prep.add_argument('--' + name, required=True)
    imp = commands.add_parser('import')
    for name in ['export', 'mapping', 'site']:
        imp.add_argument('--' + name, required=True)
    imp.add_argument('--batch-size', type=int, choices=range(1, 6), default=1)
    imp.add_argument('--commit', action='store_true')
    imp.add_argument('--confirm-digest')
    args = parser.parse_args()
    try:
        prepare(args) if args.command == 'prepare' else run_import(args)
    except Exception:
        # HTTP errors may contain Identity details; never print exception bodies or input records.
        print('Migration stopped. No success was asserted. Check backup, mappings, admin session, batch size, and server configuration; rerun the dry run.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
