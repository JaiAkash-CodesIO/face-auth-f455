param(
    [Parameter(Position=0, Mandatory=$true)]
    [string]$Command,

    [Parameter(Position=1)]
    [string]$Port = "COM4",

    [Parameter(Position=2)]
    [string]$Param = ""
)

$ErrorActionPreference = "Stop"

$toolsDir = "C:\Users\govar\AppData\Local\Programs\RealSenseID Tools"
$dllPath = "$toolsDir\rsid_dotnet.dll"

# Register AssemblyResolve handler so .NET finds rsid_dotnet.dll
[System.AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($sender, $eventArgs)
    if ($eventArgs.Name -like "rsid_dotnet*") {
        return [System.Reflection.Assembly]::LoadFrom($dllPath)
    }
    return $null
})

# Preload unmanaged C++ DLLs
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Native {
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Auto)]
    public static extern IntPtr LoadLibrary(string path);
}
"@ -ErrorAction SilentlyContinue

[Win32Native]::LoadLibrary("$toolsDir\rsid.dll") | Out-Null
[Win32Native]::LoadLibrary("$toolsDir\rsid_c.dll") | Out-Null

# Compile Native Host Biometric Engine
Add-Type -ReferencedAssemblies @($dllPath, "System.Web.Extensions.dll") -TypeDefinition @"
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using rsid;

public class NativeHostBridge {
    public class StoredFaceprint {
        public string worker_id { get; set; }
        public int version { get; set; }
        public int featuresType { get; set; }
        public int flags { get; set; }
        public short[] vector { get; set; }
    }

    public static string Enroll(string port, string userId) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                short[] extractedVec = null;
                int version = 9;
                int featuresType = 0;
                int flags = 0;
                EnrollStatus finalStatus = EnrollStatus.Failure;

                var args = new EnrollExtractArgs();
                args.resultClbk = new EnrollExtractionResultCallback((status, handle, ctx) => {
                    finalStatus = status;
                    if (status == EnrollStatus.Success && handle != IntPtr.Zero) {
                        version = Marshal.ReadInt32(handle, 20);
                        featuresType = Marshal.ReadInt32(handle, 24);
                        flags = Marshal.ReadInt32(handle, 28);
                        extractedVec = new short[515];
                        Marshal.Copy(IntPtr.Add(handle, 32), extractedVec, 0, 515);
                    }
                });

                var eStatus = auth.EnrollExtractFaceprints(args);
                auth.Disconnect();

