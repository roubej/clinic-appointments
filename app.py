"""Clinic appointments: login-protected doctor and patient portals on Cassandra.

Run:  streamlit run app.py
All data and identities are synthetic. Self-registration does not verify identity.
Times are UTC.
"""
from __future__ import annotations

import base64
import tempfile
from datetime import date

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from db import DEPARTMENTS, Clinic, utc_now

st.set_page_config(page_title="Clinic Appointments", page_icon="🏥", layout="wide")


@st.cache_resource(show_spinner="Connecting to Cassandra...")
def get_clinic() -> Clinic:
    try:
        astra = st.secrets["astra"]
    except (KeyError, StreamlitSecretNotFoundError):
        return Clinic()

    encoded_bundle = "".join(astra["secure_connect_bundle_b64"].split())
    bundle = base64.b64decode(encoded_bundle, validate=True)
    with tempfile.NamedTemporaryFile(prefix="clinic-astra-", suffix=".zip", delete=False) as file:
        file.write(bundle)
    return Clinic(secure_connect_bundle=file.name, token=astra["token"])


try:
    clinic = get_clinic()
except Exception as exc:  # noqa: BLE001
    st.error(f"Cannot connect to Cassandra: {exc}\n\n"
             "For local development, start Cassandra with `docker compose up -d`. "
             "For Astra DB, configure the `[astra]` Streamlit secrets.")
    st.stop()


# ------------------------------------------------------------- doctor portal
def doctor_portal(doctor_id) -> None:
    rows = clinic.list_doctors()
    row = next((doctor for doctor in rows if doctor.doctor_id == doctor_id), None)
    if row is None:
        st.error("This doctor account no longer matches an available doctor.")
        return
    doctor = {"doctor_id": row.doctor_id, "name": row.name, "department": row.department}

    st.title(doctor["name"])
    st.caption(f"Department: {doctor['department']}")
    patients_tab, appts_tab = st.tabs(["My patients", "Appointments (next 30 days)"])

    with patients_tab:
        links = clinic.patients_of_doctor(doctor["doctor_id"])
        if not links:
            st.info("No patients are linked to you yet.")
        else:
            choices = {f"{link.patient_name} [{str(link.patient_id)[:8]}]": link.patient_id
                       for link in sorted(links, key=lambda x: x.patient_name)}
            st.caption(f"{len(choices)} patients linked through visits or appointments")
            pid = choices[st.selectbox("Patient", list(choices))]
            p = clinic.get_patient(pid)
            patient = {"patient_id": pid, "name": p.name}
            st.markdown(f"**{p.name}** · born {p.dob} · {p.gender} · {p.phone}")

            st.subheader("Add a visit")
            with st.form("visit_form", clear_on_submit=True):
                summary = st.text_input("Visit summary")
                notes = st.text_area("Notes")
                if st.form_submit_button("Save visit"):
                    if not summary.strip():
                        st.warning("Please enter a visit summary.")
                    else:
                        clinic.add_visit(patient, doctor, summary.strip(), notes.strip())
                        st.success("Visit saved.")

            st.subheader("Visit history")
            visits = clinic.visits_of_patient(pid)
            if visits:
                st.dataframe(pd.DataFrame([{
                    "Date (UTC)": v.visit_time.strftime("%Y-%m-%d %H:%M"),
                    "Doctor": v.doctor_name, "Summary": v.summary, "Notes": v.notes}
                    for v in visits]), hide_index=True)
            else:
                st.caption("No visits recorded yet.")

    with appts_tab:
        appts = clinic.doctor_appointments(doctor["doctor_id"], days=30)
        if not appts:
            st.info("No booked appointments in the next 30 days.")
        else:
            st.caption(f"{len(appts)} booked appointments")
            st.dataframe(pd.DataFrame([{
                "Date": a.slot_time.strftime("%Y-%m-%d"), "Time (UTC)": a.slot_time.strftime("%H:%M"),
                "Patient": a.patient_name, "Reason": a.reason} for a in appts]),
                hide_index=True)


