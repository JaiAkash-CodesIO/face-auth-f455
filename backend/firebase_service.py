import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("firebase_service")

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    HAS_FIREBASE_LIB = True
except ImportError:
    HAS_FIREBASE_LIB = False

class FirebaseService:
    def __init__(self, cred_path=None, project_id=None):
        self.db = None
        self.initialized = False
        self.local_logs_file = os.path.join(os.path.dirname(__file__), "attendance_logs.json")
        self.local_workers_file = os.path.join(os.path.dirname(__file__), "workers_db.json")
        
        if not cred_path:
            cred_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
        self._init_firebase(cred_path, project_id)

    def _init_firebase(self, cred_path, project_id):
        if not HAS_FIREBASE_LIB:
            logger.warning("firebase_admin library not installed. Operating in Local Memory Mode.")
            return

        if cred_path and os.path.exists(cred_path):
            try:
                if not firebase_admin._apps:
                    cred = credentials.Certificate(cred_path)
                    firebase_admin.initialize_app(cred)
                self.db = firestore.client()
                self.initialized = True
                logger.info("Firebase Firestore initialized successfully via Service Account certificate.")
                return
            except Exception as e:
                logger.error(f"Failed to initialize Firebase Admin: {e}")

        logger.info("Firebase operating in Local Storage Mode (Ready for Firebase credentials).")

    def save_worker_profile(self, worker_id: str, name: str, department: str = "General", role: str = "Worker", image_b64: str = None, biometric_mode: str = "HOST", host_enrolled: bool = True, faceprint_status: str = "STORED_IN_FIREBASE"):
        """Stores worker metadata and faceprint profile in Firebase Firestore / Local DB."""
        worker_data = {
            "worker_id": worker_id,
            "name": name,
            "department": department,
            "role": role,
            "biometric_mode": biometric_mode,  # "HOST" (Stored in Firebase) or "DEVICE" (Hardware flash)
            "host_enrolled": host_enrolled,
            "faceprint_status": faceprint_status,
            "enrolled_at": datetime.now().isoformat(),
            "image_b64": image_b64,
            "device": "Intel RealSense ID F455"
        }

        if self.initialized and self.db:
            try:
                self.db.collection("workers").document(worker_id).set(worker_data)
                logger.info(f"Saved worker faceprint profile '{worker_id}' ({biometric_mode} mode) to Firebase Firestore.")
            except Exception as e:
                logger.error(f"Error saving worker to Firebase: {e}")

        # Local backup
        workers = self.get_all_workers()
        workers[worker_id] = worker_data
        try:
            with open(self.local_workers_file, "w") as f:
                json.dump(workers, f, indent=2)
        except Exception as e:
            logger.error(f"Local worker save error: {e}")

        return worker_data

    def get_all_workers(self):
        if self.initialized and self.db:
            try:
                docs = self.db.collection("workers").stream()
                return {doc.id: doc.to_dict() for doc in docs}
            except Exception as e:
                logger.error(f"Error reading workers from Firebase: {e}")

        if os.path.exists(self.local_workers_file):
            try:
                with open(self.local_workers_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def get_worker(self, worker_id: str):
        workers = self.get_all_workers()
        return workers.get(worker_id)

    def get_host_workers(self):
        """Returns all workers registered in Host Mode (Firebase faceprint database)."""
        workers = self.get_all_workers()
        return {wid: w for wid, w in workers.items() if w.get("biometric_mode") == "HOST" or w.get("host_enrolled")}

    def get_last_punch_status(self, worker_id: str):
        """Returns the last punch type for a worker to toggle PUNCH IN vs PUNCH OUT."""
        logs = self.get_attendance_logs(limit=100)
        for log in logs:
            if log.get("worker_id") == worker_id:
                return log.get("punch_type")
        return "PUNCH_OUT"  # Default to PUNCH_IN for first scan

    def save_punch_event(self, worker_id: str, name: str, punch_type: str, image_b64: str = None, status: str = "SUCCESS", message: str = "", auth_mode: str = "HOST"):
        """Saves a Punch-In or Punch-Out attendance record to Firebase."""
        now = datetime.now()
        record = {
            "worker_id": worker_id or "UNKNOWN",
            "name": name or "Worker",
            "punch_type": punch_type,  # "PUNCH_IN" or "PUNCH_OUT"
            "status": status,
            "auth_mode": auth_mode,    # "HOST" (Firebase Biometrics) or "DEVICE" (Hardware Flash)
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%I:%M:%S %p"),
            "image_b64": image_b64,
            "device": "Intel RealSense ID F455",
            "message": message
        }

        if self.initialized and self.db:
            try:
                self.db.collection("attendance_logs").add(record)
                logger.info(f"Saved {punch_type} record for worker {worker_id} ({auth_mode} mode) to Firebase Firestore.")
            except Exception as e:
                logger.error(f"Firebase attendance log save error: {e}")

        self._append_local_attendance_log(record)
        return record

    def _append_local_attendance_log(self, record):
        logs = self.get_attendance_logs(limit=200)
        logs.insert(0, record)
        logs = logs[:200]
        try:
            with open(self.local_logs_file, "w") as f:
                json.dump(logs, f, indent=2)
        except Exception as e:
            logger.error(f"Local log append error: {e}")

    def get_attendance_logs(self, limit=50):
        if self.initialized and self.db:
            try:
                docs = self.db.collection("attendance_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).stream()
                return [doc.to_dict() for doc in docs]
            except Exception as e:
                logger.error(f"Firebase log fetch error: {e}")

        if os.path.exists(self.local_logs_file):
            try:
                with open(self.local_logs_file, "r") as f:
                    return json.load(f)[:limit]
            except Exception:
                return []
        return []

    def clear_attendance_logs(self):
        try:
            if os.path.exists(self.local_logs_file):
                os.remove(self.local_logs_file)
            return True
        except Exception as e:
            return False
