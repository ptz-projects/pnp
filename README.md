# PnP onboarding data

Files read by the "Catalyst Center PnP Lifecycle" workflow (from the repo's main branch):

| File | Who edits it | Purpose |
|---|---|---|
| `devices.xlsx` | Anyone adding switches | The current batch of switches (Devices, VLANs, Ports tabs) |
| `template.xlsx` | Nobody (copy it) | Blank copy with headers, dropdowns, Instructions and Examples tabs |
| `config.json` | Network team | Sites, Day 0 templates and variables per mode, defaults, image IDs |

## Adding a batch
1. Copy `template.xlsx` to `devices.xlsx`. Do not reuse an old filled-in file; git history keeps the old batches.
2. Fill the Devices, VLANs and Ports tabs. The Examples tab shows a Staging row and a Factory row.
3. Commit `devices.xlsx`, start the workflow, choose **Staging in a Lab** or **Factory Install at Site**. The choice applies to every row in the file.

## Columns by mode
- Staging in a Lab: serial, pid, hostname, site, mgmt_vlan (optional: role, system_mtu, software_upgrade).
- Factory Install at Site: also mgmt_ip (with prefix), gateway, uplink_interface (optional: native_vlan).
- `software_upgrade`: Yes upgrades to the image for the model in `config.json` -> `images` (error if none); blank or No skips the upgrade.

## config.json
- `defaults`: role, template project, MTU, native VLAN, Day N template name.
- `modes`: per mode, the Day 0 template name and its template variables. Values use placeholders: `{hostname}`, `{system_mtu}`, `{mgmt_vlan}`, `{uplink_interface}`, `{allowed_vlans}`, `{native_vlan}`, `{mgmt_ip}`, `{subnet_mask}`, `{gateway}`, `{pid}`, `{serial}`.
- `sites`: sheet site name -> Catalyst Center path (`catc_path`) and NetBox site (`netbox_site`). Both must exist.
- `images`: model (pid) -> Catalyst Center image ID, used only when `software_upgrade` is Yes.
- Adding a site? Add it here and to the site list on the Lists tab of `template.xlsx`.

## Day N (runs later, as a second workflow)
- When a switch is claimed, workflow 1 tags the NetBox device `dayn-pending`, sets its `pnp_option` custom field, and stores the Day N plan (templates and every variable, including host ports) in the device's local context. The spreadsheet is not needed again.
- Workflow 2 ("Catalyst Center PnP Day N") runs on a schedule. Pending switches that are reachable and managed in Catalyst Center get the composite template deployed (tag `dayn-deploying`). On the next run it checks the result: success removes the tags, clears the stored plan and sets NetBox status to staged (active is set by the cleanup workflow after installation); failure sets `dayn-failed` with the reason. To retry a failed switch, fix the cause and add the `dayn-pending` tag again.
- NetBox needs: tags `dayn-pending`, `dayn-deploying`, `dayn-failed`; custom field `pnp_option` (text) on devices.
- Staging: base + management interface + host ports (composite in `config.json`). Factory: base + host ports. L3 is not set up yet.
- Template secrets (admin password, enable secret, key-encrypt string) are never sent by the workflow; keep them as defaults inside the Catalyst Center templates.
- `config.json` -> `dayn`: project, composites and member templates, template variables per mode. Sites need a `tz` entry (name, hours, minutes).
- Ports tab: full interface names (GigabitEthernet1/0/1-24), one common prefix, numbered 1-48; optional `description` column.

## Cleanup workflow (not built yet) - notes
Staging switches are managed by Catalyst Center through the Gi0/0 management port (DHCP) while in the lab. The Day N management-interface template configures the final SVI (static IP) but does not change the address Catalyst Center manages, so the switch stays reachable after Day N. When the switch is installed at site, a cleanup workflow should, in this order:
1. confirm the switch answers on its final SVI address, and update its management IP in Catalyst Center to that address (resync)
2. only then shut Gi0/0 (a small cleanup template), so management is never lost
3. set the NetBox primary IP and status active (Day N leaves the device `staged`)
4. remove any leftover Catalyst Center device tag and NetBox `dayn-*` tags, and re-check compliance

## Safety behaviour
- Existing NetBox device: kept, never overwritten; only a blank serial is filled. Different site, model or serial = skipped with a Webex message.
- Switch already in Catalyst Center (anything but Unclaimed) = skipped with a Webex message.
- A skipped switch is not imported or claimed. One bad row in the sheet's validation stops the whole run.

Dummy data only: the repo is public.
