# Startup fix and development handoff

## Branded TV calendar — 2026-09-15

Application revision **5f1ac2dddd38c303f50bf898723b21c7d74d9caf** is published on the
fork branch and installed on the Pi. The TV now has a scalable dark teal room
calendar: local clock/date, availability, current/next booking, countdown, up to
four agenda entries, and a welcome footer. Room setup includes brand name, accent
colour, welcome message, normalized PNG/JPEG logo upload/removal, and an option to
hide meeting titles on the TV. Defaults use the existing room name, Cubby House.
No logo or different organization name was assumed.

Fresh calendar data drives availability. Failed/absent/stale sync never reports
free; ended/cancelled/declined events are excluded and overlapping reservations
extend the busy-until time. This is a native Qt view, without another browser or
continuous animation. Branding changes apply through the existing protected setup
save flow, which restarts room services only while idle.

Verification: **140 focused checks passed**, including calendar projection,
privacy, branding persistence, image normalization and invalid input rejection.
Local renders covered multiple bookings, long titles and failed sync. Browser
checks verified editing, saving and reloading the branding controls. All **102**
package files matched the installed wheel, SHA-256
`484a66cb9a86c04f6ab9aa8f7ab3037a940e8cb7248e3be895f97c2c6cd0122c`.
The live HDMI-A-1 output was captured and visually checked at 3840×2160: the real
14:00 booking appeared with a countdown, while the room remained available until
14:00. HTTPS and calendar sync were healthy; the user service had zero automatic
restarts. No call was started during this update.

Backup: `/opt/croom/backups/pre-tv-branding-5f1ac2d` contains the previous wheel,
configuration and deployment record. Provenance is under `tv_branding_update` in
`/opt/croom/DEPLOYMENT.json`. The Pi again reported active undervoltage (`0x50005`)
during the preflight. Attended Teams/media and reboot validation remain pending.


## Explain unavailable joins — 2026-09-15

The first attempted join was rejected by the configured one-minute early-join
window: the accepted booking and Teams link were valid, but its start was still
about 16 minutes away. Calendar sync was healthy and the meeting provider remained
idle; no browser join had begun. The generic UI exception message hid the reason.

Application revision **399f23290ba3d3c84c4d3eaa617b1d4997722d28** now shares eligibility
checks between the agent and touchscreen. Selecting a future booking shows its
local join-available time and disables Join until eligible. Cancelled, declined,
ended or linkless bookings and calendar-refresh failures have specific safe error
messages. Unexpected backend exception details remain hidden. The configured
one-minute window and manual-join policy are unchanged.

Focused core/UI/calendar/meeting/setup checks: **125 passed**. The 800×480 layout
was visually checked; all **100** package files matched the deployed wheel. The
Pi user service restarted with zero automatic restarts. Backup:
`/opt/croom/backups/pre-join-errors-399f232`. Provenance is recorded under
`join_eligibility_update` in `/opt/croom/DEPLOYMENT.json`.

No call was retried during diagnosis or deployment. Teams/media verification
remains outstanding. The latest firmware reading was `0x50000` (historical
undervoltage/throttling flags, no active flags at that check).

## Live calendar and TV display — 2026-09-15

Microsoft room-calendar access is now configured on the Pi; a live refresh returned
one booking with no sync error. Credentials remain only in the Pi's protected state.
The connected Samsung TV is HDMI-A-1 at 3840×2160, positioned to the right of DSI-1
(800×480). Both saved output roles match the detected displays.

The first TV selection/connection left the TV showing desktop wallpaper despite
API readiness reporting success. Reapplying settings did not restore the welcome
window. Standalone native Qt placement checks worked on both outputs. Restarting
`croom-room.service` restored the real room welcome screen, verified with a capture
of the HDMI output. The exact cause of the old process's missing window remains
unconfirmed; hotplug/window visibility recovery and checking actual window placement
in readiness are follow-up work. No application source changed for this recovery.

The Pi still reported active undervoltage (`0x50005`). Actual camera/microphone,
Teams joining and reboot/autostart validation remain outstanding.

## Readable setup passwords — 2026-09-15

Application revision **ab7cafac58cc4e91b8dd7f65749340e3202a0399** is published and
installed on the Pi. New setup passwords have eight unambiguous characters grouped
4-4, displayed in large text. Entry ignores capitals, spaces and dashes for these
codes. Existing long passwords retain their exact behavior until explicitly changed.
The owner's current Pi password was replaced with a short code as requested.

