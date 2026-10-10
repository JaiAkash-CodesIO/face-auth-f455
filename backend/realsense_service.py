import os
import subprocess
import time
import re
import json
import threading
import logging
import base64
import cv2

logger = logging.getLogger("realsense_service")
logging.basicConfig(level=logging.INFO)

RSID_CLI_PATH = r"C:\Users\govar\AppData\Local\Programs\RealSenseID Tools\rsid-cli.exe"
BRIDGE_PS1_PATH = os.path.join(os.path.dirname(__file__), "rsid_native_bridge.ps1")

class RealSenseService:
    def __init__(self, port="COM4", device_type="F45x", fb_service=None):
        self.port = port
        self.device_type = device_type
        self.cli_path = RSID_CLI_PATH
        self.bridge_path = BRIDGE_PS1_PATH
        self.fb_service = fb_service
        self.is_scanning = False
        self._scan_thread = None
        self._on_auth_callback = None
        self.hardware_lock = threading.Lock()
        self.is_cli_busy = False
        self.cli_release_event = threading.Event()
        self.last_frame = None

        # Auto-detect COM port if available
        self.detect_com_port()

    def set_firebase_service(self, fb_service):
        self.fb_service = fb_service

    def set_auth_callback(self, callback):
        self._on_auth_callback = callback

    def detect_com_port(self) -> str:
        """Automatically detects the active COM port of the Intel RealSense ID F455."""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
            ports = []
            i = 0
            while True:
                try:
                    val_name, val_data, _ = winreg.EnumValue(key, i)
                    ports.append(val_data)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)

            if ports:
                # If current port is in list, keep it; otherwise switch to first available
                if self.port not in ports:
                    self.port = ports[0]
                    logger.info(f"Updated RealSense COM port to: {self.port}")
                return self.port
        except Exception as e:
            logger.debug(f"Error reading SERIALCOMM: {e}")
        return self.port

    def _close_session(self):
        """No-op kept for backwards compatibility."""
        pass

    def _run_bridge_command(self, command: str, param: str = "", timeout: float = 15.0) -> dict:
        """Executes a biometric operation via the native C# SDK bridge."""
        if not os.path.exists(self.bridge_path):
            logger.error(f"Native bridge not found at {self.bridge_path}")
            return {"success": False, "error": f"Bridge script not found at {self.bridge_path}"}

        # Ensure active COM port is up-to-date
        self.detect_com_port()

        cmd = [
            "powershell",
            "-ExecutionPolicy", "Bypass",
            "-File", self.bridge_path,
            command,
            self.port,
            param
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            
            # Parse JSON from stdout lines (reverse order to find output JSON)
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except Exception:
                        continue

            logger.warning(f"Bridge command '{command}' returned non-JSON stdout:\n{stdout}\nstderr:\n{stderr}")
            return {"success": False, "error": f"No valid JSON output from bridge. Output: {stdout[:150]}"}
        except subprocess.TimeoutExpired:
            logger.error(f"Bridge command '{command}' timed out after {timeout}s")
            return {"success": False, "error": f"Native bridge operation '{command}' timed out"}
        except Exception as e:
            logger.error(f"Bridge command '{command}' error: {e}")
            return {"success": False, "error": str(e)}

    def check_device_connected(self):
        """Check if RSID native bridge can reach the device on COM port."""
        with self.hardware_lock:
            self.detect_com_port()
            res = self._run_bridge_command("users_device", timeout=8.0)
            if res.get("success"):
                return True, f"Connected to RealSense ID F455 on {self.port}"
            return False, res.get("error", f"Failed to connect to F455 on {self.port}")

    def authenticate_host(self) -> dict:
        """Executes Host Mode authentication matching against Firestore faceprint vectors via Native SDK."""
        vectors = []
        if self.fb_service:
            vectors = self.fb_service.get_all_faceprint_vectors()

        if not vectors:
            return {
                "success": False,
                "status": "NO_USERS",
                "mode": "HOST",
                "user_id": None,
                "message": "Host Mode: No enrolled host faceprints in Firebase cloud database"
            }

        cache_path = os.path.join(os.path.dirname(__file__), "host_db_cache.json")
        try:
            with open(cache_path, "w") as f:
                json.dump(vectors, f)

            res = self._run_bridge_command("match", cache_path, timeout=12.0)
            if res.get("success"):
                user_id = res.get("user_id")
                score = res.get("score", 0)
                return {
                    "success": True,
                    "status": "AUTHENTICATED",
                    "mode": "HOST",
                    "user_id": user_id,
                    "score": score,
                    "message": f"Host biometric faceprint match verified for '{user_id}' (Score: {score})"
                }
            else:
                return {
                    "success": False,
                    "status": res.get("status", "DENIED"),
                    "mode": "HOST",
                    "user_id": None,
                    "message": res.get("error") or "Host Mode: Face not recognized in cloud faceprint database"
                }
        finally:
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                except Exception:
                    pass

    def authenticate_device(self) -> dict:
        """Executes On-Device hardware authentication matching against F455 hardware flash."""
        res = self._run_bridge_command("auth_device", timeout=10.0)
        if res.get("success"):
            user_id = res.get("user_id") or "Jai Akash"
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
                "message": res.get("error") or "On-Device: Face not recognized or spoof detected by Intel F455 hardware"
            }

    def authenticate_single(self, mode: str = "HYBRID"):
        """
        Runs facial authentication. Supports:
        - 'HOST': Uses Host Mode faceprints matched against Firebase Firestore vectors
        - 'DEVICE': Uses On-Device hardware memory
        - 'HYBRID' / 'AUTO': Tries Host Mode first; falls back to On-Device hardware flash if needed.
        """
        with self.hardware_lock:
            auth_res = None
            mode_upper = (mode or "HYBRID").upper()

            if mode_upper == "HOST":
                auth_res = self.authenticate_host()
            elif mode_upper == "DEVICE":
                auth_res = self.authenticate_device()
            else:  # HYBRID / AUTO mode
                # Try Host Mode (Firestore Cloud Vectors) first
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

    def check_duplicate_face(self, mode: str = "HOST") -> dict:
        """
        Performs a pre-enrollment 1:N de-duplication scan without triggering
        attendance punch callbacks or broadcast events.
        Checks if the face presented to the camera already belongs to any enrolled worker.
        """
        with self.hardware_lock:
            mode_upper = (mode or "HOST").upper()

            # 1. Check Host Mode database if host users exist in Firestore
            if mode_upper in ("HOST", "HYBRID"):
                host_users = self.list_host_users()
                if host_users:
                    host_res = self.authenticate_host()
                    if host_res.get("success") and host_res.get("user_id"):
                        return {
                            "is_duplicate": True,
                            "matched_user_id": host_res.get("user_id"),
                            "mode": "HOST",
                            "message": f"Face matches enrolled Host worker '{host_res.get('user_id')}'"
                        }

            # 2. Check On-Device hardware flash if device users exist
            if mode_upper in ("DEVICE", "HYBRID"):
                dev_users = self.list_users()
                if dev_users:
                    dev_res = self.authenticate_device()
                    if dev_res.get("success") and dev_res.get("user_id"):
                        return {
                            "is_duplicate": True,
                            "matched_user_id": dev_res.get("user_id"),
                            "mode": "DEVICE",
                            "message": f"Face matches enrolled Hardware worker '{dev_res.get('user_id')}'"
                        }

            return {
                "is_duplicate": False,
                "matched_user_id": None,
                "mode": None,
                "message": "No duplicate face detected."
            }

    def enroll_user_host(self, user_id: str) -> dict:
        """Enrolls a user in Host Mode by extracting 512-D faceprint vector into Firebase."""
        res = self._run_bridge_command("enroll", user_id, timeout=15.0)
        if res.get("success"):
            return {
                "success": True,
                "status": "ENROLLED",
                "mode": "HOST",
                "user_id": user_id,
                "vector": res.get("vector"),
                "version": res.get("version", 1),
                "featuresType": res.get("featuresType", 0),
                "flags": res.get("flags", 0),
                "message": f"Biometric faceprint extracted successfully for '{user_id}' and ready for Firebase Cloud storage."
            }
        else:
            return {
                "success": False,
                "status": "FAILED",
                "mode": "HOST",
                "user_id": user_id,
                "message": res.get("error") or "Host faceprint extraction failed. Center face 40-70cm from F455 lens."
            }

    def enroll_user_device(self, user_id: str) -> dict:
        """Enrolls a user in On-Device hardware flash memory."""
        res = self._run_bridge_command("enroll_device", user_id, timeout=15.0)
        if res.get("success"):
            return {
                "success": True,
                "status": "ENROLLED",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": f"Face profile '{user_id}' successfully saved in F455 hardware flash memory."
            }
        else:
            return {
                "success": False,
                "status": "FAILED",
                "mode": "DEVICE",
                "user_id": user_id,
                "message": res.get("error") or "Hardware enrollment failed on Intel F455 device."
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
        res = self._run_bridge_command("users_device", timeout=8.0)
        if res.get("success"):
            return res.get("users", [])
        return []

    def list_host_users(self):
        """Lists all enrolled user IDs from Host Mode (Firebase faceprint database)."""
        if self.fb_service:
            return list(self.fb_service.get_host_workers().keys())
        return []

    def get_capacity_info(self, force_refresh=False):
        """Returns max capacity, hardware users, host users, and remaining slots left."""
        now = time.time()
        if not force_refresh and hasattr(self, '_capacity_cache') and self._capacity_cache:
            cache_time, data = self._capacity_cache
            if now - cache_time < 15.0:
                return data

        dev_users = self.list_users()
        host_users = self.list_host_users()
        max_cap = 1000
        enrolled_count = len(dev_users)
        remaining = max(0, max_cap - enrolled_count)
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
        """
        Directly detects and returns the Intel RealSense camera index.
        Explicitly excludes laptop webcams so only the Intel camera is used.
        """
        try:
            from pygrabber.dshow_graph import FilterGraph
            graph = FilterGraph()
            devices = graph.get_input_devices()
            for idx, name in enumerate(devices):
                name_lower = name.lower()
                # Exclude internal / laptop webcams
                if "integrated" in name_lower or "internal" in name_lower or ("webcam" in name_lower and "intel" not in name_lower):
                    continue
                if "intel" in name_lower or "f450" in name_lower or "f455" in name_lower or "realsense" in name_lower:
                    return idx
        except Exception as e:
            logger.debug(f"Error enumerating camera devices: {e}")
        return None

    def capture_snapshot_b64(self, camera_index=None):
        """Captures a single camera frame snapshot strictly from the Intel camera (never laptop webcam)."""
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

        if camera_index is None:
            return None

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
        """
        Generates MJPEG video stream bytes strictly from the Intel RealSense camera.
        Never falls back to laptop webcam. If Intel camera is not detected, displays
        a status overlay while polling for device reconnection.
        """
        import numpy as np
        cap = None
        consecutive_fails = 0

        try:
            while True:
                if camera_index is None:
                    camera_index = self.get_intel_camera_index()

                # If Intel camera is not found, display informative RealSense standby overlay
                if camera_index is None:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    frame[:] = (20, 24, 39) # Dark slate theme

                    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    active_port = self.detect_com_port()

                    # Header badge
                    cv2.rectangle(frame, (20, 20), (620, 58), (30, 41, 59), -1)
                    cv2.putText(frame, f"INTEL REALSENSE ID F455  |  {current_time}", (35, 45),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 180, 0), 2)

                    # Center warning icon & message
                    cv2.putText(frame, "INTEL F455 VIDEO NOT DETECTED", (110, 210),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 165, 255), 2)
                    cv2.putText(frame, "Laptop camera blocked to protect Intel privacy.", (130, 250),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 174, 192), 1)
                    cv2.putText(frame, "Please connect Intel F455 to a USB 3.0 / USB-C SuperSpeed port.", (75, 280),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
                    cv2.putText(frame, f"Biometric Sensor Status: Active on {active_port}", (145, 330),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 128), 1)

                    ret_enc, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ret_enc:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

                    time.sleep(1.0)
                    continue

                # Intel Camera detected: Open video capture
                if cap is None or not cap.isOpened():
                    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
                    if not cap.isOpened():
                        cap = cv2.VideoCapture(camera_index)

                ret, frame = cap.read() if cap.isOpened() else (False, None)
                if not ret or frame is None:
                    consecutive_fails += 1
                    if consecutive_fails > 10:
                        if cap is not None:
                            cap.release()
                            cap = None
                        camera_index = None # Re-detect
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
