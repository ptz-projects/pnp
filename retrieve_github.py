import sys
import json
import io
import re
import os
import ssl
import zipfile
import base64
import zlib
import ipaddress
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

# argv[1] = mode flag (STAGING_CONFIG / FACTORY_CONFIG)
# argv[2] = GitHub location: owner/repo[/branch]  (public repo)
# Reads devices.xlsx (tabs: Devices, VLANs, Ports) and config.json from the repo
# and glues them into one JSON record per device.
flag = sys.argv[1] if len(sys.argv) > 1 else ""
loc = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
errors = []
data = []

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def tls_contexts():
    """Verified TLS contexts only: system default, certifi, then common CA bundle files."""
    yield ssl.create_default_context()
    try:
        import certifi
        yield ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    seen = set()
    for p in (ssl.get_default_verify_paths().cafile, "/etc/ssl/certs/ca-certificates.crt",
              "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem", "/etc/ssl/ca-bundle.pem",
              "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"):
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            try:
                yield ssl.create_default_context(cafile=p)
            except Exception:
                pass


def fetch(path):
    m = re.fullmatch(r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?', loc)
    if not m:
        raise ValueError(f"GitHub repo '{loc}' must look like owner/repo or owner/repo/branch")
    owner, repo, branch = m.group(1), m.group(2), m.group(3) or "main"
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    tried, last = 0, ""
    for ctx in tls_contexts():
        tried += 1
        try:
            with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ValueError(f"Could not fetch {path} from {owner}/{repo}@{branch}: HTTP {e.code}")
        except urllib.error.URLError as e:
            last = str(e.reason)
            if not isinstance(e.reason, ssl.SSLError):
                raise ValueError(f"Could not fetch {path} from GitHub: {last}")
        except Exception as e:
            raise ValueError(f"Could not fetch {path} from GitHub: {e}")
    raise ValueError(f"Could not verify GitHub's TLS certificate for {path} (tried {tried} CA bundles): {last}")


def col_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(raw):
    """Minimal .xlsx reader (stdlib only). Returns {sheet name lower: [(row number, [cells])]}."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
            shared.append("".join(t.text or "" for t in si.iter(M + "t")))
    rels = {}
    for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
        t = rel.get("Target")
        rels[rel.get("Id")] = t.lstrip("/") if t.startswith("/") else "xl/" + t
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find(M + "sheets"):
        rows = []
        root = ET.fromstring(z.read(rels[sh.get(R + "id")]))
        for row in root.iter(M + "row"):
            cells = {}
            for c in row.findall(M + "c"):
                t = c.get("t")
                v = c.find(M + "v")
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(M + "t"))
                elif v is None or v.text is None:
                    val = ""
                elif t == "s":
                    val = shared[int(v.text)]
                elif t == "b":
                    val = "TRUE" if v.text == "1" else "FALSE"
                else:
                    val = v.text
                    if t in (None, "n"):
                        try:
                            f = float(val)
                            val = str(int(f)) if f == int(f) else val
                        except ValueError:
                            pass
                cells[col_index(c.get("r"))] = val.strip()
            if cells:
                rows.append((int(row.get("r")), [cells.get(i, "") for i in range(max(cells) + 1)]))
        out[sh.get("name").strip().lower()] = rows
    return out


def table(book, tab, required, optional=()):
    """Rows of a tab as dicts keyed by normalised header. Returns list of (row number, dict)."""
    rows = book.get(tab.lower())
    if rows is None:
        errors.append(f"devices.xlsx has no '{tab}' tab")
        return []
    if not rows:
        errors.append(f"'{tab}' tab is empty")
        return []
    head = [h.strip().lower().replace(" ", "_") for h in rows[0][1]]
    miss = [c for c in required if c not in head]
    if miss:
        errors.append(f"'{tab}' tab is missing columns: " + ", ".join(miss))
        return []
    res = []
    for num, cells in rows[1:]:
        rec = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(head) if h}
        if any(rec.values()):
            res.append((num, rec))
    return res


IF_LONG = {"gi": "GigabitEthernet", "gig": "GigabitEthernet", "gigabit": "GigabitEthernet", "gigabitethernet": "GigabitEthernet",
           "te": "TenGigabitEthernet", "ten": "TenGigabitEthernet", "tengigabitethernet": "TenGigabitEthernet",
           "fa": "FastEthernet", "fastethernet": "FastEthernet", "eth": "Ethernet", "ethernet": "Ethernet",
           "twe": "TwentyFiveGigE", "twentyfivegige": "TwentyFiveGigE", "fo": "FortyGigabitEthernet", "fortygigabitethernet": "FortyGigabitEthernet",
           "hu": "HundredGigE", "hundredgige": "HundredGigE"}


def norm_if(name):
    """Gi1/0/24 -> GigabitEthernet1/0/24 (spelling only; the port numbers are never changed)."""
    m = re.fullmatch(r"([A-Za-z]+)\s*(\d.*)", str(name).strip())
    if m and m.group(1).lower() in IF_LONG:
        return IF_LONG[m.group(1).lower()] + m.group(2).replace(" ", "")
    return str(name).strip()


def expand(iface):
    m = re.fullmatch(r'(.*?)(\d+)-(\d+)', iface)
    if not m:
        return [iface]
    a, b = int(m.group(2)), int(m.group(3))
    if a > b or b - a > 96:
        raise ValueError(f"bad port range '{iface}'")
    return [f"{m.group(1)}{n}" for n in range(a, b + 1)]


xlsx = cfg_text = None
try:
    xlsx = fetch("devices.xlsx")
    cfg_text = fetch("config.json").decode("utf-8-sig")
except ValueError as e:
    errors.append(str(e))

cfg = {}
book = {}
if not errors:
    try:
        cfg = json.loads(cfg_text)
    except Exception as e:
        errors.append(f"config.json is not valid JSON: {e}")
    try:
        book = read_xlsx(xlsx)
    except Exception as e:
        errors.append(f"devices.xlsx could not be read as an Excel workbook: {e}")

if not errors:
    mode = cfg.get("modes", {}).get(flag)
    if mode is None:
        errors.append(f"config.json has no entry for mode '{flag}'")
        mode = {}
    defaults = cfg.get("defaults", {})
    sites = {k.lower(): v for k, v in cfg.get("sites", {}).items()}
    images = cfg.get("images", {})
    dayn = cfg.get("dayn", {})

    devices = table(book, "Devices", ["serial", "pid", "hostname", "site", "mgmt_vlan"])
    vlan_rows = table(book, "VLANs", ["hostname", "vlan_id", "vlan_name"])
    port_rows = table(book, "Ports", ["hostname", "interface", "access_vlan"])

if not errors:
    by_host = {}
    raw_row = {}
    for num, d in devices:
        host = d["hostname"]
        tag = f"Devices row {num} [{host or d['serial']}]"
        if d.get("uplink_interface"):
            d["uplink_interface"] = norm_if(d["uplink_interface"])
        if host in by_host:
            errors.append(f"{tag} duplicate hostname")
            continue
        site = sites.get(d["site"].lower())
        if site is None:
            # not in the optional config.json overrides: the sheet value is a NetBox site name;
            # the Catalyst Center path and time zone are read from that NetBox site by the workflow
            site = {"catc_path": "", "netbox_site": d["site"]}
        role_name = d.get("role") or defaults.get("role", "")
        if not role_name:
            errors.append(f"{tag} no role given and config.json has no default role")
            continue
        raw_row[host] = d
        upg = (d.get("software_upgrade") or "no").strip().lower()
        if upg in ("yes", "y", "true"):
            img = images.get(d["pid"], "")
            if not img:
                errors.append(f"{tag} software_upgrade is Yes but config.json 'images' has no image ID for {d['pid']}")
                continue
            image_for = img
        elif upg in ("no", "n", "false", ""):
            image_for = ""
        else:
            errors.append(f"{tag} software_upgrade must be Yes or No (or blank for No)")
            continue
        by_host[host] = {
            "serial": d["serial"].upper(), "pid": d["pid"], "hostname": host,
            "site_name": site["catc_path"],
            "netbox_site": site.get("netbox_site") or d["site"],
            "role": role_name,
            "refresh_plan": "Yes" if str(defaults.get("refresh_plan", "No")).strip().lower() == "yes" else "No",
            "create_sites": "Yes" if str(defaults.get("create_missing_sites", "No")).strip().lower() == "yes" else "No",
            "country": str(defaults.get("country", "")),
            "day0_template": mode.get("day0_template", ""),
            "template_project": mode.get("project") or defaults.get("template_project", ""),
            "template_project_enc": urllib.parse.quote(mode.get("project") or defaults.get("template_project", ""), safe=""),
            "dayn_template": defaults.get("dayn_template", ""),
            "image_id": image_for,
            "mgmt_vlan": d["mgmt_vlan"], "vlans": [], "ports": []}

    for num, v in vlan_rows:
        host = v["hostname"]
        targets = list(by_host.values()) if host == "*" else [by_host.get(host)]
        if targets == [None]:
            errors.append(f"VLANs row {num}: hostname '{host}' is not on the Devices tab")
            continue
        if not v["vlan_id"].isdigit():
            errors.append(f"VLANs row {num}: vlan_id '{v['vlan_id']}' is not a number")
            continue
        for dev in targets:
            if any(x["id"] == int(v["vlan_id"]) for x in dev["vlans"]):
                errors.append(f"VLANs row {num}: VLAN {v['vlan_id']} listed twice for {dev['hostname']}")
                continue
            dev["vlans"].append({"id": int(v["vlan_id"]), "name": v["vlan_name"], "desc": v["vlan_name"]})

    for num, p in port_rows:
        dev = by_host.get(p["hostname"])
        if dev is None:
            errors.append(f"Ports row {num}: hostname '{p['hostname']}' is not on the Devices tab")
            continue
        if not p["access_vlan"].isdigit():
            errors.append(f"Ports row {num}: access_vlan '{p['access_vlan']}' is not a number")
            continue
        try:
            names = expand(norm_if(p["interface"]))
        except ValueError as e:
            errors.append(f"Ports row {num}: {e}")
            continue
        for name in names:
            if any(x["name"] == name for x in dev["ports"]):
                errors.append(f"Ports row {num}: {name} listed twice for {dev['hostname']}")
            else:
                dev["ports"].append({"name": name, "vlan": int(p["access_vlan"]), "desc": re.sub(r"[\r\n;|]", " ", str(p.get("description") or "")).strip()[:200]})

    # Cross-row checks: the same serial, management IP, or uplink-as-host-port is almost always a copy/paste slip
    def short_if(name):
        m = re.match(r"^([A-Za-z]{2})[A-Za-z]*\s*(.*)$", str(name).strip())
        return (m.group(1).lower() + m.group(2).replace(" ", "")) if m else str(name).strip().lower()

    seen_serial, seen_ip = {}, {}
    for host, dev in by_host.items():
        other = seen_serial.setdefault(dev["serial"], host)
        if other != host:
            errors.append(f"Devices [{host}] serial {dev['serial']} is also used by {other}")
        ipv = str(raw_row[host].get("mgmt_ip", "")).split("/")[0].strip()
        if ipv:
            other = seen_ip.setdefault(ipv, host)
            if other != host:
                errors.append(f"Devices [{host}] mgmt_ip {ipv} is also used by {other}")
        up = str(raw_row[host].get("uplink_interface", "")).strip()
        if up:
            for pt in dev["ports"]:
                if short_if(pt["name"]) == short_if(up):
                    errors.append(f"Devices [{host}] uplink_interface {up} is also listed as a host port on the Ports tab ({pt['name']})")

    # Template (Day 0) variables, rendered per switch from the mode's "params" in config.json
    for host, dev in by_host.items():
        raw = raw_row[host]
        tag = f"Devices [{host}]"
        vals = {"hostname": dev["hostname"], "serial": dev["serial"], "pid": dev["pid"], "mgmt_vlan": dev["mgmt_vlan"],
                "uplink_interface": raw.get("uplink_interface", ""),
                "native_vlan": raw.get("native_vlan") or str(defaults.get("native_vlan", "")),
                "system_mtu": raw.get("system_mtu") or str(defaults.get("system_mtu", "1500")),
                "allowed_vlans": ",".join(str(v["id"]) for v in sorted(dev["vlans"], key=lambda v: v["id"])),
                "mgmt_ip": "", "subnet_mask": "", "gateway": raw.get("gateway", "")}
        net = None
        if raw.get("mgmt_ip"):
            try:
                ifc = ipaddress.ip_interface(raw["mgmt_ip"])
                if "/" not in raw["mgmt_ip"]:
                    raise ValueError("use address/prefix, e.g. 10.1.10.21/24")
                vals["mgmt_ip"], vals["subnet_mask"], net = str(ifc.ip), str(ifc.network.netmask), ifc.network
            except ValueError as e:
                errors.append(f"{tag} mgmt_ip '{raw['mgmt_ip']}' is not valid ({e})")
        if vals["gateway"]:
            try:
                gw = ipaddress.ip_address(vals["gateway"])
                if net is not None and gw not in net:
                    errors.append(f"{tag} gateway {gw} is not inside {net}")
            except ValueError:
                errors.append(f"{tag} gateway '{vals['gateway']}' is not a valid IP address")
        params = []
        for var, expr in mode.get("params", {}).items():
            def sub(m):
                name = m.group(1)
                if name not in vals:
                    errors.append(f"config.json param '{var}' uses unknown field '{{{name}}}'")
                    return ""
                if vals[name] == "":
                    errors.append(f"{tag} template variable '{var}' needs '{name}' "
                                  "(Devices column of that name; allowed_vlans comes from the VLANs tab, subnet_mask from mgmt_ip)")
                return vals[name]
            params.append({"key": var, "value": re.sub(r"\{(\w+)\}", sub, expr)})
        dev["config_params_b64"] = base64.b64encode(json.dumps(params).encode()).decode()

        # ---- Day N plan: rendered now and stored on the NetBox device for the Day N workflow ----
        stype = (raw.get("switch_type") or "L2").strip().upper()
        dev["switch_type"] = stype
        dev["dayn_b64"] = ""
        plan = dayn.get("plans", {}).get(flag, {}).get(stype)
        if plan is None:
            errors.append(f"{tag} no Day N plan for {flag} / {stype} in config.json" +
                          (" (L3 is not set up yet)" if stype == "L3" else ""))
            continue
        cs = sites.get(raw["site"].lower())
        if cs:
            tz = cs.get("tz", {})
            v2 = dict(vals, site=raw["site"], tz_name=str(tz.get("name", "")), tz_hours=str(tz.get("hours", "")),
                      tz_minutes=str(tz.get("minutes", "")), mgmt_if="Vlan" + str(dev["mgmt_vlan"]))
        else:
            # filled in by the workflow from the NetBox site (name and time zone)
            v2 = dict(vals, site="@@SITE@@", tz_name="@@TZ_NAME@@", tz_hours="@@TZ_HOURS@@",
                      tz_minutes="@@TZ_MINUTES@@", mgmt_if="Vlan" + str(dev["mgmt_vlan"]))
        for req in plan.get("require", []):
            if not v2.get(req):
                errors.append(f"{tag} column '{req}' is needed for {stype} Day N configuration")
        dparams = {}
        for var, expr in plan.get("params", {}).items():
            def sub2(m):
                nm = m.group(1)
                if nm not in v2:
                    errors.append(f"config.json Day N param '{var}' uses unknown field '{{{nm}}}'")
                    return ""
                if v2[nm] == "":
                    errors.append(f"{tag} Day N variable '{var}' needs '{nm}' (a Devices column, or a tz entry for the site in config.json)")
                return v2[nm]
            dparams[var] = re.sub(r"\{(\w+)\}", sub2, expr)
        if plan.get("host_ports"):
            maxp = int(dayn.get("host_port_max", 48))
            prefix, byn = None, {}
            for pt in dev["ports"]:
                m = re.fullmatch(r"(.+)/(\d+)", pt["name"])
                if not m or not (1 <= int(m.group(2)) <= maxp):
                    errors.append(f"{tag} port {pt['name']} cannot be set by the host port template (needs <prefix>/1 to /{maxp})")
                    continue
                if prefix is None:
                    prefix = m.group(1)
                elif prefix != m.group(1):
                    errors.append(f"{tag} ports use more than one prefix ({prefix} and {m.group(1)}); the host port template supports one")
                    continue
                byn[int(m.group(2))] = pt
            dparams["vlan_name_table"] = ";".join("%d|%s" % (v["id"], v["name"]) for v in sorted(dev["vlans"], key=lambda v: v["id"]))
            dparams["interface_prefix"] = prefix or "NA"
            for n, pt in sorted(byn.items()):
                dparams["p%02d_vlan" % n] = str(pt["vlan"])
                dparams["p%02d_desc" % n] = pt.get("desc") or "NA"
        if not errors:
            rec = {"v": 1, "mode": flag, "switch_type": stype, "project": dayn.get("project", ""),
                   "composite": plan.get("composite", ""), "members": plan.get("members", []),
                   "params": dparams, "hostname": dev["hostname"], "site": raw["site"]}
            if plan.get("host_ports"):
                rec["fill_ports"] = int(dayn.get("host_port_max", 48))
            # checked against the switch's real interface list before anything is deployed
            rec["interfaces"] = ([str(raw["uplink_interface"])] if raw.get("uplink_interface") else []) + [pt["name"] for pt in dev["ports"]]
            dev["dayn_b64"] = base64.b64encode(zlib.compress(json.dumps(rec, separators=(",", ":")).encode())).decode()
    data = list(by_host.values())

if errors:
    data = []
result = json.dumps(data)
fetch_error = "; ".join(errors)
