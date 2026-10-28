"""Package the native replica overlay, including both complete pinned Skill trees."""
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
output = root / "outputs" / "native-candidate-source.tar.gz"
directories = ["backend/app", "backend/migrations", "backend/source_manifests",
    "backend/skill_sources/aurora-ecommerce-reference-remix",
    "backend/skill_sources/all-category-style-benchmark-still", "app", "components", "lib", "features"]
files = ["backend/alembic.ini", "backend/scripts/replica_native_live.py",
    "deploy/validate_replica_native.py", "deploy/release_replica_native.py",
    "deploy/replica-backend.Dockerfile", "deploy/replica-frontend.Dockerfile",
    "deploy/inspiration-preview.Dockerfile"]
output.parent.mkdir(exist_ok=True)
with tarfile.open(output, "w:gz") as archive:
    for relative in directories:
        for path in sorted((root / relative).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                assert not path.name.startswith(".env")
                archive.add(path, arcname=path.relative_to(root).as_posix())
    for relative in files:
        archive.add(root / relative, arcname=relative)
print(output.name, output.stat().st_size)
