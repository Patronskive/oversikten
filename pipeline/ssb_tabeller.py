"""Lister SSB-tabeller som finnes på grunnkrets- og bydelsnivå, som grunnlag for neste steg.

Skriver en Markdown-rapport til standard ut (havner i GitHub Actions-sammendraget).
"""
import requests

UA = {"User-Agent": "stedsassistent/0.1 (privat prosjekt)"}
SØK = ["grunnkrets", "bydel", "rutenett", "inntekt kommune", "husholdninger kommune"]


def søk_v2(ord):
    r = requests.get(
        "https://data.ssb.no/api/pxwebapi/v2/tables",
        params={"query": ord, "lang": "no", "pageSize": 100},
        headers=UA, timeout=60,
    )
    r.raise_for_status()
    ut = []
    for t in r.json().get("tables", []):
        ut.append((t.get("id"), t.get("label"), t.get("firstPeriod"), t.get("lastPeriod")))
    return ut


def søk_v0(ord):
    r = requests.get("https://data.ssb.no/api/v0/no/table/", params={"query": ord}, headers=UA, timeout=60)
    r.raise_for_status()
    return [(t.get("id"), t.get("title") or t.get("text"), "", t.get("published", "")) for t in r.json()]


def main():
    print("# SSB-tabeller for stedsprofilen\n")
    for ord in SØK:
        print(f"## Søk: {ord}\n")
        try:
            treff = søk_v2(ord)
            kilde = "PxWebApi v2"
        except Exception as e:
            print(f"_v2 feilet ({e}), prøver v0_\n")
            try:
                treff = søk_v0(ord)
                kilde = "API v0"
            except Exception as e2:
                print(f"_Feilet: {e2}_\n")
                continue
        print(f"{len(treff)} treff ({kilde})\n")
        print("| Tabell | Tittel | Fra | Til |")
        print("| --- | --- | --- | --- |")
        for t in treff:
            tittel = (t[1] or "").replace("|", "/")
            print(f"| {t[0]} | {tittel} | {t[2] or ''} | {t[3] or ''} |")
        print()


if __name__ == "__main__":
    main()
