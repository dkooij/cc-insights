#!/bin/bash
# Builds "CC Insights.app": a native window around the dashboard. It starts the
# server when opened and stops it when you quit. Needs the Xcode command line tools.
# Usage: ./install-app.sh [target-dir]   (default: ~/Applications)
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$HOME/Applications}"
APP="$TARGET/CC Insights.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
echo "$REPO" > "$APP/Contents/Resources/repo-path"

swiftc -O "$REPO/app/main.swift" -o "$APP/Contents/MacOS/cc-insights-bin"
swift "$REPO/app/make-icon.swift" "$REPO/app/icon.svg" "$APP/Contents/Resources/AppIcon.icns"

# Thin launcher so the bundle knows where the repo lives.
cat > "$APP/Contents/MacOS/cc-insights" <<LAUNCHER
#!/bin/bash
exec "\$(dirname "\$0")/cc-insights-bin" "\$(cat "\$(dirname "\$0")/../Resources/repo-path")"
LAUNCHER
chmod +x "$APP/Contents/MacOS/cc-insights"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>CC Insights</string>
  <key>CFBundleDisplayName</key><string>CC Insights</string>
  <key>CFBundleIdentifier</key><string>local.cc-insights</string>
  <key>CFBundleExecutable</key><string>cc-insights</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "Installed: $APP"
