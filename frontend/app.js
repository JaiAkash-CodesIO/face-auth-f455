// Worker Punch-In / Punch-Out Dashboard - Frontend Logic

let socket = null;
let stats = {
  total: 0,
  punchIns: 0,
  punchOuts: 0
};

// DOM Elements
const statusBanner = document.getElementById("statusBanner");
const mainStatusTitle = document.getElementById("mainStatusTitle");
const mainStatusSubtext = document.getElementById("mainStatusSubtext");
const metaUser = document.getElementById("metaUser");
const metaTime = document.getElementById("metaTime");
const metaMode = document.getElementById("metaMode");
const metaAction = document.getElementById("metaAction");
const capturedSnapshotImg = document.getElementById("capturedSnapshotImg");

const deviceDot = document.getElementById("deviceDot");
const deviceStatusText = document.getElementById("deviceStatusText");
const biometricModeText = document.getElementById("biometricModeText");

const btnPunchAuto = document.getElementById("btnPunchAuto");
const btnOpenRegister = document.getElementById("btnOpenRegister");
const btnClearLogs = document.getElementById("btnClearLogs");

const logsTableBody = document.getElementById("logsTableBody");
const registerModal = document.getElementById("registerModal");
const btnCloseModal = document.getElementById("btnCloseModal");
const btnSubmitRegister = document.getElementById("btnSubmitRegister");

const regBiometricMode = document.getElementById("regBiometricMode");
const regWorkerId = document.getElementById("regWorkerId");
const regName = document.getElementById("regName");
const regDepartment = document.getElementById("regDepartment");
const regRole = document.getElementById("regRole");

// Stat elements
const statHostUsers = document.getElementById("statHostUsers");
const statRemainingSlots = document.getElementById("statRemainingSlots");
const statPunchIns = document.getElementById("statPunchIns");
const statPunchOuts = document.getElementById("statPunchOuts");

const DEFAULT_AVATAR = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' fill='%2364748b'><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='40'>👤</text></svg>";

// Initialize WebSocket & Event Listeners
document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
  checkDeviceStatus();
  fetchInitialLogs();

  const videoFeed = document.getElementById("videoFeed");
  if (videoFeed) {
    videoFeed.onerror = () => {
      console.warn("Video stream stalled/interrupted. Reconnecting...");
      setTimeout(() => {
        videoFeed.src = "/api/video_feed?t=" + Date.now();
      }, 1000);
    };
  }

  // Attach button click events
  btnPunchAuto.addEventListener("click", triggerPunch);
  btnOpenRegister.addEventListener("click", () => registerModal.classList.add("active"));
  btnCloseModal.addEventListener("click", () => registerModal.classList.remove("active"));
  btnSubmitRegister.addEventListener("click", submitRegistration);
  btnClearLogs.addEventListener("click", clearLogs);
});

// WebSocket Connection
function connectWebSocket() {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsProtocol}//${window.location.host}/ws`;

  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("WebSocket connected to RealSense & Firebase Backend.");
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "INIT_STATUS") {
        updateDeviceStatusUI(data.connected, data.message);
        if (data.logs) renderLogsTable(data.logs);
      } else if (data.type === "PUNCH_EVENT") {
        handlePunchEvent(data.data);
      }
    } catch (e) {
      console.error("WS message parse error:", e);
    }
  };

  socket.onclose = () => {
    setTimeout(connectWebSocket, 3000);
  };
}

// Check device status via API
async function checkDeviceStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    updateDeviceStatusUI(data.connected, data.message);
  } catch (err) {
    updateDeviceStatusUI(false, "API Offline");
  }
  fetchCapacityInfo();
}

async function fetchCapacityInfo() {
  try {
    const res = await fetch("/api/capacity");
    const data = await res.json();
    if (statRemainingSlots && data.remaining_slots !== undefined) {
      statRemainingSlots.innerText = `${data.remaining_slots} / ${data.max_capacity}`;
    }
    if (statHostUsers && data.host_enrolled_count !== undefined) {
      statHostUsers.innerText = `${data.host_enrolled_count} Enrolled`;
    }
  } catch (e) {
    console.error("Error fetching capacity:", e);
  }
}

