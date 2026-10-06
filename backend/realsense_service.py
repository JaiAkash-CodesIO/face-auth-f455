import os
import subprocess
import time
import re
import threading
import logging
import base64
import cv2

logger = logging.getLogger("realsense_service")
logging.basicConfig(level=logging.INFO)

RSID_CLI_PATH = r"C:\Users\govar\AppData\Local\Programs\RealSenseID Tools\rsid-cli.exe"

class RealSenseService:
    def __init__(self, port="COM4", device_type="F45x"):
        self.port = port
        self.device_type = device_type
        self.cli_path = RSID_CLI_PATH
        self.is_scanning = False
        self._scan_thread = None
        self._on_auth_callback = None
        self.hardware_lock = threading.Lock()
        self.is_cli_busy = False
        self.cli_release_event = threading.Event()
        self.last_frame = None

    def set_auth_callback(self, callback):
        self._on_auth_callback = callback

    def check_device_connected(self):
        """Check if RSID CLI can reach the device on COM port."""
        if not os.path.exists(self.cli_path):
            return False, f"CLI binary not found at {self.cli_path}"

        with self.hardware_lock:
            try:
                cmd = [self.cli_path, "--port", self.port, "--device-type", self.device_type]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                out, _ = proc.communicate(input="q\n", timeout=5)
                if "Using port: COM4" in out or proc.returncode == 0:
                    return True, f"Connected to RealSense ID F455 on {self.port}"
                return False, "Failed to connect to F455"
            except Exception as e:
                return False, str(e)

    def authenticate_single(self):
        """Runs a single authentication scan on F455 hardware."""
        if not os.path.exists(self.cli_path):
            return {
                "success": False,
                "status": "CLI_NOT_FOUND",
                "message": f"RealSense CLI tool missing at {self.cli_path}"
            }

        with self.hardware_lock:
            authenticated_user = None
            auth_success = False
            error_msg = None

            try:
                cmd = [self.cli_path, "--port", self.port, "--device-type", self.device_type]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                # Send 'a' then 'q' to authenticate and exit safely
                out, _ = proc.communicate(input="a\nq\n", timeout=8)
                logger.info(f"[F455 Auth Output]:\n{out}")

                if "authenticate success" in out.lower() or "got result: success" in out.lower():
                    auth_success = True
                    match = re.search(r"user(?:_id)?:\s*([^\*\r\n]+)", out, re.IGNORECASE)
                    if match:
                        authenticated_user = match.group(1).strip()
                    else:
                        authenticated_user = "Jai Akash"
                elif "got result: failure" in out.lower() or "got result: forbidden" in out.lower() or "got result: spoof" in out.lower():
                    auth_success = False
                    authenticated_user = None
                    error_msg = "Face not recognized or spoof detected by Intel F455"
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                error_msg = "Authentication timed out"
            except Exception as e:
                logger.error(f"Error during single auth execution: {e}")
                error_msg = str(e)

            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            snapshot_b64 = self.capture_snapshot_b64()

            if auth_success:
                result = {
                    "success": True,
                    "status": "AUTHENTICATED",
                    "user_id": authenticated_user or "Jai Akash",
                    "timestamp": timestamp,
                    "device": "Intel RealSense ID F455",
                    "image_b64": snapshot_b64,
                    "message": f"Face authenticated successfully for '{authenticated_user or 'Jai Akash'}'"
                }
            else:
                result = {
                    "success": False,
                    "status": "DENIED",
                    "user_id": None,
                    "timestamp": timestamp,
                    "device": "Intel RealSense ID F455",
                    "image_b64": snapshot_b64,
                    "message": error_msg or "Authentication failed or face not recognized / spoof detected"
                }

            if self._on_auth_callback:
                self._on_auth_callback(result)

            return result

    def enroll_user(self, user_id: str):
        """Enrolls a new user face profile in F455 hardware memory."""
        if not user_id:
            return {"success": False, "message": "User ID is required"}

        with self.hardware_lock:
            enroll_success = False
            is_duplicate = False
            message_detail = ""

            try:
                cmd = [self.cli_path, "--port", self.port, "--device-type", self.device_type]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                # Send 'e', user_id, and then 'q'
                out, _ = proc.communicate(input=f"e\n{user_id}\nq\n", timeout=10)
                logger.info(f"[F455 Enroll Output]:\n{out}")

                if "duplicatefaceprints" in out.lower() or "duplicate" in out.lower():
                    is_duplicate = True
                    enroll_success = False
                    message_detail = f"Face is already enrolled in Intel F455 hardware memory."
                elif "enroll success" in out.lower() or "got result: success" in out.lower() or "status: ok" in out.lower():
                    enroll_success = True
                    message_detail = f"Face profile '{user_id}' successfully saved in F455 hardware memory."
                elif "got result: failure" in out.lower() or "got result: forbidden" in out.lower():
                    enroll_success = False
                    message_detail = "Enrollment failed on Intel F455 hardware."
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                message_detail = "Enrollment timed out"
            except Exception as e:
                message_detail = f"Enrollment exception: {e}"

            snapshot_b64 = self.capture_snapshot_b64()

            if enroll_success:
                return {
                    "success": True,
                    "status": "ENROLLED",
                    "user_id": user_id,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "image_b64": snapshot_b64,
                    "message": message_detail or f"Successfully enrolled '{user_id}'"
                }
            elif is_duplicate:
                return {
                    "success": False,
                    "status": "DUPLICATE",
                    "user_id": user_id,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "image_b64": snapshot_b64,
                    "message": "Faceprint is already enrolled in Intel F455 hardware memory."
                }
            else:
                return {
                    "success": False,
                    "status": "FAILED",
                    "user_id": user_id,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "image_b64": snapshot_b64,
                    "message": message_detail or "Face enrollment failed or timed out. Center face 40-70cm from F455 lens."
                }

    def list_users(self):
        """Lists all enrolled user IDs from the F455 hardware."""
        if not os.path.exists(self.cli_path):
            return []

        with self.hardware_lock:
            users = []
            try:
                cmd = [self.cli_path, "--port", self.port, "--device-type", self.device_type]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                out, _ = proc.communicate(input="u\nq\n", timeout=6)
                for line in out.splitlines():
                    match = re.match(r"^\d+\.\s*(.+)$", line.strip())
                    if match:
                        user_name = match.group(1).strip()
                        if user_name:
                            users.append(user_name)
            except Exception as e:
                logger.error(f"Error listing users: {e}")

            return users

    def get_capacity_info(self, force_refresh=False):
        """Returns max capacity, enrolled count, and remaining slots left with caching."""
        now = time.time()
        if not force_refresh and hasattr(self, '_capacity_cache') and self._capacity_cache:
            cache_time, data = self._capacity_cache
            if now - cache_time < 30.0:
                return data

        users = self.list_users()
        max_cap = 1000
        enrolled_count = len(users)
        remaining = max_cap - enrolled_count
        data = {
            "max_capacity": max_cap,
            "enrolled_count": enrolled_count,
            "remaining_slots": remaining,
            "users": users
        }
        self._capacity_cache = (now, data)
        return data

    def get_intel_camera_index(self):
        """Directly detects and returns the Intel RealSense camera index."""
        try:
            from pygrabber.dshow_graph import FilterGraph
            graph = FilterGraph()
            devices = graph.get_input_devices()
            for idx, name in enumerate(devices):
                if "intel" in name.lower() or "f450" in name.lower() or "f455" in name.lower():
                    return idx
        except Exception:
            pass
        return 0

    def capture_snapshot_b64(self, camera_index=None):
        """Captures a single camera frame snapshot from cache or camera device."""
        if self.last_frame is not None:
            try:
                frame_resized = cv2.resize(self.last_frame, (640, 480))
                ret_enc, buffer = cv2.imencode('.jpg', frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret_enc:
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    return f"data:image/jpeg;base64,{b64_str}"
            except Exception as e:
                logger.error(f"Error encoding cached snapshot: {e}")

        if camera_index is None:
            camera_index = self.get_intel_camera_index()

        try:
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(camera_index)
            
            if not cap.isOpened():
                return None

            ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                frame_resized = cv2.resize(frame, (640, 480))
                ret_enc, buffer = cv2.imencode('.jpg', frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret_enc:
                    b64_str = base64.b64encode(buffer).decode('utf-8')
                    return f"data:image/jpeg;base64,{b64_str}"
            return None
        except Exception as e:
            logger.error(f"Error capturing camera snapshot: {e}")
            return None

    def generate_video_feed(self, camera_index=None):
        """Generates MJPEG video stream bytes from the Intel camera continuously with live timestamp."""
        if camera_index is None:
            camera_index = self.get_intel_camera_index()

        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(camera_index)

        consecutive_fails = 0

        try:
            while True:
                if not cap.isOpened():
                    cap.open(camera_index, cv2.CAP_DSHOW)
                    if not cap.isOpened():
                        cap.open(camera_index)

                ret, frame = cap.read()
                if not ret or frame is None:
                    consecutive_fails += 1
                    if consecutive_fails > 10:
                        cap.release()
                        time.sleep(0.2)
                        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
                        consecutive_fails = 0
                    time.sleep(0.04)
                    continue

                consecutive_fails = 0
                self.last_frame = frame.copy()

                # Overlay F455 status badge & Live Clock on video feed
                h, w = frame.shape[:2]
                current_time = time.strftime("%Y-%m-%d %H:%M:%S")
                cv2.rectangle(frame, (10, 10), (w - 10, 45), (15, 23, 42), -1)
                cv2.putText(frame, f"INTEL F455 LIVE  |  {current_time}", (20, 33),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 200), 2)

                ret_enc, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ret_enc:
                    frame_bytes = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                time.sleep(0.033)
        finally:
            if cap is not None and cap.isOpened():
                cap.release()

