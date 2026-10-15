import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from htmlreview import picker
from htmlreview.server import create_app

EXAMPLES = Path(__file__).parent.parent / "examples"
TOKEN = "test-token"
HOST = "testserver"


@pytest.fixture
def report(tmp_path):
    shutil.copytree(EXAMPLES / "sales-report", tmp_path / "sales-report")
    return tmp_path / "sales-report" / "report.html"


@pytest.fixture
def picked():
    return {"path": None}


@pytest.fixture
def client(picked):
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=lambda _: picked["path"])
    return TestClient(app, headers={"X-HR-Token": TOKEN})


def open_doc(client, path) -> str:
    res = client.post("/api/open", json={"path": str(path)})
    assert res.status_code == 200, res.text
    return res.json()["url"]


def config_of(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="__hr-config">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


# -- security ---------------------------------------------------------------


def test_rejects_foreign_host(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_api_requires_token(client, report):
    res = client.post("/api/open", json={"path": str(report)}, headers={"X-HR-Token": "nope"})
    assert res.status_code == 403


def test_start_page_carries_token(client):
    res = client.get("/")
    assert res.status_code == 200
    assert TOKEN in res.text


def test_static_passthrough_stays_inside_folder(client, report, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    url = open_doc(client, report)
    base = url.rsplit("/", 1)[0]
    assert client.get(f"{base}/assets/style.css").status_code == 200
    assert client.get(f"{base}/../secret.txt").status_code == 404
    assert client.get(f"{base}/..%2Fsecret.txt").status_code == 404
    assert client.get(f"{base}/assets/missing.css").status_code == 404


def test_unknown_doc_token(client):
    assert client.get("/doc/nope/report.html").status_code == 404


# -- opening ----------------------------------------------------------------


def test_open_serves_tagged_page_with_editor(client, report):
    url = open_doc(client, report)
    res = client.get(url)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html; charset=utf-8")
    assert 'data-eid="e1"' in res.text
    assert "/__hr/editor.js" in res.text
    cfg = config_of(res.text)
    assert cfg["apiToken"] == TOKEN
    assert cfg["name"] == "report.html"
    assert cfg["editable"]["e1"] == "Q3 2026 Sales Review"
    # Serving never writes anything next to the report.
    assert sorted(p.name for p in report.parent.iterdir()) == ["assets", "report.html"]


def test_open_same_file_twice_reuses_url(client, report):
    assert open_doc(client, report) == open_doc(client, report)


def test_open_accepts_quoted_windows_style_path(client, report):
    assert client.post("/api/open", json={"path": f'  "{report}" '}).status_code == 200


@pytest.mark.parametrize("bad", ["", "nope.html", "{dir}", "{dir}/assets/style.css"])
def test_open_rejects_bad_paths(client, report, bad):
    res = client.post("/api/open", json={"path": bad.format(dir=report.parent)})
    assert res.status_code == 400
    assert res.json()["error"]


def test_opening_working_copy_resumes_original(client, report):
    edit = report.with_name("report.edit.html")
    edit.write_text(report.read_text(encoding="utf-8").replace("Summary", "Overview"), encoding="utf-8")
    url = open_doc(client, edit)
    assert url.endswith("/report.html")
    page = client.get(url).text
    assert ">Overview<" in page
    assert config_of(page)["resumed"] is True


def test_legacy_encoding_served_as_utf8(client, tmp_path):
    shutil.copytree(EXAMPLES / "legacy-export", tmp_path / "legacy")
    res = client.get(open_doc(client, tmp_path / "legacy" / "report.htm"))
    assert "Café expansion" in res.text


# -- picker -----------------------------------------------------------------


def test_pick_cancel_and_success(client, picked, report):
    assert client.post("/api/pick").json() == {"cancelled": True}
    picked["path"] = str(report)
    assert client.post("/api/pick").json()["url"].endswith("/report.html")


def test_pick_without_dialog(report):
    def no_dialog(_):
        raise picker.DialogUnavailable("no tk")

    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=no_dialog)
    res = TestClient(app, headers={"X-HR-Token": TOKEN}).post("/api/pick")
    assert res.status_code == 501
    assert res.json()["noDialog"] is True


# -- save and publish ---------------------------------------------------------


def load_page(client, report):
    url = open_doc(client, report)
    page = client.get(url).text
    cfg = config_of(page)
    return cfg, url


def save(client, cfg, edits=None, comments=None):
    return client.post(
        f"/api/save?doc={cfg['doc']}",
        json={"version": cfg["version"], "edits": edits or {}, "comments": comments or []},
    )


COMMENT = {
    "id": "c1",
    "anchor": {"eid": "e7", "quote": "Revenue grew", "prefix": "", "suffix": " steadily"},
    "body": "Source?",
    "status": "open",
    "author": "alex",
    "created": "2026-10-15T10:10:00-07:00",
    "replies": [],
    "resolution": None,
}


def test_save_writes_working_copy_and_sidecar_never_original(client, report):
    original = report.read_bytes()
    cfg, url = load_page(client, report)
    res = save(client, cfg, {"e6": "Overview"}, [COMMENT])
    assert res.status_code == 200, res.text
    assert report.read_bytes() == original
    working = report.with_name("report.edit.html").read_text(encoding="utf-8")
    assert '<h2 data-eid="e6">Overview</h2>' in working
    assert "__hr" not in working
    side = json.loads(report.with_name("report.comments.json").read_text(encoding="utf-8"))
    assert side["version"] == 1 and side["source"] == "report.html"
    assert side["comments"] == [COMMENT]
    # Reloading resumes from the working copy and the sidecar.
    cfg2 = config_of(client.get(url).text)
    assert cfg2["resumed"] is True
    assert cfg2["comments"] == [COMMENT]
    assert cfg2["editable"]["e6"] == "Overview"
    assert cfg2["version"] == res.json()["version"]


def test_save_with_no_edits_keeps_scripts_and_data_identical(client, report):
    cfg, _ = load_page(client, report)
    assert save(client, cfg).status_code == 200
    from htmlreview.transform import strip_eids

    working = report.with_name("report.edit.html").read_bytes().decode("utf-8")
    assert strip_eids(working).encode("utf-8") == report.read_bytes()


def test_save_refuses_when_files_changed_on_disk(client, report):
    cfg, _ = load_page(client, report)
    assert save(client, cfg, {"e6": "A"}).status_code == 200
    # Same (now stale) version again, as a second tab or an agent edit would cause.
    res = save(client, cfg, {"e6": "B"})
    assert res.status_code == 409
    assert res.json()["conflict"] is True


def test_save_rejects_bad_comments_and_bad_eids(client, report):
    cfg, _ = load_page(client, report)
    assert save(client, cfg, comments=[{"id": "c1"}]).status_code == 400
    assert save(client, cfg, {"e999": "x"}).status_code == 400
    assert not report.with_name("report.edit.html").exists()


def test_publish_writes_clean_file_and_cleans_up(client, report):
    original = report.read_bytes()
    cfg, url = load_page(client, report)
    res = save(client, cfg, {"e6": "Overview"}, [COMMENT])
    cfg["version"] = res.json()["version"]
    res = client.post(
        f"/api/publish?doc={cfg['doc']}",
        json={"version": cfg["version"], "edits": {"e7": "New summary."}, "comments": []},
    )
    assert res.status_code == 200, res.text
    published = report.with_name("report.published.html").read_text(encoding="utf-8")
    assert "data-eid" not in published and "__hr" not in published
    expected = (
        original.decode("utf-8")
        .replace("<h2>Summary</h2>", "<h2>Overview</h2>")
    )
    expected = re.sub(r"(<p>)Revenue grew steadily.*?(</p>)", r"\1New summary.\2", expected, count=1, flags=re.S)
    assert published == expected
    assert report.read_bytes() == original
    assert not report.with_name("report.edit.html").exists()
    assert not report.with_name("report.comments.json").exists()
    # The published file is viewable as a plain file.
    view = client.get(res.json()["url"])
    assert view.status_code == 200 and "__hr" not in view.text


def test_publish_without_edits_is_byte_identical(client, tmp_path):
    shutil.copytree(EXAMPLES / "legacy-export", tmp_path / "legacy")
    report = tmp_path / "legacy" / "report.htm"
    cfg, _ = load_page(client, report)
    res = client.post(f"/api/publish?doc={cfg['doc']}", json={"version": cfg["version"]})
    assert res.status_code == 200
    assert report.with_name("report.published.htm").read_bytes() == report.read_bytes()


def test_original_changed_warning(client, report):
    cfg, url = load_page(client, report)
    save(client, cfg, comments=[COMMENT])
    report.write_text(report.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert config_of(client.get(url).text)["originalChanged"] is True


def test_broken_sidecar_blocks_save(client, report):
    report.with_name("report.comments.json").write_text("{not json", encoding="utf-8")
    cfg, _ = load_page(client, report)
    assert "not valid JSON" in cfg["commentsError"]
    assert save(client, cfg, {"e6": "x"}).status_code == 400
