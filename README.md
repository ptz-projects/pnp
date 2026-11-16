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
- `sites`: optional overrides only (leave empty `{}`). Sites come from NetBox, see **Sites** below.
- `images`: model (pid) -> Catalyst Center image ID, used only when `software_upgrade` is Yes.

## Sites (NetBox is the source of truth)
- The `site` column in the sheet is the **NetBox site name** (case does not matter). Nothing to edit in `config.json` when a site is added.
- Each NetBox site needs two things (and, for auto-create, an optional third, a text custom field `country` holding the full country name): the custom field **`catc_site_path`** (Site model, text), e.g. `Global/APAC/Australia/Perth/Perth CBD Office`, and the built-in **Time zone** (e.g. Australia/Perth). The time zone becomes the switch `clock timezone` at Day N.
- The time zone is converted to the switch's `clock timezone` (standard-time name and offset, e.g. Europe/Berlin = CET +1) from a built-in table of about 90 common zones (plus the runner's own zone database where available). Daylight saving is not configured. A zone not in the table fails with `has time zone '<z>' which could not be converted`: use a nearby listed zone in NetBox, or add a `tz` override for that site in `config.json` -> `sites`.
- **Brand new site?** The workflow checks the Catalyst Center site *before* importing anything. If it does not exist, that switch is skipped with a Webex message and nothing is imported, claimed or changed in NetBox. Create the site under Design > Network Hierarchy, then run again.
- **Auto-create (optional):** set `config.json` -> `defaults.create_missing_sites` to `Yes` (default `No`, and committing the change is your confirmation). A missing site is then created as a building under its parent path using the NetBox site's Physical address, Latitude and Longitude, plus the country: the NetBox site's `country` custom field (e.g. Germany). There is no silent default: a site with no country is not created, because a wrong country is worse than a skipped site. For an estate in one country only, you can opt in to a fallback with `defaults.country_fallback` = `Yes` (uses `defaults.country`). The parent area must already exist. The workflow waits 20 seconds, looks the site up again and only continues if it is found. The Webex success message says whether the site was created this run. Set it back to `No` afterwards.
- **Site dropdown:** run `python3 sync_sites.py devices.xlsx template.xlsx` (set `NETBOX_URL` and `NETBOX_TOKEN` first, close Excel first). It copies the NetBox site names into the Lists tab, widens the dropdown, and writes `sites.csv` listing any site missing `catc_site_path` or Time zone. Run it whenever a site is added in NetBox.
- Optional per-site override in `config.json` -> `sites` (`catc_path`, `netbox_site`, `tz`) still works for odd cases.

## Day N (runs later, as a second workflow)
- When a switch is claimed, workflow 1 tags the NetBox device `dayn-pending`, sets its `pnp_option` custom field, and stores the Day N plan (templates and every variable, including host ports) in the device's local context. The spreadsheet is not needed again.
- Workflow 2 ("Catalyst Center PnP Day N") runs on a schedule. Pending switches that are reachable and managed in Catalyst Center get the composite template deployed (tag `dayn-deploying`). On the next run it checks the result: success removes the tags, clears the stored plan and sets NetBox status: the status in `config.json` -> `dayn.final_status` (Staging = `staged`, Factory = `installed`); the cleanup workflow then sets `active` for both once installation cleanup is done (shut the Gi0/0 management port, final management IP, etc.); failure sets `dayn-failed` with the reason. To retry a failed switch, fix the cause and add the `dayn-pending` tag again.
- NetBox needs: tags `dayn-pending`, `dayn-deploying`, `dayn-failed`; custom field `pnp_option` (text) on devices.
- Staging: base + management interface + host ports (composite in `config.json`). Factory: base + host ports. L3 is not set up yet.
- Template secrets (admin password, enable secret, key-encrypt string) are never sent by the workflow; keep them as defaults inside the Catalyst Center templates.
- `config.json` -> `dayn`: project, composites and member templates, template variables per mode. The time zone comes from the NetBox site (Time zone field).
- Ports tab and `uplink_interface`: short names are expanded (Gi, Te, Fa to GigabitEthernet etc.; port numbers are never changed). Full interface names (GigabitEthernet1/0/1-24), one common prefix, numbered 1-48; optional `description` column.

## NetBox status `installed` (create it once)
Factory switches finish Day N as `installed`, which is not a built-in NetBox device status. Add it in the NetBox configuration, then restart NetBox:
`FIELD_CHOICES = {'dcim.Device.status+': (('installed', 'Installed', 'cyan'),)}`
If the status does not exist, NetBox rejects the update at the end of Day N: the tags stay on the switch and the next Day N run reports it again. To use a built-in status instead, change `dayn.final_status.FACTORY_CONFIG` in `config.json` (e.g. `active`). A change only applies to switches whose plan is saved after the change (see Refreshing a stale Day N plan).

