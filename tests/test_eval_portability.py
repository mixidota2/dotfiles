"""Portable v3 ledger regressions; only synthetic files and stubbed agent calls."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / 'skills-local/.codex/skills/create-validated-skill/scripts/run_eval.py'
SPEC = importlib.util.spec_from_file_location('run_eval', RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
RUN_ID = '20260101T000000Z'


class EvalPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='eval-portability-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_fixture(self, home_name, results_mode='default', outcome='pass'):
        home = self.root / home_name
        skill = home / 'checkout 日本語' / 'skills' / 'example-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: example-skill\ndescription: Synthetic\n---\nUse data.')
        runner.write_yaml(skill / 'evals/eval.yaml', {'name':'example-skill','version':3,'tasks':['tasks/task.yaml']})
        runner.write_yaml(skill / 'evals/tasks/task.yaml', {'id':'事例 sample','kind':'typical','scenario':'Synthetic scenario',
            'requirements':[{'id':'r1','critical':True,'description':'Exact token','objective':[{'kind':'contains','value':'validation'}]}]})
        legacy = {'id':'legacy','results_dir':'/legacy-machine/home/old-person/run','behavior_trials':[{'output_path':'behavior/old.json'}]}
        runner.write_yaml(skill / 'evals/ledger.yaml', {'skill':skill.name,'automated_runs':[legacy]})
        args = [str(RUNNER),str(skill)]
        if results_mode == 'inside':
            args += ['--results-dir',str(skill/'custom 結果')]
        elif results_mode == 'outside':
            args += ['--results-dir',str(home/'external 結果')]
        if outcome == 'empty-trials':
            args += ['--case','absent']
        snapshots = {}
        def execute(**kw):
            value = {} if outcome == 'empty-output' else {'deliverable':'validation'}
            if outcome != 'failed':
                kw['output'].write_text(json.dumps(value))
                snapshots[kw['output']] = kw['output'].read_bytes()
            kw['trace'].write_text(json.dumps({'type':'turn.completed','usage':{}})+'\n')
            kw['stderr'].write_text('')
            return {'exit_code':124 if outcome == 'failed' else 0,'duration_ms':1,'tool_uses':0,'usage':{},'isolation':'unverified'}
        stdout = io.StringIO()
        with mock.patch.object(runner.sys,'argv',args), mock.patch.object(runner,'utc_stamp',return_value=RUN_ID), \
             mock.patch.object(runner.uuid,'uuid4',return_value=mock.Mock(hex='1'*32)), \
             mock.patch.object(runner.agent_adapters,'inspect_cli',return_value='test-cli 1'), \
             mock.patch.object(runner.agent_adapters,'execute',side_effect=execute) as call, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = runner.main()
        ledger = runner.load_yaml(skill/'evals/ledger.yaml')
        self.assertEqual(ledger['automated_runs'][0],legacy)
        if outcome == 'empty-trials':
            self.assertEqual(code,2)
            self.assertEqual(len(ledger['automated_runs']),1)
            self.assertEqual(call.call_count,0)
            return None,None,skill
        record = ledger['automated_runs'][-1]
        cli = json.loads(stdout.getvalue())
        raw = runner.load_yaml(Path(cli['results_dir'])/'summary.yaml')
        self.assertEqual(code,2 if outcome in {'failed','empty-output'} else 3)
        self.assertEqual(record['schema_version'],3)
        self.assertEqual(record['decision'],'blocked' if code == 2 else 'inconclusive')
        for path,value in snapshots.items():
            self.assertEqual(path.read_bytes(),value)
        serialized = json.dumps(record,ensure_ascii=False)
        self.assertNotIn(str(home),serialized)
        self.assertNotIn(str(self.root),serialized)
        trial = record['behavior_trials'][0]
        self.assertEqual(trial['output_path'],'behavior/事例 sample/candidate/trial-1/executor-output.json')
        self.assertEqual(trial['unresolved_artifacts'],[])
        return record,raw,skill

    def test_same_evaluation_portable_across_homes(self):
        for mode in ('default','inside','outside'):
            with self.subTest(mode=mode):
                a,_,_=self.run_fixture('alice '+mode,mode)
                b,_,_=self.run_fixture('別ユーザー '+mode,mode)
                self.assertEqual(a,b)
                self.assertEqual(a['results_dir_scope'],'external' if mode=='outside' else 'skill')

    def test_failed_missing_and_empty_runs_fail_closed(self):
        for outcome in ('failed','empty-trials','empty-output'):
            with self.subTest(outcome=outcome):
                a,_,_=self.run_fixture('alice '+outcome,outcome=outcome)
                b,_,_=self.run_fixture('bob '+outcome,outcome=outcome)
                self.assertEqual(a,b)

    def test_external_artifacts_are_omitted_without_modifying_evidence(self):
        _,raw,skill=self.run_fixture('artifact owner')
        root=Path(raw['results_dir'])
        outside=skill.parent/'private evidence.json'
        outside.write_text('original')
        link=root/'linked.json'
        link.symlink_to(outside)
        raw['behavior_trials'][0]['output_path']=str(link)
        raw['behavior_trials'][0]['events_path']=str(root/'../../outside.jsonl')
        before=copy.deepcopy(raw)
        compact=runner.compact_run_record(raw,skill)
        trial=compact['behavior_trials'][0]
        self.assertIsNone(trial['output_path'])
        self.assertEqual(trial['unresolved_artifacts'],['output_path','events_path'])
        self.assertEqual(raw,before)
        self.assertEqual(outside.read_text(),'original')

    def test_append_preserves_historical_records(self):
        _,raw,skill=self.run_fixture('history owner')
        path=skill/'evals/ledger.yaml'
        previous=copy.deepcopy(runner.load_yaml(path)['automated_runs'])
        runner.append_ledger(skill,raw)
        self.assertEqual(runner.load_yaml(path)['automated_runs'][:-1],previous)

    def test_symlink_result_root_is_external(self):
        skill=self.root/'skill'; skill.mkdir()
        outside=self.root/'outside'; outside.mkdir()
        link=skill/'results'; link.symlink_to(outside,target_is_directory=True)
        self.assertIsNone(runner.contained_path(link/'run',skill))

if __name__=='__main__':
    unittest.main()
