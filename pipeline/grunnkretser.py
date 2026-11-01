"""Henter grenser for grunnkretser (og avleder delområder) i regionen fra Kartverkets WFS og lagrer dem i Supabase.

Kilde: https://wfs.geonorge.no/skwms1/wfs.grunnkretser (CC BY 4.0, SSB/Kartverket).
Tjenesten støtter ikke paging, så den spørres per kommune med kartutsnitt.
"""
import os
import re
import sys
import tempfile

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import MultiPolygon, Polygon

from ssb_rutenett import UA, finn_kolonne, koble_til, kommunepolygoner

WFS = "https://wfs.geonorge.no/skwms1/wfs.grunnkretser"


def hent_lag(lag, bbox):
    minx, miny, maxx, maxy = bbox
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": lag, "srsName": "urn:ogc:def:crs:EPSG::25833",
        "bbox": f"{minx},{miny},{maxx},{maxy},urn:ogc:def:crs:EPSG::25833",
    }
    r = requests.get(WFS, params=params, headers=UA, timeout=600)
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".gml", delete=False) as f:
        f.write(r.content)
        sti = f.name
    try:
        gdf = gpd.read_file(sti)
    except Exception as e:
        print(f"  kunne ikke lese svar for {lag}: {e}\n  {r.text[:300]}")
        return gpd.GeoDataFrame()
    finally:
        os.unlink(sti)
    if gdf.crs is None:
        gdf = gdf.set_crs(25833)
    elif gdf.crs.to_epsg() != 25833:
        gdf = gdf.to_crs(25833)
    return gdf


def som_multi(g):
    if g is None:
        return None
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    if isinstance(g, MultiPolygon):
        return g
    deler = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
    return MultiPolygon(deler) if deler else None


def main():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        sys.exit("Mangler SUPABASE_DB_URL")
    with koble_til(url) as conn:
        nummer = [r[0] for r in conn.execute("select kommunenr from kommune where i_region order by 1")]
        kommuner = kommunepolygoner(nummer)

        gk_deler = []
        for _, k in kommuner.iterrows():
            gk = hent_lag("app:Grunnkrets", k.geometry.bounds)
            print(f"{k.kommunenr}: {len(gk)} grunnkretser i utsnittet")
            gk_deler.append(gk)

        gk = pd.concat([d for d in gk_deler if not d.empty], ignore_index=True)
        print(f"Kolonner grunnkrets: {list(gk.columns)}")

        gk_nr = finn_kolonne(gk.columns, [r"^grunnkretsnummer$", r"grunnkretsnr", r"grunnkrets.*nummer"])
        gk_navn = finn_kolonne(gk.columns, [r"^grunnkretsnavn$", r"grunnkrets.*navn"])
        if not gk_nr:
            sys.exit("Fant ikke nummerkolonner – se kolonnelistene over.")

        prefiks = tuple(nummer)
        gk = gk[gk[gk_nr].astype(str).str.startswith(prefiks)].drop_duplicates(subset=[gk_nr])
        print(f"{len(gk)} grunnkretser i regionen")

        with conn.cursor() as cur:


            rader = []
            for _, r in gk.iterrows():
                g = som_multi(r.geometry)
                if g is None:
                    continue
                nr = str(r[gk_nr])
                rader.append((nr, str(r[gk_navn]) if gk_navn else None, nr[:4], nr[:6] + "00", g.wkt))
            cur.executemany(
                """insert into grunnkrets (grunnkretsnr, navn, kommunenr, delomradenr, geom)
                   values (%s, %s, %s, %s, extensions.st_geomfromtext(%s, 25833))
                   on conflict (grunnkretsnr) do update set navn = excluded.navn, kommunenr = excluded.kommunenr,
                     delomradenr = excluded.delomradenr, geom = excluded.geom""",
                rader,
            )
            print(f"Lagret {len(rader)} grunnkretser")
            # Delområder = grunnkretser med samme første seks sifre, slått sammen
            cur.execute("""
                insert into delomrade (delomradenr, kommunenr, geom)
                select delomradenr, min(kommunenr),
                       extensions.st_multi(extensions.st_collectionextract(
                         extensions.st_union(extensions.st_makevalid(geom)), 3))
                from grunnkrets
                where kommunenr = any(%s) and delomradenr is not null
                group by delomradenr
                on conflict (delomradenr) do update set kommunenr = excluded.kommunenr, geom = excluded.geom
            """, (list(nummer),))
            print(f"Lagret {cur.rowcount} delområder (sammenslått fra grunnkretser)")
            cur.execute(
                "insert into kilde_logg (kilde, datasett, rader, merknad) values (%s, %s, %s, %s)",
                ("Kartverket WFS", "Grunnkretser og delområder", len(rader), "delområder avledet fra grunnkretser"),
            )
        conn.commit()


if __name__ == "__main__":
    main()
