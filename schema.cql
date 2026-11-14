-- Clinic appointment app. One table per query.

CREATE KEYSPACE IF NOT EXISTS clinic
  WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

-- Patient profiles
CREATE TABLE IF NOT EXISTS patients (
  patient_id uuid PRIMARY KEY,
  name text,
  dob date,
  gender text,
  phone text
);

-- Doctor profiles
CREATE TABLE IF NOT EXISTS doctors (
  doctor_id uuid PRIMARY KEY,
  name text,
  department text
);

-- Patient portal: list doctors by department
CREATE TABLE IF NOT EXISTS doctors_by_department (
  department text,
  doctor_id uuid,
  name text,
  PRIMARY KEY ((department), doctor_id)
);

-- Doctor portal: patients linked to a doctor.
-- Written whenever a visit is saved or an appointment is booked.
CREATE TABLE IF NOT EXISTS patients_by_doctor (
  doctor_id uuid,
  patient_id uuid,
  patient_name text,
  PRIMARY KEY ((doctor_id), patient_id)
);

-- Visit history of a patient, newest first
CREATE TABLE IF NOT EXISTS visits_by_patient (
  patient_id uuid,
  visit_time timestamp,
  visit_id uuid,
  doctor_id uuid,
  doctor_name text,
  summary text,
  notes text,
  PRIMARY KEY ((patient_id), visit_time, visit_id)
) WITH CLUSTERING ORDER BY (visit_time DESC, visit_id ASC);

-- A doctor's calendar. The pair (doctor_id, slot_time) is unique, which is what
-- lets INSERT ... IF NOT EXISTS prevent double booking.
CREATE TABLE IF NOT EXISTS appointments_by_doctor (
  doctor_id uuid,
  slot_time timestamp,
  patient_id uuid,
  patient_name text,
  reason text,
  PRIMARY KEY ((doctor_id), slot_time)
);

-- A patient's appointments
CREATE TABLE IF NOT EXISTS appointments_by_patient (
  patient_id uuid,
  slot_time timestamp,
  doctor_id uuid,
  doctor_name text,
  department text,
  reason text,
  PRIMARY KEY ((patient_id), slot_time, doctor_id)
);

-- Self-registered demo accounts. The identity table ensures one account per person.
CREATE TABLE IF NOT EXISTS accounts_by_username (
  username text PRIMARY KEY,
  role text,
  identity_id uuid,
  password_salt blob,
  password_hash blob
);

CREATE TABLE IF NOT EXISTS account_by_identity (
  role text,
  identity_id uuid,
  username text,
  PRIMARY KEY ((role, identity_id))
);
