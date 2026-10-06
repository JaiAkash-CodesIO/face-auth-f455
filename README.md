# Face Authentication & Attendance System (Intel RealSense F455 & Firebase)

A biometric worker attendance and face authentication dashboard powered by the **Intel® RealSense™ ID Solution F455** hardware and **Firebase Cloud Firestore**.

---

## 🌟 Key Features

- **Intel RealSense ID F455 Hardware Integration**:
  - Secure on-device biometric face authentication (spoof-resistant, dedicated neural network).
  - Face enrollment directly into F455 secure hardware storage.
  - Hardware capacity tracking (up to 1,000 enrolled faceprints).
- **Live Video Streaming**:
  - Continuous, non-blocking MJPEG camera feed streamed directly from the Intel RealSense F455 sensor via OpenCV.
  - Live timestamp overlay and camera status indicators.
- **Attendance Punch In / Punch Out**:
  - Fast single-scan authentication triggered via web dashboard.
  - Automatic Punch-In / Punch-Out toggle based on the worker's previous attendance state.
- **Firebase Firestore Cloud Sync**:
  - Real-time logging of attendance events with face snapshot captures.
  - Worker profile storage (Worker ID, Name, Department, Role).
- **Real-Time Web Dashboard**:
  - WebSockets for immediate live punch event broadcasts.
  - Responsive, modern user interface for worker registration, attendance logs, and device diagnostics.

---

## 🏗️ System Architecture

```
[ Intel RealSense ID F455 ]
   ├── Video Stream (DirectShow / OpenCV) ──────> FastAPI (/api/video_feed) ──> Web UI
   └── Serial Interface (COM4 / rsid-cli.exe) ──> RealSenseService ──────────> Attendance Logic
                                                                                  │
                                                                                  ├──> Firebase Firestore
                                                                                  └──> WebSocket (/ws) ──> Web UI
```

---

## 📋 Prerequisites

1. **Operating System**: Windows 10 / 11 (64-bit)
2. **Hardware**: Intel RealSense ID F455 connected via USB 3.0 / USB-C
3. **Software**:
   - Python 3.10+
   - [Intel RealSense ID Tools](https://github.com/IntelRealSense/RealSenseID) (`rsid-cli.exe`)
   - Firebase Service Account Key (`serviceAccountKey.json`) placed in `backend/`

---

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/JaiAkash-CodesIO/face-auth-f455.git
cd face-auth-f455
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Firebase (Optional but Recommended)

Place your Firebase Service Account JSON file at:
```
backend/serviceAccountKey.json
```
*(If omitted, the backend will safely operate with local JSON storage).*

### 4. Run the Application

Start the server using the entrypoint script:

```bash
python run_app.py
```

Or using Uvicorn directly:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

Open your browser at **[http://localhost:8080](http://localhost:8080)**.

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Hardware connection & Firebase status |
| `GET` | `/api/capacity` | Enrolled faceprint count & remaining slots |
| `GET` | `/api/video_feed` | Live MJPEG camera video stream |
| `POST` | `/api/punch` | Trigger face scan & log Punch-In/Out |
| `POST` | `/api/workers/register` | Enroll new worker faceprint to F455 & Firebase |
| `GET` | `/api/attendance` | Retrieve attendance history logs |
| `DELETE` | `/api/attendance` | Clear attendance records |
| `WS` | `/ws` | WebSocket for real-time attendance broadcasts |

---

## 📁 Project Structure

```
face-auth-f455/
├── backend/
│   ├── main.py                 # FastAPI application, routing, and WebSocket server
│   ├── realsense_service.py    # Intel F455 camera capture & rsid-cli integration
│   ├── firebase_service.py     # Firebase Firestore cloud storage & local backup
│   └── serviceAccountKey.json  # (Excluded from git) Firebase credentials
├── frontend/
│   ├── index.html              # Modern biometric attendance dashboard UI
│   ├── app.js                  # Frontend WebSocket client and UI controller
│   └── style.css               # Dashboard styling
├── .gitignore                  # Git ignore rules
├── requirements.txt            # Python dependencies
├── run_app.py                  # Convenient startup script
└── README.md                   # Documentation
```

---

## 🛡️ License

This project is licensed under the MIT License.