## Cleanup workflow (not built yet) - notes
Staging switches are managed by Catalyst Center through the Gi0/0 management port (DHCP) while in the lab. The Day N management-interface template configures the final SVI (static IP) but does not change the address Catalyst Center manages, so the switch stays reachable after Day N. When the switch is installed at site, a cleanup workflow should, in this order:
1. confirm the switch answers on its final SVI address, and update its management IP in Catalyst Center to that address (resync)
2. only then shut Gi0/0 (a small cleanup template), so management is never lost
3. set the NetBox primary IP and status active (Day N leaves the device `staged`, or `installed` for Factory)
4. remove any leftover Catalyst Center device tag and NetBox `dayn-*` tags, and re-check compliance

## Future iterations (not built)
- **Stacks:** the host-port template supports one interface prefix per switch (e.g. `GigabitEthernet1/0`). A stack (Gi1/0/x plus Gi2/0/x) fails validation with "ports use more than one prefix". Needs a second prefix parameter or generating the port lines in Python. Single switches only for now. Stack members also need `stack: true` on the PnP import and per-member serials.
- **Day N progress and completion (observability):** "Day N started: ... Templates are deploying; the next run will confirm the result" is the only message until the next scheduled Day N run, which posts the success or failure. Nothing tells Webex the moment the templates finish. Catalyst Center's Activities log shows it live: "Going to deploy template ..." and "Successfully deployed template ..." for each member, in order (e.g. MGMT-INTERFACE, then BASE-CONFIG, then HOST-CONFIG, about a minute apart on the lab switch). Peter found no webhook or event subscription for template-deploy progress, so for now: watch Activities, or shorten the Day N schedule interval. Options to close the gap: a short-interval schedule for the confirm step only; a Catalyst Center Events subscription (Platform > Developer Toolkit > Event Notifications) to Workflows or Webex, if template-deploy events turn out to be offered; or a follow-up check in the same run that polls the task for a few minutes after the deploy.
- **Open bug: Catalyst Center device tag not removed after a successful Day N** (Staging, sw-03). Cause not found: the lab switch was deleted from Catalyst Center and NetBox before it was diagnosed. The removal path (`DELETE /dna/intent/api/v1/tag/<tag id>/member/<device id>`) matches the documented API, but the DELETE call has never been seen working in Workflows. On the next run, read the tag note at the end of the "Day N complete" Webex message (removed / tag does not exist / switch not found in inventory / could not read it) and the output of "CatC - Remove Day N Tag from Device". Check whether the Factory run does the same.
- **Switch added to Catalyst Center first (not by this workflow):** not catered for. If someone already added the switch to PnP by hand (or it registered itself), Workflow 1 reports "already in Catalyst Center" and skips it: no NetBox device, no Day N plan. Needs a decision on what to do with an unclaimed or hand-claimed switch: adopt it (create the NetBox device and plan, and claim it if still unclaimed), or only report it. Also decide how to treat a switch already in the PnP list with a different hostname, site or template.
- **PnP errors after the claim** (e.g. Day-0 config generation failing on old firmware, `NCOB02088`) are only visible in Catalyst Center today. Planned: report the PnP error text to Webex from the Day N workflow.
- New sites inherit device credentials from the parent area; the workflow does not set credentials. Check the area's credentials (e.g. SNMPv3 SHA-2 needs IOS-XE 17.10.1a or later) before the first switch at a new site.
- Auto-create of missing sites needs a live test (documented site-create call, untested).

## Refreshing a stale Day N plan
- The Day N plan is saved in NetBox (Local config context, `pnp_dayn`) when the switch is claimed. Fixing the sheet afterwards does not change it.
- To pick up sheet changes for a switch that is already in Catalyst Center (e.g. a corrected uplink), set `config.json` -> `defaults.refresh_plan` to `Yes`, commit, and run Workflow 1 again. A switch that is already in PnP then gets its plan rebuilt from the sheet and re-queued (`dayn-pending`). Webex says "Day N plan refreshed from the sheet".
- Only switches that still have Day N outstanding are refreshed (tag `dayn-failed` or `dayn-pending`, or a plan still stored). A switch whose Day N is deploying right now is left alone, and so is one that finished Day N (no stored plan). Nothing else in NetBox is changed. Set it back to `No` afterwards.

