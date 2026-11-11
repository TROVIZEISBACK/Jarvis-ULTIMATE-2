#!/bin/zsh
# Build JARVIS.app into ~/Applications (re-run after moving this folder).
set -e
cd "${0:A:h}"
PROJECT="$PWD"
APP="${JARVIS_APP_DIR:-$HOME/Applications}/JARVIS.app"
[ -x .venv/bin/python ] || { echo "Run ./run.sh once first to set up the virtualenv."; exit 1; }
xcrun --find swiftc >/dev/null 2>&1 || { echo "Needs the Swift compiler: xcode-select --install"; exit 1; }

echo "Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -O -target "$(uname -m)-apple-macos13.0" -o "$APP/Contents/MacOS/JARVIS" macapp/JARVIS.swift
.venv/bin/python macapp/make_icon.py "$APP/Contents/Resources/JARVIS.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.mk2</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundleIconFile</key><string>JARVIS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppSleepDisabled</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>JARVIS listens for one command each time you press ⌘H.</string>
  <key>NSCameraUsageDescription</key><string>JARVIS uses the camera for hand gestures and to see what you show it.</string>
  <key>NSCalendarsFullAccessUsageDescription</key><string>JARVIS reads your agenda, adds events you ask for, and reminds you before meetings.</string>
  <key>NSCalendarsUsageDescription</key><string>JARVIS reads your agenda, adds events you ask for, and reminds you before meetings.</string>
  <key>NSRemindersFullAccessUsageDescription</key><string>JARVIS reads and adds reminders when you ask.</string>
  <key>NSRemindersUsageDescription</key><string>JARVIS reads and adds reminders when you ask.</string>
  <key>NSAppleEventsUsageDescription</key><string>JARVIS controls apps on your request.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>JARVISProjectDir</key><string>$PROJECT</string>
  <key>JARVISPort</key><string>${JARVIS_PORT:-8765}</string>
</dict></plist>
PLIST

# Sign with the stable local certificate if there is one, so macOS keeps JARVIS's permissions
# across rebuilds (create it once with macapp/make_signing_identity.sh). Otherwise sign ad hoc.
IDENTITY="JARVIS Local Signing"
if security find-certificate -c "$IDENTITY" >/dev/null 2>&1; then
  codesign --force --deep --sign "$IDENTITY" "$APP"
else
  echo "Note: signing ad hoc; permissions will reset on every rebuild. Run macapp/make_signing_identity.sh once to fix that."
  codesign --force --deep --sign - "$APP"
fi
touch "$APP"
echo "Done. Open it with:  open \"$APP\""
