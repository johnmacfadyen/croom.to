# One Pi, touchscreen and TV

`croom-ui` runs the room services, touchscreen controller and a password-protected
HTTPS setup page. The touchscreen selects bookings and controls the meeting; a
separate TV shows the room welcome screen and Teams browser.

## Configure your room

1. On the touchscreen, tap **Room setup** to find this Pi's address and password.
2. Open `https://<pi-hostname>.local:3000` on a laptop on the same network. The Pi
   creates its own HTTPS certificate, so your browser will show a certificate warning.
   Confirm that you are opening your own Pi before accepting it.
3. Sign in. New setup passwords contain eight characters in two groups of four,
   without confusing `0/O` or `1/I/L` characters. Capitals, spaces and the dash
   are optional when typing these codes. Existing long passwords continue to work
   unchanged until explicitly replaced. Set the room name and timezone.
4. Select the touchscreen (usually **DSI-1**) and the connected TV's HDMI output.
   **Identify connected displays** briefly labels each screen. You can save other
   settings before connecting the TV. Joins stay disabled if either selected screen
   is absent or if their desktops overlap. On labwc, selected screens are arranged
   side by side when needed, and a browser placement rule targets the TV.
5. Enable Microsoft 365 and enter the room mailbox, tenant ID, application ID and
   client secret **value**. Save, then **Test saved calendar connection**. The app
   must already have calendar-read permission scoped to the room; see the
   [Microsoft authorization guide](microsoft-365-room-calendar.md#microsoft-authorization-boundary).
6. Choose camera and microphone defaults and save. Changes restart room services;
   leave a meeting before saving. A blank secret field keeps the saved secret.

Bookings then appear on the touchscreen. Select a current/starting booking and
press **Join selected booking**. Teams opens on the TV. Microphone, camera and
Leave buttons operate that browser. No call starts automatically. The browser uses
its default webcam, microphone and speakers; individual device selection is not
implemented in setup. Real Teams controls and hardware still need an attended test.

## TV appearance and branding

In Room setup, open **TV appearance & branding**. Set an optional brand name,
accent colour, welcome message and logo, then **Save and apply settings**. A blank
brand name uses the room name. Logos are PNG/JPEG, at most 256 KB and 2048×2048;
the app stores a small normalized PNG on the Pi. Transparent PNGs suit the dark
background. **Remove logo** takes effect when saved.

The TV shows the local clock, room availability, current/next booking and up to
four bookings in the next seven days. Ended, cancelled and declined bookings are
excluded. Overlapping reservations keep the room marked booked until the final
contiguous reservation ends. A failed sync, missing first sync or data older than
five minutes (or three configured polling intervals, if longer) displays unknown
availability rather than claiming the room is free.

**Hide meeting titles on the TV** replaces titles with “Reserved meeting” while
keeping times visible. The touchscreen still shows its normal booking list. TV
branding applies to the standby calendar; Teams has its own meeting interface.
The native layout scales to 1080p and 4K without an additional browser and only
repaints when displayed information changes.

## Desktop startup

Install the Python `microsoft` and `ui` extras, Playwright, and Chromium in the room
runtime. The process must run as the logged-in desktop user, inside its graphical
session. Use a writable configuration file owned by that user:

```sh
croom-ui --config ~/.config/croom/config.yaml --fullscreen
```

Useful initial config (calendar can be added in setup):

```yaml
room:
  name: Conference Room
  timezone: Australia/Melbourne
meeting:
  platforms: [teams]
  browser_executable: /usr/bin/chromium
  browser_media: true
  auto_leave: false
  camera_default_on: false
  mic_default_on: false
display:
  backend: none
  controller_output: DSI-1
  meeting_output: ''
ai:
  enabled: false
dashboard:
  enabled: false
```

The browser opens only when joining. Native audio/video capture is skipped in this
mode so it cannot hold the webcam away from Chromium. The setup page stays running
if room services fail to start, allowing settings to be corrected.

Do not run a separate `croom.service` agent alongside the desktop room process.
Use desktop autostart to import the real session environment and start a user
service. Do not force `eglfs` for a Wayland desktop.

## Installed Pi: mirror

The owner-specific migration uses John’s existing labwc session:

- Setup: `https://192.168.1.162:3000` or `https://mirror.local:3000`.
- User service: `croom-room.service`; launched by
  `~/.config/autostart/croom-room.desktop` at desktop login.
- Launcher: `~/.local/bin/croom-room-start` imports the graphical session environment.
- Config: `~/.config/croom/config.yaml`, readable/writable only by John.
- Setup state: `~/.local/state/croom`; password in `setup-password`, TLS key and
  certificate alongside it. Microsoft secrets live in protected credential files.
- Old system agent/UI units are disabled to avoid competing room processes.

From an SSH session as John:

```sh
systemctl --user status croom-room.service
sudo journalctl _SYSTEMD_USER_UNIT=croom-room.service -n 80 --no-pager
```

Desktop autostart requires desktop login (or an existing auto-login configuration).
A reboot and physical two-display/media test remain necessary after stable power
and the TV are available. This setup is independent of the unfinished fleet
frontend and does not activate the legacy port-8080 web interface.
