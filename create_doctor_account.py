"""Provision a doctor login from the command line.

Usage: python create_doctor_account.py
"""
from __future__ import annotations

from getpass import getpass

from db import Clinic


def main() -> None:
    clinic = Clinic()
    try:
        doctors = sorted(clinic.list_doctors(), key=lambda doctor: doctor.name)
        if not doctors:
            print("No doctors found. Run `python seed.py` first.")
            return

        for index, doctor in enumerate(doctors, start=1):
            print(f"{index}. {doctor.name} ({doctor.department})")
        try:
            selection = int(input("Choose a doctor number: "))
        except ValueError:
            print("Enter a valid doctor number.")
            return
        if not 1 <= selection <= len(doctors):
            print("Doctor number is out of range.")
            return

        username = input("Username: ")
        password = getpass("Password (at least 10 characters): ")
        if password != getpass("Confirm password: "):
            print("Passwords do not match.")
            return

        doctor = doctors[selection - 1]
        created, message = clinic.register_account(
            username, password, "doctor", doctor.doctor_id
        )
        print(message)
        if created:
            print(f"Doctor account created for {doctor.name}.")
    finally:
        clinic.cluster.shutdown()


if __name__ == "__main__":
    main()
