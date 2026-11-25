#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Release tooling tests; no credentials, network, scheduler, or inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import update_publication_manifest as update


class Manifest(unittest.TestCase):
    def setup_tree(self,root):
        (root/'tools/sched_ext/new_a_final').mkdir(parents=True)
        (root/'tools/sched_ext/new_a_final/policy-manifest.json').write_text(json.dumps(dict(policy_change='NONE',strict_acceptance='FAILED',safety_verdict='FAIL',files={})))
        (root/'PUBLICATION_MANIFEST.json').write_text(json.dumps(dict(files=[],policy='UNCHANGED')))
        (root/'PUBLICATION_PROVENANCE.json').write_text(json.dumps(dict(files=[dict(path='source.py',source='PRESERVED_ORIGIN',kind='OLD',release_sha256='0'*64)],notes=[])))
        (root/'source.py').write_text('REVIEWED = True\n')
        return [p.relative_to(root).as_posix()for p in root.rglob('*')if p.is_file()]
    def test_provenance_preserved_and_refresh_idempotent(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);names=self.setup_tree(root)
            with patch.object(update.verify_release,'ROOT',root):
                update.refresh(root,names,write=True);before=(root/'PUBLICATION_PROVENANCE.json').read_bytes()
                row=next(r for r in json.loads(before)['files']if r['path']=='source.py')
                self.assertEqual(row['previous_releases'][0]['source'],'PRESERVED_ORIGIN')
                update.refresh(root,names,write=True);self.assertEqual((root/'PUBLICATION_PROVENANCE.json').read_bytes(),before)
                self.assertEqual(update.refresh(root,names)['status'],'MANIFEST_CURRENT')
    def test_stale_metadata_needs_explicit_write(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);names=self.setup_tree(root);before=(root/'PUBLICATION_MANIFEST.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'stale'):update.refresh(root,names)
            self.assertEqual((root/'PUBLICATION_MANIFEST.json').read_bytes(),before)
    def test_existing_secret_scan_rejects_and_restores_metadata(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);names=self.setup_tree(root);before={p:p.read_bytes()for p in root.glob('PUBLICATION*.json')}
            (root/'source.py').write_text('credential = '+repr('ghp_'+'Z'*40))
            with patch.object(update.verify_release,'ROOT',root),self.assertRaisesRegex(ValueError,'rejected'):update.refresh(root,names,write=True)
            self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))
    def test_original_policy_violation_still_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);names=self.setup_tree(root)
            policy=root/'tools/sched_ext/new_a_final/policy-manifest.json';v=json.loads(policy.read_text());v['strict_acceptance']='PASS';policy.write_text(json.dumps(v))
            with patch.object(update.verify_release,'ROOT',root),self.assertRaisesRegex(ValueError,'verdict_changed'):update.refresh(root,names,write=True)
    def test_symlink_rejected_before_metadata_write(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);names=self.setup_tree(root);(root/'alias.py').symlink_to(root/'source.py')
            with self.assertRaisesRegex(ValueError,'symlink'):update.refresh(root,names+['alias.py'],write=True)


if __name__=='__main__':unittest.main(verbosity=2)
