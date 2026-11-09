"""Create a reproducible source release without databases, sessions or cache."""
import os
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

root=Path(__file__).resolve().parent
target=root.parent/'game-insight-local.zip'
temporary=target.with_suffix('.zip.tmp')
allowed={'server.py','data.py','schema.sql','README.md','requirements.txt','start.cmd','start.sh','Dockerfile','compose.yaml','.dockerignore','.gitignore','package_release.py'}
with ZipFile(temporary,'w',ZIP_DEFLATED,compresslevel=9) as archive:
    for file in sorted(root.rglob('*')):
        if not file.is_file(): continue
        rel=file.relative_to(root)
        if any(part in ('__pycache__','.qa','.git','data') for part in rel.parts): continue
        if len(rel.parts)==1 and rel.name not in allowed: continue
        if len(rel.parts)>1 and rel.parts[0] not in ('web','docs','tests'): continue
        name=(Path('game-insight')/rel).as_posix()
        info=ZipInfo(name,date_time=(2026,11,9,0,0,0))
        info.compress_type=ZIP_DEFLATED
        info.external_attr=((0o755 if rel.as_posix()=='start.sh' else 0o644) << 16)
        archive.writestr(info,file.read_bytes(),compress_type=ZIP_DEFLATED,compresslevel=9)
os.replace(temporary,target)
print(f'Release: {target} ({target.stat().st_size:,} bytes)')
