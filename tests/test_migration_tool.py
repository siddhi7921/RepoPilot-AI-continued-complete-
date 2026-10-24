import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('migration_tool', ROOT / 'scripts/migrate_legacy.py')
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class MigrationToolTest(unittest.TestCase):
    def test_backup_export_and_unowned_record_reporting(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, backup, export = [folder / name for name in ['source.sqlite', 'backup.sqlite', 'export.json']]
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE users(username TEXT, password_hash TEXT, created_at TEXT)')
                db.execute('CREATE TABLE jobs(id TEXT, details_json TEXT)')
                db.execute('INSERT INTO users VALUES (?, ?, ?)', ('legacy', 'must-not-export', '2025-01-01T00:00:00Z'))
                db.execute('INSERT INTO jobs VALUES (?, ?)', ('report-id', json.dumps({'owner': 'legacy', 'password': 'must-not-export', 'job_id': 'report-id', 'source_files': {'a.py': 'x=1'}})))
                db.execute('INSERT INTO jobs VALUES (?, ?)', ('orphan', '{}'))
            result = subprocess.run(['python3', str(ROOT / 'scripts/migrate_legacy.py'), 'prepare', '--sqlite', str(source), '--backup', str(backup), '--export', str(export)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(export.read_text())
            self.assertEqual(manifest['original_count'], 2)
            self.assertEqual(len(manifest['records']), 1)
            self.assertEqual(len(manifest['skipped']), 1)
            self.assertNotIn('must-not-export', export.read_text())
            self.assertNotIn('"legacy"', result.stdout)
            self.assertTrue(source.exists())
            self.assertEqual(export.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(backup) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)

    def test_full_dry_run_precedes_writes_and_confirmation_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            export, mapping = folder / 'export.json', folder / 'mapping.json'
            export.write_text(json.dumps({'source_id': 'a' * 64, 'accounts': [{'username': 'legacy', 'created_at': '2025-01-01T00:00:00Z'}], 'records': [{'id': 'a'}], 'skipped': [], 'original_count': 1}))
            mapping.write_text('{}')
            args = type('Args', (), {'site': 'https://site.example.test', 'export': str(export), 'mapping': str(mapping), 'batch_size': 1, 'commit': True, 'confirm_digest': 'wrong'})()
            calls = []
            original = TOOL.send
            def send(site, payload):
                calls.append(payload)
                return {'accounts': len(payload.get('accounts', [])), 'total': len(payload['records']), 'eligible': len(payload['records']), 'already_imported': 0, 'skipped': [], 'plan_digest': 'test'}
            TOOL.send = send
            try:
                with self.assertRaises(ValueError):
                    TOOL.run_import(args)
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(not call.get('commit') for call in calls))
            finally:
                TOOL.send = original


if __name__ == '__main__':
    unittest.main()
