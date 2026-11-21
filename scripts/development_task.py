"""Run development checks and automatically package the instructional output."""
import argparse
import json
from pathlib import Path
from core import storage
from core.development_teaching import complete


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--base',required=True)
    p.add_argument('--checks-json',type=Path,required=True)
    p.add_argument('--outdir',type=Path,required=True)
    p.add_argument('--token-file',type=Path)
    p.add_argument('--data-dir',type=Path)
    p.add_argument('--enforce-spec-update',action='store_true')
    a=p.parse_args()
    if a.data_dir: storage.DATA=a.data_dir.resolve()
    receipt=complete(Path.cwd(),a.spec,a.base,json.loads(a.checks_json.read_text()),a.outdir,
        token_file=a.token_file,enforce_spec_update=a.enforce_spec_update)
    print(json.dumps(receipt,indent=2))
    raise SystemExit(0 if receipt['checks_passed'] else 1)
