"""Seed a single anonymized, consented participant for a pilot session.

    python scripts/seed.py [ANON_CODE]

Prints the generated participant id to use in /api/run and /api/sol/turn.
Requires STORE_BACKEND=sql.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.store.db import SessionLocal, init_db  # noqa: E402
from app.store.models import Participant  # noqa: E402


def main():
    anon_code = sys.argv[1] if len(sys.argv) > 1 else "anon-" + uuid.uuid4().hex[:6]
    init_db()
    pid = "p_" + uuid.uuid4().hex[:12]
    with SessionLocal() as s:
        s.add(Participant(id=pid, anon_code=anon_code, consent=True))
        s.commit()
    print(f"participant_id={pid}  anon_code={anon_code}  consent=True")


if __name__ == "__main__":
    main()
