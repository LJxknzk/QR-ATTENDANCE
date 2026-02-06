param(
    [string]$javaPath = "",
    [string]$sdkPath = ""
)

Write-Host "This script helps set JAVA_HOME and ANDROID_SDK_ROOT using setx." -ForegroundColor Cyan

if (-not $javaPath) {
    $javaPath = Read-Host "Enter JDK installation path (e.g. C:\\Program Files\\Eclipse Adoptium\\jdk-17)"
}

if ($javaPath) {
    Write-Host "Setting JAVA_HOME to: $javaPath" -ForegroundColor Yellow
    setx JAVA_HOME "$javaPath"
    $bin = Join-Path $javaPath "bin"
    Write-Host "Adding $bin to PATH (setx)" -ForegroundColor Yellow
    setx PATH "$env:PATH;$bin"
    Write-Host "JAVA_HOME set. Restart your terminal session to apply changes." -ForegroundColor Green
}

if (-not $sdkPath) {
    $sdkPath = Read-Host "Enter Android SDK path (e.g. C:\\Users\\<you>\\AppData\\Local\\Android\\Sdk) or leave blank to skip"
}

if ($sdkPath) {
    Write-Host "Setting ANDROID_SDK_ROOT to: $sdkPath" -ForegroundColor Yellow
    setx ANDROID_SDK_ROOT "$sdkPath"
    $platformTools = Join-Path $sdkPath "platform-tools"
    Write-Host "Adding $platformTools to PATH (setx)" -ForegroundColor Yellow
    setx PATH "$env:PATH;$platformTools"
    Write-Host "ANDROID_SDK_ROOT set. Restart your terminal session to apply changes." -ForegroundColor Green
}

Write-Host "Done. Run .\check_env.ps1 to verify." -ForegroundColor Cyan
