"""Every asset index.html asks for must actually be served.

app/main.py registers front-end assets one explicit route at a time, so adding
a <script> tag without the matching route yields a silent 404: the page still
renders, the module is just missing, and the feature degrades into whatever
its "not available" branch says. That is exactly how waist_axis.js shipped
broken — the tile blamed an unset height instead.

    python3 test/test_static_routes.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")

failed = 0
passed = 0


def check(cond, msg):
    global failed, passed
    if cond:
        passed += 1
    else:
        print("FAIL:", msg)
        failed += 1


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


main_py = read("app/main.py")
# Routes registered as @app.get("/foo.js") — the pattern main.py uses for assets.
routes = set(re.findall(r'@app\.get\("(/[^"]+)"\)', main_py))
# StaticFiles mounts cover whole directories.
mounts = set(re.findall(r'app\.mount\("(/[^"]+)"', main_py))

pages = ["public/index.html", "public/login.html", "public/about.html", "public/admin.html"]
checked = 0

for page in pages:
    if not os.path.isfile(os.path.join(ROOT, page)):
        continue
    html = read(page)
    refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    for ref in refs:
        path = ref.split("?", 1)[0]
        if path.startswith("//"):
            continue  # protocol-relative external
        # Covered by a StaticFiles mount?
        if any(path.startswith(m.rstrip("/") + "/") for m in mounts):
            continue
        # Not an asset we ship (API endpoints, in-app links)
        if not re.search(r"\.(js|css|png|svg|ico|webmanifest)$", path):
            continue
        checked += 1
        on_disk = os.path.isfile(os.path.join(ROOT, "public", path.lstrip("/")))
        check(on_disk, f"{page} references {path} but public{path} does not exist")
        check(path in routes, f"{page} references {path} but app/main.py has no route for it")

check(checked > 0, "found some assets to check (regex still matches the markup)")
# The file that started this: guard it by name so a rename cannot quietly drop it.
check("/waist_axis.js" in routes, "app/main.py serves /waist_axis.js")
check("/bf_axis.js" in routes, "app/main.py serves /bf_axis.js")

print(f"{passed} passed, {failed} failed ({checked} asset references checked)")
sys.exit(1 if failed else 0)
