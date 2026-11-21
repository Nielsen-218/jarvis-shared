# Jarvis Link: offline build without Gradle (the cached Gradle 8.11.1 has no AGP 8.9.1 jars, so Gradle needs the
# internet). Plain SDK tools: aapt2 compile/link -> javac against android.jar -> d8 -> add classes.dex -> zipalign ->
# apksigner (v1 + v2) -> verify. Output: dist\JarvisLink.apk
#
#   pwsh -File android\jarvis-link\build.ps1
#
# Signing: the keystore and its password are OUTSIDE git, in a properties file (storeFile, storePassword, keyAlias,
# keyPassword); default C:\Users\you\ECC\jarvis-backups\jarvis-link-signing\signing.properties, or $env:JARVIS_LINK_SIGNING.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { 'C:\Android' }
$bt = Join-Path $sdk 'build-tools\35.0.0'
$platform = Join-Path $sdk 'platforms\android-34\android.jar'
$jdk = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot' }
$signingFile = if ($env:JARVIS_LINK_SIGNING) { $env:JARVIS_LINK_SIGNING } else { 'C:\Users\you\ECC\jarvis-backups\jarvis-link-signing\signing.properties' }

$src = Join-Path $here 'app\src\main'
$out = Join-Path $here 'build'
$dist = Join-Path $here 'dist'

function Invoke-Tool([string]$exe, [string[]]$toolArgs) {
    & $exe @toolArgs
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($exe)) failed ($LASTEXITCODE)" }
}

if (-not (Test-Path $signingFile)) { throw "Signing properties not found: $signingFile (see README.md)" }
$props = @{}
foreach ($line in Get-Content $signingFile) {
    if ($line -match '^\s*([^#=]+?)\s*=\s*(.*)$') { $props[$Matches[1]] = $Matches[2] }
}

if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out, "$out\gen", "$out\classes", "$out\dex", $dist | Out-Null

# 1. resources + manifest (the source manifest has no package attribute, as AGP 8 wants; aapt2 needs one)
$manifest = (Get-Content -Raw "$src\AndroidManifest.xml") -replace '<manifest ', '<manifest package="com.jarvis.link" '
[IO.File]::WriteAllText("$out\AndroidManifest.xml", $manifest)
Invoke-Tool "$bt\aapt2.exe" @('compile', '--dir', "$src\res", '-o', "$out\res.zip")
Invoke-Tool "$bt\aapt2.exe" @('link', '-I', $platform, '--manifest', "$out\AndroidManifest.xml",
    '--min-sdk-version', '26', '--target-sdk-version', '34', '--version-code', '4', '--version-name', '1.3',
    '--java', "$out\gen", '-o', "$out\unsigned.apk", "$out\res.zip")

# 2. Java -> class files -> dex
$sources = @(Get-ChildItem -Recurse -Filter *.java "$src\java", "$out\gen" | ForEach-Object FullName)
Invoke-Tool "$jdk\bin\javac.exe" (@('-encoding', 'UTF-8', '-source', '11', '-target', '11', '-Xlint:-options',
    '-classpath', $platform, '-d', "$out\classes") + $sources)
$classes = @(Get-ChildItem -Recurse -Filter *.class "$out\classes" | ForEach-Object FullName)
Invoke-Tool "$bt\d8.bat" (@('--release', '--min-api', '26', '--lib', $platform, '--output', "$out\dex") + $classes)

# 3. classes.dex into the APK (existing entries, e.g. the uncompressed resources.arsc, are copied as they are)
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open("$out\unsigned.apk", [IO.Compression.ZipArchiveMode]::Update)
try {
    [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, "$out\dex\classes.dex", 'classes.dex',
        [IO.Compression.CompressionLevel]::Optimal) | Out-Null
} finally { $zip.Dispose() }

# 4. align, sign (passwords via environment, never on a command line), verify
Invoke-Tool "$bt\zipalign.exe" @('-p', '-f', '4', "$out\unsigned.apk", "$out\aligned.apk")
$env:JL_STORE_PASS = $props['storePassword']
$env:JL_KEY_PASS = $props['keyPassword']
try {
    Invoke-Tool "$bt\apksigner.bat" @('sign', '--ks', $props['storeFile'], '--ks-key-alias', $props['keyAlias'],
        '--ks-pass', 'env:JL_STORE_PASS', '--key-pass', 'env:JL_KEY_PASS',
        '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true',
        '--out', "$dist\JarvisLink.apk", "$out\aligned.apk")
} finally {
    Remove-Item Env:JL_STORE_PASS, Env:JL_KEY_PASS -ErrorAction SilentlyContinue
}
if (Test-Path "$dist\JarvisLink.apk.idsig") { Remove-Item "$dist\JarvisLink.apk.idsig" }
Invoke-Tool "$bt\apksigner.bat" @('verify', '--verbose', "$dist\JarvisLink.apk")
Invoke-Tool "$bt\aapt2.exe" @('dump', 'badging', "$dist\JarvisLink.apk")
$size = (Get-Item "$dist\JarvisLink.apk").Length
Write-Host "Built $dist\JarvisLink.apk ($size bytes)"
