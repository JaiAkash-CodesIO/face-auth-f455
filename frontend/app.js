// ==========================================================================
// VeriFace ID • Enterprise Frontend Terminal Controller
// Biometric Attendance, Dual Theme (Light/Dark), Real-Time WebSockets
// ==========================================================================

let socket = null;
let stats = {
  total: 0,
  punchIns: 0,
  punchOuts: 0
};
let cachedLogs = [];
let currentFilter = "ALL";

// DOM References
const statusBanner = document.getElementById("statusBanner");
const mainStatusTitle = document.getElementById("mainStatusTitle");
const mainStatusSubtext = document.getElementById("mainStatusSubtext");
const metaUser = document.getElementById("metaUser");
const metaTime = document.getElementById("metaTime");
const metaMode = document.getElementById("metaMode");
const metaAction = document.getElementById("metaAction");
const capturedSnapshotImg = document.getElementById("capturedSnapshotImg");
const scanLaser = document.getElementById("scanLaser");

const deviceDot = document.getElementById("deviceDot");
const deviceStatusText = document.getElementById("deviceStatusText");
const firebaseStatusText = document.getElementById("firebaseStatusText");
const biometricModeText = document.getElementById("biometricModeText");
const liveClockTime = document.getElementById("liveClockTime");
const liveClockDate = document.getElementById("liveClockDate");

const themeToggleBtn = document.getElementById("themeToggleBtn");
const themeLabel = document.getElementById("themeLabel");

const punchBiometricMode = document.getElementById("punchBiometricMode");
const btnPunchAuto = document.getElementById("btnPunchAuto");
const btnOpenRegister = document.getElementById("btnOpenRegister");
const btnClearLogs = document.getElementById("btnClearLogs");
const btnRefreshLogs = document.getElementById("btnRefreshLogs");
const btnExportCSV = document.getElementById("btnExportCSV");
const logSearchInput = document.getElementById("logSearchInput");
const btnClearSearch = document.getElementById("btnClearSearch");
const logCounterBadge = document.getElementById("logCounterBadge");

const logsTableBody = document.getElementById("logsTableBody");
const registerModal = document.getElementById("registerModal");
const btnCloseModal = document.getElementById("btnCloseModal");
const btnCloseModalX = document.getElementById("btnCloseModalX");
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
const capacityBar = document.getElementById("capacityBar");

// Image preview modal
const imagePreviewModal = document.getElementById("imagePreviewModal");
const previewModalImg = document.getElementById("previewModalImg");
const previewModalName = document.getElementById("previewModalName");
const previewModalMeta = document.getElementById("previewModalMeta");
const btnCloseImagePreview = document.getElementById("btnCloseImagePreview");

const toastContainer = document.getElementById("toastContainer");

const DEFAULT_AVATAR = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' fill='%2364748b'><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-size='40'>👤</text></svg>";

// --- Initialize Application on DOM Ready ---
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  startLiveClock();
  connectWebSocket();
  checkDeviceStatus();
  fetchInitialLogs();
  setupFilterTabs();

  // Video feed autoreconnect logic
  const videoFeed = document.getElementById("videoFeed");
  if (videoFeed) {
    videoFeed.onerror = () => {
      console.warn("Video stream stalled/interrupted. Reconnecting...");
      setTimeout(() => {
        videoFeed.src = "/api/video_feed?t=" + Date.now();
      }, 1200);
    };
  }

  // Event Listeners
  if (themeToggleBtn) themeToggleBtn.addEventListener("click", toggleTheme);
  if (btnPunchAuto) btnPunchAuto.addEventListener("click", triggerPunch);
  if (btnOpenRegister) btnOpenRegister.addEventListener("click", () => openModal(registerModal));
  if (btnCloseModal) btnCloseModal.addEventListener("click", () => closeModal(registerModal));
  if (btnCloseModalX) btnCloseModalX.addEventListener("click", () => closeModal(registerModal));
  if (btnSubmitRegister) btnSubmitRegister.addEventListener("click", submitRegistration);
  if (btnClearLogs) btnClearLogs.addEventListener("click", clearLogs);
  if (btnRefreshLogs) btnRefreshLogs.addEventListener("click", () => {
    fetchInitialLogs();
    showToast("Audit Stream", "Attendance records refreshed from Firebase", "info");
  });
  if (btnExportCSV) btnExportCSV.addEventListener("click", exportLogsToCSV);

  if (logSearchInput) {
    logSearchInput.addEventListener("input", () => {
      if (btnClearSearch) btnClearSearch.style.display = logSearchInput.value ? "block" : "none";
      applyFiltersAndRender();
    });
  }

  if (btnClearSearch) {
    btnClearSearch.addEventListener("click", () => {
      logSearchInput.value = "";
      btnClearSearch.style.display = "none";
      applyFiltersAndRender();
    });
  }

  if (capturedSnapshotImg) {
    capturedSnapshotImg.addEventListener("click", () => {
      if (capturedSnapshotImg.src && !capturedSnapshotImg.src.startsWith("data:image/svg")) {
        openImagePreview(capturedSnapshotImg.src, metaUser.innerText || "Face Scan", metaTime.innerText);
      }
    });
  }

  if (btnCloseImagePreview) {
    btnCloseImagePreview.addEventListener("click", () => closeModal(imagePreviewModal));
  }

  if (punchBiometricMode) {
    punchBiometricMode.addEventListener("change", (e) => {
      const mode = e.target.value;
      if (biometricModeText) {
        if (mode === "HOST") biometricModeText.innerText = "Host Mode (Cloud)";
        else if (mode === "DEVICE") biometricModeText.innerText = "On-Device (Flash)";
        else biometricModeText.innerText = "Hybrid Mode";
      }
    });
  }
});

