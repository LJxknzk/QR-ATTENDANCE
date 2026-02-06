QR Attendance - Capacitor Mobile Scaffold

This folder contains a minimal Capacitor scaffold to package the existing web UI as a native installable app for Android/iOS.

Quick steps (developer machine with Node.js installed):

QR Attendance - Capacitor Mobile Scaffold

This folder contains a minimal Capacitor scaffold to package the existing web UI as a native installable app for Android/iOS.

Quick steps (developer machine with Node.js installed):

1. Install dependencies

```powershell
cd mobile
npm install --legacy-peer-deps
```

2. Copy web assets (project root -> mobile/www)

```powershell
npm run copy-web
```

3. Initialize Capacitor (first time only)

```powershell
npx cap init qr-attendance com.example.qrattendance --web-dir=www
```

4. Add native platforms

```powershell
npx cap add android
# or
npx cap add ios
```

5. Build and copy web assets, then open native IDE

```powershell
# Re-copy web files after edits
npm run copy-web
npx cap copy
npx cap open android
# or
npx cap open ios
```

Notes:
- The `copy-web` script copies index.html, teacher.html, student.html, admin.html, accountcreate.html and the CSS/JS folders into mobile/www. Adjust the list in copy-web.js if you have additional assets.
- For scanning, we recommend installing the plugin: @capacitor-community/barcode-scanner
  - npm install @capacitor-community/barcode-scanner --legacy-peer-deps
  - npx cap sync
- The example helper `www/capacitor-scanner.js` shows how to call the native scanner and POST the result to /api/attendance/scan. Include it in `teacher.html` when packaging to native.
- Ensure your backend is reachable from the device (use a public URL or `ngrok` during development). Update CORS_ORIGINS and environment variables on the server.

Security & production:
- Set FLASK_SECRET_KEY and QR_SIGNING_SECRET as environment variables on your server.
- Use HTTPS for backend endpoints.
- Consider switching from SQLite to a managed Postgres DB (set DATABASE_URL) before deploying.

Troubleshooting: Java / Android SDK / Gradle
-----------------------------------------
This project requires a Java JDK and Android SDK to build a native Android APK. If you see errors like "JAVA_HOME is not set" or "Unable to launch Android Studio", follow these steps.

1) Verify environment (built-in helper)

  From the `mobile` folder run:

  ```powershell
  .\check_env.ps1
  ```

  The script reports `java -version`, `JAVA_HOME`, `ANDROID_SDK_ROOT`/`ANDROID_HOME`, and whether `android\gradlew.bat` exists.

2) Install Java (JDK 11+)

  - Download and install a JDK (Adoptium/Temurin, Azul Zulu, or Oracle). Recommended: Temurin 17.
  - Set `JAVA_HOME` (PowerShell example - adjust path to your JDK install):

  ```powershell
  setx JAVA_HOME "C:\Program Files\Eclipse Adoptium\jdk-17"
  setx PATH "$env:PATH;C:\Program Files\Eclipse Adoptium\jdk-17\bin"
  # Close and reopen terminal after setx
  java -version
  ```

3) Install Android Studio (includes Android SDK)

  - Download Android Studio and install the recommended SDK and platform tools.
  - After install, set `ANDROID_SDK_ROOT` (example path):

  ```powershell
  setx ANDROID_SDK_ROOT "C:\Users\<YourUser>\AppData\Local\Android\Sdk"
  setx PATH "$env:PATH;C:\Users\<YourUser>\AppData\Local\Android\Sdk\platform-tools"
  # Restart terminal; verify:
  adb --version
  ```

4) Build workflow (once env is configured)

  From `mobile`:

  ```powershell
  npm run copy-web
  npx cap copy
  npx cap sync
  npx cap open android   # opens Android Studio
  ```

  Or build from the CLI inside the Android project (requires Java + SDK):

  ```powershell
  cd mobile\android
  .\gradlew.bat assembleDebug
  # APK output: android\app\build\outputs\apk\debug\app-debug.apk
  ```

5) If Capacitor cannot auto-open Android Studio

  - Set the full path to Android Studio executable using the CAPACITOR_ANDROID_STUDIO_PATH env var, or open the `mobile/android` folder manually in Android Studio (File → Open).

Quick helper (optional)
-----------------------
I added `mobile/set_env.ps1` to help you set `JAVA_HOME` and `ANDROID_SDK_ROOT` interactively. Running it will call `setx` for you (you still must replace paths with the correct installation paths if the defaults are wrong).

Security notes
--------------
- `setx` persists environment variables system-wide — review before running. Restart your terminal after running it.
- Do not commit secrets or local paths into version control.