Setup/UI checks: **25 passed**; the 650×315 password dialog was visually checked
inside the 800×480 controller. Live Pi login using lowercase with no dash passed,
and room services restarted successfully. All 99 package files matched the wheel.
The prior wheel/password/provenance are protected in
`/opt/croom/backups/pre-short-password-ab7cafa`; the latest installed revision is
recorded under `setup_password_update` in `/opt/croom/DEPLOYMENT.json`.

## Touchscreen and TV setup — 2026-09-15

The room application now includes a password-protected HTTPS setup page on port
3000, explicit touchscreen/TV roles and a TV welcome screen. Configuration is
allowlisted, written atomically, and Microsoft secrets are stored in protected
files outside YAML. API sessions require authentication; writes require same-origin
requests and CSRF tokens. This is a separate server from the inactive legacy web UI.

`croom-ui` owns the agent, setup server and displays in one desktop process. Setup
stays available after service startup failures. Saving restarts room services and
is rejected during a meeting or join. The meeting browser opens only after Join,
uses the selected TV, and owns media capture. Separate raw camera/audio services
are skipped in desktop room mode. Missing or mirrored displays block joining.

Read [room setup](room-setup.md) for configuration and the owner-specific Pi
startup paths. Application revision **55fff00e14b3d5ef919fb88373769df9de876a8e** is
published on the fork branch and installed on `mirror`. All **99** packaged files
matched the deployed wheel, SHA-256
`4ab5d42bf7312e7b7d5f47029f47a93309a4399dc79979ad4b07f8bbbbc10256`.

The active user service is `croom-room.service`, running as John in the real
Wayland session. Both old system services are disabled. XDG desktop autostart
starts the user service with the session environment. Live checks verified:

- `pip check`, native process startup with zero automatic restarts, and HTTPS
  status 200 from the laptop using the Pi certificate as an explicit trust anchor.
- Unauthenticated API requests are rejected; authenticated settings read/save
  works; room services restart successfully after save without restarting the UI.
- Teams prerequisites are ready, DSI-1 is detected at 800×480, HDMI-A-1 and HDMI-A-2
  are disconnected, and joining is blocked until a TV is selected and connected.
- Microsoft credentials remain absent; the calendar test explicitly requests
  saved settings instead of reporting a synthetic connection.

Rollback: `/opt/croom/backups/pre-room-app-55fff00/rollback.sh` restores the previous
fork package and headless service. Desktop/config snapshot:
`/opt/croom/backups/pre-room-desktop-20260915-131548`. Provenance is in
`/opt/croom/DEPLOYMENT.json` under `desktop_room_update`; the older top-level
migration fields describe the first fork installation.

The Pi still reports **active undervoltage/throttling (`0x50005`)**. The Logitech
webcam was not enumerating in the earlier USB check. The TV is not yet attached.
No reboot, media capture, live Microsoft sign-in or real Teams call was performed.
Use the new power supply before attended TV/media/reboot validation.
Earlier port-3000 findings below describe the previous installation.

Validation: **119 passing** focused setup, runtime recovery, display gating, core startup, Microsoft
calendar, meeting and Qt tests; browser fixture sign-in, save/reload, display choices
and calendar form; visually checked the native controller at 800×480. These checks
do not prove physical dual-display placement, live Microsoft access or a Teams call.

## Latest installation migration — 2026-09-15

The Bookworm Pi installation now uses the owner's fork,
`https://github.com/johnmacfadyen/croom.to.git`, pinned to application revision
`4c61b77563631a8d107b88873e725952cf8540f7` from `codex/microsoft-room-calendar`.
The fork's `main` branch was not changed. The startup/calendar/UI changes were
published as `8c91165`; the deployment also includes `4c61b77`, which prevents
Raspberry Pi codec/ISP processing nodes from being mistaken for physical cameras.
Video/startup regression checks for that fix: **69 passed** locally.

Migration verification on the Pi:

- The installed package's Git provenance points to the fork and exact revision;
  all 14 changed application Python files matched their Git source SHA-256 hashes.
- `pip check`, both agent/UI help entry points, and a headless Qt runtime check passed.
- A direct service lifecycle check started and stopped the real services, kept the
  event loop responsive, and found no cameras. The enabled `croom.service` was then
  started successfully, with no restarts or the earlier repeated capture timeouts.
