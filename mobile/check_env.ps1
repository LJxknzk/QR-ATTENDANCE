Write-Host "Checking environment for Android build..." -ForegroundColor Cyan

# Java
Write-Host "\nJava:" -ForegroundColor Yellow
try {
    & java -version 2>&1 | ForEach-Object { Write-Host $_ }
} catch {
    Write-Host "java not found in PATH" -ForegroundColor Red
}

Write-Host "JAVA_HOME: " -NoNewline
if ($env:JAVA_HOME) { Write-Host $env:JAVA_HOME -ForegroundColor Green } else { Write-Host "(not set)" -ForegroundColor Red }

# Android SDK
Write-Host "\nAndroid SDK variables:" -ForegroundColor Yellow
Write-Host "ANDROID_HOME: " -NoNewline
if ($env:ANDROID_HOME) { Write-Host $env:ANDROID_HOME -ForegroundColor Green } else { Write-Host "(not set)" -ForegroundColor Red }
Write-Host "ANDROID_SDK_ROOT: " -NoNewline
if ($env:ANDROID_SDK_ROOT) { Write-Host $env:ANDROID_SDK_ROOT -ForegroundColor Green } else { Write-Host "(not set)" -ForegroundColor Red }

# Gradle wrapper
Write-Host "\nGradle wrapper:" -ForegroundColor Yellow
if (Test-Path "android\gradlew.bat") { Write-Host "android\gradlew.bat exists" -ForegroundColor Green } else { Write-Host "android\gradlew.bat not found" -ForegroundColor Red }

# Node/npm
Write-Host "\nNode & npm:" -ForegroundColor Yellow
try { & node -v | ForEach-Object { Write-Host "node: " $_ } } catch { Write-Host "node not found" -ForegroundColor Red }
try { & npm -v | ForEach-Object { Write-Host "npm: " $_ } } catch { Write-Host "npm not found" -ForegroundColor Red }

# Capacitor
Write-Host "\nCapacitor files:" -ForegroundColor Yellow
if (Test-Path "android") { Write-Host "android folder exists" -ForegroundColor Green } else { Write-Host "android folder missing" -ForegroundColor Red }

Write-Host "\nDone." -ForegroundColor Cyan