## Catalyst Center device tag (Day N)
- Day N adds a Catalyst Center tag to the switch just before it deploys the templates, and removes it after a successful deploy. A failed deploy leaves the tag on.
- Tag name: `<switch type>-<mode>-PNP-DAY-N`, e.g. `L2-STAGING-PNP-DAY-N` or `L2-FACTORY-PNP-DAY-N`. Create the tags in Catalyst Center first. If the tag does not exist, Day N still deploys and the Webex message says the tag was not added.
- To use a different name, set `catc_tag` in the device's NetBox plan (`pnp_dayn`).

## Safety behaviour
- Existing NetBox device: kept, never overwritten; only a blank serial is filled. Different site, model or serial = skipped with a Webex message.
- Switch already in Catalyst Center (anything but Unclaimed) = skipped with a Webex message.
- Spreadsheet checks (the whole run stops, nothing is imported): the same serial on two rows, the same mgmt_ip on two rows, and a Ports row for the same interface as the switch's uplink_interface.
- The Day N plan is saved on the NetBox device when the switch is claimed and is not refreshed afterwards. If you change the sheet later, edit the device's local context data in NetBox (pnp_dayn.params) as well.
- A skipped switch is not imported or claimed. One bad row in the sheet's validation stops the whole run.

## Failures: what you will see and how to fix them

"Where" says how you find out: **Webex** = a message in the room; **NetBox** = tag `dayn-failed` plus the reason in the device's local context (`pnp_dayn.last_error`); **Run only** = nothing is sent, open the failed step in the workflow run.

### Workflow 1 - Catalyst Center PnP Lifecycle

| Situation | Where | Message / what you see | Fix |
|---|---|---|---|
| Spreadsheet or config.json problem (any row) | Webex | `Workflow Failed during validation: <list>`. Nothing is imported for any switch. | Fix every item in the list (see the validation table below), commit, run again. |
| PnP option dropdown empty or wrong | Webex | `config.json has no entry for mode ''` / `Unknown PnP option flag ''` | Pick a value in the run input. If the dropdown shows but is ignored, relink the condition's Property to the input variable in the workflow editor. |
| NetBox site, device type or role not found (or matches several) | Webex | `NetBox lookup failed for <host> (<serial>), not onboarded: <what> '<name>' not found in NetBox` | Create it in NetBox, or fix the name in the sheet. |
| Site name typed wrongly | Webex | `NetBox lookup failed ...: Site 'Perht CBD Office' not found in NetBox (closest NetBox sites: Perth CBD Office, ...)` | Fix the spelling in the sheet. |
| NetBox site has no `catc_site_path` or no Time zone | Webex | `NetBox lookup failed ...: Site '<x>' has no Catalyst Center site path (set the catc_site_path custom field on the NetBox site)` / `has no time zone` / `time zone '<z>' which could not be converted` | Set the field on the NetBox site, run again. |
| Site does not exist in Catalyst Center (new site) | Webex | `Skipped, not onboarded: <host> (<serial>). site '<path>' was not found in Catalyst Center. Create the site under Design > Network Hierarchy ... Nothing was imported or claimed and NetBox was not changed.` | Create the site in Catalyst Center (or fix `catc_site_path`), run again. With auto-create on, the message instead says why it could not create it (parent area missing, NetBox address, latitude or longitude blank, or the site still not found after creating). |
| NetBox tag missing | Webex | `...NetBox tag 'dayn-pending' does not exist (create the tags dayn-pending, dayn-deploying and dayn-failed first)` | Create the three tags in NetBox. |
| Device exists in NetBox with a different site, model or serial | Webex | `Skipped, not onboarded: <host> (<serial>). NetBox device <host> (ID n) already exists but: site is '...' in NetBox; device type is '...'; serial is '...'`. NetBox is not changed. | Fix the sheet or the NetBox record, run again. |
| Two devices with the same name in NetBox | Webex | `Skipped... n devices named <host> exist in NetBox` | Remove the duplicate in NetBox. |
| Switch already in Catalyst Center PnP (anything but Unclaimed) | Webex | `Skipped, not onboarded: <host> (<serial>) is already in Catalyst Center PnP with state 'Provisioned' (onboarding state '...')` | State `Error` (e.g. the switch booted and started PnP before the run, or Day-0 config generation failed): reset the device in Catalyst Center PnP, then run again. A device in Error can only be reset or deleted. Wrong serial in the sheet? Fix the sheet. Otherwise delete the switch from Inventory and delete its PnP entry, then run again. |
| Day 0 template missing, duplicated, or its project wrong | Webex | `Could not prepare the Catalyst Center claim for <host> (<serial>), not claimed: Expected exactly 1 Day 0 template named '<name>', found n among m in project '<project>'` | Fix the template name in `config.json` (modes) or the project in Catalyst Center. |
| Serial number invalid / not accepted by PnP import | Run only | Step "Lookup PnP Device ID" fails with `No results found for JsonPath: '$[0].id'` (the import returned nothing). No Webex message. | Check the serial in the sheet (3 letters + 4 digits + 4 letters/digits, e.g. FOC2416L5CP). Placeholder serials are rejected. |
| NetBox create failed | Webex | `NetBox device create failed for <host> (<serial>). Status code: n` | Look at the step output for NetBox's reason (usually a required field or a duplicate). The switch is already claimed in Catalyst Center; fix NetBox and add the device by hand. |
| Webex message not arriving | Run only | Step "Report ... to Webex" fails with 404 (wrong room ID) or 401 (token expired; bot/user tokens last 12 hours) | Correct the room ID in the run input; use a bot token for scheduled runs. |
| Workflows target "disconnected" | Run only | The workflow cannot start | Start the remote target VM and its docker containers, and check it shows connected in Workflows. |
| Day 0 config rejected by the switch | Run only (switch console) | `%PARSER-4-BADCFG: Unexpected end of configuration file` | The Day 0 template must finish with `end`. |
| Day 0 config - 3560 with jumbo MTU | Run only (switch console) | `system mtu` rejected on a 3560 | Use `system mtu jumbo <n>` for the 3560 PIDs (template `#if` on the model). |

