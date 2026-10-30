"""Package the committed, bounded frontend release against the captured server image."""
from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
BASE='5c61c979a417c0dc731bfe1f76983d876bf373ee'

def git(*args):
 return subprocess.check_output(['git',*args],cwd=ROOT).decode().splitlines()

def sha(path):
 b=path.read_bytes()
 if path.suffix in ('.ts','.tsx','.css','.mjs','.json'):b=b.replace(b'\r\n',b'\n')
 return hashlib.sha256(b).hexdigest()

def main():
 assert not git('status','--porcelain'),'Commit the scoped source first'
 baseline=json.loads((Path(__file__).parent/'production-baseline.json').read_text())
 changed=git('diff','--name-only',BASE,'HEAD')
 files=[p for p in changed if p.startswith(('app/','components/','lib/','public/images/cases/'))]
 assert files and not any(p.startswith(('backend/','features/')) for p in changed)
 assert all(not Path(p).name.startswith('.env') for p in files)
 smoke=['/images/cases/olay-20261030/replica-01.png','/images/cases/olay-20261030/replica-07.png','/images/cases/olay-20261030/product-01.webp','/images/cases/platform-v3/background-result.png','/images/cases/platform-v3/color-result.png','/images/cases/platform-v2/try-on-result.png']
 manifest={'revision':git('rev-parse','HEAD')[0],'baseline':baseline,'sources':{p:sha(ROOT/p) for p in files},'public_smoke_assets':{p:sha(ROOT/'public'/p.lstrip('/')) for p in smoke}}
 docker=['FROM '+baseline['image'],'USER root']+[f'COPY --chown=1000:1000 {p} /app/{p}' for p in files]+['USER 1000:1000','RUN cp -a /app/dist/client/_polaris-static /tmp/previous-static && npm run build && cp -an /tmp/previous-static/. /app/dist/client/_polaris-static/']
 out=ROOT/'release-package/showcase-cases-20261030';out.mkdir(parents=True,exist_ok=True)
 with tarfile.open(out/'source.tar.gz','w:gz') as archive:
  for name in files:archive.add(ROOT/name,arcname=name)
  archive.add(Path(__file__).parent/'release.py',arcname='release.py')
  for name,value in [('frontend.Dockerfile','\n'.join(docker)+'\n'),('release-manifest.json',json.dumps(manifest,indent=2))]:
   data=value.encode();info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
 (out/'release-manifest.json').write_text(json.dumps(manifest,indent=2))
 print(json.dumps({'archive':str(out/'source.tar.gz'),'revision':manifest['revision'],'source_files':len(files),'package_bytes':(out/'source.tar.gz').stat().st_size}))

if __name__=='__main__':main()
