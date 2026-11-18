"""Build and activate only the tested security frontend on the existing stack."""
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
IMAGE = 'huipin-frontend-security/frontend:' + MANIFEST['revision']
COMPOSE = ['docker','compose','--project-name','huipin-prelaunch','--env-file','/etc/huipin-prelaunch/prelaunch.env','-f',str(RUNTIME/'compose.yml'),'-f',str(OVERRIDE)]
PROTECTED = [str(RUNTIME/'compose.yml'),str(RUNTIME/'Caddyfile.prelaunch'),'/etc/huipin-prelaunch/prelaunch.env','/srv/huipin-membership-20261028/secrets/payments.env']


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


def verify_sources(container=False, expected=None):
    script="""const fs=require('fs'),c=require('crypto');let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const m=JSON.parse(s);for(const [p,expected] of Object.entries(m)){const b=fs.readFileSync('/app/'+p);const data=p.endsWith('.tgz')?b:Buffer.from(b.toString('utf8').replace(/\\r\\n/g,'\\n'));if(c.createHash('sha256').update(data).digest('hex')!==expected)throw new Error('Source mismatch: '+p)}console.log(JSON.stringify({matched_files:Object.keys(m).length}))})"""
    command=['docker','exec','-i',CONTAINER,'node','-e',script] if container else ['docker','run','--rm','-i','--network','none','--entrypoint','node',IMAGE,'-e',script]
    return json.loads(subprocess.check_output(command,input=json.dumps(MANIFEST['sources'] if expected is None else expected).encode()))


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
    assert verify_sources(container=True, expected=MANIFEST['unchanged_app_sources'])
    with (ROOT/'build.log').open('w') as log:
        subprocess.run(['docker','build','--network','host','-f',str(ROOT/'frontend.Dockerfile'),'-t',IMAGE,str(ROOT)],stdout=log,stderr=subprocess.STDOUT,check=True)
    proof=verify_sources()
    write_json('build-result.json',{'image':IMAGE,'image_id':inspect(IMAGE)['Id'],**proof})
    print(json.dumps({'status':'built','image':IMAGE,**proof}))


def test():
    baseline=unchanged_baseline()
    built=json.loads((ROOT/'build-result.json').read_text())
    assert built['image_id']==inspect(IMAGE)['Id']
    name='huipin-security-test-' + MANIFEST['revision'][:12]
    private=ROOT/'candidate.private.env'
    private.write_text('\n'.join(baseline['Config']['Env'])+'\n');private.chmod(0o600)
    network=next(iter(baseline['NetworkSettings']['Networks']))
    subprocess.run(['docker','run','-d','--name',name,'--network',network,'--env-file',str(private),IMAGE],check=True,stdout=subprocess.DEVNULL)
    check="""(async()=>{for(const path of ['/','/tools','/style-replica','/workspace/product-image/try-on','/account','/jobs','/help']){const r=await fetch('http://127.0.0.1:3000'+path,{signal:AbortSignal.timeout(15000)});if(r.status!==200)throw new Error('Route '+path+': '+r.status);const text=await r.text();if(path==='/'&&(!text.includes('home-outcome-grid')||!text.includes('/images/cases/olay-20261030/replica-01.png')))throw new Error('Missing approved homepage');if(text.includes('usr_isolated_olay_preview')||text.includes('aria-label="本地模拟预览导航"'))throw new Error('Local preview configuration leaked')}console.log('Candidate routes passed')})().catch(e=>{console.error(e.message);process.exit(1)})"""
    try:
        for _ in range(30):
            state=inspect(name)['State'];assert state['Running'],'Candidate did not start'
            result=subprocess.run(['docker','exec',name,'node','-e',check],capture_output=True)
            if result.returncode==0:break
            time.sleep(2)
        else:
            raise RuntimeError('Candidate route check failed: '+result.stderr.decode()[-800:])
        write_json('test-result.json',{'status':'passed','image_id':built['image_id'],'runtime_routes':7,'source_files':verify_sources()['matched_files']})
        print(json.dumps({'status':'candidate_verified','runtime_routes':7}))
    finally:
        subprocess.run(['docker','rm','-f',name],check=True,stdout=subprocess.DEVNULL)
        private.unlink(missing_ok=True)