- Existing configuration and both service unit files were preserved byte-for-byte.
  `croom-ui.service` remains disabled. Microsoft (`msal`) and UI (`PySide6`, `qasync`)
  dependencies are installed; PySide6 resolved to Bookworm-compatible 6.8.0.2.

This is an installation migration, not a verified conferencing room. Calendar
credentials remain absent; no meeting provider initializes in the existing service
setup. Browser runtime/desktop-session integration, audio backend and actual
camera/microphone setup remain. Optional AI models are also absent. No calendar
invitation, real Teams join, or reboot test was performed.

Rollback snapshot (old environment, config, and service units):
`/opt/croom/backups/pre-fork-20260915`. Its root-owned `rollback.sh` restores the old
installation and leaves services stopped, matching the state before migration.
Deployment provenance is recorded in `/opt/croom/DEPLOYMENT.json`.

A subsequent port-3000 check confirmed no HTTP listener or dashboard service on
the Pi. Port 3000 belongs to the separate fleet frontend's development server.
The README and administration/deployment guides now distinguish that unfinished
fleet deployment from the installed agent and native Qt UI. No web server was
activated as part of this documentation correction.

The sections below record the earlier development milestones; statements about
no remote deployment in those historical sections precede this migration.

## Original startup repair state

Target: `mirror`, a Raspberry Pi 4 on Bookworm with Python 3.11. The intended workflow
is a Microsoft 365 room calendar with real meeting controls. The owner reported
that the installed agent ran after the standalone startup hotfix. Physical media,
calendar access, and real meeting joins have not been verified here.

This checkout incorporates that repair into ordinary Python source. The standalone
installed-package patcher is unnecessary when installing this checkout. The starting
upstream revision was `d1ecb7be06ff09160e28cc0380d1aecc616e9808`.

## Repair scope

- `ComponentService` bridges audio, video, display, and calendar into the manager's
  lifecycle: name, state, status, initialization, startup, and shutdown. Component
  state such as display power remains separate from lifecycle state.
- Configuration mapping fixes constructor arguments, device defaults, video
  resolution/FPS, display backend selection, and supported calendar credentials.
- Initialization failure propagates and cleans up partially initialized and already
  started services. Failed agent startup exits with an error.
- AI dependencies only reference registered services. Privacy mode and disabled AI
  prevent the adapter from enabling AI noise reduction.
- An unconfigured calendar is explicitly skipped. Google service-account and
  authorized-user credential files are accepted; live Google authentication is untested.
- Agent status accepts dictionary results from meeting/AI services.
- Lazy public imports preserve `CroomAgent` while fixing the `python -m` import warning.

An enabled dashboard with a URL still raises an unsupported-integration error.
Microsoft room-calendar support has now been implemented locally; see the continuation below.

An active process is not proof of functioning conferencing. Existing components can
start with no devices or meeting providers. No remote deployment, live calendar
configuration, or physical Pi validation was performed when adding this source fix.

## Development and tests