                if (finalStatus == EnrollStatus.Success && extractedVec != null) {
                    string vectorJson = "[" + string.Join(",", extractedVec) + "]";
                    return "{\"success\":true,\"user_id\":\"" + userId + "\",\"version\":" + (version > 0 ? version : 9) + ",\"featuresType\":" + featuresType + ",\"flags\":" + flags + ",\"vector\":" + vectorJson + "}";
                } else {
                    return "{\"success\":false,\"error\":\"Face extraction ended with status: " + finalStatus + " (device status: " + eStatus + ")\"}";
                }
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string Match(string port, string dbJsonPath) {
        try {
            if (!File.Exists(dbJsonPath)) {
                return "{\"success\":false,\"error\":\"Database JSON file not found: " + dbJsonPath.Replace("\\", "\\\\") + "\"}";
            }

            string jsonContent = File.ReadAllText(dbJsonPath);
            var serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = int.MaxValue;
            var users = serializer.Deserialize<List<StoredFaceprint>>(jsonContent);

            if (users == null || users.Count == 0) {
                return "{\"success\":false,\"status\":\"NO_USERS\",\"error\":\"No enrolled host faceprints in database\"}";
            }

            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                short[] scannedVec = null;
                int scannedVer = 9;
                int scannedFeaturesType = 0;
                AuthStatus finalAuthStatus = AuthStatus.Failure;

                var args = new AuthExtractArgs();
                args.resultClbk = new AuthExtractionResultCallback((status, handle, ctx) => {
                    finalAuthStatus = status;
                    if (status == AuthStatus.Success && handle != IntPtr.Zero) {
                        scannedVer = Marshal.ReadInt32(handle, 0);
                        scannedFeaturesType = Marshal.ReadInt32(handle, 4);
                        scannedVec = new short[515];
                        Marshal.Copy(IntPtr.Add(handle, 12), scannedVec, 0, 515);
                    }
                });

                var aStatus = auth.AuthenticateExtractFaceprints(args);

                if (finalAuthStatus != AuthStatus.Success || scannedVec == null) {
                    auth.Disconnect();
                    return "{\"success\":false,\"status\":\"DENIED\",\"error\":\"Face extraction failed or no face in frame (status: " + finalAuthStatus + ")\"}";
                }

                MatchElement scanned = new MatchElement();
                scanned.version = (scannedVer > 0 ? scannedVer : 9);
                scanned.flags = 1; // RealSenseID OpFlagAuthWithoutMask
                scanned.featuresType = scannedFeaturesType;
                scanned.featuresVector = scannedVec;

                int bestScore = -1;
                string winningId = null;

                foreach (var u in users) {
                    if (u.vector == null || u.vector.Length == 0) continue;

                    short[] fullVec = new short[515];
                    Array.Copy(u.vector, fullVec, Math.Min(u.vector.Length, 515));

                    MatchArgs mArgs = new MatchArgs();
                    mArgs.newFaceprints = scanned;

                    mArgs.existingFaceprints = new Faceprints();
                    mArgs.existingFaceprints.reserved = new int[5];
                    mArgs.existingFaceprints.version = (u.version > 0 ? u.version : scanned.version);
                    mArgs.existingFaceprints.flags = 0;
                    mArgs.existingFaceprints.featuresType = (u.featuresType >= 0 ? u.featuresType : scanned.featuresType);
                    mArgs.existingFaceprints.enrollmentDescriptor = fullVec;
                    mArgs.existingFaceprints.adaptiveDescriptorWithoutMask = fullVec;

                    mArgs.updatedFaceprints = new Faceprints();
                    mArgs.updatedFaceprints.reserved = new int[5];
                    mArgs.updatedFaceprints.adaptiveDescriptorWithoutMask = new short[515];
                    mArgs.updatedFaceprints.enrollmentDescriptor = new short[515];

                    MatchResult mRes = auth.MatchFaceprintsToFaceprints(ref mArgs);
                    if (mRes.success == 1 && mRes.score > bestScore) {
                        bestScore = mRes.score;
                        winningId = u.worker_id;
                    }
                }

                auth.Disconnect();

                if (winningId != null && bestScore >= 0) {
                    return "{\"success\":true,\"status\":\"AUTHENTICATED\",\"user_id\":\"" + winningId + "\",\"score\":" + bestScore + "}";
                } else {
                    return "{\"success\":false,\"status\":\"DENIED\",\"error\":\"Face not recognized in cloud faceprint database\"}";
                }
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string AuthenticateDevice(string port) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                AuthStatus finalStatus = AuthStatus.Failure;
                string matchedUser = null;

                var args = new AuthArgs();
                args.resultClbk = new AuthResultCallback((status, userId, idx, ctx) => {
                    finalStatus = status;
                    if (status == AuthStatus.Success) {
                        matchedUser = userId;
                    }
                });

                var aStatus = auth.Authenticate(args);
                auth.Disconnect();

                if (finalStatus == AuthStatus.Success && !string.IsNullOrEmpty(matchedUser)) {
                    return "{\"success\":true,\"status\":\"AUTHENTICATED\",\"user_id\":\"" + matchedUser + "\"}";
                } else {
                    return "{\"success\":false,\"status\":\"DENIED\",\"error\":\"Device authentication failed (status: " + finalStatus + ")\"}";
                }
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string EnrollDevice(string port, string userId) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                EnrollStatus finalStatus = EnrollStatus.Failure;

                var args = new EnrollArgs();
                args.userId = userId;
                args.resultClbk = new EnrollResultCallback((status, ctx) => {
                    finalStatus = status;
                });

                var eStatus = auth.Enroll(args);
                auth.Disconnect();

                if (finalStatus == EnrollStatus.Success) {
                    return "{\"success\":true,\"status\":\"ENROLLED\",\"user_id\":\"" + userId + "\"}";
                } else {
                    return "{\"success\":false,\"status\":\"FAILED\",\"error\":\"Device enrollment failed (status: " + finalStatus + ")\"}";
                }
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string QueryDeviceUsers(string port) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                string[] users = null;
                var qStatus = auth.QueryUserIds(out users);
                auth.Disconnect();

                if (qStatus == Status.Ok && users != null) {
                    var serializer = new JavaScriptSerializer();
                    return "{\"success\":true,\"users\":" + serializer.Serialize(users) + "}";
                } else {
                    return "{\"success\":true,\"users\":[]}";
                }
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string RemoveDeviceUser(string port, string userId) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                var rStatus = auth.RemoveUser(userId);
                auth.Disconnect();

                return "{\"success\":" + (rStatus == Status.Ok ? "true" : "false") + ",\"status\":\"" + rStatus + "\"}";
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }

    public static string GetDeviceFaceprints(string port) {
        try {
            using (var auth = new Authenticator(DeviceType.F45x)) {
                var cfg = new SerialConfig { port = port };
                var cStatus = auth.Connect(cfg);
                if (cStatus != Status.Ok) {
                    return "{\"success\":false,\"error\":\"Connect failed: " + cStatus + "\"}";
                }

                var list = auth.GetUsersFaceprints();
                auth.Disconnect();

                if (list == null || list.Count == 0) {
                    return "{\"success\":true,\"users\":[]}";
                }

                var serializer = new JavaScriptSerializer();
                serializer.MaxJsonLength = int.MaxValue;
                var resList = new List<Dictionary<string, object>>();

                foreach (var u in list) {
                    var dict = new Dictionary<string, object>();
                    dict["worker_id"] = u.userID;
                    dict["version"] = u.faceprints.version;
                    dict["featuresType"] = u.faceprints.featuresType;
                    dict["flags"] = u.faceprints.flags;
                    dict["vector"] = u.faceprints.adaptiveDescriptorWithoutMask ?? u.faceprints.enrollmentDescriptor;
                    resList.Add(dict);
                }

                return "{\"success\":true,\"users\":" + serializer.Serialize(resList) + "}";
            }
        } catch (Exception ex) {
            return "{\"success\":false,\"error\":\"Exception: " + ex.Message.Replace("\"", "'") + "\"}";
        }
    }
}
"@ -ErrorAction SilentlyContinue

switch ($Command.ToLower()) {
    "enroll" {
        $result = [NativeHostBridge]::Enroll($Port, $Param)
        Write-Output $result
    }
    "match" {
        $result = [NativeHostBridge]::Match($Port, $Param)
        Write-Output $result
    }
    "auth_device" {
        $result = [NativeHostBridge]::AuthenticateDevice($Port)
        Write-Output $result
    }
    "enroll_device" {
        $result = [NativeHostBridge]::EnrollDevice($Port, $Param)
        Write-Output $result
    }
    "users_device" {
        $result = [NativeHostBridge]::QueryDeviceUsers($Port)
        Write-Output $result
    }
    "get_device_faceprints" {
        $result = [NativeHostBridge]::GetDeviceFaceprints($Port)
        Write-Output $result
    }
    "remove_device" {
        $result = [NativeHostBridge]::RemoveDeviceUser($Port, $Param)
        Write-Output $result
    }
    default {
        Write-Output "{\"success\":false,\"error\":\"Unknown command: $Command\"}"
    }
}
