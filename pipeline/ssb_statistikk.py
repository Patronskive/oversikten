"""Henter SSB-statistikk for regionen fra PxWebApi v2 og lagrer i Supabase.

- 04362: alder per grunnkrets (summert over kjønn) -> grunnkrets_stat
- 06944: husholdningsinntekt per kommune, bydel og delområde -> region_stat
- 06070, 12306, 14916, 14890: husholdninger, familier, bygningstype, eierstatus -> region_stat
"""
import itertools
import os
import sys
from collections import defaultdict

import requests

from ssb_rutenett import UA, koble_til

API = "https://data.ssb.no/api/pxwebapi/v2/tables"

# kategori_dim: dimensjonen som blir "kategori"; filter: faste verdier; sum_over: dimensjoner som summeres
TABELLER = [
    {"tabell": "04362", "mål": "grunnkrets", "tema": "alder", "kategori_dim": "Alder",
     "filter": {"ContentsCode": ["Personer"]}, "sum_over": ["Kjonn"]},
    {"tabell": "06944", "mål": "region", "tema_fra": "ContentsCode", "kategori_dim": "HusholdType",
     "filter": {"ContentsCode": ["InntSkatt", "AntallHushold"]}},
    {"tabell": "06070", "mål": "region", "tema": "husholdningstype", "kategori_dim": "HushType",
     "filter": {"ContentsCode": ["Husholdninger"]}},
    {"tabell": "12306", "mål": "region", "tema": "familier", "kategori_dim": "FamilieType",
     "filter": {"ContentsCode": ["Familier"]}},
    {"tabell": "14916", "mål": "region", "tema": "bygningstype_pst", "kategori_dim": "BygnType",
     "filter": {"ContentsCode": ["PersonerProsent"]}},
    {"tabell": "14890", "mål": "region", "tema": "eierstatus_pst", "kategori_dim": "EierStatus",
     "filter": {"ContentsCode": ["PersonerProsent"]}},
]
TEMANAVN = {"InntSkatt": "inntekt_etter_skatt_median", "AntallHushold": "husholdninger_antall"}


def metadata(tabell):
    r = requests.get(f"{API}/{tabell}/metadata", params={"lang": "no", "outputFormat": "json-stat2"},
                     headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()


def koder(meta, dim):
    idx = meta["dimension"][dim]["category"]["index"]
    return sorted(idx, key=lambda k: idx[k]) if isinstance(idx, dict) else list(idx)


def etiketter(meta, dim):
    return meta["dimension"][dim]["category"].get("label", {})


def hent_data(tabell, utvalg):
    """POST med utvalg; faller tilbake til GET hvis POST ikke støttes."""
    kropp = {"selection": [{"variableCode": k, "valueCodes": v} for k, v in utvalg.items()]}
    r = requests.post(f"{API}/{tabell}/data", params={"lang": "no", "outputFormat": "json-stat2"},
                      json=kropp, headers=UA, timeout=300)
    if r.status_code in (404, 405, 415):
        params = {"lang": "no", "outputFormat": "json-stat2"}
        for k, v in utvalg.items():
            params[f"valueCodes[{k}]"] = ",".join(v)
        r = requests.get(f"{API}/{tabell}/data", params=params, headers=UA, timeout=300)
    if not r.ok:
        raise RuntimeError(f"{tabell}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def rader_fra_jsonstat(js):
    """Gir (dict dim->kode, verdi) for hver celle."""
    dims = js["id"]
    lister = []
    for d in dims:
        idx = js["dimension"][d]["category"]["index"]
        lister.append(sorted(idx, key=lambda k: idx[k]) if isinstance(idx, dict) else list(idx))
    verdier = js["value"]
    if isinstance(verdier, dict):  # sparsom form
        verdier = [verdier.get(str(i)) for i in range(len(list(itertools.product(*lister))))]
    for i, kombinasjon in enumerate(itertools.product(*lister)):
        yield dict(zip(dims, kombinasjon)), verdier[i]


def nivaa(kode):
    return {4: "kommune", 6: "bydel", 8: "delomrade"}.get(len(kode))


def main():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with koble_til(url) as conn:
        kommuner = tuple(r[0] for r in conn.execute("select kommunenr from kommune where i_region"))
        for cfg in TABELLER:
            t = cfg["tabell"]
            meta = metadata(t)
            tid = koder(meta, "Tid")[-1]
            alle_regioner = koder(meta, "Region")
            if cfg["mål"] == "grunnkrets":
                regioner = [k for k in alle_regioner if len(k) == 8 and k.startswith(kommuner) and not k.endswith("9999")]
            else:
                regioner = [k for k in alle_regioner
                            if (len(k) == 4 and k in kommuner)
                            or (len(k) in (6, 8) and k.startswith(kommuner) and not k.endswith(("99", "9900")))]
            print(f"\n{t}: {meta.get('label')}\n  år {tid}, {len(regioner)} regioner")

            kat_dim = cfg["kategori_dim"]
            kat_navn = etiketter(meta, kat_dim)
            utvalg_fast = {"Tid": [tid]}
            for d in meta["id"]:
                if d in ("Region", "Tid"):
                    continue
                utvalg_fast[d] = cfg.get("filter", {}).get(d, koder(meta, d))

            summer = defaultdict(float)
            antall = defaultdict(int)
            for i in range(0, len(regioner), 400):
                utvalg = dict(utvalg_fast, Region=regioner[i:i + 400])
                js = hent_data(t, utvalg)
                for celle, v in rader_fra_jsonstat(js):
                    if v is None:
                        continue
                    tema = TEMANAVN.get(celle.get("ContentsCode"), cfg.get("tema")) if cfg.get("tema_fra") else cfg["tema"]
                    nøkkel = (celle["Region"], tema, celle[kat_dim])
                    summer[nøkkel] += float(v)
                    antall[nøkkel] += 1
                print(f"  hentet regioner {i + 1}–{min(i + 400, len(regioner))}")

            with conn.cursor() as cur:
                if cfg["mål"] == "grunnkrets":
                    rader = [(r, tema, k, kat_navn.get(k), int(tid), v, t) for (r, tema, k), v in summer.items()]
                    cur.executemany(
                        """insert into grunnkrets_stat (grunnkretsnr, tema, kategori, kategori_navn, aar, verdi, tabell)
                           values (%s, %s, %s, %s, %s, %s, %s)
                           on conflict (grunnkretsnr, tema, kategori, aar) do update set
                             verdi = excluded.verdi, kategori_navn = excluded.kategori_navn, tabell = excluded.tabell""",
                        rader,
                    )
                else:
                    rader = [(r, nivaa(r), t, tema, k, kat_navn.get(k), int(tid), v)
                             for (r, tema, k), v in summer.items() if nivaa(r)]
                    cur.executemany(
                        """insert into region_stat (regionkode, nivaa, tabell, tema, kategori, kategori_navn, aar, verdi)
                           values (%s, %s, %s, %s, %s, %s, %s, %s)
                           on conflict (regionkode, tema, kategori, aar) do update set
                             verdi = excluded.verdi, nivaa = excluded.nivaa, tabell = excluded.tabell,
                             kategori_navn = excluded.kategori_navn""",
                        rader,
                    )
                cur.execute(
                    "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                    ("SSB PxWebApi", t, len(rader), f"{meta.get('label')} – {tid}"),
                )
            conn.commit()
            print(f"  lagret {len(rader)} rader")


if __name__ == "__main__":
    main()
