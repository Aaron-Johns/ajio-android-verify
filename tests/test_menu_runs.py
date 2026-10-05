"""Menu runs (top / bottom navigation, ads, trending): how they expand into runs, how the runner starts one, and the API around them
(menu data, run summary, Excel downloads, no menu pages in schedules). A throwaway SQLite file; nothing is started for real."""
import json
import types

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from qa import pages
from web import api, db, runner

ALL_MENU = ["menu-top-nav", "menu-bottom-nav", "menu-ads", "menu-trending"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    db.init_db()
    api.scheduler.remove_all_jobs()
    yield TestClient(api.app)
    api.scheduler.remove_all_jobs()


def add_menu_run(tmp_path, run_id, page, status="done", l1="nontransacted", menu=None, state="KARNATAKA"):
    out = tmp_path / "runs" / run_id
    (out / "images").mkdir(parents=True)
    if menu is not None:
        Image.new("RGB", (40, 30), (10, 120, 200)).save(out / "images" / "a.png", "PNG")
        (out / "menu.json").write_text(json.dumps(menu), encoding="utf-8")
        (out / "menu_meta.json").write_text(json.dumps({"kind": menu["kind"], "items": len(menu["items"]), "with_images": 1}), encoding="utf-8")
    db.insert_run(run_id, str(out), "hero", None, 1, l1, "unisex", None, None, page=page, state=state)
    if status != "running":
        db.finish_run(run_id, status)
    return out


def menu(kind, l1="nontransacted"):
    items = [{"id": 0, "parent": None, "level": 0, "title": "Men", "link": "https://www.ajio.com/men", "opens": "web page", "description": "", "alt": "",
              "images": ["https://cdn/a.png"], "image_files": ["a.png"], "active": True, "audience": ""}]
    return {"kind": kind, "l1": l1, "l2": "unisex", "state": "KARNATAKA", "fetched_at": "2026-10-05T11:40:00+00:00", "items": items}


# ---- how a launch expands ----

def test_menu_pages_expand_by_l1_and_state_only_where_their_content_depends_on_them():
    combos = runner.expand_combos(["premium", "nonpremium"], ["men", "women"], ["ASSAM", "GUJARAT"], ["560029", "380001"], ALL_MENU)
    by = lambda page: [(c["l1"], c["state"], c["pincode"]) for c in combos if c["page"] == page]
    assert by("menu-top-nav") == [("premium", "ASSAM", "560029"), ("nonpremium", "ASSAM", "560029")]        # per l1; first state and pincode only
    assert by("menu-bottom-nav") == [("nontransacted", "ASSAM", "560029")]                                  # no cohort at all
    assert by("menu-ads") == [("premium", "ASSAM", "560029"), ("premium", "GUJARAT", "560029"), ("nonpremium", "ASSAM", "560029"), ("nonpremium", "GUJARAT", "560029")]
    assert by("menu-trending") == [("premium", "ASSAM", "560029"), ("nonpremium", "ASSAM", "560029")]
    assert all(c["l2"] == "unisex" for c in combos)                                                          # l2 never matters here


def test_a_launch_can_mix_banner_pages_and_menu_pages():
    combos = runner.expand_combos(["premium"], ["men"], ["KARNATAKA"], ["560029"], ["home", "menswear", "menu-trending"])
    assert [c["page"] for c in combos] == ["home", "menswear", "menu-trending"]


def test_a_menu_run_is_labelled_by_what_it_depends_on():
    label = lambda page, **kw: runner.combo_label({"page": page, "l1": "premium", "l2": "unisex", "state": "ASSAM", "pincode": "1", **kw})
    assert label("menu-top-nav") == "Top Nav · premium" and label("menu-bottom-nav") == "Bottom Nav" and label("menu-ads") == "Ads · premium · ASSAM"


def test_the_banner_pages_are_unchanged_and_menu_pages_are_separate():
    assert "menu-top-nav" not in pages.PAGE_IDS and pages.RUN_PAGE_IDS == pages.PAGE_IDS + ALL_MENU
    assert pages.menu_page("home") is None and pages.menu_page("menu-ads")["kind"] == "ads"


# ---- starting one ----

def test_a_menu_run_starts_the_menu_fetcher_not_the_banner_check(client, tmp_path, monkeypatch):
    seen = {}

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        (runner.RUNS_DIR / "20261005T114000Z_topnav_menu").mkdir()
        return types.SimpleNamespace(pid=4242, poll=lambda: None, returncode=0, communicate=lambda: ("", ""))

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    run_id = runner.start_run("premium", "unisex", "hero", 5, 3, excluded_carousels=[1], page="menu-top-nav", state="ASSAM")
    cmd = seen["cmd"]
    assert run_id == "20261005T114000Z_topnav_menu" and cmd[1:3] == ["-m", "qa.app_menus"]
    assert cmd[cmd.index("--kind") + 1] == "top-nav" and cmd[cmd.index("--l1") + 1] == "premium" and cmd[cmd.index("--state") + 1] == "ASSAM"
    assert not {"--scope", "--limit", "--workers", "--exclude-carousel", "--page"} & set(cmd)           # none of the banner options apply
    assert seen["env"]["AJIO_USER_GROUPS"] == "l1:premium|l2:p_null,false,unisex,noasp" and db.get_run(run_id)["page"] == "menu-top-nav"
    with pytest.raises(ValueError):
        runner.start_run("premium", "unisex", "hero", None, 1, page="menu-nope")


# ---- API ----

def test_meta_lists_the_menu_options(client):
    got = client.get("/api/meta").json()
    assert [m["id"] for m in got["menu_options"]] == ALL_MENU and got["menu_options"][2]["by_state"] is True
    assert "menu-ads" not in [p["id"] for p in got["page_options"]]


def test_a_run_request_may_name_menu_pages_but_a_schedule_may_not(client, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "start_batch", lambda combos, *a, **k: (calls.append(combos) or ("r1", "b1", len(combos) - 1)))
    r = client.post("/api/runs", json={"pages": ["menu-trending", "menu-top-nav"], "l1s": ["premium"], "l2s": ["unisex"]})
    assert r.status_code == 200 and [c["page"] for c in calls[0]] == ["menu-trending", "menu-top-nav"]
    s = client.post("/api/schedules", json={"name": "ZZ menu", "interval_minutes": 60, "pages": ["menu-ads"], "l1s": ["premium"], "l2s": ["men"]})
    assert s.status_code == 422 and "page must be one of" in s.json()["detail"]


def test_the_run_summary_says_it_is_a_menu_run_and_carries_its_size(client, tmp_path):
    add_menu_run(tmp_path, "m1", "menu-top-nav", menu=menu("top-nav"))
    add_menu_run(tmp_path, "f1", "home")
    m, f = client.get("/api/runs/m1").json(), client.get("/api/runs/f1").json()
    assert (m["kind"], m["menu"]["items"], m["counts"]) == ("menu", 1, {}) and (f["kind"], f["menu"] if "menu" in f else None) == ("feed", None)


def test_a_menu_runs_data_is_served_and_the_errors_say_why(client, tmp_path):
    add_menu_run(tmp_path, "m1", "menu-top-nav", menu=menu("top-nav"))
    add_menu_run(tmp_path, "m2", "menu-ads", status="failed")
    add_menu_run(tmp_path, "f1", "home")
    got = client.get("/api/runs/m1/menu").json()
    assert got["kind"] == "top-nav" and got["items"][0]["title"] == "Men" and got["items"][0]["image_files"] == ["a.png"]
    assert client.get("/api/runs/m1/images/a.png").status_code == 200                      # the same images route the banner runs use
    assert client.get("/api/runs/m2/menu").status_code == 404 and "no data" in client.get("/api/runs/m2/menu").json()["detail"]
    assert client.get("/api/runs/f1/menu").status_code == 404 and client.get("/api/runs/nope/menu").status_code == 404


XLSX = "spreadsheetml"


def test_one_menu_run_downloads_as_an_excel_file_and_a_running_one_is_refused(client, tmp_path):
    add_menu_run(tmp_path, "m1", "menu-top-nav", l1="premium", menu=menu("top-nav", "premium"))
    add_menu_run(tmp_path, "m3", "menu-top-nav", status="running")
    r = client.get("/api/runs/m1/export.xlsx")
    assert r.status_code == 200 and XLSX in r.headers["content-type"] and r.content[:2] == b"PK"
    assert "top-nav_premium_" in r.headers["content-disposition"]
    assert client.get("/api/runs/m3/export.xlsx").status_code == 409


def test_the_combined_download_takes_the_newest_finished_run_of_each_kind(client, tmp_path):
    import openpyxl, io
    assert client.get("/api/menu/export.xlsx").status_code == 404                           # nothing fetched yet
    add_menu_run(tmp_path, "old", "menu-top-nav", menu=menu("top-nav"))
    add_menu_run(tmp_path, "trend", "menu-trending", menu={**menu("trending"), "items": [{**menu("trending")["items"][0], "title": "#Trend"}]})
    add_menu_run(tmp_path, "new", "menu-top-nav", l1="premium", menu={**menu("top-nav", "premium"), "items": [{**menu("top-nav")["items"][0], "title": "Newest"}]})
    r = client.get("/api/menu/export.xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Read me", "Top menu", "Trending"]
    assert wb["Top menu"]["D2"].value == "Newest" and wb["Trending"]["C2"].value == "#Trend"


def test_the_ui_files_are_served_with_no_cache_so_a_browser_never_keeps_an_old_script(client):
    for path in ("/", "/manager/", "/manager/app.js", "/manager/style.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
    again = client.get("/manager/app.js", headers={"If-None-Match": client.get("/manager/app.js").headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"      # unchanged files still cost almost nothing


def test_a_run_folder_picture_is_served_so_it_can_only_ever_be_a_picture(client, tmp_path):
    add_menu_run(tmp_path, "m1", "menu-top-nav", menu=menu("top-nav"))
    h = client.get("/api/runs/m1/images/a.png").headers
    assert h["x-content-type-options"] == "nosniff" and "sandbox" in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
