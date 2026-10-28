"""Build a clean distributable .skill archive."""
import argparse, zipfile
from pathlib import Path
ap=argparse.ArgumentParser(); ap.add_argument('source'); ap.add_argument('--output',required=True); a=ap.parse_args(); src=Path(a.source).resolve(); out=Path(a.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for p in src.rglob('*'):
        if p.is_file() and not any(part in ('__pycache__','.pytest_cache','.git') for part in p.relative_to(src).parts) and p.suffix not in ('.pyc',): z.write(p,p.relative_to(src))
print(out)
