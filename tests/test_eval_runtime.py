"""Deterministic transport, grading, and contamination regressions; no model calls."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

REPO=Path(__file__).resolve().parents[1]
SCRIPTS=REPO/'skills-local/.codex/skills/create-validated-skill/scripts'
SPEC=importlib.util.spec_from_file_location('eval_runtime',SCRIPTS/'run_eval.py')
r=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(r)
a=r.agent_adapters


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_objective_checks_actual_deliverable_not_self_report(self):
        out={'deliverable':'real answer','phase_trace':{'success':'expected'}}
        self.assertEqual(r.objective_check({'kind':'contains','value':'expected'},out,self.root,self.root,self.root,0)['verdict'],'fail')

    def test_missing_artifact_not_satisfied_by_narrative(self):
        self.assertEqual(r.objective_check({'kind':'file_exists','path':'output.json'}, {'deliverable':'I wrote output.json'},self.root,self.root,self.root,0)['verdict'],'fail')

    def test_exact_json_and_file_checks(self):
        out={'deliverable':'{"a":{"b":[2,4]}}'}
        self.assertEqual(r.objective_check({'kind':'json_equals','pointer':'/a/b/1','value':4},out,self.root,self.root,self.root,0)['verdict'],'pass')
        (self.root/'file').write_text('actual')
        self.assertEqual(r.objective_check({'kind':'file_contains','path':'file','value':'actual'},out,self.root,self.root,self.root,1)['verdict'],'pass')

    def test_unsafe_artifact_path_errors(self):
        for path in ('../secret','/tmp/secret'):
            self.assertEqual(r.objective_check({'kind':'file_exists','path':path},{'deliverable':'x'},self.root,self.root,self.root,0)['verdict'],'error')

    def test_command_grader_is_argv_and_external(self):
        check={'kind':'command','argv':['python3','{eval_root}/grader.py','{workspace}']}
        with mock.patch.object(r.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='good',stderr='')) as run:
            result=r.objective_check(check,{'deliverable':'x'},self.root,self.root,self.root,0)
        self.assertEqual(result['verdict'],'pass')
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][-1],str(self.root))

    def test_legacy_semantic_only_requires_review(self):
        task={'requirements':[{'id':'r','critical':True,'semantic':{'rubric':'Good'}}]}
        self.assertEqual(r.grade_task(task,{'deliverable':'I passed'},self.root,self.root,self.root)[0]['combined_verdict'],'review_required')

    def test_malformed_outputs_fail(self):
        for value in ('{}','[]','{"deliverable":42}','{"deliverable":" "}','bad'):
            path=self.root/'output.json'; path.write_text(value)
            self.assertTrue(r.valid_output(path)[1])

    def test_runtime_staging_excludes_evidence(self):
        source=self.root/'source'; source.mkdir()
        (source/'SKILL.md').write_text('runtime')
        (source/'evals').mkdir(); (source/'evals/holdout.yaml').write_text('hidden')
        (source/'references').mkdir(); (source/'references/guide.md').write_text('reference')
        dest=self.root/'dest'; r.stage_tree(source,dest,skill=True)
        self.assertFalse((dest/'evals').exists())
        self.assertEqual((dest/'references/guide.md').read_text(),'reference')

    def test_staging_refuses_symlinks(self):
        source=self.root/'source'; source.mkdir()
        (source/'SKILL.md').symlink_to('/tmp/unavailable')
        with self.assertRaises(ValueError): r.stage_tree(source,self.root/'dest',skill=True)

    def test_activation_requires_successful_read(self):
        skill=self.root/'SKILL.md'; trace=self.root/'trace'
        for item,expected in [
            ({'type':'command_execution','command':f'echo {skill}','exit_code':0},False),
            ({'type':'command_execution','command':f'cat {skill}','exit_code':1},False),
            ({'type':'command_execution','command':f'cat --help {skill}','exit_code':0},False),
            ({'type':'command_execution','command':f'head -n0 {skill}','exit_code':0},False),
            ({'type':'command_execution','command':'echo hello','exit_code':0,'output':str(skill)},False),
            ({'type':'command_execution','command':f'cat {skill}','exit_code':0},True)]:
            trace.write_text(json.dumps({'type':'item.completed','item':item})+'\n')
            self.assertEqual(r.detect_activation(trace,skill)[0],expected)

    def test_null_trace_fields_do_not_crash(self):
        trace=self.root/'trace'; trace.write_text('{"type":"item.completed","item":null}\n{"message":null}\n{"message":{"content":null}}\n')
        for engine in ('codex','claude'):
            self.assertFalse(r.detect_activation(trace,self.root/'SKILL.md',engine)[0])

    def test_task_selection_rejects_unsafe_empty_duplicate(self):
        skill=self.root/'skill'; (skill/'evals').mkdir(parents=True)
        args=argparse.Namespace(split='development',case=None)
        req=[{'id':'r','critical':True}]
        for task in ({'id':'.','scenario':'x','requirements':req}, {'id':'..','scenario':'x','requirements':req},
                     {'id':'safe','scenario':'x','requirements':[]}, {'id':'safe','scenario':'x','requirements':[{'id':'r','critical':False}]}):
            r.write_yaml(skill/'evals/task.yaml',task)
            with self.assertRaises(ValueError): r.selected_tasks(skill,{'tasks':['task.yaml']},args)
        with self.assertRaises(ValueError): r.selected_tasks(skill,{'tasks':[]},args)

    def test_codex_command_keeps_auth_environment(self):
        cmd=a.command('codex','codex',self.root,self.root/'output',r.EXECUTOR_SCHEMA,'pinned',False,self.root/'runtime')
        self.assertIn('--ephemeral',cmd); self.assertIn('--ignore-user-config',cmd)
        self.assertIn('read-only',cmd); self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)
        self.assertNotIn('CODEX_HOME',' '.join(cmd))

    def test_claude_uses_official_structured_protocol_without_permission_bypass(self):
        cmd=a.command('claude','claude',self.root,self.root/'output',r.EXECUTOR_SCHEMA,'pinned',False,self.root/'runtime')
        self.assertIn('stream-json',cmd); self.assertIn('--json-schema',cmd)
        self.assertIn('--no-session-persistence',cmd)
        self.assertNotIn('--dangerously-skip-permissions',cmd)
        self.assertNotIn('--allowedTools',cmd)

    def transport(self,engine,events,code=0):
        output=self.root/'output.json'; trace=self.root/'trace'; stderr=self.root/'stderr'
        def fake(cmd,**kw):
            kw['stdout'].write(''.join(json.dumps(e)+'\n' for e in events))
            if engine=='codex': output.write_text('{"deliverable":"actual"}')
            return mock.Mock(returncode=code,stderr='')
        with mock.patch.object(a,'run_process',side_effect=fake):
            result=a.execute(engine=engine,binary=engine,prompt='task',workspace=self.root,schema=r.EXECUTOR_SCHEMA,
                output=output,trace=trace,stderr=stderr,model='pinned',writable=False,timeout=10,runtime=self.root/'runtime')
        return result,output

    def test_transport_requires_completed_turn(self):
        result,_=self.transport('codex',[{'type':'item.completed','item':None}])
        self.assertNotEqual(result['exit_code'],0)
        result,_=self.transport('codex',[{'type':'turn.failed'}])
        self.assertNotEqual(result['exit_code'],0)

    def test_claude_parses_structured_output_not_result_prose(self):
        result,path=self.transport('claude',[{'type':'system','model':'pinned'}, {'type':'result','is_error':False,'structured_output':{'deliverable':'actual'},'usage':{'input_tokens':12}}])
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(json.loads(path.read_text()),{'deliverable':'actual'})
        self.assertEqual(result['resolved_model'],'pinned')

    def test_timeout_records_infrastructure_error(self):
        with mock.patch.object(a,'run_process',side_effect=subprocess.TimeoutExpired('codex',10)):
            result=a.execute(engine='codex',binary='codex',prompt='task',workspace=self.root,schema=r.EXECUTOR_SCHEMA,
                output=self.root/'out',trace=self.root/'trace',stderr=self.root/'err',model=None,writable=False,timeout=10,runtime=self.root/'runtime')
        self.assertEqual(result['exit_code'],124)

    def test_claude_writable_trial_is_explicitly_blocked(self):
        with mock.patch.object(a,'run_process',side_effect=AssertionError('must not execute')):
            result=a.execute(engine='claude',binary='claude',prompt='task',workspace=self.root,schema=r.EXECUTOR_SCHEMA,
                output=self.root/'out',trace=self.root/'trace',stderr=self.root/'err',model=None,writable=True,timeout=10,runtime=self.root/'runtime')
        self.assertEqual(result['exit_code'],78)

    def test_claude_explicit_workspace_edit_approval_is_files_only(self):
        cmd=a.command('claude','claude',self.root,self.root/'out',r.EXECUTOR_SCHEMA,'pinned',True,self.root/'runtime',True)
        self.assertEqual(cmd[cmd.index('--permission-mode')+1],'acceptEdits')
        self.assertEqual(cmd[cmd.index('--tools')+1],'Read,Glob,Grep,Write,Edit')
        self.assertIn('--strict-mcp-config',cmd)
        self.assertEqual(cmd[cmd.index('--mcp-config')+1],'{"mcpServers":{}}')
        self.assertEqual(cmd[cmd.index('--disallowedTools')+1],'mcp__*')
        for unsafe in ('Bash','PowerShell','WebFetch','--allowedTools','--dangerously-skip-permissions','--add-dir'):
            self.assertNotIn(unsafe,cmd)

    @unittest.skipUnless(a.os.name == 'posix', 'process-group cleanup is POSIX-specific')
    def test_timeout_terminates_only_owned_process_group(self):
        process=mock.Mock(pid=12345)
        process.communicate.side_effect=[subprocess.TimeoutExpired('test',1), ('','')]
        with mock.patch.object(a.subprocess,'Popen',return_value=process) as create, mock.patch.object(a.os,'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                a.run_process(['fake'],prompt='x',workspace=self.root,stdout=mock.Mock(),timeout=1)
        self.assertTrue(create.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(12345,a.signal.SIGTERM)

    def test_real_stub_cli_roundtrip_and_unique_runs(self):
        # This executes a local protocol stub, not Codex or any model.
        import sys
        skill=self.root/'fixture-skill'; (skill/'evals/tasks').mkdir(parents=True)
        (skill/'SKILL.md').write_text('---\nname: fixture-skill\ndescription: A test skill.\n---\nRead the input.')
        r.write_yaml(skill/'evals/eval.yaml',{'name':'fixture-skill','version':3,'tasks':['tasks/task.yaml']})
        r.write_yaml(skill/'evals/tasks/task.yaml',{'id':'task','kind':'typical','scenario':'Return actual.',
            'requirements':[{'id':'answer','critical':True,'description':'Exact literal','objective':[{'kind':'contains','value':'actual'}]}]})
        binary=self.root/'fake-codex'
        binary.write_text('#!'+sys.executable+'\nimport sys,json\nfrom pathlib import Path\nif "--version" in sys.argv:\n print("fake CLI protocol stub 1");sys.exit(0)\nsys.stdin.read()\nPath(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps({"deliverable":"actual"}))\nprint(json.dumps({"type":"turn.completed","usage":{"input_tokens":1}}))\n')
        binary.chmod(0o700)
        summaries=[]
        for _ in range(2):
            result=subprocess.run([sys.executable,str(SCRIPTS/'run_eval.py'),str(skill),'--binary',str(binary),'--baseline','--model','fake','--no-ledger'],capture_output=True,text=True)
            self.assertEqual(result.returncode,3,result.stderr)
            response=json.loads(result.stdout)
            summaries.append(response)
            self.assertEqual(response['case_comparisons'][0]['result'],'tie')
        self.assertNotEqual(summaries[0]['id'],summaries[1]['id'])
        for summary in summaries:
            self.assertTrue((Path(summary['results_dir'])/'summary.yaml').is_file())

    def test_baseline_error_makes_comparison_inconclusive(self):
        trials=[{'config':arm,'task':'t','trial':1,'verdict':verdict,'executor_duration_ms':1} for arm,verdict in [('candidate','pass'),('no_skill','error')]]
        self.assertEqual(r.summarize(trials)[1][0]['result'],'inconclusive')

if __name__=='__main__': unittest.main()