// --- Theme Management (Light / Dark Mode) ---
function initTheme() {
  const savedTheme = localStorage.getItem("veriface_theme");
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const initialTheme = savedTheme || (prefersDark ? "dark" : "dark"); // Default dark for enterprise HUD

  setTheme(initialTheme, false);
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
  const newTheme = currentTheme === "dark" ? "light" : "dark";
  setTheme(newTheme, true);
}

function setTheme(theme, triggerToast = true) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("veriface_theme", theme);
  
  if (themeLabel) {
    themeLabel.innerText = theme === "dark" ? "Dark" : "Light";
  }
  if (themeToggleBtn) {
    themeToggleBtn.title = theme === "dark" ? "Switch to Light Theme" : "Switch to Dark Theme";
  }

  if (triggerToast) {
    showToast("Theme Changed", `Switched to ${theme === "dark" ? "Dark Obsidian" : "Clean Light"} theme`, "info", 2000);
  }
}

// --- Live Clock & Date Widget ---
function startLiveClock() {
  function updateTime() {
    const now = new Date();
    if (liveClockTime) {
      liveClockTime.innerText = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    if (liveClockDate) {
      liveClockDate.innerText = now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    }
  }
  updateTime();
  setInterval(updateTime, 1000);
}

// --- Modal Utilities ---
function openModal(modal) {
  if (modal) modal.classList.add("active");
}

function closeModal(modal) {
  if (modal) modal.classList.remove("active");
}

function openImagePreview(imgSrc, title, subtitle) {
  if (previewModalImg) previewModalImg.src = imgSrc;
  if (previewModalName) previewModalName.innerText = title;
  if (previewModalMeta) previewModalMeta.innerText = subtitle || "";
  openModal(imagePreviewModal);
}

// --- WebSocket Connection ---
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
        if (data.logs) {
          cachedLogs = data.logs;
          applyFiltersAndRender();
          calculateStatsFromLogs(cachedLogs);
        }
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

// --- Check Device Status ---
async function checkDeviceStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    updateDeviceStatusUI(data.connected, data.message);
    if (firebaseStatusText && data.firebase_active !== undefined) {
      firebaseStatusText.innerText = data.firebase_active ? "Active" : "Offline";
    }
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
      if (capacityBar && data.max_capacity > 0) {
        const pct = Math.min(100, Math.max(0, (data.remaining_slots / data.max_capacity) * 100));
        capacityBar.style.width = `${pct}%`;
      }
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
    deviceStatusText.innerText = "Intel F455 Online";
  } else {
    deviceDot.className = "dot offline";
    deviceStatusText.innerText = message || "Disconnected";
  }
}

