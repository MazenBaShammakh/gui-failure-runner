# Mobilerun — Manual Setup Notes

## System requirements

- ADB (Android Debug Bridge)
  - macOS: `brew install android-platform-tools`
  - Linux:  `sudo apt install adb`
  - Windows: install Android SDK Platform Tools and add to PATH

## Device requirements

1. Install the Portal APK on the Android device
2. Enable USB debugging on the device (Settings > Developer Options)
3. Connect device via USB and confirm with `adb devices`

## Verify connection

```bash
adb devices
# Should list your device. If "unauthorized", accept the prompt on the phone.
```

## Environment variable

Set `ANDROID_DEVICE_ID` in `.env` if you have multiple connected devices:

```
ANDROID_DEVICE_ID=emulator-5554
```
