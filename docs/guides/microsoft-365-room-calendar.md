# Microsoft 365 room calendar

## Implemented room workflow

The agent reads one room mailbox using **client_credentials** authentication.
MSAL runs outside the event loop and refreshes expiring tokens. Graph requests use
`/users/{room}/calendar/calendarView` and follow paging for the full seven-day
window, including recurring occurrences and exceptions. Failed or incomplete syncs
retain previous bookings with an error; they do not become an empty calendar.
Initial sync failure fails agent startup.

The packaged `croom-ui` runs the agent in the same process and shows its calendar.
A user selects a booking and presses **Join selected booking**. Joining refreshes
Graph again, rejects missing/cancelled/declined/ended bookings, and permits joining
only during the meeting or within `join_early_minutes`. Calendar notifications never
start calls. Leave, microphone and camera buttons call the meeting service.
Unsupported instant meetings, recording, device/network settings and simulated data
are not exposed in this UI. The old QML sources are retained as inactive prototypes;
the old `src/croom-ui/main.py` launcher redirects to the packaged UI.

Teams uses a visible Playwright Chromium browser. Media changes are confirmed from
browser controls. Missing or unrecognised controls raise an error, and a lobby's
Leave button alone does not imply a connected call. Browser selectors and actual
Teams operation still require live validation. No claim of certified Teams Rooms
support is made.

## Configuration

For everyday configuration, use the [room setup page](room-setup.md). It saves
protected credentials and provides a calendar connection test. The following YAML
and installation details are for manual provisioning.

Install into the intended Python 3.11 environment:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[microsoft,ui]'
.venv/bin/python -m playwright install chromium
```

On the Pi, browser system dependencies and the desktop session must be available.
Run as the actual desktop user with that session's environment. The installed
`mirror` Pi now uses a desktop autostart/user-service setup described in the room guide.

Example configuration (replace identifiers and the absolute credential path):

```yaml
version: 2
room:
  name: Conference Room
  timezone: Australia/Melbourne
meeting:
  platforms: [teams]
  join_policy: manual
  join_early_minutes: 1
  auto_leave: false
  camera_default_on: false
  mic_default_on: false
calendar:
  providers: [microsoft]
  sync_interval_seconds: 60
  microsoft_tenant_id: YOUR-TENANT-ID
  microsoft_client_id: YOUR-APPLICATION-ID
  microsoft_room_mailbox: room@example.com
  microsoft_auth_mode: client_credentials
  microsoft_credentials_path: /etc/croom/credentials/microsoft.json
ai:
  enabled: false
dashboard:
  enabled: false
```

The credential file must be a regular file readable by the runtime user, with mode
`0600` or `0400`, containing a JSON object with a nonempty `client_secret` string.
Symlinks and files accessible to group/others are rejected. Provision its contents
through the approved secret-management process outside the repository. Do not paste
the secret into config YAML, command arguments, logs, screenshots or Git. Config
serialization retains only the credential-file reference. Restart the agent after
rotating the credential file. Delegated/device-code login and certificate credentials
are not implemented in this workflow.

Launch one room process from its desktop session:

```bash
.venv/bin/croom-ui --config /etc/croom/config.yaml --fullscreen
```

The UI owns the agent and its hardware/browser resources. Do not run it alongside a
separate headless agent for the same room. Coordinate stopping any installed service
before this attended launch. Closing the UI cancels pending joins and stops services.
The UI starts its own authenticated HTTPS setup server. The legacy unauthenticated
web interface remains inactive.

## Microsoft authorization boundary

An administrator must provision and approve a scoped **Application Calendars.Read**
assignment for this app and room mailbox using Exchange application RBAC. Event-body
access is used to extract invitation links. The room mailbox in config selects a
request target; it does not restrict the application's tenant permissions.

Review existing Entra application grants too: unscoped grants are independent of
Exchange RBAC assignments. Verify the approved room is allowed and an out-of-scope
mailbox is denied. No tenant permissions, registrations or credentials were changed
in this development session.

Primary references:

- [Graph calendarView](https://learn.microsoft.com/en-us/graph/api/calendar-list-calendarview?view=graph-rest-1.0)
- [Client credentials flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Exchange application RBAC and authorization testing](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)

## Validation still required on `mirror`

1. Establish the actual desktop user, session/display environment, and installed
   agent service before changing startup. Check Python, Chromium and system libraries.
2. Provision approved room-scoped credentials; verify the first sync, a recurring
   invitation, a changed occurrence, a cancellation, and mailbox access denial.
3. Select an invitation and join with an attendee present. Verify lobby vs connected
   status, microphone, speaker, camera, mute/unmute, camera toggling, leave/rejoin,
   and failure feedback. Confirm the browser controls match the current Teams client.
4. Verify disconnect/reconnect and reboot/resynchronization under the real desktop
   session. Only then adjust desktop-session startup using the observed environment.

Local automated tests mock Microsoft and browser/hardware boundaries. Headless Qt
checks prove service wiring and rendering, not conferencing or device behavior.
