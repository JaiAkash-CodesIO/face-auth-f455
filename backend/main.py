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
rs_service = RealSenseService(port="COM4", device_type="F45x")
fb_service = FirebaseService()

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

class ManualPunchRequest(BaseModel):
    mode: Optional[str] = "AUTO"  # "PUNCH_IN", "PUNCH_OUT", or "AUTO"
    biometric_mode: Optional[str] = "HYBRID"  # "HYBRID", "HOST", or "DEVICE"

# --- API Endpoints ---

@app.get("/api/status")
def get_status():
    connected, msg = rs_service.check_device_connected()
    host_users = rs_service.list_host_users()
    dev_users = rs_service.list_users()
    return {
        "device": "Intel RealSense ID F455",
        "port": rs_service.port,
        "connected": connected,
        "message": msg,
        "is_auto_scanning": rs_service.is_scanning,
        "firebase_active": fb_service.initialized,
        "biometric_modes": ["HOST", "DEVICE", "HYBRID"],
        "active_mode": "PURE_HOST_&_HYBRID",
        "host_users_count": len(host_users),
        "device_users_count": len(dev_users)
    }

@app.get("/api/capacity")
def get_capacity():
    """Returns total capacity, currently enrolled users count, and remaining slots left."""
    return rs_service.get_capacity_info()

@app.get("/api/video_feed")
async def video_feed():
    """Live MJPEG video stream from Intel RealSense F455 camera."""
    async def async_frame_stream():
        for frame in rs_service.generate_video_feed():
            yield frame
            await asyncio.sleep(0.001)

    return StreamingResponse(
        async_frame_stream(),
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
    Enrolls worker face:
    - In Host Mode (Default): Extracts biometric vector from F455 and saves faceprint profile to Firebase.
    - In Device Mode: Saves profile directly into F455 hardware flash and Firebase metadata.
    """
    if not req.worker_id or not req.name:
        raise HTTPException(status_code=400, detail="Worker ID and Name are required")

    target_mode = (req.biometric_mode or "HOST").upper()
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
