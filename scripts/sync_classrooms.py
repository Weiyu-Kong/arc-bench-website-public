#!/usr/bin/env python3
"""Reconcile configured classes with registered accounts on the deployed server.

Run after arcbench-migrate. The API also reconciles at startup/registration.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import SessionLocal
from app.services.classroom_service import ClassroomService


def main():
    with SessionLocal() as db:
        print(json.dumps(ClassroomService(db).sync_rosters(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