Validation messages (all arrive in the single `Workflow Failed during validation` message):

| Message | Fix |
|---|---|
| `devices.xlsx has no '<tab>' tab` / `'<tab>' tab is empty` / `...is missing columns: ...` | Start again from `template.xlsx`. |
| `... duplicate hostname` | Hostnames must be unique in the sheet. |
| `... serial X is also used by <host>` | Same serial on two rows (copy/paste). |
| `... mgmt_ip X is also used by <host>` | Same management address on two rows. |
| `... uplink_interface X is also listed as a host port on the Ports tab` | Remove that interface from Ports (it would be turned into an access port). |
| `... mgmt_ip '<x>' is not valid` / `gateway ... is not inside <net>` / `gateway '<x>' is not a valid IP address` | Use address/prefix (10.1.10.21/24); the gateway must be inside that subnet. |
| `... software_upgrade is Yes but config.json 'images' has no image ID for <pid>` | Add the image ID, or set software_upgrade to No. |
| `VLANs row n: hostname '<x>' is not on the Devices tab` / `vlan_id '<x>' is not a number` / `VLAN n listed twice` | Fix the VLANs tab (hostname `*` applies to every switch). |
| `Ports row n: ... is not on the Devices tab` / `access_vlan ... is not a number` / `<port> listed twice` | Fix the Ports tab. Use full names (GigabitEthernet0/1) or ranges (GigabitEthernet0/1-8). |
| `... no Day N plan for <mode> / <type> in config.json` | Add the plan, or set switch_type to a supported value (L2). |
| `... column '<x>' is needed for <type> Day N configuration` | Fill the column (for Staging L2: mgmt_ip, gateway, uplink_interface). |
| `... Day N variable '<x>' needs '<y>' (a Devices column, or a tz entry for the site in config.json)` | Add the column, or set the NetBox site's Time zone. |
| `... port <x> cannot be set by the host port template (needs <prefix>/1 to /48)` | Host ports must look like GigabitEthernet0/5, numbered 1-48. |
| `... ports use more than one prefix (A and B)` | One prefix per switch (the host port template supports one). |
| `config.json is not valid JSON` / `param '<x>' uses unknown field` | Fix `config.json` syntax or the placeholder name. |

### Workflow 2 - Catalyst Center PnP Day N

