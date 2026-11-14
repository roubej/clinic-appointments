"""Cassandra data-access layer for the clinic appointment app.

All times are naive UTC datetimes (the Cassandra driver stores naive
datetimes as UTC), so what you see in the app is always UTC.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster
from cassandra.query import BatchStatement, BatchType

KEYSPACE = "clinic"
DEPARTMENTS = ["Cardiology", "Pediatrics", "Orthopedics", "Neurology", "General Medicine"]
# Half-hour slots from 09:00 to 16:30 UTC
SLOT_TIMES = [time(h, m) for h in range(9, 17) for m in (0, 30)]
TABLES = ["patients", "doctors", "doctors_by_department", "patients_by_doctor",
          "visits_by_patient", "appointments_by_doctor", "appointments_by_patient",
          "accounts_by_username", "account_by_identity"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


QUERIES = {
    "insert_patient": "INSERT INTO patients (patient_id, name, dob, gender, phone) "
                      "VALUES (?, ?, ?, ?, ?)",
    "get_patient": "SELECT * FROM patients WHERE patient_id = ?",
    "delete_patient": "DELETE FROM patients WHERE patient_id = ?",
    "list_patients": "SELECT patient_id, name FROM patients LIMIT ?",
    "insert_doctor": "INSERT INTO doctors (doctor_id, name, department) VALUES (?, ?, ?)",
    "insert_doctor_dept": "INSERT INTO doctors_by_department (department, doctor_id, name) "
                          "VALUES (?, ?, ?)",
    "list_doctors": "SELECT doctor_id, name, department FROM doctors",
    "doctors_by_dept": "SELECT doctor_id, name FROM doctors_by_department WHERE department = ?",
    "link": "INSERT INTO patients_by_doctor (doctor_id, patient_id, patient_name) "
            "VALUES (?, ?, ?)",
    "doctor_patients": "SELECT patient_id, patient_name FROM patients_by_doctor "
                       "WHERE doctor_id = ?",
    "insert_visit": "INSERT INTO visits_by_patient (patient_id, visit_time, visit_id, doctor_id, "
                    "doctor_name, summary, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
    "patient_visits": "SELECT visit_time, doctor_name, summary, notes FROM visits_by_patient "
                      "WHERE patient_id = ? LIMIT 50",
    "book_slot": "INSERT INTO appointments_by_doctor (doctor_id, slot_time, patient_id, "
                 "patient_name, reason) VALUES (?, ?, ?, ?, ?) IF NOT EXISTS",
    "insert_appt_patient": "INSERT INTO appointments_by_patient (patient_id, slot_time, "
                           "doctor_id, doctor_name, department, reason) VALUES (?, ?, ?, ?, ?, ?)",
    "taken_slots": "SELECT slot_time FROM appointments_by_doctor "
                   "WHERE doctor_id = ? AND slot_time >= ? AND slot_time <= ?",
    "doctor_appts": "SELECT slot_time, patient_name, reason FROM appointments_by_doctor "
                    "WHERE doctor_id = ? AND slot_time >= ? AND slot_time < ?",
    "patient_upcoming": "SELECT slot_time, doctor_name, department, reason "
                        "FROM appointments_by_patient WHERE patient_id = ? AND slot_time >= ?",
    "account_by_username": "SELECT username, role, identity_id, password_salt, password_hash "
                           "FROM accounts_by_username WHERE username = ?",
    "insert_account": "INSERT INTO accounts_by_username "
                      "(username, role, identity_id, password_salt, password_hash) "
                      "VALUES (?, ?, ?, ?, ?) IF NOT EXISTS",
    "identity_account": "SELECT username FROM account_by_identity "
                        "WHERE role = ? AND identity_id = ?",
    "claim_identity": "INSERT INTO account_by_identity (role, identity_id, username) "
                      "VALUES (?, ?, ?) IF NOT EXISTS",
    "release_identity": "DELETE FROM account_by_identity WHERE role = ? AND identity_id = ? "
                        "IF username = ?",
}

_USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}\Z")
_PASSWORD_MIN_LENGTH = 10
_PASSWORD_MAX_LENGTH = 128


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)


def make_cluster(
    secure_connect_bundle: str | None = None,
    token: str | None = None,
) -> Cluster:
    secure_connect_bundle = secure_connect_bundle or os.getenv("CASSANDRA_SECURE_CONNECT_BUNDLE")
    token = token or os.getenv("CASSANDRA_TOKEN")
    if secure_connect_bundle:
        if not token:
            raise ValueError("CASSANDRA_TOKEN is required with a secure connect bundle.")
        return Cluster(
            cloud={
                "secure_connect_bundle": secure_connect_bundle,
                "use_default_tempdir": True,
            },
            auth_provider=PlainTextAuthProvider(username="token", password=token),
        )
    if token:
        raise ValueError("CASSANDRA_SECURE_CONNECT_BUNDLE is required with CASSANDRA_TOKEN.")

    hosts = os.getenv("CASSANDRA_HOSTS", "127.0.0.1").split(",")
    port = int(os.getenv("CASSANDRA_PORT", "9042"))
    kwargs = {}
    try:  # asyncore was removed in Python 3.12, fall back to the asyncio reactor
        import asyncore  # noqa: F401
    except ModuleNotFoundError:
        from cassandra.io.asyncioreactor import AsyncioConnection

        kwargs["connection_class"] = AsyncioConnection
    return Cluster(hosts, port=port, **kwargs)


class Clinic:
    def __init__(
        self,
        secure_connect_bundle: str | None = None,
        token: str | None = None,
    ) -> None:
        self.cluster = make_cluster(secure_connect_bundle, token)
        self.session = self.cluster.connect(KEYSPACE)
        self.stmts = {k: self.session.prepare(q) for k, q in QUERIES.items()}

    def _rows(self, key: str, params: tuple = ()) -> list:
        return list(self.session.execute(self.stmts[key], params))

    def _batch(self) -> BatchStatement:
        return BatchStatement(BatchType.LOGGED)

    # ------------------------------------------------------------ people
    def add_patient(self, name: str, dob: date, gender: str, phone: str) -> dict:
        pid = uuid.uuid4()
        self.session.execute(self.stmts["insert_patient"], (pid, name, dob, gender, phone))
        return {"patient_id": pid, "name": name}

    def add_doctor(self, name: str, department: str) -> dict:
        did = uuid.uuid4()
        batch = self._batch()
        batch.add(self.stmts["insert_doctor"], (did, name, department))
        batch.add(self.stmts["insert_doctor_dept"], (department, did, name))
        self.session.execute(batch)
        return {"doctor_id": did, "name": name, "department": department}

    def get_patient(self, patient_id):
        rows = self._rows("get_patient", (patient_id,))
        return rows[0] if rows else None

    def authenticate(self, username: str, password: str) -> dict | None:
        normalized_username = username.strip().lower()
        rows = self._rows("account_by_username", (normalized_username,))
        if not rows:
            return None
        account = rows[0]
        candidate = _password_hash(password, account.password_salt)
        if not hmac.compare_digest(candidate, account.password_hash):
            return None
        return {
            "username": account.username,
            "role": account.role,
            "identity_id": account.identity_id,
        }

    def register_account(self, username: str, password: str, role: str,
                         identity_id: uuid.UUID) -> tuple[bool, str]:
        normalized_username = username.strip().lower()
        if not _USERNAME_PATTERN.fullmatch(normalized_username):
            return False, "Use a 3-32 character username with letters, numbers, '.', '_' or '-'."
        if not _PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH:
            return False, f"Password must be {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_LENGTH} characters."
        if role not in {"patient", "doctor"}:
            raise ValueError("Invalid account role")

        identity_result = self.session.execute(
            self.stmts["claim_identity"], (role, identity_id, normalized_username)
        ).one()
        if not identity_result[0]:
            existing_identity = self._rows("identity_account", (role, identity_id))[0]
            if existing_identity.username != normalized_username:
                return False, "This patient or doctor already has an account."

        existing = self._rows("account_by_username", (normalized_username,))
        if existing:
            account = existing[0]
            if account.role == role and account.identity_id == identity_id:
                return False, "This account already exists. Please sign in."
            self.session.execute(
                self.stmts["release_identity"], (role, identity_id, normalized_username)
            )
            return False, "That username is already in use."

        salt = secrets.token_bytes(16)
        result = self.session.execute(
            self.stmts["insert_account"],
            (normalized_username, role, identity_id, salt, _password_hash(password, salt)),
        ).one()
        if not result[0]:
            existing = self._rows("account_by_username", (normalized_username,))
            if existing and existing[0].role == role and existing[0].identity_id == identity_id:
                return False, "This account already exists. Please sign in."
            self.session.execute(
                self.stmts["release_identity"], (role, identity_id, normalized_username)
            )
            return False, "That username is already in use."
        return True, "Account created."

    def register_patient_account(self, username: str, password: str, name: str, dob: date,
                                 gender: str, phone: str) -> tuple[bool, str, dict | None]:
        if not name.strip() or not phone.strip():
            return False, "Enter your name and phone number.", None
        if dob > utc_now().date():
            return False, "Date of birth cannot be in the future.", None
        normalized_username = username.strip().lower()
        if not _USERNAME_PATTERN.fullmatch(normalized_username):
            return False, "Use a 3-32 character username with letters, numbers, '.', '_' or '-'.", None
        if not _PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH:
            return False, f"Password must be {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_LENGTH} characters.", None
        if self._rows("account_by_username", (normalized_username,)):
            return False, "That username is already in use.", None

        patient = self.add_patient(name.strip(), dob, gender, phone.strip())
        try:
            created, message = self.register_account(
                normalized_username, password, "patient", patient["patient_id"]
            )
        except Exception:
            self.session.execute(self.stmts["delete_patient"], (patient["patient_id"],))
            raise
        if not created:
            self.session.execute(self.stmts["delete_patient"], (patient["patient_id"],))
            return False, message, None
        return True, message, {
            "username": normalized_username,
            "role": "patient",
            "identity_id": patient["patient_id"],
        }

    def list_patients(self, limit: int = 200) -> list:
        return self._rows("list_patients", (limit,))

    def list_doctors(self) -> list:
        return self._rows("list_doctors")

    def doctors_in_department(self, department: str) -> list:
        return self._rows("doctors_by_dept", (department,))

    # ----------------------------------------------------- doctor portal
    def patients_of_doctor(self, doctor_id) -> list:
        return self._rows("doctor_patients", (doctor_id,))

    def visits_of_patient(self, patient_id) -> list:
        return self._rows("patient_visits", (patient_id,))

    def add_visit(self, patient: dict, doctor: dict, summary: str, notes: str,
                  visit_time: datetime | None = None) -> None:
        """Save a visit and make sure the patient is linked to the doctor."""
        batch = self._batch()
        batch.add(self.stmts["insert_visit"],
                  (patient["patient_id"], visit_time or utc_now(), uuid.uuid4(),
                   doctor["doctor_id"], doctor["name"], summary, notes))
        batch.add(self.stmts["link"], (doctor["doctor_id"], patient["patient_id"], patient["name"]))
        self.session.execute(batch)

    def doctor_appointments(self, doctor_id, days: int = 30) -> list:
        now = utc_now()
        return self._rows("doctor_appts", (doctor_id, now, now + timedelta(days=days)))

    # ---------------------------------------------------- patient portal
    def free_slots(self, doctor_id, day: date) -> list[datetime]:
        """Half-hour slots 09:00-16:30 UTC on `day` that are free and in the future."""
        all_slots = [datetime.combine(day, t) for t in SLOT_TIMES]
        taken = {r.slot_time for r in
                 self._rows("taken_slots", (doctor_id, all_slots[0], all_slots[-1]))}
        now = utc_now()
        return [s for s in all_slots if s not in taken and s > now]

    def book_appointment(self, patient: dict, doctor: dict, slot: datetime, reason: str) -> bool:
        """Returns False if the slot is already taken. Uses a lightweight
        transaction (IF NOT EXISTS), so two people can never get the same slot."""
        if slot.time() not in SLOT_TIMES or slot <= utc_now():
            raise ValueError("Invalid appointment time")
        result = self.session.execute(
            self.stmts["book_slot"],
            (doctor["doctor_id"], slot, patient["patient_id"], patient["name"], reason))
        if not result.one()[0]:  # the [applied] column
            return False
        batch = self._batch()
        batch.add(self.stmts["insert_appt_patient"],
                  (patient["patient_id"], slot, doctor["doctor_id"], doctor["name"],
                   doctor["department"], reason))
        batch.add(self.stmts["link"], (doctor["doctor_id"], patient["patient_id"], patient["name"]))
        self.session.execute(batch)
        return True

    def patient_upcoming(self, patient_id) -> list:
        return self._rows("patient_upcoming", (patient_id, utc_now()))

    # ------------------------------------------------------------- admin
    def truncate_all(self) -> None:
        for table in TABLES:
            self.session.execute(f"TRUNCATE {table}")