function updateDeviceStatusUI(connected, message) {
  if (connected) {
    deviceDot.className = "dot online";
    deviceStatusText.innerText = "Intel F455 Connected";
  } else {
    deviceDot.className = "dot offline";
    deviceStatusText.innerText = message || "Disconnected";
  }
}

// Trigger Worker Punch (In/Out)
async function triggerPunch() {
  setLoadingState("AUTHENTICATING...", "Scanning worker face via Intel RealSense F455 (Host / Hybrid Biometrics)...");
  try {
    const res = await fetch("/api/punch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "AUTO", biometric_mode: "HYBRID" })
    });
    const data = await res.json();
    handlePunchEvent(data);
  } catch (err) {
    handlePunchEvent({
      status: "DENIED",
      punch_type: "DENIED",
      auth_mode: "ERROR",
      message: "Punch request failed: " + err.message,
      name: "Unknown",
      timestamp: new Date().toLocaleTimeString()
    });
  }
}

// Submit Worker Registration
async function submitRegistration() {
  const wId = regWorkerId.value.trim();
  const name = regName.value.trim();
  const dept = regDepartment.value.trim() || "General";
  const role = regRole.value.trim() || "Worker";
  const bioMode = regBiometricMode ? regBiometricMode.value : "HOST";

  if (!wId || !name) {
    alert("Please enter both Worker ID and Full Name");
    return;
  }

  registerModal.classList.remove("active");
  const modeLabel = bioMode === "HOST" ? "Firebase Cloud Faceprints" : "F455 Hardware Flash";
  setLoadingState("ENROLLING FACEPRINT...", `Capturing biometric faceprint for '${name}' (${wId}) in ${modeLabel}...`);

  try {
    const res = await fetch("/api/workers/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        worker_id: wId,
        name: name,
        department: dept,
        role: role,
        biometric_mode: bioMode
      })
    });
    const data = await res.json();
    
    if (data.success) {
      updateBanner(
        "state-authenticated",
        "WORKER REGISTERED!",
        `Faceprint for '${name}' saved in ${bioMode === 'HOST' ? 'Firebase Cloud' : 'F455 Flash'}.`,
        `${name} (${wId})`,
        new Date().toLocaleTimeString(),
        "ENROLLED",
        data.worker ? data.worker.image_b64 : null,
        bioMode === "HOST" ? "🔥 Host Mode (Firebase)" : "💾 On-Device (Hardware)"
      );
    } else {
      updateBanner("state-denied", "REGISTRATION FAILED", data.message || "Failed to capture face.", `${name} (${wId})`, new Date().toLocaleTimeString(), "FAILED", null, bioMode);
    }
    fetchInitialLogs();
    fetchCapacityInfo();
  } catch (err) {
    alert("Registration error: " + err.message);
  }
}

// Handle Punch Event
function handlePunchEvent(event) {
  stats.total++;
  const imgData = event.image_b64 || DEFAULT_AVATAR;
  const modeStr = event.auth_mode === "HOST" ? "🔥 Host Mode (Firebase)" : (event.auth_mode === "DEVICE" ? "💾 On-Device (F455)" : "🧬 Hybrid");

  if (event.status === "AUTHENTICATED" || event.success) {
    if (event.punch_type === "PUNCH_IN") {
      stats.punchIns++;
      updateBanner("state-authenticated", "🟢 PUNCH IN SUCCESS", `Worker ${event.name || event.worker_id} Punched In`, `${event.name || 'Worker'} (${event.worker_id || 'ID'})`, event.time || event.timestamp, "PUNCH IN", imgData, modeStr);
    } else {
      stats.punchOuts++;
      updateBanner("state-authenticated", "🔵 PUNCH OUT SUCCESS", `Worker ${event.name || event.worker_id} Punched Out`, `${event.name || 'Worker'} (${event.worker_id || 'ID'})`, event.time || event.timestamp, "PUNCH OUT", imgData, modeStr);
    }
  } else {
    updateBanner("state-denied", "⛔ ACCESS DENIED", event.message || "Unrecognized face or spoof detected.", "Unknown", event.timestamp || new Date().toLocaleTimeString(), "DENIED", imgData, modeStr);
  }

  updateStatsUI();
  prependLogRecord(event);
}

