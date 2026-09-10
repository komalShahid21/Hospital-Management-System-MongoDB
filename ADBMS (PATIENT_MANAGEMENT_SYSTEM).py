import os
import re
import datetime as dt
import tkinter as tk

import customtkinter as ctk
from pymongo import MongoClient
from pymongo.errors import PyMongoError


# -----------------------------
# MongoDB connection
# -----------------------------
# Set your MongoDB Atlas connection string as an environment variable named MONGODB_URI.
# Example connection string format:
#   mongodb+srv://<USERNAME>:<PASSWORD>@<cluster>.mongodb.net/?retryWrites=true&w=majority
MONGODB_URI = os.environ.get("MONGODB_URI")

if not MONGODB_URI:
    raise SystemExit(
        "MONGODB_URI is not set.\n"
        "Set it to your MongoDB Atlas connection string and run again.\n\n"
        "Example (macOS/Linux):\n"
        "  export MONGODB_URI=\"mongodb+srv://<USER>:<PASS>@<cluster>.mongodb.net/?retryWrites=true&w=majority\"\n"
        "  python3 app.py\n"
        "Example (Windows PowerShell):\n"
        "  $env:MONGODB_URI=\"mongodb+srv://<USER>:<PASS>@<cluster>.mongodb.net/?retryWrites=true&w=majority\"\n"
        "  python app.py\n"
    )


