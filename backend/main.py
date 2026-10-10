import asyncio
import os
import json
import logging
from typing import List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.realsense_service import RealSenseService
from backend.firebase_service import FirebaseService

app = FastAPI(title="Worker Punch-In / Punch-Out Attendance System - Intel F455 & Firebase")

# Enable CORS for local web development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Services
fb_service = FirebaseService()
rs_service = RealSenseService(port="COM4", device_type="F45x", fb_service=fb_service)

# WebSocket Manager for Live Broadcasting
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
main_loop = None

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    # Auto-sync any on-device hardware faceprints into Firestore in background thread
    def _bg_sync():
        try:
            import time
            time.sleep(1.0)
            dev_prints = rs_service._run_bridge_command("get_device_faceprints")
            if dev_prints.get("success"):
                for u in dev_prints.get("users", []):
                    wid = u.get("worker_id")
                    vec = u.get("vector")
                    if wid and vec:
                        existing = fb_service.get_worker(wid)
                        if not existing or not existing.get("faceprint_vector"):
                            fb_service.save_worker_profile(
                                worker_id=wid,
                                name=existing.get("name", wid) if existing else wid,
                                department=existing.get("department", "General") if existing else "General",
                                role=existing.get("role", "Worker") if existing else "Worker",
                                biometric_mode="HOST",
                                host_enrolled=True,
                                faceprint_status="STORED_IN_FIREBASE",
                                faceprint_vector=vec,
                                vector_version=u.get("version", 9),
                                features_type=u.get("featuresType", 0),
                                flags=u.get("flags", 0)
                            )
                            logging.info(f"Auto-synced hardware faceprint vector for '{wid}' into Firestore.")
        except Exception as e:
            logging.warning(f"Startup hardware faceprint sync skipped: {e}")

    import threading
    threading.Thread(target=_bg_sync, daemon=True).start()

# Hook real-time auth callback to push to Firebase and WebSocket subscribers
def on_auth_event(event: dict):
    # Broadcast to connected UI Clients
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({
                "type": "PUNCH_EVENT",
                "data": event
            }),
            main_loop
        )

rs_service.set_auth_callback(on_auth_event)

# --- Pydantic Models ---
class RegisterWorkerRequest(BaseModel):
    worker_id: str
    name: str
    department: Optional[str] = "General"
    role: Optional[str] = "Worker"
    biometric_mode: Optional[str] = "HOST"  # "HOST" (Firebase Cloud Faceprints) or "DEVICE" (Hardware Flash)
    overwrite: Optional[bool] = False

class ManualPunchRequest(BaseModel):
    mode: Optional[str] = "AUTO"  # "PUNCH_IN", "PUNCH_OUT", or "AUTO"
    biometric_mode: Optional[str] = "HYBRID"  # "HYBRID", "HOST", or "DEVICE"

# --- API Endpoints ---

@app.get("/api/status")
def get_status():
    connected, msg = rs_service.check_device_connected()
    dev_users = rs_service.list_users()
    host_workers = fb_service.get_host_workers()
    return {
        "device": "Intel RealSense ID F455",
        "port": rs_service.port,
        "connected": connected,
        "message": msg,
        "is_auto_scanning": rs_service.is_scanning,
        "firebase_active": fb_service.initialized,
        "biometric_modes": ["HOST", "DEVICE", "HYBRID"],
        "active_mode": "PURE_HOST_&_HYBRID",
        "host_users_count": len(host_workers),
        "device_users_count": len(dev_users)
    }

@app.get("/api/capacity")
def get_capacity():
    """Returns total capacity, hardware flash users count, and Firebase Cloud enrolled count."""
    cap = rs_service.get_capacity_info()
    host_workers = fb_service.get_host_workers()
    cap["host_enrolled_count"] = len(host_workers)
    cap["host_users"] = list(host_workers.keys())
    return cap