function setLoadingState(title, subtext) {
  statusBanner.className = "status-card state-ready";
  mainStatusTitle.innerText = title;
  mainStatusSubtext.innerText = subtext;
}

function updateBanner(stateClass, title, subtext, user, time, action, imageB64, mode) {
  statusBanner.className = `status-card ${stateClass}`;
  mainStatusTitle.innerText = title;
  mainStatusSubtext.innerText = subtext;
  metaUser.innerText = user || "--";
  metaTime.innerText = time || new Date().toLocaleTimeString();
  if (metaMode) metaMode.innerText = mode || "🔥 Host Mode (Firebase)";
  metaAction.innerText = action || "--";

  if (imageB64) {
    capturedSnapshotImg.src = imageB64;
  }
}

function updateStatsUI() {
  if (statPunchIns) statPunchIns.innerText = stats.punchIns;
  if (statPunchOuts) statPunchOuts.innerText = stats.punchOuts;
}

// Fetch logs
async function fetchInitialLogs() {
  try {
    const res = await fetch("/api/attendance");
    const logs = await res.json();
    renderLogsTable(logs);
    calculateStatsFromLogs(logs);
  } catch (e) {
    console.error("Error fetching attendance logs:", e);
  }
}

function calculateStatsFromLogs(logs) {
  stats.total = logs.length;
  stats.punchIns = logs.filter(l => l.punch_type === "PUNCH_IN").length;
  stats.punchOuts = logs.filter(l => l.punch_type === "PUNCH_OUT").length;
  updateStatsUI();
}

function renderLogsTable(logs) {
  if (!logs || logs.length === 0) {
    logsTableBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 30px;">No attendance records logged yet.</td></tr>`;
    return;
  }

  logsTableBody.innerHTML = logs.map(log => {
    const isHost = (log.auth_mode === "HOST" || !log.auth_mode);
    const modeBadge = isHost 
      ? `<span class="pill" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3);">🔥 Host Mode</span>`
      : `<span class="pill" style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3);">💾 On-Device</span>`;
    return `
      <tr>
        <td><img class="log-face-thumb" src="${log.image_b64 || DEFAULT_AVATAR}" alt="Face Photo"></td>
        <td><strong style="color: var(--text-main);">${log.timestamp || log.date}</strong></td>
        <td><span class="pill ${log.punch_type || 'DENIED'}">${(log.punch_type || 'DENIED').replace('_', ' ')}</span></td>
        <td><strong>${log.name || 'Worker'}</strong> <span style="color: var(--text-muted); font-size: 0.8rem;">(${log.worker_id || 'N/A'})</span></td>
        <td>${modeBadge}</td>
        <td style="color: var(--text-muted);">${log.message || ''}</td>
      </tr>
    `;
  }).join("");
}

function prependLogRecord(log) {
  const row = document.createElement("tr");
  const imgData = log.image_b64 || DEFAULT_AVATAR;
  const isHost = (log.auth_mode === "HOST" || !log.auth_mode);
  const modeBadge = isHost 
    ? `<span class="pill" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3);">🔥 Host Mode</span>`
    : `<span class="pill" style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3);">💾 On-Device</span>`;
  row.innerHTML = `
    <td><img class="log-face-thumb" src="${imgData}" alt="Face Photo"></td>
    <td><strong style="color: var(--text-main);">${log.timestamp || new Date().toLocaleTimeString()}</strong></td>
    <td><span class="pill ${log.punch_type || 'DENIED'}">${(log.punch_type || 'DENIED').replace('_', ' ')}</span></td>
    <td><strong>${log.name || 'Worker'}</strong> <span style="color: var(--text-muted); font-size: 0.8rem;">(${log.worker_id || 'N/A'})</span></td>
    <td>${modeBadge}</td>
    <td style="color: var(--text-muted);">${log.message || ''}</td>
  `;
  logsTableBody.insertBefore(row, logsTableBody.firstChild);
}

async function clearLogs() {
  if (confirm("Are you sure you want to clear all attendance logs?")) {
    await fetch("/api/attendance", { method: "DELETE" });
    logsTableBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 30px;">No attendance records logged yet.</td></tr>`;
    stats = { total: 0, punchIns: 0, punchOuts: 0 };
    updateStatsUI();
  }
}