# ------------------------------------------------------------ patient portal
def patient_portal(patient_id) -> None:
    row = clinic.get_patient(patient_id)
    if row is None:
        st.error("This patient account no longer matches an available patient.")
        return
    patient = {"patient_id": row.patient_id, "name": row.name}

    st.title(f"Welcome, {row.name}")
    book_tab, mine_tab = st.tabs(["Book an appointment", "My upcoming appointments"])

    with book_tab:
        flash = st.session_state.pop("flash", None)
        if flash:
            st.success(flash)

        dept = st.selectbox("Department", DEPARTMENTS)
        docs = clinic.doctors_in_department(dept)
        if not docs:
            st.info("No doctors in this department.")
        else:
            by_name = {d.name: d for d in sorted(docs, key=lambda d: d.name)}
            d = by_name[st.selectbox("Doctor", list(by_name))]
            doctor = {"doctor_id": d.doctor_id, "name": d.name, "department": dept}
            day = st.date_input("Date", value=utc_now().date(), min_value=utc_now().date())
            free = clinic.free_slots(doctor["doctor_id"], day)
            if not free:
                st.warning("No free slots on this date. Try another day.")
            else:
                slot = st.selectbox("Available time (UTC)", free,
                                    format_func=lambda s: s.strftime("%H:%M"))
                reason = st.text_input("Reason for visit", value="General consultation")
                if st.button("Book appointment", type="primary"):
                    if clinic.book_appointment(patient, doctor, slot, reason.strip() or "Visit"):
                        st.session_state["flash"] = (
                            f"Booked with {doctor['name']} on {slot:%Y-%m-%d} at {slot:%H:%M} UTC.")
                        st.rerun()
                    else:
                        st.error("Sorry, that slot was just taken. Please pick another time.")

    with mine_tab:
        appts = clinic.patient_upcoming(patient["patient_id"])
        if not appts:
            st.info("You have no upcoming appointments.")
        else:
            st.dataframe(pd.DataFrame([{
                "Date": a.slot_time.strftime("%Y-%m-%d"), "Time (UTC)": a.slot_time.strftime("%H:%M"),
                "Doctor": a.doctor_name, "Department": a.department, "Reason": a.reason}
                for a in appts]), hide_index=True)


def login_page() -> None:
    st.title("Clinic Appointments")
    st.caption("Sign in or create a patient account.")

    login_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            account = clinic.authenticate(username, password)
            if account is None:
                st.error("Invalid username or password.")
            else:
                st.session_state["account"] = account
                st.rerun()

    with register_tab:
        with st.form("register_form"):
            name = st.text_input("Full name")
            dob = st.date_input(
                "Date of birth",
                value=date(1990, 1, 1),
                min_value=date(1900, 1, 1),
                max_value=utc_now().date(),
            )
            gender = st.selectbox("Gender", ["F", "M", "Other", "Prefer not to say"])
            phone = st.text_input("Phone")
            new_username = st.text_input("Username")
            new_password = st.text_input("Password (at least 10 characters)", type="password")
            confirm_password = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account")
        if submitted:
            if new_password != confirm_password:
                st.error("Passwords do not match.")
            else:
                created, message, account = clinic.register_patient_account(
                    new_username, new_password, name, dob, gender, phone
                )
                if created:
                    st.session_state["account"] = account
                    st.rerun()
                else:
                    st.error(message)


# ------------------------------------------------------------------- router
account = st.session_state.get("account")
if account is None:
    login_page()
else:
    st.sidebar.title("🏥 Clinic")
    st.sidebar.write(f"Signed in as **{account['username']}**")
    st.sidebar.caption(f"{account['role'].title()} account")
    if st.sidebar.button("Log out"):
        del st.session_state["account"]
        st.rerun()
    if account["role"] == "doctor":
        doctor_portal(account["identity_id"])
    else:
        patient_portal(account["identity_id"])
