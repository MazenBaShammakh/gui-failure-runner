# Mobilerun — Manual Setup Notes

## 1. Install ADB

- macOS:   `brew install android-platform-tools`
- Linux:   `sudo apt install adb`
- Windows: download Android SDK Platform Tools, add to PATH

## 2. Enable USB debugging on the device

Settings → About Phone → tap Build Number 7 times → Developer Options → enable USB Debugging.
Connect via USB and accept the prompt on the phone.

Verify:
```bash
adb devices          # device should appear (not "unauthorized")
adb version
```

## 3. Install the Portal APK

With the venv activated and device connected:
```bash
mobilerun setup      # downloads + installs the Portal APK automatically
mobilerun ping       # confirms Portal is reachable — must succeed before running tasks
```

Portal is `com.mobilerun.portal`. It provides accessibility tree extraction and action
execution locally via ADB — no data leaves the device.

Manual checks if setup fails:
```bash
adb shell pm list packages | grep mobilerun       # confirm APK is installed
adb shell settings get secure enabled_accessibility_services  # confirm accessibility service
```

## 4. Optional: set device serial

If multiple devices are connected, set in `.env`:
```
ANDROID_DEVICE_ID=emulator-5554
```
Or pass `--device <serial>` to the CLI.

## 5. Run the test script

```bash
cd agents/mobilerun
source venv/bin/activate     # or: venv\Scripts\activate on Windows
python test_agent.py
```

Inspect any log/trajectory files produced, then document them in LOG_FORMAT.md.
