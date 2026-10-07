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

import queue

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

        # Persistent CLI Session for high-performance Host & Device Biometrics
        self._session_proc = None
        self._session_queue = None
        self._session_thread = None

    def set_auth_callback(self, callback):
        self._on_auth_callback = callback

    def _ensure_session(self):
        """Ensures the background rsid-cli process is running and ready to accept commands."""
        if not os.path.exists(self.cli_path):
            return False

        if self._session_proc is not None and self._session_proc.poll() is None:
            return True

        # Clean up any dead process
        self._close_session()

        try:
            cmd = [self.cli_path, "--port", self.port, "--device-type", self.device_type]
            self._session_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self._session_queue = queue.Queue()

            def _reader():
                try:
                    for line in iter(self._session_proc.stdout.readline, ''):
                        if self._session_queue:
                            self._session_queue.put(line)
                except Exception:
                    pass

            self._session_thread = threading.Thread(target=_reader, daemon=True)
            self._session_thread.start()

            # Wait briefly to consume startup banner
            start_wait = time.time()
            while time.time() - start_wait < 1.5:
                try:
                    line = self._session_queue.get(timeout=0.1)
                    if "[?]" in line or "Choose an option" in line:
                        break
                except queue.Empty:
                    continue
            return True
        except Exception as e:
            logger.error(f"Error starting rsid-cli session: {e}")
            self._close_session()
            return False

    def _close_session(self):
        """Safely shuts down the background CLI process."""
        if self._session_proc:
            try:
                self._session_proc.stdin.write("q\n")
                self._session_proc.stdin.flush()
                self._session_proc.wait(timeout=1.5)
            except Exception:
                try:
                    self._session_proc.kill()
                except Exception:
                    pass
        self._session_proc = None
        self._session_queue = None
        self._session_thread = None

    def _exec_command(self, cmd_input: str, end_markers: list, timeout: float = 8.0) -> str:
        """Sends command to CLI session and gathers response until an end marker is seen."""
        if not self._ensure_session():
            return ""

        # Flush any leftover output from queue
        while not self._session_queue.empty():
            try:
                self._session_queue.get_nowait()
            except Exception:
                break

        try:
            self._session_proc.stdin.write(cmd_input + "\n")
            self._session_proc.stdin.flush()
        except Exception as e:
            logger.warning(f"Session pipe write error ({e}). Restarting session...")
            self._close_session()
            if not self._ensure_session():
                return ""
            self._session_proc.stdin.write(cmd_input + "\n")
            self._session_proc.stdin.flush()

        lines = []
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                line = self._session_queue.get(timeout=0.1)
                lines.append(line)
                if any(m.lower() in line.lower() for m in end_markers):
                    break
            except queue.Empty:
                continue

        output = "".join(lines)
        logger.info(f"[CLI Session Output for '{cmd_input}']:\n{output}")
        return output

    def check_device_connected(self):
        """Check if RSID CLI can reach the device on COM port."""
        if not os.path.exists(self.cli_path):
            return False, f"CLI binary not found at {self.cli_path}"

        with self.hardware_lock:
            try:
                output = self._exec_command("U", ["[?]", "users\n"], timeout=4)
                if "Using port: COM4" in output or "users" in output.lower():
                    return True, f"Connected to RealSense ID F455 on {self.port}"
                return False, "Failed to connect to F455"
            except Exception as e:
                return False, str(e)

    def authenticate_host(self) -> dict:
        """Executes Host Mode authentication ('A') matching against host faceprints."""
        output = self._exec_command("A", ["[?]", "Match success", "Forbidden", "failed with status"], timeout=8)
        
        if "match success" in output.lower():
            match = re.search(r"match success\.\s*user(?:_id)?:\s*([^\*\r\n]+)", output, re.IGNORECASE)
            user_id = match.group(1).strip() if match else "Verified Host Worker"
            return {
                "success": True,
                "status": "AUTHENTICATED",
                "mode": "HOST",
                "user_id": user_id,
                "message": f"Host biometric faceprint match verified for '{user_id}'"
            }
        elif "forbidden" in output.lower():
            return {
                "success": False,
                "status": "DENIED",
                "mode": "HOST",
                "user_id": None,
                "message": "Host Mode: Face not recognized in host faceprint database"
            }
        else:
            return {
                "success": False,
                "status": "DENIED",
                "mode": "HOST",
                "user_id": None,
                "message": "Host Mode: Face extraction failed or no face detected"
            }

    def authenticate_device(self) -> dict:
        """Executes On-Device hardware authentication ('a') matching against F455 hardware flash."""
        output = self._exec_command("a", ["[?]", "authenticate success", "got result:", "status:"], timeout=8)

        if "authenticate success" in output.lower() or "got result: success" in output.lower():
            match = re.search(r"user(?:_id)?:\s*([^\*\r\n]+)", output, re.IGNORECASE)
            user_id = match.group(1).strip() if match else "Jai Akash"
            return {
                "success": True,
                "status": "AUTHENTICATED",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": f"Hardware on-device facial authentication verified for '{user_id}'"
            }
        else:
            return {
                "success": False,
                "status": "DENIED",
                "mode": "DEVICE",
                "user_id": None,
                "message": "On-Device: Face not recognized or spoof detected by Intel F455 hardware"
            }

    def authenticate_single(self, mode: str = "HYBRID"):
        """
        Runs facial authentication. Supports:
        - 'HOST': Uses Host Mode faceprints ('A')
        - 'DEVICE': Uses On-Device hardware memory ('a')
        - 'HYBRID' / 'AUTO': Tries Host Mode first; falls back to On-Device memory if needed.
        """
        if not os.path.exists(self.cli_path):
            return {
                "success": False,
                "status": "CLI_NOT_FOUND",
                "message": f"RealSense CLI tool missing at {self.cli_path}"
            }

        with self.hardware_lock:
            auth_res = None
            mode_upper = (mode or "HYBRID").upper()

            if mode_upper == "HOST":
                auth_res = self.authenticate_host()
            elif mode_upper == "DEVICE":
                auth_res = self.authenticate_device()
            else:  # HYBRID / AUTO mode
                # Try Host Mode first
                host_res = self.authenticate_host()
                if host_res.get("success"):
                    auth_res = host_res
                else:
                    # Fall back to On-Device hardware flash
                    dev_res = self.authenticate_device()
                    if dev_res.get("success"):
                        auth_res = dev_res
                    else:
                        auth_res = {
                            "success": False,
                            "status": "DENIED",
                            "mode": "HYBRID",
                            "user_id": None,
                            "message": "Face not recognized in Host Faceprints or On-Device Hardware Flash"
                        }

            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            snapshot_b64 = self.capture_snapshot_b64()

            result = {
                "success": auth_res.get("success", False),
                "status": auth_res.get("status", "DENIED"),
                "auth_mode": auth_res.get("mode", "HYBRID"),
                "user_id": auth_res.get("user_id"),
                "timestamp": timestamp,
                "device": "Intel RealSense ID F455",
                "image_b64": snapshot_b64,
                "message": auth_res.get("message", "Authentication scan completed")
            }

            if self._on_auth_callback:
                self._on_auth_callback(result)

            return result

    def enroll_user_host(self, user_id: str) -> dict:
        """Enrolls a user in Host Mode ('E') by extracting faceprint vector into host session."""
        cmd = f"E\n{user_id}"
        output = self._exec_command(cmd, ["[?]", "Status: Ok", "on_result:"], timeout=12)

        if "got faceprints from device" in output.lower() or "on_result: status: success" in output.lower() or "status: ok" in output.lower():
            return {
                "success": True,
                "status": "ENROLLED",
                "mode": "HOST",
                "user_id": user_id,
                "message": f"Biometric faceprint extracted successfully for '{user_id}' and ready for Firebase Cloud storage."
            }
        elif "duplicate" in output.lower():
            return {
                "success": False,
                "status": "DUPLICATE",
                "mode": "HOST",
                "user_id": user_id,
                "message": f"Faceprint for '{user_id}' already enrolled in Host database."
            }
        else:
            return {
                "success": False,
                "status": "FAILED",
                "mode": "HOST",
                "user_id": user_id,
                "message": "Host faceprint extraction failed. Center face 40-70cm from F455 lens."
            }

    def enroll_user_device(self, user_id: str) -> dict:
        """Enrolls a user in On-Device hardware flash memory ('e')."""
        cmd = f"e\n{user_id}"
        output = self._exec_command(cmd, ["[?]", "enroll success", "duplicatefaceprints", "got result:"], timeout=12)

        if "enroll success" in output.lower() or "got result: success" in output.lower():
            return {
                "success": True,
                "status": "ENROLLED",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": f"Face profile '{user_id}' successfully saved in F455 hardware flash memory."
            }
        elif "duplicate" in output.lower():
            return {
                "success": False,
                "status": "DUPLICATE",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": "Faceprint is already enrolled in Intel F455 hardware flash memory."
            }
        else:
            return {
                "success": False,
                "status": "FAILED",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": "Hardware enrollment failed on Intel F455 device."
            }

    def enroll_user(self, user_id: str, mode: str = "HOST"):
        """
        Enrolls a new user face profile.
        - mode='HOST' (Default): Extracts biometric faceprint vector for Firebase storage.
        - mode='DEVICE': Enrolls directly into F455 hardware flash memory.
        """
        if not user_id:
            return {"success": False, "message": "User ID is required"}

        with self.hardware_lock:
            mode_upper = (mode or "HOST").upper()
            if mode_upper == "DEVICE":
                enroll_res = self.enroll_user_device(user_id)
            else:
                enroll_res = self.enroll_user_host(user_id)

            snapshot_b64 = self.capture_snapshot_b64()
            enroll_res["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            enroll_res["image_b64"] = snapshot_b64
            enroll_res["device"] = "Intel RealSense ID F455"
            return enroll_res

    def list_users(self):
        """Lists all enrolled user IDs from the F455 hardware flash memory."""
        if not os.path.exists(self.cli_path):
            return []

        with self.hardware_lock:
            users = []
            try:
                out = self._exec_command("u", ["[?]"], timeout=5)
                for line in out.splitlines():
                    match = re.match(r"^\d+\.\s*(.+)$", line.strip())
                    if match:
                        user_name = match.group(1).strip()
                        if user_name:
                            users.append(user_name)
            except Exception as e:
                logger.error(f"Error listing hardware users: {e}")
            return users

    def list_host_users(self):
        """Lists all enrolled user IDs from Host Mode database."""
        if not os.path.exists(self.cli_path):
            return []

        with self.hardware_lock:
            host_users = []
            try:
                out = self._exec_command("U", ["[?]"], timeout=5)
                for line in out.splitlines():
                    match = re.match(r"^\*\s*(.+)$", line.strip())
                    if match:
                        user_name = match.group(1).strip()
                        if user_name:
                            host_users.append(user_name)
            except Exception as e:
                logger.error(f"Error listing host users: {e}")
            return host_users

    def get_capacity_info(self, force_refresh=False):
        """Returns max capacity, hardware users, host users, and remaining slots left."""
        now = time.time()
        if not force_refresh and hasattr(self, '_capacity_cache') and self._capacity_cache:
            cache_time, data = self._capacity_cache
            if now - cache_time < 30.0:
                return data

        dev_users = self.list_users()
        host_users = self.list_host_users()
        max_cap = 1000
        enrolled_count = len(dev_users)
        remaining = max_cap - enrolled_count
        data = {
            "max_capacity": max_cap,
            "enrolled_count": enrolled_count,
            "remaining_slots": remaining,
            "users": dev_users,
            "host_users": host_users,
            "host_enrolled_count": len(host_users)
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

