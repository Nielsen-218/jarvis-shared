"""A real process race checks the read/append/write serialization boundary."""
from pathlib import Path
import subprocess
import sys
from core import storage,skill_memory


def test_parallel_processes_retain_all_notebook_revisions():
    code="""import sys
from pathlib import Path
from core import storage,skill_memory
storage.DATA=Path(sys.argv[1])
skill_memory._mine=lambda:'household/learned-procedures-pc.json'
skill_memory.capture('Python','Concurrent notebook procedure','Independently checked procedure number '+sys.argv[2],'owner-authorized process race','Run the process race and count all retained revisions')
"""
    root=Path(__file__).resolve().parents[2]
    processes=[subprocess.Popen([sys.executable,'-c',code,str(storage.DATA),str(i)],cwd=root,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for i in range(6)]
    for process in processes:
        stdout,stderr=process.communicate(timeout=20)
        assert process.returncode==0,stderr.decode(errors='replace')
    rows=skill_memory.entries()
    assert len(rows)==6 and {r['revision'] for r in rows}==set(range(1,7))
