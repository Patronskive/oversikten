"""Skriver ut strukturen til SSB-tabeller og Kartverkets grunnkrets-WFS, som grunnlag for innhentingen."""
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "stedsassistent/0.1 (privat prosjekt)"}
TABELLER = ["04362", "06944", "06070", "12306", "14916", "14890"]
GK_WFS = "https://wfs.geonorge.no/skwms1/wfs.grunnkretser"


def meta_v2(tabell):
    r = requests.get(
        f"https://data.ssb.no/api/pxwebapi/v2/tables/{tabell}/metadata",
        params={"lang": "no", "outputFormat": "json-stat2"}, headers=UA, timeout=60,
    )
    r.raise_for_status()
    return r.json()


def main():
    for t in TABELLER:
        print(f"\n## Tabell {t}")
        try:
            m = meta_v2(t)
        except Exception as e:
            print(f"Feilet: {e}")
            continue
        print(f"Tittel: {m.get('label')}")
        print(f"Roller: {m.get('role')}")
        for dim in m.get("id", []):
            kat = m["dimension"][dim]["category"]
            idx = kat.get("index", {})
            lab = kat.get("label", {})
            koder = sorted(idx, key=lambda k: idx[k]) if isinstance(idx, dict) else list(idx)
            print(f"- {dim} ({m['dimension'][dim].get('label')}): {len(koder)} verdier")
            vis = koder if len(koder) <= 40 else koder[:12] + ["…"] + koder[-5:]
            for k in vis:
                print(f"    {k} = {lab.get(k, '') if k != '…' else ''}")
        try:
            r = requests.get(
                f"https://data.ssb.no/api/pxwebapi/v2/tables/{t}/data",
                params={"lang": "no", "outputFormat": "json-stat2", "valueCodes[Tid]": "top(1)",
                        "valueCodes[Region]": "0301*"},
                headers=UA, timeout=60,
            )
            print(f"Test data-kall (Region=0301*, Tid=top(1)): HTTP {r.status_code}, {len(r.content)} byte")
            if r.ok:
                j = r.json()
                print(f"  størrelse: {j.get('size')}, første verdier: {j.get('value', [])[:8]}")
            else:
                print(f"  {r.text[:300]}")
        except Exception as e:
            print(f"Test data-kall feilet: {e}")

    print("\n## Grunnkrets-WFS")
    r = requests.get(GK_WFS, params={"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"},
                     headers=UA, timeout=60)
    print(f"HTTP {r.status_code}")
    rot = ET.fromstring(r.content)
    for ft in rot.iter():
        if ft.tag.rsplit("}", 1)[-1] == "FeatureType":
            navn = [c.text for c in ft if c.tag.rsplit("}", 1)[-1] in ("Name", "Title")]
            print(f"- {navn}")
    for op in rot.iter():
        if op.tag.rsplit("}", 1)[-1] == "Constraint" and op.get("name") in ("CountDefault", "ImplementsResultPaging"):
            verdi = [c.text for c in op.iter() if c.tag.rsplit("}", 1)[-1] == "DefaultValue"]
            print(f"  {op.get('name')}: {verdi}")


if __name__ == "__main__":
    main()