@app.get("/api/video_feed")
def video_feed():
    """Live MJPEG video stream from Intel RealSense F455 camera."""
    return StreamingResponse(
        rs_service.generate_video_feed(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.post("/api/punch")
def trigger_worker_punch(req: ManualPunchRequest):
    """Authenticates worker face via Intel F455 (Host / Hybrid mode) and logs PUNCH IN / PUNCH OUT to Firebase."""
    auth_res = rs_service.authenticate_single(mode=req.biometric_mode or "HYBRID")
    auth_mode = auth_res.get("auth_mode", "HYBRID")
    
    if auth_res.get("status") == "AUTHENTICATED":
        worker_id = auth_res.get("user_id", "Worker")
        
        # Lookup worker profile name from Firebase or fallback to hardware user ID
        workers = fb_service.get_all_workers()
        worker_info = workers.get(worker_id, {})
        worker_name = worker_info.get("name", worker_id)

        if worker_name == "Verified Worker" or not worker_name:
            worker_name = worker_id if worker_id != "Verified Worker" else "Jai Akash"

        # Toggle Punch-In / Punch-Out automatically based on last punch
        last_punch = fb_service.get_last_punch_status(worker_id)
        if req.mode == "AUTO":
            next_punch = "PUNCH_OUT" if last_punch == "PUNCH_IN" else "PUNCH_IN"
        else:
            next_punch = req.mode

        mode_badge = "Firebase Host Mode" if auth_mode == "HOST" else "On-Device Hardware"

        # Save Attendance Punch Event to Firebase
        punch_record = fb_service.save_punch_event(
            worker_id=worker_id,
            name=worker_name,
            punch_type=next_punch,
            image_b64=auth_res.get("image_b64"),
            status="SUCCESS",
            auth_mode=auth_mode,
            message=f"Worker {worker_name} ({worker_id}) {next_punch.replace('_', ' ')} verified via {mode_badge}."
        )
        return {
            "success": True,
            "status": "AUTHENTICATED",
            "punch_type": next_punch,
            "worker_id": worker_id,
            "name": worker_name,
            "auth_mode": auth_mode,
            "timestamp": punch_record.get("timestamp"),
            "time": punch_record.get("time"),
            "image_b64": auth_res.get("image_b64"),
            "message": punch_record.get("message")
        }
    else:
        # Save Denied Attempt to Firebase
        denied_record = fb_service.save_punch_event(
            worker_id="UNRECOGNIZED",
            name="Unknown",
            punch_type="DENIED",
            image_b64=auth_res.get("image_b64"),
            status="DENIED",
            auth_mode=auth_mode,
            message=auth_res.get("message") or "Facial authentication failed or unverified face."
        )
        return {
            "success": False,
            "status": "DENIED",
            "punch_type": "DENIED",
            "auth_mode": auth_mode,
            "worker_id": None,
            "name": "Unknown",
            "timestamp": denied_record.get("timestamp"),
            "image_b64": auth_res.get("image_b64"),
            "message": auth_res.get("message") or "Access Denied: Unrecognized face or spoof attempt."
        }

@app.post("/api/workers/register")
def register_worker(req: RegisterWorkerRequest):
    """
    Enrolls worker face with pre-enrollment 1:N de-duplication verification:
    1. Checks if worker_id is already assigned in Firebase.
    2. Performs a pre-auth biometric scan ('A'/'a') to verify if the physical face is already enrolled.
    3. If no duplicate is detected, captures faceprint vectors and saves profile to Firebase.
    """
    if not req.worker_id or not req.name:
        raise HTTPException(status_code=400, detail="Worker ID and Name are required")

    target_mode = (req.biometric_mode or "HOST").upper()

    # 1. Check if the Worker ID is already assigned in Firebase
    existing_worker = fb_service.get_worker(req.worker_id)
    if existing_worker:
        existing_vec = existing_worker.get("faceprint_vector") or []
        if len(existing_vec) > 0 and not req.overwrite:
            existing_name = existing_worker.get("name", "Existing Worker")
            return {
                "success": False,
                "status": "DUPLICATE_ID",
                "biometric_mode": target_mode,
                "message": f"Registration Rejected: Worker ID '{req.worker_id}' is already registered to '{existing_name}'. (Pass overwrite=true to re-enroll)."
            }

    # 2. Pre-Enrollment Biometric De-duplication Check (1:N Anti-Duplication)
    duplicate_check = rs_service.check_duplicate_face(mode=target_mode)
    if duplicate_check.get("is_duplicate") and duplicate_check.get("matched_user_id"):
        matched_id = duplicate_check.get("matched_user_id")
        if matched_id != req.worker_id:
            all_workers = fb_service.get_all_workers()
            matched_worker = all_workers.get(matched_id, {})
            matched_name = matched_worker.get("name", matched_id)
            matched_mode = duplicate_check.get("mode", target_mode)

            logging.warning(f"Duplicate enrollment prevented: face matches '{matched_name}' ({matched_id})")
            return {
                "success": False,
                "status": "DUPLICATE_FACE",
                "biometric_mode": target_mode,
                "matched_worker_id": matched_id,
                "matched_name": matched_name,
                "message": f"Duplicate Enrollment Rejected: This face is already enrolled as '{matched_name}' (ID: {matched_id}) in {matched_mode} Mode!"
            }

    # 3. Proceed with enrollment since no duplicate exists
    enroll_res = rs_service.enroll_user(req.worker_id, mode=target_mode)

    if enroll_res.get("success"):
        # Save profile and biometric metadata into Firebase
        worker_profile = fb_service.save_worker_profile(
            worker_id=req.worker_id,
            name=req.name,
            department=req.department,
            role=req.role,
            biometric_mode=target_mode,
            host_enrolled=(target_mode == "HOST"),
            faceprint_status="STORED_IN_FIREBASE" if target_mode == "HOST" else "STORED_ON_HARDWARE_FLASH",
            faceprint_vector=enroll_res.get("vector"),
            vector_version=enroll_res.get("version", 1),
            features_type=enroll_res.get("featuresType", 0),
            flags=enroll_res.get("flags", 0),
            image_b64=enroll_res.get("image_b64")
        )
        return {
            "success": True,
            "biometric_mode": target_mode,
            "message": f"Worker '{req.name}' ({req.worker_id}) faceprint successfully enrolled in {target_mode} Mode and saved to Firebase!",
            "worker": worker_profile
        }
    else:
        return {
            "success": False,
            "biometric_mode": target_mode,
            "message": enroll_res.get("message") or f"Failed to capture face on Intel F455 in {target_mode} mode."
        }

@app.get("/api/attendance")
def get_attendance_logs(limit: int = 50):
    return fb_service.get_attendance_logs(limit=limit)

@app.get("/api/workers")
def get_workers():
    return fb_service.get_all_workers()

@app.delete("/api/workers/{worker_id}")
def delete_worker(worker_id: str):
    success = fb_service.delete_worker(worker_id)
    return {"success": success, "message": f"Worker '{worker_id}' deleted successfully."}

@app.delete("/api/attendance")
def clear_attendance_logs():
    success = fb_service.clear_attendance_logs()
    return {"success": success}

# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        connected, msg = rs_service.check_device_connected()
        await websocket.send_json({
            "type": "INIT_STATUS",
            "connected": connected,
            "message": msg,
            "logs": fb_service.get_attendance_logs(limit=20)
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Mount Frontend static files
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
