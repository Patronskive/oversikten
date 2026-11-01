"""Eksporterer stedsdata fra Supabase til en kompakt JSON-fil som appen kan lese fra GitHub Pages.

Bruk: python eksport.py ../data/stedsdata.json
"""
import json
import os
import sys
from datetime import date

from ssb_rutenett import koble_til

FELT = ["tog", "tbane", "buss", "dagl", "skole", "bhg", "sjo", "skog"]


def main():
    ut = sys.argv[1] if len(sys.argv) > 1 else "../data/stedsdata.json"
    with koble_til(os.environ["SUPABASE_DB_URL"]) as conn:
        do_rader = conn.execute("""
            select d.delomradenr,
                   max(s.verdi) filter (where s.tema = 'inntekt_etter_skatt_median' and s.kategori = '0000'),
                   max(s.aar) filter (where s.tema = 'inntekt_etter_skatt_median')
            from delomrade d
            left join region_stat s on s.regionkode = d.delomradenr and s.nivaa = 'delomrade'
            group by 1 order by 1
        """).fetchall()
        do_idx = {r[0]: i for i, r in enumerate(do_rader)}

        km = conn.execute("""
            select k.kommunenr, k.navn,
                   max(s.verdi) filter (where s.tema = 'inntekt_etter_skatt_median' and s.kategori = '0000'),
                   extensions.st_asgeojson(extensions.st_transform(
                     extensions.st_simplifypreservetopology(extensions.st_union(g.geom), 150), 4326), 4)
            from kommune k
            join grunnkrets g using (kommunenr)
            left join region_stat s on s.regionkode = k.kommunenr and s.nivaa = 'kommune'
            where k.i_region
            group by 1, 2 order by 1
        """).fetchall()
        k_idx = {r[0]: i for i, r in enumerate(km)}

        alder = conn.execute("""
            with t as (
              select g.kommunenr, s.grunnkretsnr, s.kategori, s.verdi from grunnkrets_stat s join grunnkrets g using (grunnkretsnr)
              where s.tema = 'alder')
            select grunnkretsnr,
                   sum(verdi) filter (where kategori in ('00-05','06-15')) as barn,
                   sum(verdi) as alle
            from t group by 1
        """).fetchall()
        barn = {r[0]: (r[1] or 0, r[2] or 0) for r in alder}

        ruter = conn.execute("""
            select r.ssbid, r.kommunenr, r.bosatte,
                   extensions.st_x(extensions.st_transform(extensions.st_centroid(r.geom), 4326)),
                   extensions.st_y(extensions.st_transform(extensions.st_centroid(r.geom), 4326)),
                   a.gange_tog_m, a.gange_tbane_m, a.gange_buss_m, a.gange_dagligvare_m,
                   a.gange_skole_m, a.gange_barnehage_m, a.luft_sjo_m, a.luft_storskog_m,
                   g.grunnkretsnr, g.delomradenr
            from rute250 r
            left join rute_avstand a using (ssbid)
            left join lateral (
              select grunnkretsnr, delomradenr from grunnkrets g
              where extensions.st_contains(g.geom, extensions.st_centroid(r.geom)) limit 1) g on true
            order by r.ssbid
        """).fetchall()

        aar = conn.execute("select max(aar) from rute250").fetchone()[0]

    data = {
        "laget": date.today().isoformat(),
        "befolkningsaar": aar,
        "kilder": [
            "SSB: befolkning på 250 m-rutenett, alder per grunnkrets, husholdningsinntekt per delområde (NLOD)",
            "Kartverket: grunnkretsgrenser (CC BY 4.0)",
            "OpenStreetMap-bidragsytere: gangnett, stasjoner, butikker, skoler, kyst og skog (ODbL)",
        ],
        "kommuner": [
            {"nr": r[0], "navn": r[1], "inntekt": r[2], "geom": json.loads(r[3])} for r in km
        ],
        "delomrader": [{"nr": r[0], "inntekt": r[1]} for r in do_rader],
        "ruter": {"lon": [], "lat": [], "bosatte": [], "kommune": [], "delomrade": [], "barn_pst": [],
                  **{f: [] for f in FELT}},
    }
    R = data["ruter"]
    for r in ruter:
        R["lon"].append(round(r[3], 5))
        R["lat"].append(round(r[4], 5))
        R["bosatte"].append(r[2])
        R["kommune"].append(k_idx.get(r[1]))
        R["delomrade"].append(do_idx.get(r[14]))
        b, alle = barn.get(r[13], (0, 0))
        R["barn_pst"].append(round(100 * b / alle) if alle >= 30 else None)
        for f, v in zip(FELT, r[5:13]):
            R[f].append(v)

    os.makedirs(os.path.dirname(os.path.abspath(ut)), exist_ok=True)
    with open(ut, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Skrev {ut}: {len(ruter)} ruter, {len(km)} kommuner, {len(do_rader)} delområder, "
          f"{os.path.getsize(ut) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
