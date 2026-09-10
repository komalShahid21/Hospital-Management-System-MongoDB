# Hospital-Management-System-MongoDB
A NoSQL-based Patient Management System with advanced appointment overlap checking.
# 🏥 KMD Hospital Management System (MongoDB)

## 📌 Description
A modern, NoSQL-based desktop application for hospital management. It features real-time dropdowns and a sophisticated 30-minute overlap logic to prevent double-booking of doctors.

## 🛠️ Technologies Used
*   Python
*   CustomTkinter
*   MongoDB (PyMongo)
*   MongoDB Atlas

## ✨ Key Features
*   **NoSQL Data Storage:** Utilizes MongoDB collections for Patients, Doctors, and Appointments.
*   **Smart Scheduling:** Prevents exact double-booking and overlapping 30-minute appointment slots for doctors.
*   **Dynamic UI:** Dropdowns automatically fetch and display available doctors and patients.
*   **Aggregation Pipeline:** Uses MongoDB aggregation to display doctor counts by specialization.
*   **Unique Indexing:** Ensures data integrity by creating unique indexes on IDs and time slots.

## 🚀 How to Run
1. Set your MongoDB Atlas connection string as an environment variable (`MONGODB_URI`).
2. Install required libraries: `pip install customtkinter pymongo`.
3. Run the application: `python app.py`.
