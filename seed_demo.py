"""Add pretend stores so you can look around before connecting anything real.

    python seed_demo.py         adds five demo stores
    python seed_demo.py --wipe  removes them again

Demo stores are marked by the token "demo" and never touch Shopify or Google.
"""
import sys

from dotenv import load_dotenv

load_dotenv()

import db
from demo import PROFILES


def main():
    db.init()
    wipe = "--wipe" in sys.argv

    existing = {s["shop_domain"]: s for s in db.list_stores()}
    touched = 0

    for name in PROFILES:
        domain = "demo-" + name.lower().replace(" & ", "-").replace(" ", "-") + ".myshopify.com"
        store = existing.get(domain)

        if wipe:
            if store:
                db.delete_store(store["id"])
                touched += 1
            continue

        if store:
            continue
        sid = db.upsert_store(name, domain, "demo", "USD", "UTC")
        db.set_cost_pct(sid, 42.0)
        touched += 1

    if wipe:
        print(f"Removed {touched} demo stores.")
    else:
        print(f"Added {touched} demo stores. Run: python app.py")


if __name__ == "__main__":
    main()
