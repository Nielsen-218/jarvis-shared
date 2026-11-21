"""Run one approved studio work order to the end (started detached by core.studio.spawn; see core/studiojob.py).

    python -m scripts.studio_job WO-3
"""
import sys

from dotenv import load_dotenv

from core.config import ROOT


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    for stream in (sys.stdout, sys.stderr):              # the job log is a redirected file: never die on a non-ASCII line
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    load_dotenv(ROOT / ".env")
    from core import studiojob
    order = studiojob.run(argv[0])
    print(order["state"], order.get("report", ""), flush=True)
    return 0 if order["state"] == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
