"""Fill Cassandra with synthetic doctors, patients, visits and appointments.

Usage:
    python seed.py            # seed an empty database
    python seed.py --reset    # wipe all tables first, then seed
"""
from __future__ import annotations

import argparse
import random
from datetime import timedelta

from faker import Faker

from db import DEPARTMENTS, SLOT_TIMES, Clinic, utc_now
from datetime import datetime

SUMMARIES = [
    "Routine check-up, no concerns",
    "Blood pressure follow-up",
    "Persistent cough, chest clear",
    "Migraine, advised rest and hydration",
    "Back pain, referred to physiotherapy",
    "Seasonal allergies, antihistamine prescribed",
    "Diabetes review, medication adjusted",
    "Stomach pain, dietary advice given",
]
REASONS = ["Follow-up", "Routine check-up", "Lab results review", "New symptoms",
           "Prescription renewal"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", type=int, default=30)
    ap.add_argument("--reset", action="store_true", help="wipe all tables first")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)
    fake = Faker()
    clinic = Clinic()

    if args.reset:
        clinic.truncate_all()
        print("Tables wiped.")
    elif clinic.list_doctors():
        print("Database already has data. Run `python seed.py --reset` to start over.")
        return

    doctors = [clinic.add_doctor(f"Dr. {fake.first_name()} {fake.last_name()}", dept)
               for dept in DEPARTMENTS for _ in range(2)]
    print(f"Created {len(doctors)} doctors")

    patients = []
    for _ in range(args.patients):
        gender = random.choice(["F", "M"])
        first = fake.first_name_female() if gender == "F" else fake.first_name_male()
        dob = (utc_now() - timedelta(days=random.randint(1 * 365, 90 * 365))).date()
        patients.append(clinic.add_patient(f"{first} {fake.last_name()}", dob, gender,
                                           fake.phone_number()))
    print(f"Created {len(patients)} patients")

    visits = 0
    for p in patients:
        for _ in range(random.randint(1, 3)):
            when = utc_now() - timedelta(days=random.randint(1, 365), hours=random.randint(0, 8))
            clinic.add_visit(p, random.choice(doctors), random.choice(SUMMARIES),
                             fake.sentence(nb_words=12), when)
            visits += 1
    print(f"Created {visits} past visits")

    booked = 0
    for _ in range(60):
        day = (utc_now() + timedelta(days=random.randint(1, 30))).date()
        slot = datetime.combine(day, random.choice(SLOT_TIMES))
        if clinic.book_appointment(random.choice(patients), random.choice(doctors), slot,
                                   random.choice(REASONS)):
            booked += 1
    print(f"Booked {booked} future appointments")

    clinic.cluster.shutdown()
    print("\nDone. Next: streamlit run app.py")


if __name__ == "__main__":
    main()