// --- Trigger Worker Attendance Punch ---
async function triggerPunch() {
  const chosenMode = punchBiometricMode ? punchBiometricMode.value : "HYBRID";
  const modeLabel = chosenMode === "HOST" 
    ? "Host Mode (Firebase Cloud)" 
    : (chosenMode === "DEVICE" ? "On-Device Hardware" : "Hybrid Engine (Auto)");

  setScanningState("BIOMETRIC SCANNING...", `Matching face against ${modeLabel}...`);

  try {
    const res = await fetch("/api/punch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "AUTO", biometric_mode: chosenMode })
    });
    const data = await res.json();
    stopScanningAnimation();
    handlePunchEvent(data);
  } catch (err) {
    stopScanningAnimation();
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

// --- Submit Worker Registration ---
async function submitRegistration() {
  const wId = regWorkerId.value.trim();
  const name = regName.value.trim();
  const dept = regDepartment.value.trim() || "General";
  const role = regRole.value.trim() || "Worker";
  const bioMode = regBiometricMode ? regBiometricMode.value : "HOST";

  if (!wId || !name) {
    showToast("Validation Error", "Please enter both Worker ID and Full Name", "error");
    return;
  }

  closeModal(registerModal);
  const modeLabel = bioMode === "HOST" ? "Firebase Cloud Faceprints" : "F455 Hardware Flash";
  setScanningState("ENROLLING FACEPRINT...", `Extracting biometric vectors for '${name}' (${wId}) in ${modeLabel}...`);

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
    stopScanningAnimation();
    
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
      showToast("Worker Registered", `Successfully enrolled ${name} (${wId})`, "success");
      // Reset inputs
      regWorkerId.value = "";
      regName.value = "";
    } else {
      updateBanner("state-denied", "REGISTRATION FAILED", data.message || "Failed to capture face.", `${name} (${wId})`, new Date().toLocaleTimeString(), "FAILED", null, bioMode);
      showToast("Registration Failed", data.message || "Face not detected", "error");
    }
    fetchInitialLogs();
    fetchCapacityInfo();
  } catch (err) {
    stopScanningAnimation();
    showToast("Error", "Registration request failed: " + err.message, "error");
  }
}

// --- Handle Punch Result Event ---
function handlePunchEvent(event) {
  stats.total++;
  const imgData = event.image_b64 || DEFAULT_AVATAR;
  const modeStr = event.auth_mode === "HOST" 
    ? "🔥 Host Mode (Firebase)" 
    : (event.auth_mode === "DEVICE" ? "💾 On-Device (F455)" : "🧬 Hybrid");

  if (event.status === "AUTHENTICATED" || event.success) {
    if (event.punch_type === "PUNCH_IN") {
      stats.punchIns++;
      updateBanner("state-authenticated", "🟢 PUNCH IN SUCCESS", `Worker ${event.name || event.worker_id} Punched In`, `${event.name || 'Worker'} (${event.worker_id || 'ID'})`, event.time || event.timestamp, "PUNCH IN", imgData, modeStr);
      showToast("Punch In Verified", `${event.name || 'Worker'} punched in successfully`, "success");
    } else {
      stats.punchOuts++;
      updateBanner("state-authenticated", "🔵 PUNCH OUT SUCCESS", `Worker ${event.name || event.worker_id} Punched Out`, `${event.name || 'Worker'} (${event.worker_id || 'ID'})`, event.time || event.timestamp, "PUNCH OUT", imgData, modeStr);
      showToast("Punch Out Verified", `${event.name || 'Worker'} punched out successfully`, "info");
    }
  } else {
    updateBanner("state-denied", "⛔ ACCESS DENIED", event.message || "Unrecognized face or spoof detected.", "Unknown", event.timestamp || new Date().toLocaleTimeString(), "DENIED", imgData, modeStr);
    showToast("Access Denied", event.message || "Face biometric unverified", "error");
  }

  updateStatsUI();
  prependLogRecord(event);
}

function setScanningState(title, subtext) {
  statusBanner.className = "status-card state-ready";
  mainStatusTitle.innerText = title;
  mainStatusSubtext.innerText = subtext;
  if (scanLaser) {
    scanLaser.style.opacity = "1";
    scanLaser.style.animation = "scanMotion 1.2s infinite alternate ease-in-out";
  }
}

function stopScanningAnimation() {
  if (scanLaser) {
    scanLaser.style.opacity = "0";
    scanLaser.style.animation = "none";
  }
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

// --- Filter Tabs Setup ---
function setupFilterTabs() {
  const tabs = document.querySelectorAll(".filter-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      currentFilter = tab.getAttribute("data-filter") || "ALL";
      applyFiltersAndRender();
    });
  });
}

