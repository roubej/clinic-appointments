# Clinic Appointments (Cassandra + Streamlit)

A small synthetic appointment app with a **doctor portal** and a **patient portal**.
Patients can create an account and patient profile. Doctor accounts are provisioned
separately by an administrator. All data is synthetic and times are UTC.

## Run it

```bash
pip install -r requirements.txt

docker compose up -d     # wait ~1 minute until `docker compose ps` says healthy
python init_db.py        # creates the `clinic` keyspace and tables
python seed.py           # 10 doctors, 30 patients, past visits, future appointments

streamlit run app.py     # opens http://localhost:8501
```

If the earlier `hospital-cassandra` container is still running, skip `docker compose up`.
This app uses its own keyspace (`clinic`) in the same Cassandra, so nothing clashes.
Starting both would fail because they use the same port (9042).

Start over with fresh data: `python seed.py --reset`.

## Deploy a synthetic demo

The app can use local Docker Cassandra or a hosted DataStax Astra DB. Keep the
Astra token and secure-connect bundle out of Git.

1. Create an Astra DB and a keyspace named `clinic`. Create a database token and
   download its secure-connect bundle.
2. Set these environment variables locally, then initialize and seed Astra:

   ```powershell
   $env:CASSANDRA_SECURE_CONNECT_BUNDLE = "C:\path\to\secure-connect.zip"
   $env:CASSANDRA_TOKEN = Read-Host "Astra database token"
   .\.venv311\Scripts\python.exe init_db.py
   .\.venv311\Scripts\python.exe seed.py
   ```

3. Encode the bundle for Streamlit secrets:

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes($env:CASSANDRA_SECURE_CONNECT_BUNDLE)) | Set-Clipboard
   ```

4. Deploy this repository on [Streamlit Community Cloud](https://share.streamlit.io):
   choose `main` and `app.py`, and select **Python 3.11** in Advanced settings.
   Add these secrets in the app settings, replacing the placeholders:

   ```toml
   [astra]
   token = "your Astra database token"
   secure_connect_bundle_b64 = """paste the bundle's base64 text here"""
   ```

5. To provision a doctor account in the hosted database, set the same two
   environment variables locally and run `python create_doctor_account.py`.

The app's login is intended for synthetic demo data only, not real patient data.
Check the current Astra plan and pricing before creating a database.

## What it does

**Login**
- Patient signup creates a new patient profile and account
- Provision doctor accounts with `python create_doctor_account.py`
- Passwords are stored as salted hashes; there is no password recovery

**Doctor portal**
- Shows the doctor's name and department
- Lists patients linked to the doctor through visits or appointments
- Shows the selected patient's details and visit history
- Lets the doctor add a visit summary and notes
- Lists the doctor's booked appointments for the next 30 days

**Patient portal**
- Lists doctors by department
- Shows free half-hour slots from 09:00 to 16:30 UTC
- Books an appointment (a slot can never be booked twice)
- Shows the patient's upcoming appointments

## Data model (one table per query)

| Table | Primary key | Used for |
|---|---|---|
| `patients` | `patient_id` | patient details |
| `doctors` | `doctor_id` | doctor sign-in list |
| `doctors_by_department` | `((department), doctor_id)` | patient portal doctor list |
| `patients_by_doctor` | `((doctor_id), patient_id)` | doctor's linked patients |
| `visits_by_patient` | `((patient_id), visit_time DESC, visit_id)` | visit history |
| `appointments_by_doctor` | `((doctor_id), slot_time)` | free slots, 30-day calendar, double-booking guard |
| `appointments_by_patient` | `((patient_id), slot_time, doctor_id)` | patient's upcoming appointments |
| `accounts_by_username` | `username` | salted password hashes and account identity |
| `account_by_identity` | `((role, identity_id))` | prevents multiple accounts for one identity |

Saving a visit or booking an appointment writes to several tables in one batch
(the visit or appointment itself, plus `patients_by_doctor`). That is normal Cassandra
practice: copy data on write so every screen needs just one simple query.

Booking uses `INSERT ... IF NOT EXISTS` on `(doctor_id, slot_time)`. To see it work,
open the app in two browser tabs, pick the same doctor and slot, and book in both.
The second booking is refused.

## Files

- `schema.cql`, `init_db.py`: tables
- `db.py`: all Cassandra queries
- `seed.py`: synthetic data
- `create_doctor_account.py`: provision doctor logins
- `app.py`: the two portals