# -----------------------------
# UI config
# -----------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class HospitalApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Mongo client (reuse one client for the whole app)
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client["kmd_hospital"]

        # Collections
        self.patients = self.db["patients"]
        self.doctors = self.db["doctors"]
        self.appointments = self.db["appointments"]

        # Indexes
        self.patients.create_index("patient_id", unique=True)
        self.doctors.create_index("doctor_id", unique=True)
        self.appointments.create_index("appointment_id", unique=True)

        # Prevent exact double booking: same doctor + date + time
        self.appointments.create_index(
            [("doctor_id", 1), ("appointment_date", 1), ("appointment_time", 1)],
            unique=True,
            name="uniq_doctor_datetime",
        )

        self.doctors.create_index("specialization")

        # Window
        self.title("KMD Hospital - Patient Management System (MongoDB)")
        self.geometry("960x720")
        self.minsize(860, 620)
        self.resizable(True, True)

        header = ctk.CTkFrame(self, height=70, fg_color="#025784")
        header.pack(fill="x")

        title_label = ctk.CTkLabel(
            header,
            text="KMD Hospital - Patient Management System",
            font=("Calibri", 22, "bold"),
            text_color="white",
        )
        title_label.place(relx=0.5, rely=0.5, anchor="center")

        self.tabview = ctk.CTkTabview(self, fg_color="#D3D3D3")
        self.tabview.pack(pady=15, padx=12, fill="both", expand=True)

        self.patient_tab = self.tabview.add("Patient Info")
        self.doctor_tab = self.tabview.add("Doctor Registry")
        self.appointment_tab = self.tabview.add("Appointments")
        self.doctor_count_tab = self.tabview.add("Doctor Count")

        self.build_patient_tab()
        self.build_doctor_tab()
        self.build_appointment_tab()
        self.build_doctor_count_tab()

    # -----------------------------
    # Helpers
    # -----------------------------
    def _is_valid_date(self, s: str) -> bool:
        s = (s or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return False
        try:
            d = dt.datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return False
        if d < dt.date.today():
            return False
        return True

    def _norm_time(self, t: str) -> str:
        """Normalize time to HH:MM (e.g. 9:30 -> 09:30)."""
        t = (str(t) if t is not None else "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", t)
        if not m:
            return t
        h = int(m.group(1))
        mm = int(m.group(2))
        if h < 0 or h > 23 or mm < 0 or mm > 59:
            return t
        return f"{h:02d}:{mm:02d}"

    def _parse_time_to_minutes(self, hhmm: str):
        """Return minutes since midnight for normalized HH:MM; None if invalid."""
        hhmm = self._norm_time(hhmm)
        m = re.match(r"^(\d{2}):(\d{2})$", hhmm)
        if not m:
            return None
        h = int(m.group(1))
        mm = int(m.group(2))
        if h < 0 or h > 23 or mm < 0 or mm > 59:
            return None
        return h * 60 + mm

    def _add_30min(self, hhmm: str) -> str:
        m = self._parse_time_to_minutes(hhmm)
        if m is None:
            return hhmm
        m2 = m + 30
        h = (m2 // 60) % 24
        mm = m2 % 60
        return f"{h:02d}:{mm:02d}"

    def _get_booked_starts_minutes(self, doctor_id: int, appt_date: str):
        booked = []
        for a in self.appointments.find(
            {"doctor_id": int(doctor_id), "appointment_date": appt_date},
            {"appointment_time": 1},
        ):
            t = self._parse_time_to_minutes(a.get("appointment_time"))
            if t is not None:
                booked.append(t)
        return booked

    def _is_exact_taken(self, doctor_id: int, appt_date: str, appt_time_hhmm: str) -> bool:
        """Exact match check (same keys as uniq_doctor_datetime)."""
        try:
            doc = self.appointments.find_one(
                {
                    "doctor_id": int(doctor_id),
                    "appointment_date": str(appt_date).strip(),
                    "appointment_time": str(appt_time_hhmm).strip(),
                },
                {"_id": 1},
            )
            return doc is not None
        except Exception:
            return True  # safest

    def _has_overlap_30min(self, doctor_id: int, appt_date: str, start_hhmm: str) -> bool:
        """
        30-minute fixed appointment overlap checker.
        New interval: [start, start+30)
        Existing interval: [t, t+30)
        Overlap if: start < t+30 and t < start+30
        """
        start_min = self._parse_time_to_minutes(start_hhmm)
        if start_min is None:
            return True
        end_min = start_min + 30

        try:
            booked_starts = self._get_booked_starts_minutes(doctor_id, appt_date)
        except PyMongoError:
            return True

        for t in booked_starts:
            t_end = t + 30
            if start_min < t_end and t < end_min:
                return True
        return False

    def _is_appointment_id_taken(self, appointment_id: int) -> bool:
        try:
            return self.appointments.find_one({"appointment_id": int(appointment_id)}, {"_id": 1}) is not None
        except Exception:
            return True

    def _duplicate_error_type(self, msg: str) -> str:
        msg = msg or ""
        if "appointment_id_1" in msg:
            return "appointment_id"
        if "uniq_doctor_datetime" in msg or "doctor_id_1_appointment_date_1_appointment_time_1" in msg:
            return "doctor_time"
        if "E11000" in msg:
            return "unknown"
        return ""

    # -----------------------------
    # Patient Tab
    # -----------------------------
    def build_patient_tab(self):
        title = ctk.CTkLabel(
            self.patient_tab,
            text="Patient Registration",
            font=("Calibri", 20, "bold"),
            text_color="#004d4d",
        )
        title.pack(pady=12)

        form_container = ctk.CTkFrame(self.patient_tab, fg_color="#D3D3D3", corner_radius=12)
        form_container.pack(pady=10, padx=10)

        self.patient_patient_id = ctk.CTkEntry(
            form_container, placeholder_text="Patient ID", width=350, height=35, border_color="#025784"
        )
        self.patient_patient_id.pack(pady=10)

        self.patient_first_name = ctk.CTkEntry(
            form_container, placeholder_text="First Name", width=350, height=35, border_color="#025784"
        )
        self.patient_first_name.pack(pady=10)

        self.patient_last_name = ctk.CTkEntry(
            form_container, placeholder_text="Last Name", width=350, height=35, border_color="#025784"
        )
        self.patient_last_name.pack(pady=10)

        self.patient_dob = ctk.CTkEntry(
            form_container, placeholder_text="DOB (YYYY-MM-DD)", width=350, height=35, border_color="#025784"
        )
        self.patient_dob.pack(pady=10)

        self.patient_gender = ctk.CTkEntry(
            form_container, placeholder_text="Gender", width=350, height=35, border_color="#025784"
        )
        self.patient_gender.pack(pady=10)

        self.patient_contact = ctk.CTkEntry(
            form_container, placeholder_text="Contact Number", width=350, height=35, border_color="#025784"
        )
        self.patient_contact.pack(pady=10)

        self.patient_email = ctk.CTkEntry(
            form_container, placeholder_text="Email", width=350, height=35, border_color="#025784"
        )
        self.patient_email.pack(pady=10)

        submit_button = ctk.CTkButton(
            self.patient_tab,
            text="Create Patient",
            command=self.create_patient,
            font=("Arial", 14, "bold"),
            fg_color="#006666",
            hover_color="#025784",
            width=250,
        )
        submit_button.pack(pady=15)

        self.patient_output = ctk.CTkLabel(self.patient_tab, text="", font=("Arial", 13))
        self.patient_output.pack(pady=5)

    def validate_patient_input(self):
        try:
            int(self.patient_patient_id.get())
        except ValueError:
            self.patient_output.configure(text="Patient ID must be a number.", text_color="red")
            return False

        if not self.patient_first_name.get() or not self.patient_last_name.get() or not self.patient_contact.get():
            self.patient_output.configure(
                text="Fill required fields (Patient ID, First Name, Last Name, Contact).",
                text_color="red",
            )
            return False
        return True

    def clear_patient_fields(self):
        self.patient_patient_id.delete(0, ctk.END)
        self.patient_first_name.delete(0, ctk.END)
        self.patient_last_name.delete(0, ctk.END)
        self.patient_dob.delete(0, ctk.END)
        self.patient_gender.delete(0, ctk.END)
        self.patient_contact.delete(0, ctk.END)
        self.patient_email.delete(0, ctk.END)

    def create_patient(self):
        if not self.validate_patient_input():
            return

        patient_doc = {
            "patient_id": int(self.patient_patient_id.get()),
            "first_name": self.patient_first_name.get().strip(),
            "last_name": self.patient_last_name.get().strip(),
            "dob": self.patient_dob.get().strip(),
            "gender": self.patient_gender.get().strip(),
            "contact_number": self.patient_contact.get().strip(),
            "email": self.patient_email.get().strip(),
        }

        try:
            self.patients.insert_one(patient_doc)
            self.patient_output.configure(text="Patient added successfully!", text_color="green")
            self.clear_patient_fields()
        except PyMongoError as e:
            self.patient_output.configure(text=f"MongoDB Error: {e}", text_color="red")

    # -----------------------------
    # Doctor Tab
    # -----------------------------
    def build_doctor_tab(self):
        title = ctk.CTkLabel(
            self.doctor_tab,
            text="Register New Doctor",
            font=("Calibri", 20, "bold"),
            text_color="#004d4d",
        )
        title.pack(pady=12)

        form_container = ctk.CTkFrame(self.doctor_tab, fg_color="#D3D3D3", corner_radius=12)
        form_container.pack(pady=10, padx=10)

        self.doctor_doctor_id = ctk.CTkEntry(
            form_container, placeholder_text="Doctor ID", width=350, height=35, border_color="#025784"
        )
        self.doctor_doctor_id.pack(pady=10)

        self.doctor_first_name = ctk.CTkEntry(
            form_container, placeholder_text="First Name", width=350, height=35, border_color="#025784"
        )
        self.doctor_first_name.pack(pady=10)

        self.doctor_last_name = ctk.CTkEntry(
            form_container, placeholder_text="Last Name", width=350, height=35, border_color="#025784"
        )
        self.doctor_last_name.pack(pady=10)

        self.doctor_specialization = ctk.CTkEntry(
            form_container, placeholder_text="Specialization", width=350, height=35, border_color="#025784"
        )
        self.doctor_specialization.pack(pady=10)

        self.doctor_contact = ctk.CTkEntry(
            form_container, placeholder_text="Contact Number", width=350, height=35, border_color="#025784"
        )
        self.doctor_contact.pack(pady=10)

        submit_button = ctk.CTkButton(
            self.doctor_tab,
            text="Submit Doctor Info",
            command=self.create_doctor,
            font=("Arial", 14, "bold"),
            fg_color="#006666",
            hover_color="#025784",
            width=250,
        )
        submit_button.pack(pady=15)

        self.doctor_output = ctk.CTkLabel(self.doctor_tab, text="", font=("Arial", 13))
        self.doctor_output.pack(pady=5)

    def validate_doctor_input(self):
        try:
            int(self.doctor_doctor_id.get())
        except ValueError:
            self.doctor_output.configure(text="Doctor ID must be a number.", text_color="red")
            return False

        if not self.doctor_first_name.get() or not self.doctor_last_name.get() or not self.doctor_contact.get():
            self.doctor_output.configure(
                text="Fill required fields (Doctor ID, First Name, Last Name, Contact).",
                text_color="red",
            )
            return False
        return True

    def clear_doctor_fields(self):
        self.doctor_doctor_id.delete(0, ctk.END)
        self.doctor_first_name.delete(0, ctk.END)
        self.doctor_last_name.delete(0, ctk.END)
        self.doctor_specialization.delete(0, ctk.END)
        self.doctor_contact.delete(0, ctk.END)

    def create_doctor(self):
        if not self.validate_doctor_input():
            return

        doctor_doc = {
            "doctor_id": int(self.doctor_doctor_id.get()),
            "first_name": self.doctor_first_name.get().strip(),
            "last_name": self.doctor_last_name.get().strip(),
            "specialization": self.doctor_specialization.get().strip(),
            "contact_number": self.doctor_contact.get().strip(),
        }

        try:
            self.doctors.insert_one(doctor_doc)
            self.doctor_output.configure(text="Doctor added successfully!", text_color="green")
            self.clear_doctor_fields()
            self.load_doctor_count()
        except PyMongoError as e:
            self.doctor_output.configure(text=f"MongoDB Error: {e}", text_color="red")

    # -----------------------------
    # Appointment Tab
    # -----------------------------
    def build_appointment_tab(self):
        title = ctk.CTkLabel(
            self.appointment_tab,
            text="Create Appointment",
            font=("Calibri", 20, "bold"),
            text_color="#004d4d",
        )
        title.pack(pady=12)

        outer = ctk.CTkFrame(self.appointment_tab, fg_color="#D3D3D3", corner_radius=12)
        outer.pack(padx=14, pady=10, fill="both", expand=True)

        actions = ctk.CTkFrame(outer, fg_color="#D3D3D3")
        actions.pack(fill="x", padx=12, pady=(12, 6))

        refresh_btn = ctk.CTkButton(
            actions,
            text="Refresh Doctors/Patients",
            command=self.refresh_appointment_dropdowns,
            font=("Arial", 12, "bold"),
            fg_color="#006666",
            hover_color="#025784",
            width=220,
        )
        refresh_btn.pack(side="right")

        form = ctk.CTkFrame(outer, fg_color="#D3D3D3")
        form.pack(fill="both", expand=True, padx=12, pady=6)

        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=2)

        ctk.CTkLabel(form, text="Appointment ID", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=0, column=0, sticky="w", pady=(6, 6), padx=(0, 10)
        )
        self.appointment_appointment_id = ctk.CTkEntry(
            form, placeholder_text="e.g., 1001", height=35, border_color="#025784"
        )
        self.appointment_appointment_id.grid(row=0, column=1, sticky="ew", pady=(6, 6))

        ctk.CTkLabel(form, text="Doctor", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=1, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        self.selected_doctor = tk.StringVar(value="Select doctor...")
        self.doctor_dropdown = ctk.CTkOptionMenu(
            form,
            variable=self.selected_doctor,
            values=["Select doctor..."],
            command=self.on_doctor_selected,
            height=35,
            fg_color="#025784",
            button_color="#025784",
            button_hover_color="#01405f",
        )
        self.doctor_dropdown.grid(row=1, column=1, sticky="ew", pady=6)

        ctk.CTkLabel(form, text="Patient", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=2, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        self.selected_patient = tk.StringVar(value="Select patient...")
        self.patient_dropdown = ctk.CTkOptionMenu(
            form,
            variable=self.selected_patient,
            values=["Select patient..."],
            command=self.on_patient_selected,
            height=35,
            fg_color="#025784",
            button_color="#025784",
            button_hover_color="#01405f",
        )
        self.patient_dropdown.grid(row=2, column=1, sticky="ew", pady=6)
        self.patient_dropdown.configure(state="disabled")

        ctk.CTkLabel(form, text="Date (YYYY-MM-DD)", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=3, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        self.manual_date_var = tk.StringVar(value="")
        self.date_entry = ctk.CTkEntry(
            form,
            textvariable=self.manual_date_var,
            placeholder_text="YYYY-MM-DD",
            height=35,
            border_color="#025784",
        )
        self.date_entry.grid(row=3, column=1, sticky="ew", pady=6)
        self.date_entry.configure(state="disabled")
        self.date_entry.bind("<FocusOut>", lambda _e: self.check_time_availability())
        self.date_entry.bind("<Return>", lambda _e: self.check_time_availability())

        ctk.CTkLabel(form, text="Time (HH:MM)", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=4, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        self.time_var = tk.StringVar(value="")
        self.time_entry = ctk.CTkEntry(
            form,
            textvariable=self.time_var,
            placeholder_text="e.g., 09:00 or 9:30",
            height=35,
            border_color="#025784",
        )
        self.time_entry.grid(row=4, column=1, sticky="ew", pady=6)
        self.time_entry.configure(state="disabled")
        self.time_entry.bind("<KeyRelease>", lambda _e: self.check_time_availability())
        self.time_entry.bind("<FocusOut>", lambda _e: self.check_time_availability())
        self.time_entry.bind("<Return>", lambda _e: self.check_time_availability())

        self.time_status = ctk.CTkLabel(form, text="", font=("Arial", 12))
        self.time_status.grid(row=5, column=1, sticky="w", pady=(0, 6))

        ctk.CTkLabel(form, text="Reason", text_color="#004d4d", font=("Arial", 13, "bold")).grid(
            row=6, column=0, sticky="w", pady=6, padx=(0, 10)
        )
        self.appointment_reason = ctk.CTkEntry(
            form, placeholder_text="Reason for visit", height=35, border_color="#025784"
        )
        self.appointment_reason.grid(row=6, column=1, sticky="ew", pady=6)

        submit_button = ctk.CTkButton(
            outer,
            text="Create Appointment",
            command=self.create_appointment,
            font=("Arial", 14, "bold"),
            fg_color="#006666",
            hover_color="#025784",
            width=260,
        )
        submit_button.pack(pady=8)

        self.appointment_output = ctk.CTkLabel(outer, text="", font=("Arial", 13))
        self.appointment_output.pack(pady=(0, 10))

        self.refresh_appointment_dropdowns()

    def refresh_appointment_dropdowns(self):
        try:
            doctor_docs = list(
                self.doctors.find(
                    {}, {"doctor_id": 1, "first_name": 1, "last_name": 1, "specialization": 1}
                ).sort("doctor_id", 1)
            )
            patient_docs = list(
                self.patients.find({}, {"patient_id": 1, "first_name": 1, "last_name": 1}).sort("patient_id", 1)
            )

            self._doctor_display_to_id = {}
            self._patient_display_to_id = {}

            doctor_values = ["Select doctor..."]
            for d in doctor_docs:
                did = d.get("doctor_id")
                name = f"{d.get('first_name','').strip()} {d.get('last_name','').strip()}".strip()
                spec = (d.get("specialization") or "").strip()
                disp = f"{did} - {name}" + (f" ({spec})" if spec else "")
                doctor_values.append(disp)
                self._doctor_display_to_id[disp] = did

            patient_values = ["Select patient..."]
            for p in patient_docs:
                pid = p.get("patient_id")
                name = f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip()
                disp = f"{pid} - {name}"
                patient_values.append(disp)
                self._patient_display_to_id[disp] = pid

            self.doctor_dropdown.configure(values=doctor_values)
            self.patient_dropdown.configure(values=patient_values)

            self.selected_doctor.set("Select doctor...")
            self.selected_patient.set("Select patient...")
            self.manual_date_var.set("")
            self.time_var.set("")
            self.time_status.configure(text="")
            self.appointment_output.configure(text="")

            self.patient_dropdown.configure(state="disabled")
            self.date_entry.configure(state="disabled")
            self.time_entry.configure(state="disabled")

        except PyMongoError as e:
            self.appointment_output.configure(text=f"MongoDB Error loading dropdowns: {e}", text_color="red")

    def on_doctor_selected(self, _value=None):
        if self.selected_doctor.get() in ("Select doctor...", ""):
            self.patient_dropdown.configure(state="disabled")
            self.date_entry.configure(state="disabled")
            self.time_entry.configure(state="disabled")
            return
        self.patient_dropdown.configure(state="enabled")
        self.date_entry.configure(state="disabled")
        self.time_entry.configure(state="disabled")
        self.selected_patient.set("Select patient...")
        self.manual_date_var.set("")
        self.time_var.set("")
        self.time_status.configure(text="")
        self.appointment_output.configure(text="")

    def on_patient_selected(self, _value=None):
        if self.selected_patient.get() in ("Select patient...", ""):
            self.date_entry.configure(state="disabled")
            self.time_entry.configure(state="disabled")
            return
        self.date_entry.configure(state="normal")
        self.time_entry.configure(state="normal")
        self.manual_date_var.set("")
        self.time_var.set("")
        self.time_status.configure(text="")
        self.appointment_output.configure(text="")

    def check_time_availability(self):
        if self.selected_doctor.get() in ("Select doctor...", ""):
            self.time_status.configure(text="")
            return
        if self.selected_patient.get() in ("Select patient...", ""):
            self.time_status.configure(text="")
            return

        appt_date = (self.manual_date_var.get() or "").strip()
        if not self._is_valid_date(appt_date):
            self.time_status.configure(text="Enter valid date (YYYY-MM-DD).", text_color="red")
            return

        raw_time = (self.time_var.get() or "").strip()
        norm = self._norm_time(raw_time)

        if self._parse_time_to_minutes(norm) is None:
            if raw_time == "":
                self.time_status.configure(text="")
            else:
                self.time_status.configure(text="Time format invalid. Use HH:MM (e.g., 09:30).", text_color="red")
            return

        doctor_id = self._doctor_display_to_id.get(self.selected_doctor.get())
        if doctor_id is None:
            self.time_status.configure(text="Doctor mapping issue. Press Refresh.", text_color="red")
            return

        if self._is_exact_taken(doctor_id, appt_date, norm):
            self.time_status.configure(text=f"{norm}-{self._add_30min(norm)} is booked.", text_color="red")
            return

        if self._has_overlap_30min(int(doctor_id), appt_date, norm):
            self.time_status.configure(text=f"{norm}-{self._add_30min(norm)} overlaps (booked).", text_color="red")
        else:
            self.time_status.configure(text=f"{norm}-{self._add_30min(norm)} available.", text_color="green")

    def validate_appointment_input(self) -> bool:
        try:
            appt_id = int(self.appointment_appointment_id.get())
        except ValueError:
            self.appointment_output.configure(text="Appointment ID must be a number.", text_color="red")
            return False

        if self._is_appointment_id_taken(appt_id):
            self.appointment_output.configure(text="Appointment ID already exists. Use a new ID.", text_color="red")
            return False

        if self.selected_doctor.get() in ("Select doctor...", ""):
            self.appointment_output.configure(text="Please select a doctor first.", text_color="red")
            return False
        if self.selected_patient.get() in ("Select patient...", ""):
            self.appointment_output.configure(text="Please select a patient.", text_color="red")
            return False

        doctor_id = self._doctor_display_to_id.get(self.selected_doctor.get())
        if doctor_id is None:
            self.appointment_output.configure(text="Doctor mapping issue. Press Refresh.", text_color="red")
            return False

        appt_date = (self.manual_date_var.get() or "").strip()
        if not self._is_valid_date(appt_date):
            self.appointment_output.configure(text="Enter a valid date (YYYY-MM-DD).", text_color="red")
            return False

        raw_time = (self.time_var.get() or "").strip()
        appt_time = self._norm_time(raw_time)
        if self._parse_time_to_minutes(appt_time) is None:
            self.appointment_output.configure(text="Enter a valid time (HH:MM).", text_color="red")
            return False

        if self._is_exact_taken(doctor_id, appt_date, appt_time):
            self.appointment_output.configure(
                text=f"That exact start time is already taken ({appt_date} {appt_time}).",
                text_color="red",
            )
            self.check_time_availability()
            return False

        if self._has_overlap_30min(int(doctor_id), appt_date, appt_time):
            self.appointment_output.configure(
                text=f"That slot overlaps an existing appointment ({appt_date} {appt_time}).",
                text_color="red",
            )
            self.check_time_availability()
            return False

        return True

    def clear_appointment_fields(self):
        self.appointment_appointment_id.delete(0, ctk.END)
        self.appointment_reason.delete(0, ctk.END)
        self.selected_doctor.set("Select doctor...")
        self.selected_patient.set("Select patient...")
        self.manual_date_var.set("")
        self.time_var.set("")
        self.time_status.configure(text="")
        self.appointment_output.configure(text="")
        self.patient_dropdown.configure(state="disabled")
        self.date_entry.configure(state="disabled")
        self.time_entry.configure(state="disabled")

    def create_appointment(self):
        if not self.validate_appointment_input():
            return

        appointment_id = int(self.appointment_appointment_id.get())
        doctor_id = self._doctor_display_to_id.get(self.selected_doctor.get())
        patient_id = self._patient_display_to_id.get(self.selected_patient.get())

        appt_date = (self.manual_date_var.get() or "").strip()
        appt_time = self._norm_time((self.time_var.get() or "").strip())

        if self._is_appointment_id_taken(appointment_id):
            self.appointment_output.configure(text="Appointment ID already exists. Use a new ID.", text_color="red")
            return

        if self._is_exact_taken(doctor_id, appt_date, appt_time):
            self.appointment_output.configure(
                text=f"That exact start time is already taken ({appt_date} {appt_time}).",
                text_color="red",
            )
            self.check_time_availability()
            return

        appt_doc = {
            "appointment_id": appointment_id,
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "appointment_date": appt_date,
            "appointment_time": appt_time,
            "reason_for_visit": self.appointment_reason.get().strip(),
            "created_at": dt.datetime.now(dt.timezone.utc),
        }

        try:
            self.appointments.insert_one(appt_doc)
            self.appointment_output.configure(text="Appointment created successfully!", text_color="green")
            self.clear_appointment_fields()

        except PyMongoError as e:
            msg = str(e)
            dup_type = self._duplicate_error_type(msg)

            if dup_type == "appointment_id":
                self.appointment_output.configure(
                    text="Appointment ID already exists. Use a new ID.",
                    text_color="red",
                )
                return

            if dup_type == "doctor_time":
                self.appointment_output.configure(
                    text=f"That exact start time is already taken ({appt_date} {appt_time}).",
                    text_color="red",
                )
                self.check_time_availability()
                return

            if dup_type == "unknown":
                self.appointment_output.configure(
                    text=f"Duplicate key error (E11000). Full: {msg}",
                    text_color="red",
                )
                return

            self.appointment_output.configure(text=f"MongoDB Error: {e}", text_color="red")

    # -----------------------------
    # Doctor Count Tab
    # -----------------------------
    def build_doctor_count_tab(self):
        title = ctk.CTkLabel(
            self.doctor_count_tab,
            text="Doctor Count by Specialization",
            font=("Calibri", 20, "bold"),
            text_color="#004d4d",
        )
        title.pack(pady=12)

        self.doctor_count_output = ctk.CTkTextbox(
            self.doctor_count_tab,
            width=650,
            height=340,
            fg_color="#2F2E2E",
            corner_radius=12,
        )
        self.doctor_count_output.pack(pady=10)

        refresh_button = ctk.CTkButton(
            self.doctor_count_tab,
            text="Refresh Count",
            command=self.load_doctor_count,
            font=("Arial", 14, "bold"),
            fg_color="#006666",
            hover_color="#025784",
            width=150,
        )
        refresh_button.pack(pady=5)

        self.load_doctor_count()

    def load_doctor_count(self):
        try:
            pipeline = [
                {"$group": {"_id": "$specialization", "count": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ]
            rows = list(self.doctors.aggregate(pipeline))

            self.doctor_count_output.delete("0.0", ctk.END)
            if not rows:
                self.doctor_count_output.insert(ctk.END, "No doctors found.\n")
                return

            for row in rows:
                spec = row.get("_id") or "Unknown"
                count = row.get("count", 0)
                self.doctor_count_output.insert(ctk.END, f"{spec}: {count}\n")
        except PyMongoError as e:
            self.doctor_count_output.delete("0.0", ctk.END)
            self.doctor_count_output.insert(ctk.END, f"MongoDB Error: {e}")

    def on_close(self):
        try:
            self.client.close()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = HospitalApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()