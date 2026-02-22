import os
import json
import sys
import time

# Check dependencies first
try:
    import cv2
    import requests
except ImportError as e:
    print("=" * 60)
    print("ERROR: Missing required package!")
    print("=" * 60)
    print(f"Details: {e}")
    print("\nPlease install the missing packages:")
    print("  pip install opencv-python requests")
    print("=" * 60)
    input("Press Enter to exit...")
    sys.exit(1)

# Optional: pyzbar is a more robust QR decoder; fall back to OpenCV's built-in if missing
try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    _HAS_PYZBAR = True
    print("Using pyzbar QR decoder (more robust)")
except ImportError:
    _HAS_PYZBAR = False
    print("pyzbar not installed; using OpenCV QR decoder (install pyzbar for better detection)")

# ---------------------------------------------------------------------------
# Server URL resolution: the scanner needs to know where the Flask API lives.
#   Priority order:
#     1. QR_SCANNER_API_URL env var  (set by app.py launch-scanner endpoint)
#     2. desktop_config.json cloud_url (when mode == 'online')
#     3. Fallback: http://localhost:5000
# ---------------------------------------------------------------------------
def _resolve_api_url():
    """Determine the attendance scan API endpoint."""
    # 1. Explicit env var (highest priority)
    env_url = os.environ.get('QR_SCANNER_API_URL')
    if env_url:
        base = env_url.rstrip('/')
        return base if base.endswith('/api/attendance/scan') else f"{base}/api/attendance/scan"

    # 2. desktop_config.json next to this script
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'desktop_config.json')
    try:
        with open(config_path, 'r') as f:
            cfg = json.load(f)
        mode = cfg.get('mode', 'offline')
        if mode == 'online':
            cloud = cfg.get('cloud_url', '').rstrip('/')
            if cloud:
                print(f"[config] Online mode → {cloud}")
                return f"{cloud}/api/attendance/scan"
        else:
            local = cfg.get('server_url', 'http://localhost:5000').rstrip('/')
            print(f"[config] Offline mode → {local}")
            return f"{local}/api/attendance/scan"
    except Exception as e:
        print(f"[config] Could not read desktop_config.json: {e}")

    # 3. Fallback
    return "http://localhost:5000/api/attendance/scan"

API_URL = _resolve_api_url()
print(f"Scanner API endpoint: {API_URL}")

# Scanner secret must match SCANNER_SECRET on the server
SCANNER_SECRET = os.environ.get('SCANNER_SECRET', 'dev-scanner')