From the repository root:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/unit/core/test_agent_startup.py
.venv/bin/python -m pytest -q tests/unit/core tests/unit/audio tests/unit/video tests/unit/display tests/unit/calendar tests/unit/meeting
PYTHONPATH=src .venv/bin/python -Werror -m croom.core.agent --help
```

The new regression tests use actual repository classes with hardware discovery and
cloud authentication mocked. They do not require camera/microphone access or secrets.
The one-off installed-package patcher's rollback checks are separate from these
source tests because ordinary repository installation replaces that patching process.

### Results when this fix was added

Verified locally with Python 3.11 on macOS:

- New startup regression tests: **15 passed**.
- Core/audio/video/display/calendar/meeting tests: **118 passed, 23 failed**.
- The same selection on untouched upstream `HEAD`: **103 passed, the same 23 failed**.
- The 23 existing failures are four audio tests and nineteen calendar tests that
  expect different constructor fields or methods than the current implementation.
  They remain follow-up work; the startup repair introduces no additional failures
  in this selection.
- `git diff --check` passed. The full repository suite was not run.

## Microsoft 365 continuation — 2026-09-15

Implemented locally, preserving the startup repair:

- Explicit app-only Microsoft configuration, room mailbox selection, validation,
  serialization and protected credential-file loading; optional `microsoft` dependencies.
- Graph calendarView recurring occurrences, complete paging, expiring-token refresh,
  UTC conversion, invitation-body URL extraction, cancellation/decline filtering and
  duplicate notification suppression. Sync failure retains stale bookings and disables joins.
- Manual booking selection revalidates the invitation against Graph before joining.
- A packaged `croom_ui` desktop entry point runs the agent and real meeting controls
  in the same process. The legacy launcher redirects to it. Inactive QML mock screens
  are not included in the new UI. Teams controls now verify browser state instead of
  flipping local booleans or treating a lobby Leave button as proof of admission.
- Malformed config now fails explicitly instead of silently using defaults.

See [configuration and attended validation guide](microsoft-365-room-calendar.md).
No remote deployment, tenant configuration, real calendar access, or physical
meeting validation has been performed. Bookworm session/service changes await the
actual desktop environment. The web API authentication problem below remains open.

### Continuation validation

Verified locally with Python 3.11 on macOS:

- Focused core, Microsoft calendar, meeting and headless Qt tests: **95 passed**.
- Core/audio/video/display/calendar/meeting/UI selection: **163 passed, 23 failed**.
  All 23 failure identifiers match the pre-change baseline (**118 passed, 23 failed**).
  These remain the four existing audio and nineteen existing calendar contract tests.
- A wheel was built and installed into an isolated temporary target; imports and
  `python -m croom_ui.main --help` resolve from that installed wheel. It includes
  `croom_ui/main.py`, excludes the legacy UI tree, and contains no bytecode.
- A headless Qt/qasync smoke test rendered the room window, selected a fixture
  booking, dispatched its Join action to the service boundary, and closed cleanly.
  Screenshot visually inspected; fixture data is not a live mailbox result.
- Agent `python -Werror -m croom.core.agent --help` and `git diff --check` passed.
- The full repository suite, cloud authentication and physical Pi tests were not run.

Reproduce the focused tests with the UI and Microsoft extras installed:

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/unit/core tests/unit/calendar/test_microsoft.py tests/unit/meeting tests/unit/ui
```

### Original milestone checklist (implementation history)

Items 1–5 below guided this continuation. UI packaging in item 6 is repaired for the
new room UI; desktop-session startup and the attended validation in item 7 remain.


1. Extend `CalendarConfig`, serialization, validation, and example configuration with
   the room mailbox, explicit authentication mode, and a protected credential-file
   reference. Keep secrets out of Git and logs.
2. Repair `calendar/providers/microsoft.py`: app authentication currently requires
   `client_secret` without `user_email`, while mailbox-targeted requests require both.
   Separate the mailbox target from authentication mode. Handle token expiry and paging.
3. Use the room's Graph `calendarView` for recurring occurrences. Test cancellations,
   timezones, duplicate notifications, and meeting URL extraction.
4. Connect calendar events and actual meeting controls. Choose an explicit joining
   policy; showing a booking does not itself authorize an automatic join.
5. Connect a real room UI to the agent. The Qt `MeetingController.joinMeeting()` has
   a `pass` for real calls and simulates success otherwise. Audio/video and network
   controllers also contain mock data. Remove simulated success from normal usage.
6. Fix UI packaging and Bookworm desktop-session startup. UI sources live under
   `src/croom-ui/`, but entry points expect `croom_ui.main`. The installer hardcodes
   `DISPLAY=:0`, `/run/user/1000`, and `QT_QPA_PLATFORM=eglfs`. Establish the Pi's actual
   desktop user/session and display backend before changing its services.
7. Validate an actual room invitation and attended Teams join on the Pi: camera,
   microphone, speakers, leave/rejoin, reboot, and calendar resynchronization.

The local web interface is not started by the agent. Before using it as an alternative
UI, repair its authentication: the public-prefix list includes `/`, so every route
currently bypasses authentication. Several API handlers also expect methods or
return types absent from the real services. Add integration/authentication tests
before exposing it on a network.

For unattended Microsoft access, design calendar-read permission scoped to the room
mailbox. Tenant permissions and credentials have not been configured in this work.

References:

- [Graph calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0)
- [Exchange application RBAC](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)

## Suggested next-session prompt

> Read docs/guides/startup-fix-and-handoff.md and inspect the current working tree.
> Continue the Microsoft 365 room-calendar integration for a Raspberry Pi 4 on
> Bookworm. Build real configuration and meeting controls, replacing simulated UI
> behavior. Preserve the startup repair and distinguish unit tests, cloud validation,
> and attended physical-device results.