| Situation | Where | Message / what you see | Fix |
|---|---|---|---|
| Switch not in inventory, or not Reachable and Managed yet | Run only | Nothing sent. The switch stays `dayn-pending` and is retried every run. | Wait for onboarding; check the switch is reachable from Catalyst Center. |
| Interface (uplink or host port) does not exist on the switch, e.g. `Gi0/24` instead of `Gi1/0/24` | Webex + NetBox | `Day N NOT started: <host> (<serial>). interface not on this switch: 'GigabitEthernet0/24' (closest: GigabitEthernet1/0/24, ...). Nothing was deployed.` Tagged `dayn-failed`. | Fix `uplink_interface` / Ports in the sheet, edit the NetBox Local config context (`pnp_dayn.params`, or delete `pnp_dayn` and re-run Workflow 1), re-add `dayn-pending`. If Catalyst Center cannot return the interface list, the check is skipped and the deploy proceeds. |
| Composite or member template missing, renamed, or lacking a parameter value | Webex + NetBox | `Day N NOT started: <host> (<serial>). composite template '<x>' not found in project '<p>'` / `composite '<x>' does not contain: <t>` / `template '<t>' needs parameter '<p>' but there is no value or default` / `could not read template '<t>' from Catalyst Center` | Create or rename the template to match `config.json` (e.g. `TM-FACTORY-L2-SWITCH`); give the parameter a default in the template. Then re-add `dayn-pending`. |
| More than 3 member templates | Webex + NetBox | `plans with more than 3 member templates are not supported yet` | Reduce the composite to 3 members (or ask for the limit to be raised). |
| Catalyst Center rejects the deploy request | Webex + NetBox | `Day N NOT started... Catalyst Center did not accept the template deploy (no task or deployment id returned)` | Open the "Deploy Day N Templates" step: the response body says why (bad ids, unknown device). |
| Deploy task fails | Webex + NetBox | `Day N FAILED... (task: NCTP10035: Error while deploying provisioning workflow)`, normally preceded by `Catalyst Center reported: <template>: <error>` | Read the member error before the task text; if there is none, check Activities > Tasks in Catalyst Center. |
| A command is rejected by the switch | Webex + NetBox | `Day N FAILED... Catalyst Center reported: <template>: NCTP10214: Provisioning failed for the template <template>. Message: Unable to push the invalid CLI to the device <ip>... Invalid CLI - '<command>' ... % Invalid input detected` | The named template contains a command this platform does not support (e.g. `diagnostic bootup level minimal` on a 3560CX). Remove or guard it in that template, then re-add `dayn-pending`. |
| Deployment id unknown to Catalyst Center | Webex + NetBox | `deployment status could not be read (deployment id unknown to Catalyst Center)` plus the task text | Normally means the task's id could not be used; open "CatC - Get Deploy Task" and "CatC - Get Deploy Status" in the run. |
| Task finished but gave no deployment id | Webex + NetBox | `the deploy task finished but no deployment id was found in it: <progress text>` | Send the progress text to the workflow owner; the workflow reads the id after the text `Template Deployemnt Id:` (Catalyst Center's spelling). |
| Deploy never finishes | Run only | Nothing sent. The switch stays `dayn-deploying` and is re-checked every run. There is **no timeout yet**. | Check Activities > Tasks in Catalyst Center. To retry: remove `dayn-deploying`, add `dayn-pending`. |
| Catalyst Center tag does not exist | Webex (inside the "Day N started" message) | `Catalyst Center tag '<name>' does not exist, so it was not added (create it in Catalyst Center first).` The deploy still goes ahead. | Create the tag (e.g. `L2-STAGING-PNP-DAY-N`, `L2-FACTORY-PNP-DAY-N`). |
| NetBox update failed | Run only | A "NetBox - Mark ..." step fails and the tags are not changed. If it was "Mark Deploying", the switch is still `dayn-pending` and the **next run deploys the templates again**. | Fix NetBox access or the tag names (`dayn-pending`, `dayn-deploying`, `dayn-failed`); check Template Hub deployment history for duplicate deploys. |
| Plan on the NetBox device is out of date | Run only | The deploy uses old values (wrong uplink, old ports). The plan is saved when the switch is claimed and is not refreshed from the spreadsheet. | Edit the device's Local config context data in NetBox (`pnp_dayn.params`), then re-add `dayn-pending`. |
| Same management IP planned on two switches in different runs | Not detected | Nothing is sent; the second switch causes an address clash. | Check mgmt_ip against NetBox before running. Only duplicates within one spreadsheet are caught. |

To retry any Day N failure: fix the cause, remove `dayn-failed`, add `dayn-pending`. A failed switch is never retried automatically.

### Things that broke our own tests (so they are recognisable)
- **Uplink or host port equals the management port**: the template reconfigures the port Catalyst Center is using; the SSH session drops mid-deploy and the failure is blamed on a later line. Keep the management port out of the uplink and out of the Ports tab.
- **Interface that does not exist on the model** (e.g. TenGigabitEthernet1/1/1 on a 3560CX-12PC): the template line is rejected. Use the real uplink (Gi0/13 or Gi0/14 on that model).
- **Composite fails at an earlier member**: Catalyst Center aborts the rest and reports the failure on the last member, so the message can name the wrong template. The workflow now lists each member's own result.
- **Velocity string comparison**: `#if(${MgmtVlan} > 1)` compares text with a number and can be false; compare strings (`!= "1"`).
- **Test placeholders**: example serials (e.g. FOCREPLACE01) are rejected by PnP.

Dummy data only: the repo is public.
