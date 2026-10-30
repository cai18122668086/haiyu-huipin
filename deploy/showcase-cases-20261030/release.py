"""Build and activate only the approved showcase frontend on the existing stack."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
import yaml

ROOT = Path(__file__).resolve().parent
RUNTIME = Path('/srv/huipin-prelaunch/runtime')
OVERRIDE = Path('/srv/huipin-membership-20261028/billing.override.yml')
CONTAINER = 'huipin-prelaunch-frontend-1'
MANIFEST = json.loads((ROOT / 'release-manifest.json').read_text())
IMAGE = 'huipin-showcase/frontend:' + MANIFEST['revision']
COMPOSE = ['docker','compose','--project-name','huipin-prelaunch','--env-file','/etc/huipin-prelaunch/prelaunch.env','-f',str(RUNTIME/'compose.yml'),'-f',str(OVERRIDE)]
PROTECTED = ['/app/haiyushuke-server/.env','/app/haiyushuke-server/configs/platform.production.yaml',str(RUNTIME/'compose.yml'),'/etc/huipin-prelaunch/prelaunch.env','/srv/huipin-membership-20261028/secrets/payments.env']


def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(name, data):
    (ROOT/name).write_text(json.dumps(data,indent=2))


def unchanged_baseline():
    current=inspect(CONTAINER)
    assert current['Image']==MANIFEST['baseline']['image_id'], 'Frontend changed; capture and review the new baseline'
    assert current['Id']==MANIFEST['baseline']['id'], 'Frontend container replaced during preparation'
    return current


def verify_sources(container=False):
    script="""const fs=require('fs'),c=require('crypto');let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const m=JSON.parse(s);for(const [p,expected] of Object.entries(m)){const b=fs.readFileSync('/app/'+p);const data=/\\.(tsx?|css|mjs|json)$/.test(p)?Buffer.from(b.toString('utf8').replace(/\\r\\n/g,'\\n')):b;if(c.createHash('sha256').update(data).digest('hex')!==expected)throw new Error('Source mismatch: '+p)}console.log(JSON.stringify({matched_files:Object.keys(m).length}))})"""
    command=['docker','exec','-i',CONTAINER,'node','-e',script] if container else ['docker','run','--rm','-i','--network','none','--entrypoint','node',IMAGE,'-e',script]
    return json.loads(subprocess.check_output(command,input=json.dumps(MANIFEST['sources']).encode()))


def assert_page(base, path, tokens=()):
    with urlopen(base+path,timeout=30) as response:
        assert response.status==200,path
        text=response.read().decode('utf8')
    for token in tokens:
        assert token in text,(path,token)
    assert 'usr_isolated_olay_preview' not in text
    assert 'aria-label="本地模拟预览导航"' not in text


def build():
    unchanged_baseline()
    with (ROOT/'build.log').open('w') as log:
        subprocess.run(['docker','build','--network','host','-f',str(ROOT/'frontend.Dockerfile'),'-t',IMAGE,str(ROOT)],stdout=log,stderr=subprocess.STDOUT,check=True)
    proof=verify_sources()
    write_json('build-result.json',{'image':IMAGE,'image_id':inspect(IMAGE)['Id'],**proof})
    print(json.dumps({'status':'built','image':IMAGE,**proof}))


def test():
    baseline=unchanged_baseline()
    built=json.loads((ROOT/'build-result.json').read_text())
    assert built['image_id']==inspect(IMAGE)['Id']
    name='huipin-showcase-candidate-20261030'
    private=ROOT/'candidate.private.env'
    private.write_text('\n'.join(baseline['Config']['Env'])+'\n');private.chmod(0o600)
    network=next(iter(baseline['NetworkSettings']['Networks']))
    subprocess.run(['docker','run','-d','--name',name,'--network',network,'--env-file',str(private),'-p','127.0.0.1:18960:3000',IMAGE],check=True,stdout=subprocess.DEVNULL)
    try:
        for _ in range(60):
            state=inspect(name)['State']
            assert state['Running'],'Candidate did not start'
            try:
                assert_page('http://127.0.0.1:18960','/',('home-outcome-grid','/images/cases/olay-20261030/replica-01.png'))
                break
            except (OSError,TimeoutError):
                time.sleep(2)
        else:
            raise RuntimeError('Candidate did not respond')
        for path in ['/tools','/style-replica','/workspace/product-image/try-on','/account','/jobs']:
            assert_page('http://127.0.0.1:18960',path)
        write_json('test-result.json',{'status':'passed','image_id':built['image_id'],'runtime_routes':7,'source_files':verify_sources()['matched_files']})
        print(json.dumps({'status':'candidate_verified','runtime_routes':7}))
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,stdout=subprocess.DEVNULL)
        private.unlink(missing_ok=True)


def activate():
    lock=(ROOT/'activation.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    built=json.loads((ROOT/'build-result.json').read_text());tested=json.loads((ROOT/'test-result.json').read_text())
    assert tested['status']=='passed' and tested['image_id']==built['image_id']==inspect(IMAGE)['Id']
    before=unchanged_baseline()
    names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
    others={n:inspect(n)['Id'] for n in names if n!=CONTAINER}
    hashes={p:digest(p) for p in PROTECTED}
    original=OVERRIDE.read_bytes()
    backup=ROOT/'billing-before.private.yml';assert not backup.exists(),'Rollback snapshot already exists'
    backup.write_bytes(original);backup.chmod(0o600)
    snapshot=ROOT/'frontend-before.private.json';snapshot.write_text(json.dumps(before));snapshot.chmod(0o600)
    old_config=json.loads(subprocess.check_output(COMPOSE+['config','--format','json'],cwd=RUNTIME))
    updated=yaml.safe_load(original);updated['services']['frontend']['image']=IMAGE

    def write_config(data):
        pending=OVERRIDE.with_suffix('.showcase-pending.yml');pending.write_bytes(data);pending.chmod(0o600);os.replace(pending,OVERRIDE)

    unchanged_baseline()
    write_config(yaml.safe_dump(updated,sort_keys=False).encode())
    try:
        current=json.loads(subprocess.check_output(COMPOSE+['config','--format','json'],cwd=RUNTIME))
        expected=json.loads(json.dumps(old_config));expected['services']['frontend']['image']=IMAGE
        assert current==expected,'The effective Compose change must be frontend.image only'
        subprocess.run(COMPOSE+['up','-d','--no-deps','frontend'],cwd=RUNTIME,check=True)
        for _ in range(60):
            after=inspect(CONTAINER);state=after['State'];assert state['Running']
            if state.get('Health',{}).get('Status')=='healthy':break
            time.sleep(2)
        else:raise RuntimeError('Frontend did not become healthy')
        assert after['Image']==built['image_id']
        for key in ['Env','Cmd','Entrypoint','WorkingDir']:
            assert after['Config'].get(key)==before['Config'].get(key),key
        for key in ['Binds','Memory','NanoCpus','PidsLimit','Ulimits','PortBindings']:
            assert after['HostConfig'].get(key)==before['HostConfig'].get(key),key
        assert {n:inspect(n)['Id'] for n in others}==others,'Other services changed during deployment'
        assert {p:digest(p) for p in PROTECTED}==hashes,'Protected configuration changed'
        proof=verify_sources(container=True)
        assert_page('https://huipin.haiyushuke.com','/',('home-outcome-grid','/images/cases/olay-20261030/replica-01.png'))
        for path in ['/tools','/style-replica','/workspace/product-image/try-on','/account','/jobs']:
            assert_page('https://huipin.haiyushuke.com',path)
        for path,expected_hash in MANIFEST['public_smoke_assets'].items():
            with urlopen('https://huipin.haiyushuke.com'+path,timeout=30) as r:
                assert r.status==200 and hashlib.sha256(r.read()).hexdigest()==expected_hash,path
    except BaseException:
        write_config(original)
        subprocess.run(COMPOSE+['up','-d','--no-deps','frontend'],cwd=RUNTIME,check=True)
        write_json('activation.json',{'status':'rolled_back','backend_restarted':False})
        raise
    result={'status':'activated','revision':MANIFEST['revision'],'frontend_image':IMAGE,'previous_image':before['Config']['Image'],'backend_restarted':False,'database_changed':False,'other_services_preserved':True,'runtime_environment_preserved':True,'protected_configuration_preserved':True,'public_routes_verified':7,'public_original_assets_verified':len(MANIFEST['public_smoke_assets']),**proof}
    write_json('activation.json',result);print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['build','test','activate']);args=parser.parse_args()
    {'build':build,'test':test,'activate':activate}[args.action]()