def activate():
    lock=(ROOT/'activation.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    built=json.loads((ROOT/'build-result.json').read_text());tested=json.loads((ROOT/'test-result.json').read_text())
    assert tested['status']=='passed' and tested['image_id']==built['image_id']==inspect(IMAGE)['Id']
    assert tested.get('security_and_unit_tests_passed') is True
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
        pending=OVERRIDE.with_suffix('.frontend-security-pending.yml')
        with os.fdopen(os.open(pending,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending,OVERRIDE)

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
        for key in ['Env','Cmd','Entrypoint','WorkingDir','User']:
            if key=='Env':
                assert dict(v.split('=',1) for v in after['Config'][key])==dict(v.split('=',1) for v in before['Config'][key]),key
            else:
                assert after['Config'].get(key)==before['Config'].get(key),key
        for key in ['Binds','Memory','NanoCpus','PidsLimit','Ulimits','PortBindings']:
            assert after['HostConfig'].get(key)==before['HostConfig'].get(key),key
        assert {n:inspect(n)['Id'] for n in others}==others,'Other services changed during deployment'
        assert {p:digest(p) for p in PROTECTED}==hashes,'Protected configuration changed'
        proof=verify_sources(container=True)
        assert_page('https://huipin.haiyushuke.com','/',('home-outcome-grid','/images/cases/olay-20261030/replica-01.png'))
        for path in ['/tools','/style-replica','/workspace/product-image/try-on','/account','/jobs','/help']:
            assert_page('https://huipin.haiyushuke.com',path)
        for path,expected_hash in MANIFEST['public_smoke_assets'].items():
            with urlopen('https://huipin.haiyushuke.com'+path,timeout=30) as r:
                assert r.status==200 and hashlib.sha256(r.read()).hexdigest()==expected_hash,path
    except BaseException:
        write_config(original)
        subprocess.run(COMPOSE+['up','-d','--no-deps','frontend'],cwd=RUNTIME,check=True)
        write_json('activation.json',{'status':'rolled_back','backend_restarted':False})
        raise
    result={'status':'activated','revision':MANIFEST['revision'],'frontend_image':IMAGE,'previous_image':before['Config']['Image'],'backend_restarted':False,'database_changed':False,'other_services_preserved':True,'runtime_environment_preserved':True,'protected_configuration_preserved':True,'public_routes_verified':7,'public_original_assets_verified':len(MANIFEST['public_smoke_assets']),'security_and_unit_tests_passed':tested['security_and_unit_tests_passed'],**proof}
    write_json('activation.json',result);print(json.dumps(result))



# Audit and run the complete frontend suite against the final runtime image.
route_test = test

def test():
    unchanged_baseline()
    built=json.loads((ROOT/'build-result.json').read_text())
    assert built['image_id']==inspect(IMAGE)['Id']
    fixtures=ROOT/'test-fixtures'
    for path, expected in MANIFEST['test_fixtures'].items():
        data=(fixtures/path).read_bytes()
        if Path(path).suffix not in ['.png','.webp']:
            data=data.replace(b'\r\n',b'\n')
        assert hashlib.sha256(data).hexdigest()==expected,path
    # Chromium and repository-only fixtures belong to this disposable test
    # image/mounts; the deployable image and its dependency tree stay identical.
    test_image='huipin-frontend-security/test:'+MANIFEST['revision']
    dockerfile=ROOT/'test.Dockerfile'
    browser_base='mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:08530fb1cfa94f223f47c60ba23dce91bc01b9f8a3b79485f35bdbbfe61dbb73'
    dockerfile.write_text('FROM '+IMAGE+' AS candidate\nFROM '+MANIFEST['node_base']+' AS node\nFROM '+browser_base+'\nUSER root\nCOPY --from=node /usr/local /usr/local\nCOPY --from=candidate --chown=1000:1000 /app /app\nRUN ln -s /ms-playwright/chromium-1248/chrome-linux64/chrome /usr/bin/chromium\nENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0\nWORKDIR /app\nUSER 1000:1000\n')
    with (ROOT/'test-browser-build.log').open('w') as log:
        subprocess.run(['docker','build','--network','none','-f',str(dockerfile),'-t',test_image,str(ROOT)],stdout=log,stderr=subprocess.STDOUT,check=True)
    mounts=[]
    for path in ['docs/openapi-v1.json','deploy/Caddyfile.domestic-detail-native','backend/catalog_assets/model-pose-transfer']:
        mounts+=['-v',str(fixtures/path)+':/app/'+path+':ro']
    with (ROOT/'dependency-test.log').open('w') as log:
        result=subprocess.run(['docker','run','--rm','--network','host','--memory','2g','--cpus','1.5','-e','npm_config_cache=/tmp/npm-cache',*mounts,'--entrypoint','sh',test_image,'-c','npm run audit:security && node --experimental-strip-types --test tests/*.test.mjs'],stdout=log,stderr=subprocess.STDOUT)
    print((ROOT/'dependency-test.log').read_text()[-4500:])
    assert result.returncode==0,'Final-image dependency audit or frontend tests failed'
    route_test()
    data=json.loads((ROOT/'test-result.json').read_text());data['security_and_unit_tests_passed']=True
    data['test_fixtures_verified']=len(MANIFEST['test_fixtures'])
    data['browser_test_environment']=test_image
    data['browser_base']=browser_base
    data['browser_base_id']=inspect(browser_base)['Id']
    data['runtime_dependencies_copied_from']=built['image_id']
    write_json('test-result.json',data)


def preview():
    baseline=unchanged_baseline()
    tested=json.loads((ROOT/'test-result.json').read_text());assert tested['status']=='passed'
    assert tested['image_id']==inspect(IMAGE)['Id']
    app='huipin-security-preview-'+MANIFEST['revision'][:12]
    proxy=app+'-proxy'
    network=next(iter(baseline['NetworkSettings']['Networks']))
    private=ROOT/'preview.private.env'
    with private.open('x') as f:
        private.chmod(0o600);f.write('\n'.join(baseline['Config']['Env'])+'\n')
    try:
        subprocess.run(['docker','run','-d','--name',app,'--network',network,'--memory','512m','--cpus','0.5','--env-file',str(private),IMAGE],check=True,stdout=subprocess.DEVNULL)
    finally:
        private.unlink(missing_ok=True)
    caddy=ROOT/'Caddyfile.preview'
    caddy.write_text('{\n admin off\n auto_https off\n}\nhttp://:8080 {\n handle /api/* {\n  reverse_proxy huipin-prelaunch-api-1:8000\n }\n handle /health/* {\n  reverse_proxy huipin-prelaunch-api-1:8000\n }\n handle {\n  reverse_proxy '+app+':3000\n }\n}\n')
    subprocess.run(['docker','run','-d','--name',proxy,'--network',network,'--memory','64m','--cpus','0.2','-v',str(caddy)+':/etc/caddy/Caddyfile:ro',inspect('huipin-prelaunch-caddy-1')['Image']],check=True,stdout=subprocess.DEVNULL)
    address=inspect(proxy)['NetworkSettings']['Networks'][network]['IPAddress']+':8080'
    for _ in range(30):
        try:
            assert_page('http://'+address,'/',('home-outcome-grid',))
            break
        except Exception:
            time.sleep(1)
    else:
        cleanup();raise RuntimeError('Preview did not become ready')
    print(json.dumps({'preview':'ready','proxy_address':address,'application':app,'proxy':proxy}))


def cleanup():
    for suffix in ['-proxy','']:
        name='huipin-security-preview-'+MANIFEST['revision'][:12]+suffix
        if subprocess.run(['docker','inspect',name],capture_output=True).returncode==0:
            subprocess.run(['docker','rm','-f','-v',name],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['build','test','preview','cleanup','activate']);args=parser.parse_args()
    {'build':build,'test':test,'preview':preview,'cleanup':cleanup,'activate':activate}[args.action]()
