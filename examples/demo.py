#!/usr/bin/env python3
"""cairn -- the four scenarios, end to end.

Run:  python3 examples/demo.py

This is a living tour of what Cairn is for.  It uses nothing but the
public API (cairn.open + the methods on CairnGraph / EnterpriseDB) and a
handful of temp directories, so it is safe to run anywhere.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairn
from cairn.enterprise import (EnterpriseDB, issue_license, load_license,
                             save_license, vendor_keypair)
from cairn.ingest import Ingestor


def hr(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def scenario_personal_wiki() -> None:
    hr("1. Personal wiki -- \"what was I doing, when?\" (bitemporal)")
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "me"), create=True)

    # I change jobs, then I remember I mis-stated the first one.
    g.record("me", "employer", "Globex", "2019-03-01", recorder="me")
    g.record("me", "employer", "Initech", "2022-07-01", recorder="me")
    g.record("me", "employer", "Hooli", "2024-11-15", recorder="me")

    print("  current employer:", g.value("me", "employer"))
    print("  how many statements ever:", len(g.view().timeline("me")))

    # A query as-of 2020 sees only Globex, even though the log already
    # contains later facts.  The past is reproducible.
    as_of_2020 = g.view().at_time("2020-06-01")
    print("  employer as-of 2020-06-01:",
          [c.value for c in as_of_2020 if c.subject == "me"
           and c.predicate == "employer"])

    # Oops -- Globex was actually a contractor gig; I retract it.
    first = g.view().resolve("me", "employer").candidates[0]
    g.retract(first.cid, "me", "it was contract, not employment")
    print("  employer after retraction:", g.value("me", "employer"))
    # History is NOT overwritten: the retracted claim is still there,
    # just no longer the live value.
    print("  statements still on the log:", len(g.view().timeline("me")))


def scenario_game_server() -> None:
    hr("2. Game-server wiki -- \"what did this player do?\" (bot ingest)")
    g = cairn.open(os.path.join(tempfile.mkdtemp(), "server"), create=True)
    bot = Ingestor(g.path)

    # A bot streams events straight from the server log (trusted device).
    events = [
        {"subject": "player:42", "predicate": "display_name",
         "value": "NightOwl", "valid_from": "2026-03-01", "recorder": "server"},
        {"subject": "player:42", "predicate": "level",
         "value": 17, "valid_from": "2026-03-01", "recorder": "server"},
        {"subject": "player:42", "predicate": "clan",
         "value": "RedFox", "valid_from": "2026-03-02", "recorder": "server"},
        {"subject": "player:42", "predicate": "banned",
         "value": False, "valid_from": "2026-03-01", "recorder": "server"},
    ]
    report = bot.auto(g, events)
    print("  ingested:", report.summary())

    # The same person logged in under a raw account id earlier; we alias
    # the two identities so a query on either sees the whole story.
    g.record("acct:7781", "display_name", "NightOwl", "2026-02-10",
             recorder="server")
    g.alias("player:42", "acct:7781", "server")
    print("  name via alias (acct:7781):",
          g.value("acct:7781", "display_name"))

    # A moderator disputes the clan and another verifies it; both survive.
    clan = g.view().resolve("player:42", "clan")
    g.dispute(clan.candidates[-1].cid, "mod1", "unverified")
    g.endorse(clan.candidates[-1].cid, "mod2", "checked the logs")
    r = g.view().resolve("player:42", "clan")
    print(f"  clan {r.value!r} disputed={r.disputed} "
          f"verified_by={r.verified_by}")

    # Full-text search finds the player from a human-readable note.
    g.record("player:42", "note", "NightOwl 三月加入红狐公会", "2026-03-03",
             recorder="mod1")
    hits = g.view().search("红狐")
    print("  search '红狐' hits subjects:", sorted({c.subject for c in hits}))


def scenario_family_sync() -> None:
    hr("3. Family info exchange -- two devices, one shared truth")
    mom = cairn.open(os.path.join(tempfile.mkdtemp(), "mom"), create=True)
    dad = cairn.open(os.path.join(tempfile.mkdtemp(), "dad"), create=True)

    mom.record("kid", "allergy", "peanuts", "2020-01-01", recorder="mom",
               scope="family")
    mom.record("grandma", "phone", "+1-555-0100", "2018-01-01", recorder="mom",
               scope="family")
    dad.record("kid", "school", "Lincoln Elementary", "2021-09-01",
              recorder="dad", scope="family")
    dad.record("kid", "allergy", "peanuts", "2020-01-01", recorder="dad",
               scope="family")  # same fact, independent assertion -> idempotent

    # Mom exports a bundle (a USB stick / email attachment) and Dad imports it.
    bundle = os.path.join(tempfile.mkdtemp(), "bundle")
    cairn.export(mom.path, bundle)
    merged = cairn.import_(dad.path, bundle)
    print("  claims Mom contributed to Dad's db:", merged)

    shared = cairn.open(dad.path)
    print("  kid allergy (family scope):", shared.view("family").value("kid", "allergy"))
    print("  grandma phone (family scope):",
          shared.view("family").value("grandma", "phone"))
    # A private-scoped reader (the kid's own phone) sees none of it.
    print("  private reader sees allergy:",
          shared.view("private").value("kid", "allergy"))


def scenario_enterprise() -> None:
    hr("4. Enterprise -- employee info passing (license + encryption)")
    vsk, vpk = vendor_keypair()
    lic_doc = issue_license(vsk, "acme", seats=50)
    lic_path = os.path.join(tempfile.mkdtemp(), "license.json")
    save_license(lic_doc, lic_path)
    lic = load_license(lic_path, vpk)

    db = EnterpriseDB.open(os.path.join(tempfile.mkdtemp(), "hr"), lic, vpk,
                           create=True)
    db.record("alice", "title", "Staff Engineer", "2023-01-01", recorder="hr")
    db.record("alice", "salary_band", "L5", "2023-01-01", recorder="hr",
              scope="company")
    print("  seats used (distinct recorders):", db.seats_used())

    # Encrypt the whole log at rest.  A plain cairn reader now sees nothing;
    # a leaked segment file is noise without the license key.
    n = db.encrypt_database()
    print("  encrypted segments:", n, "plaintext claims visible:",
          cairn.open(db.graph.path).count())

    # Compliance export: decrypt to a normal db with the license.
    dest = os.path.join(tempfile.mkdtemp(), "hr-plain")
    db.decrypt_to(dest)
    print("  decrypted alice title:", cairn.open(dest).value("alice", "title"))

    # A different org's license CANNOT decrypt (different master secret).
    other_doc = issue_license(vsk, "rival-co", seats=5)
    other_path = os.path.join(tempfile.mkdtemp(), "lic2.json")
    save_license(other_doc, other_path)
    other = load_license(other_path, vpk)
    db2 = EnterpriseDB(graph=db.graph, license=other, vendor_pk=vpk)
    try:
        db2.decrypt_to(os.path.join(tempfile.mkdtemp(), "x"))
        print("  ERROR: rival license decrypted the data!")
    except cairn.LicenseError:
        print("  rival license correctly refused to decrypt (LicenseError)")


def main() -> None:
    print("Cairn %s -- local-first shared memory" % cairn.__version__)
    scenario_personal_wiki()
    scenario_game_server()
    scenario_family_sync()
    scenario_enterprise()
    hr("done")
    print("Every scenario above is backed by the same append-only claim "
          "log.  Nothing was overwritten; everything merges by set union.")


if __name__ == "__main__":
    main()
