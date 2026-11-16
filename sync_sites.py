#!/usr/bin/env python3
"""Copy NetBox site names into the Lists tab of devices.xlsx / template.xlsx (feeds the site dropdown).

Usage:
  export NETBOX_URL=https://netbox.example.com NETBOX_TOKEN=xxxx
  python3 sync_sites.py devices.xlsx template.xlsx
Options:
  --insecure          skip TLS certificate verification (self-signed lab NetBox only)
  --names-file FILE   read site names (one per line) instead of calling NetBox (testing)
Also writes sites.csv (name, catc_site_path, time_zone, missing fields) so gaps in NetBox are easy to see.
Needs: pip install openpyxl. Close the spreadsheet in Excel first.
"""
import argparse, csv, json, os, ssl, sys, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+")
ap.add_argument("--names-file")
ap.add_argument("--insecure", action="store_true", help="do not verify the NetBox TLS certificate (lab use only)")
a = ap.parse_args()

rows = []
if a.names_file:
    rows = [{"name": n.strip(), "custom_fields": {}, "time_zone": None} for n in open(a.names_file) if n.strip()]
else:
    base, tok = os.environ.get("NETBOX_URL", "").rstrip("/"), os.environ.get("NETBOX_TOKEN", "")
    if not base or not tok:
        sys.exit("Set NETBOX_URL and NETBOX_TOKEN")
    url = base + "/api/dcim/sites/?limit=500"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": "Token " + tok, "Accept": "application/json"})
        ctx = ssl._create_unverified_context() if a.insecure else None
        j = json.load(urllib.request.urlopen(req, timeout=30, context=ctx))
        rows += j["results"]
        url = j.get("next")
rows.sort(key=lambda r: r["name"].lower())
names = [r["name"] for r in rows]
if not names:
    sys.exit("No sites found; nothing changed")

import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation

for f in a.files:
    wb = openpyxl.load_workbook(f)
    ws = wb["Lists"] if "Lists" in wb.sheetnames else wb.create_sheet("Lists")
    ws["A1"] = "sites"
    for r in range(2, max(ws.max_row, len(names) + 1) + 1):
        ws.cell(r, 1).value = names[r - 2] if r - 2 < len(names) else None
    dev = wb["Devices"]
    col = None
    for c in dev[1]:
        if str(c.value).strip().lower() == "site":
            col = c.column_letter
    col = col or "D"
    keep = [dv for dv in dev.data_validations.dataValidation if "%s2" % col not in str(dv.sqref)]
    dev.data_validations.dataValidation = keep
    dv = DataValidation(type="list", formula1="=Lists!$A$2:$A$%d" % (len(names) + 1), allow_blank=True,
                        errorStyle="warning", showErrorMessage=True,
                        error="Not on the list. It must exactly match a NetBox site name.")
    dv.add("%s2:%s200" % (col, col))
    dev.add_data_validation(dv)
    wb.save(f)
    print("updated %s with %d sites" % (f, len(names)))

with open("sites.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["name", "catc_site_path", "time_zone", "problems"])
    for r in rows:
        cf = r.get("custom_fields") or {}
        tz = r.get("time_zone")
        tz = tz.get("value") if isinstance(tz, dict) else tz
        miss = [k for k, v in (("catc_site_path", cf.get("catc_site_path")), ("time_zone", tz)) if not v]
        w.writerow([r["name"], cf.get("catc_site_path") or "", tz or "", ("missing " + ", ".join(miss)) if miss else ""])
print("wrote sites.csv")
