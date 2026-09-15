# Startup fix and development handoff

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
