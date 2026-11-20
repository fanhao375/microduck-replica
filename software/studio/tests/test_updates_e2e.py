"""Real Edge/Studio/coordinator package flow; real Rust engine tested separately in QEMU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests import test_vpad_e2e as harness

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(harness._node_ok() and harness.EDGE and os.environ.get('UPDATE_E2E_PACKAGE'),
                     'requires Edge,Node and an explicit local signed fixture package')
class UpdatesBrowser(unittest.TestCase):
    def test_actual_coordinator_button_package_lifecycle(self):
        output=Path(os.environ.get('UPDATE_E2E_DIR') or tempfile.mkdtemp(prefix='updates-e2e-'))
        output.mkdir(parents=True,exist_ok=True)
        ports=[harness._port(0) for _ in range(5)]
        robot_port,config_port,web_port,cdp_port,fixture_port=ports
        processes,streams=[],[]
        record=output/'robotd.jsonl'
        record.write_text('',encoding='utf-8')
        def launch(name,args,extra=None):
            stream=(output/name).open('w',encoding='utf-8');streams.append(stream)
            processes.append(subprocess.Popen(args,cwd=ROOT,env={**os.environ,
                'PYTHONIOENCODING':'utf-8','UPDATE_E2E_DIR':str(output),
                'UPDATE_FIXTURE_PORT':str(fixture_port),**(extra or {})},stdout=stream,stderr=subprocess.STDOUT))
        try:
            launch('fake.log',[sys.executable,'-I',str(ROOT/'dev/fake_robotd.py'),'--fixture',
                str(ROOT/'tests/fixtures/now-20261018.jsonl'),'--robotd-port',str(robot_port),
                '--configd-port',str(config_port),'--posture','holding','--record',str(record)])
            launch('studio.log',[sys.executable,'-I',str(ROOT/'tests/update_fixture_launcher.py'),'--listen',
                f'127.0.0.1:{web_port}','--robotd',f'tcp:127.0.0.1:{robot_port}','--configd',
                f'tcp:127.0.0.1:{config_port}','--dev-autologin','--servo-sim',
                '--state-dir',str(output/'state'),'--log-dir',str(output/'logs')])
            harness._wait_http(f'http://127.0.0.1:{web_port}/healthz','update Studio')
            launch('edge.log',[harness.EDGE,'--headless=new','--disable-gpu','--no-first-run',
                '--no-default-browser-check',f'--remote-debugging-port={cdp_port}',
                f'--user-data-dir={output/"edge"}','about:blank'])
            harness._wait_http(f'http://127.0.0.1:{cdp_port}/json/version','update Edge')
            result=subprocess.run([harness.NODE,str(ROOT/'tests/updates_e2e.mjs'),'--url',
                f'http://127.0.0.1:{web_port}/#/settings','--cdp',str(cdp_port),
                '--fixture-port',str(fixture_port),'--package',os.environ['UPDATE_E2E_PACKAGE'],
                '--shots',str(output/'shots')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=150)
            (output/'browser.json').write_text(result.stdout,encoding='utf-8')
            (output/'browser.stderr').write_text(result.stderr,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr[-4000:]+f'\nArtifacts:{output}')
            report=json.loads(result.stdout)
            self.assertEqual(report['errors'],[])
            self.assertEqual(report['layouts'],[1440,390,320])
            for name in ('no_release','downloaded_waiting','validated','installed','reloaded_task',
                         'rollback','uploaded','invalid_package','privileged_fields_rejected','stage1_old_helper',
                         'overview_notice','offer','single_click','online_installed',
                         'native_pending','native_no_automatic_call','native_schema','native_csrf_origin',
                         'native_software_exclusion','native_reconnect_success','native_no_payload',
                         'native_unsupported','native_failure','native_recovery','bundle_complete','bundle_restore'):
                self.assertTrue(report[name],name)
            calls=[json.loads(line) for line in (output/'engine.jsonl').read_text().splitlines()]
            apply=[c['params'] for c in calls if c['method']=='update.apply']
            self.assertTrue(any(p['options'].get('dry_run') for p in apply))
            self.assertTrue(any(not p['options'].get('dry_run') for p in apply))
            self.assertTrue(any(c['method']=='update.rollback' for c in calls))
            self.assertTrue(all(p['target']=={'exact':'2026.10.27'} for p in apply))
            records=[json.loads(line) for line in record.read_text().splitlines()]
            self.assertFalse(any(c.get('method') in ('robot.enable','robot.do','robot.init') for c in records))
            self.assertTrue(any(c.get('method')=='robot.relax' for c in records),'global Space must retain real emergency path')
            native=[json.loads(line) for line in (output/'native.jsonl').read_text().splitlines()]
            mutations=[row for row in native if row['action']!='status']
            self.assertEqual([row['action'] for row in mutations],['install','install','recover','install','restore'])
            self.assertTrue(all(row['by']=='Studio' and row['confirmed'] is True for row in mutations))
        finally:
            for process in reversed(processes):harness._kill(process)
            for stream in streams:stream.close()

if __name__=='__main__':unittest.main()
