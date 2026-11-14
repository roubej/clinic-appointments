"""Create the keyspace and tables from schema.cql.  Usage: python init_db.py"""
from __future__ import annotations

import pathlib

from db import KEYSPACE, make_cluster


def load_statements() -> list[str]:
    text = (pathlib.Path(__file__).parent / "schema.cql").read_text()
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main() -> None:
    cluster = make_cluster()
    session = cluster.connect()
    for stmt in load_statements():
        session.execute(stmt)
        if stmt.upper().startswith("CREATE KEYSPACE"):
            session.set_keyspace(KEYSPACE)
        print("OK:", " ".join(stmt.split())[:70])
    cluster.shutdown()
    print("\nSchema ready.")


if __name__ == "__main__":
    main()
