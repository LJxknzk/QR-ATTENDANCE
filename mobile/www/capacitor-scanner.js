// Example Capacitor scanner helper for use inside teacher.html (www folder)
// Usage: include this script in teacher.html when running inside Capacitor

async function startNativeScan() {
  try {
    const { BarcodeScanner } = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.BarcodeScanner
      ? window.Capacitor.Plugins
      : await import('@capacitor-community/barcode-scanner');

    // Request permission
    const status = await BarcodeScanner.checkPermission({ force: true });
    if (!status.granted) {
      alert('Camera permission is required for scanning');
      return;
    }

    // Start scan
    const result = await BarcodeScanner.startScan();
    if (result && result.hasContent) {
      const payload = result.content;
      // Send to backend (token or legacy payload)
      const body = payload.startsWith('STUDENT_') ? { qr_data: payload } : { qr_token: payload };
      const res = await fetch(api('/api/attendance/scan'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      alert(data.message || JSON.stringify(data));
    }

    // Stop scanner
    await BarcodeScanner.stopScan();
  } catch (e) {
    console.error('Scan error', e);
    alert('Scan failed: ' + e.message);
  }
}

// Expose for inline use
window.startNativeScan = startNativeScan;
