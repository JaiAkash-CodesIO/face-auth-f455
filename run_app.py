import uvicorn

if __name__ == "__main__":
    print("================================================================")
    print("Intel RealSense ID F455 - Firebase Biometric Dashboard")
    print("================================================================")
    print("Server launching at: http://localhost:8080")
    print("Open http://localhost:8080 in your browser to view the Dashboard!")
    print("Press Ctrl+C to stop the server.")
    print("================================================================")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, reload=True)
