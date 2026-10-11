#!/usr/bin/env python3
"""Release follow-up boundaries with no provider or infrastructure side effects."""
import importlib.util
import pathlib
import subprocess
import shutil
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/scripts'))
import ai_agents


class Hardening(unittest.TestCase):
    def test_agent_settings_reject_flag_shaped_inputs(self):
        base=dict(id='coach',name='Coach',provider='codex',model='exact')
        for field in ('executable','model'):
            for value in ('--help',' -invalid'):
                with self.subTest(field=field,value=value):
                    with self.assertRaisesRegex(ai_agents.AgentError,'must not start with'):
                        ai_agents.agent_config(dict(base,**{field:value}))
        self.assertEqual(ai_agents.agent_config(dict(base,executable='codex'))['model'],'exact')

    def test_model_override_cannot_be_a_flag(self):
        adapter=ai_agents.Adapter(dict(id='coach',name='Coach',provider='codex',model='exact'),which=lambda _:sys.executable)
        with self.assertRaisesRegex(ai_agents.AgentError,'must not start with'):
            adapter.check_execution(dict(status='ready',auth='subscription',capabilities=ai_agents.REQUIRED_FLAGS['codex']),model='--help')

    def test_empty_execution_model_still_fails_closed(self):
        adapter=ai_agents.Adapter(dict(id='coach',name='Coach',provider='codex',model='exact'),which=lambda _:sys.executable)
        with self.assertRaisesRegex(ai_agents.AgentError,'Model must not be empty'):
            adapter.check_execution(dict(status='ready',auth='subscription',capabilities=ai_agents.REQUIRED_FLAGS['codex']),model='')

    def test_cli_rejects_old_python_before_project_imports(self):
        script="import sys,runpy;sys.version_info=(3,9);runpy.run_path(sys.argv[1],run_name='__main__')"
        result=subprocess.run([sys.executable,'-c',script,str(ROOT/'src/racecast.py')],capture_output=True,text=True,errors='replace')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Python 3.12 or newer',result.stderr)
        self.assertNotIn('Traceback',result.stderr)

    @unittest.skipUnless(shutil.which('ruff'),'Ruff style behavior checked by lint job')
    def test_style_rules_are_scoped_and_detect_compound_statements(self):
        def check(name,source):
            return subprocess.run(['ruff','check','--stdin-filename',name,'-'],input=source,
                cwd=ROOT,capture_output=True,text=True,errors='replace')
        bad=check('src/scripts/ai_jobs.py','x=1; y=2\n')
        self.assertNotEqual(bad.returncode,0)
        self.assertIn('E702',bad.stdout)
        self.assertIn('E225',bad.stdout)
        self.assertEqual(check('src/scripts/ai_jobs.py','x = 1\ny = 2\n').returncode,0)
        self.assertEqual(check('src/scripts/services.py','x=1; y=2\n').returncode,0)

    def test_preflight_uses_the_same_python_floor(self):
        import preflight
        self.assertEqual(preflight.classify_python((3,12,0)).level,'PASS')
        result=preflight.classify_python((3,9,6))
        self.assertEqual(result.level,'FAIL')
        self.assertIn('Python 3.12 or newer',result.detail)

    def test_saved_model_selection_rejects_flags(self):
        with self.assertRaisesRegex(ai_agents.AgentError,'must not start with'):
            ai_agents.settings(dict(agents=[],last=dict(model='--help')))

    def test_python_requirement_classifies_supported_and_old(self):
        path=ROOT/'src/scripts/python_runtime.py'
        spec=importlib.util.spec_from_file_location('python_runtime',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertIsNone(module.requirement((3,12)))
        self.assertIsNone(module.requirement((3,14)))
        for version in ((3,9),(3,10),(3,11)):
            self.assertIn('Python 3.12 or newer',module.requirement(version))


if __name__=='__main__':unittest.main()