def scan_qr_webcam(camera_index=0):
    try:
        # Try multiple backends and camera indices to find a working camera
        cap = None
        backends = []
        if sys.platform == 'win32':
            backends = [
                ('DirectShow', cv2.CAP_DSHOW),
                ('MSMF', cv2.CAP_MSMF),
                ('Default', cv2.CAP_ANY),
            ]
        else:
            backends = [
                ('V4L2', getattr(cv2, 'CAP_V4L2', cv2.CAP_ANY)),
                ('Default', cv2.CAP_ANY),
            ]

        indices_to_try = [camera_index] + [i for i in range(4) if i != camera_index]

        for idx in indices_to_try:
            for backend_name, backend_id in backends:
                try:
                    test_cap = cv2.VideoCapture(idx, backend_id)
                    if test_cap.isOpened():
                        ret, frame = test_cap.read()
                        if ret and frame is not None:
                            cap = test_cap
                            print(f"Camera opened: index={idx}, backend={backend_name}")
                            break
                    test_cap.release()
                except Exception:
                    pass
            if cap is not None:
                break
        
        if cap is None or not cap.isOpened():
            print("ERROR: Could not open camera!")
            print("Please check:")
            print("1. Camera is connected")
            print("2. Camera permissions are granted")
            print("3. No other application is using the camera")
            print("4. Try a different camera index: python testscanner.py <index>")
            print("   e.g.  python testscanner.py 1")
            input("Press Enter to exit...")
            return
        
        detector = cv2.QRCodeDetector()

        # Set camera resolution for better QR detection
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Get resolution
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        print("=" * 60)
        print("QR SCANNER STARTED")
        print("=" * 60)
        print("Press 'Q' key to quit")
        print("Scanning QR codes to update attendance...")
        print(f"Server: {API_URL}")
        print("=" * 60)
        
        prev_time = time.time()
        last_scanned_qr = None
        last_scan_time = 0
        scan_cooldown = 3  # seconds between scans of the same QR
        # Info overlay state: when a scan succeeds, show student info until this timestamp
        info_display_until = 0
        info_details = None

        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to read frame from camera.")
                break

            # Detect and decode QR code
            data = None
            points = None

            if _HAS_PYZBAR:
                decoded_objects = pyzbar_decode(frame)
                if decoded_objects:
                    obj = decoded_objects[0]
                    data = obj.data.decode('utf-8', errors='replace')
                    # Draw bounding polygon
                    pts = obj.polygon
                    if pts and len(pts) >= 4:
                        import numpy as np
                        poly_pts = np.array([[p.x, p.y] for p in pts], dtype=int)
                        cv2.polylines(frame, [poly_pts], True, (0, 255, 0), 2)
            else:
                data, det_points, _ = detector.detectAndDecode(frame)
                if det_points is not None:
                    pts = det_points[0].astype(int)
                    for i in range(len(pts)):
                        cv2.line(frame, tuple(pts[i]), tuple(pts[(i+1) % len(pts)]), (0, 255, 0), 2)

            # Process QR code if detected
            if data:
                current_time = time.time()
                # Prevent duplicate scans
                if data != last_scanned_qr or (current_time - last_scan_time) > scan_cooldown:
                    last_scanned_qr = data
                    last_scan_time = current_time
                    
                    # Send to database
                    try:
                        response = requests.post(
                            API_URL,
                            json={'qr_data': data},
                            headers={
                                'Content-Type': 'application/json',
                                'X-Scanner-Secret': SCANNER_SECRET
                            },
                            timeout=5
                        )
                        result = response.json()
                        
                        if result.get('success'):
                            print(f"✓ {result.get('message')}")
                            cv2.putText(frame, "SUCCESS: " + result.get('message', ''), (10, 60),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                            # Prepare info overlay to show student info for 3 seconds
                            info_details = {
                                'student_name': result.get('student_name') or '',
                                'attendance_status': result.get('attendance_status') or '',
                                'message': result.get('message') or '',
                                'timestamp': result.get('timestamp') or ''
                            }
                            info_display_until = time.time() + 3.0
                        else:
                            print(f"✗ Error: {result.get('error')}")
                            cv2.putText(frame, "ERROR: " + result.get('error', ''), (10, 60),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                    except requests.exceptions.ConnectionError:
                        print(f"✗ Connection error: Cannot connect to server at {API_URL}")
                        print("   Make sure the server is running and reachable")
                        cv2.putText(frame, "Connection Error - Check Server", (10, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                    except requests.exceptions.Timeout:
                        print("✗ Request timeout")
                        cv2.putText(frame, "Request Timeout", (10, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                    except Exception as e:
                        print(f"✗ Error: {e}")
                
                cv2.putText(frame, f"QR: {data}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (255, 0, 0), 2)

            # Calculate FPS
            curr_time = time.time()
            fps = 1 / (curr_time - prev_time)
            prev_time = curr_time

            # Overlay resolution and FPS
            cv2.putText(frame, f"Resolution: {width}x{height}", (10, height - 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(frame, f"FPS: {int(fps)}", (10, height - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

            # If we have recent scan info, draw an overlay card for the configured duration
            if info_details and time.time() < info_display_until:
                # Draw semi-transparent rectangle
                overlay = frame.copy()
                card_w, card_h = 420, 100
                x, y = 10, 70
                cv2.rectangle(overlay, (x, y), (x + card_w, y + card_h), (50, 50, 50), -1)
                alpha = 0.7
                cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

                # Draw text lines
                txt_x, txt_y = x + 10, y + 25
                cv2.putText(frame, f"Name: {info_details.get('student_name')}", (txt_x, txt_y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(frame, f"Status: {info_details.get('attendance_status')}", (txt_x, txt_y + 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2)
                cv2.putText(frame, f"{info_details.get('message')}", (txt_x, txt_y + 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            else:
                # Clear expired info
                if info_details and time.time() >= info_display_until:
                    info_details = None

            cv2.imshow("QR Scanner - Press Q to Stop", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("=" * 60)
                print("Scanner stopped by user")
                print("=" * 60)
                break

        cap.release()
        cv2.destroyAllWindows()
        
    except Exception as e:
        print("=" * 60)
        print(f"FATAL ERROR: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")

if __name__ == "__main__":
    try:
        cam_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        scan_qr_webcam(cam_idx)
    except KeyboardInterrupt:
        print("\nScanner interrupted by user")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")