// --- Fetch & Render Attendance Logs ---
async function fetchInitialLogs() {
  try {
    const res = await fetch("/api/attendance");
    const logs = await res.json();
    cachedLogs = logs || [];
    applyFiltersAndRender();
    calculateStatsFromLogs(cachedLogs);
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

function applyFiltersAndRender() {
  const query = (logSearchInput ? logSearchInput.value : "").trim().toLowerCase();
  
  let filtered = cachedLogs.filter(log => {
    // 1. Tab filter
    if (currentFilter !== "ALL") {
      const pType = (log.punch_type || "").toUpperCase();
      if (currentFilter === "PUNCH_IN" && pType !== "PUNCH_IN") return false;
      if (currentFilter === "PUNCH_OUT" && pType !== "PUNCH_OUT") return false;
      if (currentFilter === "DENIED" && pType !== "DENIED") return false;
    }
    // 2. Search query filter
    if (query) {
      const name = (log.name || "").toLowerCase();
      const wid = (log.worker_id || "").toLowerCase();
      const type = (log.punch_type || "").toLowerCase();
      const mode = (log.auth_mode || "").toLowerCase();
      const msg = (log.message || "").toLowerCase();
      return name.includes(query) || wid.includes(query) || type.includes(query) || mode.includes(query) || msg.includes(query);
    }
    return true;
  });

  if (logCounterBadge) {
    logCounterBadge.innerText = `${filtered.length} of ${cachedLogs.length} Records`;
  }

  renderLogsTable(filtered);
}

function renderLogsTable(logs) {
  if (!logs || logs.length === 0) {
    logsTableBody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 48px;">
          <div style="font-size: 1.8rem; margin-bottom: 8px;">📭</div>
          <p style="font-weight: 600;">No attendance records match your current criteria.</p>
        </td>
      </tr>
    `;
    return;
  }

  logsTableBody.innerHTML = logs.map(log => {
    const isHost = (log.auth_mode === "HOST" || !log.auth_mode);
    const modeBadge = isHost 
      ? `<span class="mode-badge host">🔥 Host Mode</span>`
      : `<span class="mode-badge device">💾 On-Device</span>`;
    
    const imgSrc = log.image_b64 || DEFAULT_AVATAR;
    const nameStr = log.name || 'Worker';
    const timeStr = log.timestamp || log.date || '--';

    return `
      <tr>
        <td>
          <img class="log-face-thumb" src="${imgSrc}" alt="${nameStr}" 
            onclick="openImagePreview('${imgSrc}', '${nameStr}', '${timeStr}')"
            title="Click to view full photo">
        </td>
        <td><strong style="color: var(--text-main); font-family: var(--font-mono); font-size: 0.82rem;">${timeStr}</strong></td>
        <td><span class="pill ${log.punch_type || 'DENIED'}">${(log.punch_type || 'DENIED').replace('_', ' ')}</span></td>
        <td>
          <strong style="color: var(--text-main);">${nameStr}</strong> 
          <span style="color: var(--text-muted); font-size: 0.8rem; margin-left: 4px;">(${log.worker_id || 'N/A'})</span>
        </td>
        <td>${modeBadge}</td>
        <td style="color: var(--text-secondary); font-size: 0.84rem;">${log.message || ''}</td>
      </tr>
    `;
  }).join("");
}

function prependLogRecord(log) {
  cachedLogs.unshift(log);
  applyFiltersAndRender();
}

async function clearLogs() {
  if (confirm("Are you sure you want to permanently clear all attendance records?")) {
    await fetch("/api/attendance", { method: "DELETE" });
    cachedLogs = [];
    applyFiltersAndRender();
    stats = { total: 0, punchIns: 0, punchOuts: 0 };
    updateStatsUI();
    showToast("Audit Logs", "Attendance audit records cleared", "info");
  }
}

// --- Export to CSV ---
function exportLogsToCSV() {
  if (!cachedLogs || cachedLogs.length === 0) {
    showToast("Export Notice", "No attendance logs available to export", "info");
    return;
  }

  const headers = ["Timestamp", "Punch Action", "Worker ID", "Worker Name", "Biometric Mode", "Verification Message"];
  const rows = cachedLogs.map(log => [
    `"${log.timestamp || log.date || ''}"`,
    `"${log.punch_type || 'DENIED'}"`,
    `"${log.worker_id || ''}"`,
    `"${log.name || ''}"`,
    `"${log.auth_mode || 'HOST'}"`,
    `"${(log.message || '').replace(/"/g, '""')}"`
  ]);

  const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  const dateStr = new Date().toISOString().split("T")[0];
  link.setAttribute("download", `veriface_attendance_${dateStr}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  showToast("CSV Exported", `Downloaded ${cachedLogs.length} attendance records`, "success");
}

// --- Toast Notification Helper ---
function showToast(title, message, type = "info", duration = 3500) {
  if (!toastContainer) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  let icon = "ℹ️";
  if (type === "success") icon = "✅";
  else if (type === "error") icon = "⚠️";

  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      <div class="toast-message">${message}</div>
    </div>
  `;

  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(40px)";
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }, duration);
}
