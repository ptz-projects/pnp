# PnP device data

Two files live at the top level of the GitHub repo. The workflow downloads both on every run.
(raw.githubusercontent.com caches for up to ~5 minutes after a commit.)

## devices.xlsx  (filled in by the person adding switches)
| Tab | One row per | Columns |
|---|---|---|
| Devices | switch | serial, pid, hostname, site, role (optional), mgmt_vlan |
| VLANs | VLAN per switch | hostname (or `*` for every switch), vlan_id, vlan_name |
| Ports | access port | hostname, interface, access_vlan |

- Ports accept ranges: `GigabitEthernet1/0/1-24` (up to 96 ports per row).
- The management VLAN must be listed on the VLANs tab; every port VLAN must be listed too.
- VLAN names: no spaces, max 32 chars. Serial: 8-14 letters/digits.
- Keep header rows and tab names unchanged. Other tabs (e.g. Instructions) are ignored.
- The workflow glues the three tabs into one record per switch and reports any mismatch (unknown hostname, duplicate VLAN/port, unknown site...) to Webex before touching Catalyst Center.

## config.json  (maintained by the network team, not by technicians)
- `modes`: Day 0 template per answer to "Staging in a Lab" / "Factory Install at Site".
- `sites`: friendly site name -> Catalyst Center site path + NetBox site name (exact).
- `images`: golden image UUID per PID (blank = skip image upgrade).
- `defaults`: default role name and the Day N template.

No NetBox IDs are stored anywhere. For each switch the workflow looks up, in NetBox:
- the site by its NetBox name (from `sites`),
- the device type by model name = the PID in the sheet,
- the role by name (sheet `role` column, or the default).
If any of these is missing or ambiguous, that switch is NOT onboarded and Webex says which name is missing.
