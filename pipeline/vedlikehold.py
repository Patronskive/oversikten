"""Ukentlig sjekk fra GitHub Actions.

Spørringen holder Supabase-prosjektet aktivt (gratisprosjekter settes på pause etter en uke uten bruk),
og sier fra om noen reisetider er eldre enn FERSK_DAGER, slik at arbeidsflyten regner dem på nytt.
"""
import os

from reisetid import FERSK_DAGER, VERSJON, reisedag
from ssb_rutenett import koble_til


def main():
    dag = reisedag()
    with koble_til(os.environ["SUPABASE_DB_URL"]) as conn:
        ruter = conn.execute("select count(*) from rute250").fetchone()[0]
        mål = conn.execute("select count(*) from reisemal where aktiv and lon is not null and lat is not null").fetchone()[0]
        ferske = conn.execute("""select count(*) from rute_reisetid t join reisemal m on m.id = t.maal_id and m.aktiv
                                 where t.beregnet_for >= %s - %s and t.versjon >= %s""", (dag, FERSK_DAGER, VERSJON)).fetchone()[0]
    mangler = ruter * mål - ferske
    status = "utdatert" if mangler > 0 else "fersk"
    print(f"Databasen svarer. {ferske} av {ruter * mål} reisetider er ferske (reisedag {dag}, grense {FERSK_DAGER} dager): {status}")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        f.write(f"reisetid={status}\n")


if __name__ == "__main__":
    main()
