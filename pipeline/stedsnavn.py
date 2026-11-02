"""Stedsnavn til kartet og områdenavn.

- Stedsnavn (by, tettsted, bygd, bydel, nabolag, grend) fra OpenStreetMap -> tabellen stedsnavn
- Navn på SSBs delområder fra metadata i tabell 06944 -> delomrade.navn

Bruk: python stedsnavn.py steder.geojsonseq
(steder.geojsonseq lages med osmium tags-filter + osmium export, se workflowen)
"""
import json
import os
import re
import sys

from ssb_rutenett import koble_til
from ssb_statistikk import etiketter, metadata

TYPER = {"city": 1, "town": 2, "suburb": 3, "village": 3, "quarter": 4, "neighbourhood": 5, "hamlet": 5}


def main():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")

    steder = []
    with open(sys.argv[1], encoding="utf-8") as f:
        for linje in f:
            linje = linje.strip().lstrip("\x1e")
            if not linje:
                continue
            o = json.loads(linje)
            p = o.get("properties", {})
            g = o.get("geometry") or {}
            if g.get("type") != "Point" or p.get("place") not in TYPER:
                continue
            navn = p.get("name:no") or p.get("name:nb") or p.get("name")
            if not navn:
                continue
            try:
                folk = int(re.sub(r"\D", "", p.get("population", "")) or 0) or None
            except ValueError:
                folk = None
            steder.append((navn, p["place"], TYPER[p["place"]], g["coordinates"][0], g["coordinates"][1], folk))
    print(f"{len(steder)} stedsnavn fra OSM")

    meta = metadata("06944")
    navn_do = {k: re.sub(r"^\d+\s*", "", v).strip() for k, v in etiketter(meta, "Region").items() if len(k) == 8}
    print(f"{len(navn_do)} delområdenavn fra SSB, f.eks. {list(navn_do.items())[:3]}")

    with koble_til(url) as conn, conn.cursor() as cur:
        cur.execute("""
            create table if not exists stedsnavn (
              id serial primary key, navn text not null, type text not null, rang int not null,
              lon double precision not null, lat double precision not null, folketall int)""")
        cur.execute("alter table stedsnavn enable row level security")
        cur.execute("""do $$ begin
            if not exists (select 1 from pg_policies where tablename = 'stedsnavn' and policyname = 'les') then
              create policy les on stedsnavn for select using (true); end if; end $$""")
        cur.execute("truncate stedsnavn restart identity")
        cur.executemany("insert into stedsnavn (navn, type, rang, lon, lat, folketall) values (%s, %s, %s, %s, %s, %s)", steder)
        cur.executemany("update delomrade set navn = %s where delomradenr = %s", [(v, k) for k, v in navn_do.items()])
        cur.execute("select count(*) filter (where navn is not null and navn <> ''), count(*) from delomrade")
        print("Delområder med navn: %s av %s" % cur.fetchone())
        cur.execute(
            "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
            ("OpenStreetMap + SSB 06944", "Stedsnavn og delområdenavn", len(steder), "place-noder og delområdeetiketter"),
        )
        conn.commit()


if __name__ == "__main__":
    main()
