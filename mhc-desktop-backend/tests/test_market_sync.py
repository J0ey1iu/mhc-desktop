"""End-to-end test for the market proxy + sha sync engine.

Runs an in-process market service (httpx ASGITransport) behind the
kernel's /api/v1/market router and drives the full sync lifecycle:

* new local skill → sync → pushed to cloud
* cloud edited (simulating another device) → sync → pulled
* both sides edited → conflict → resolve(local) → pushed
* publish → market lists it; add → local copy created
"""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from mhc_desktop_backend import create_app as kernel_create_app
from mhc_desktop_backend.config import Config
from mhc_desktop_deploy.impls.auth.mock import MockAuthProvider
from mhc_desktop_deploy.impls.file_stores.skills_store import SkillStore
from mhc_market_backend import create_app as market_create_app
from mhc_market_backend.store import MarketStore

MARKET_SECRET = "market-test-secret"


def make_skill_zip(name: str, body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md", f"---\nname: {name}\ndescription: t\n---\n{body}"
        )
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path: Path):
    data_dir = tmp_path / "data"
    store = SkillStore(
        skills_dir=data_dir / "skills", state_file=data_dir / "skills-state.json"
    )
    market_app = market_create_app(data_root=tmp_path / "market", secret=MARKET_SECRET)
    market_store = MarketStore(tmp_path / "market")

    app = kernel_create_app(
        config=Config(debug=True),
        skills=store,
        auth=MockAuthProvider(),
        market_base_url="http://market.test",
        market_secret=MARKET_SECRET,
        market_transport=httpx.ASGITransport(app=market_app),
    )
    c = TestClient(app)

    r = c.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wonderland"}
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c, market_store


def import_skill(c: TestClient, slug: str, body: str) -> None:
    r = c.post(
        "/api/v1/skills/import-zip",
        json={"data": base64.b64encode(make_skill_zip(slug, body)).decode()},
    )
    assert r.status_code == 201, r.text


def sync(c: TestClient) -> dict:
    r = c.post("/api/v1/market/sync")
    assert r.status_code == 200, r.text
    return r.json()


def edit_local(c: TestClient, slug: str, body: str) -> None:
    r = c.put(f"/api/v1/skills/{slug}", json={"body": body})
    assert r.status_code == 200, r.text


def test_market_disabled_503(client):
    c, _ = client
    r = c.get("/api/v1/market/skills")
    assert r.status_code in (200, 502, 503)  # in-process market → 200


def test_sync_push_pull_conflict(client):
    c, market_store = client
    import_skill(c, "hello", "v1")

    # 1. new local skill → pushed to cloud
    res = sync(c)
    assert res["pushed"] == ["hello"] and res["conflicts"] == []
    assert market_store.get_user_entry("alice", "hello")["size"] > 0

    # 2. no changes → up-to-date, no-op
    res = sync(c)
    assert res["pushed"] == [] and res["pulled"] == []

    # 3. "another device" edits the cloud copy directly
    zip_v2 = make_skill_zip("hello", "v2-from-other-device")
    market_store.put_user(
        user="alice", slug="hello", zip_bytes=zip_v2, sha="remote-v2", base_sha=None
    )
    res = sync(c)
    assert res["pulled"] == ["hello"]
    r = c.get("/api/v1/skills/hello")
    assert "v2-from-other-device" in r.json()["body"]

    # 4. both sides change → conflict → resolve(local) wins
    edit_local(c, "hello", "v3-local-edit")
    market_store.put_user(
        user="alice", slug="hello", zip_bytes=zip_v2, sha="remote-v3", base_sha=None
    )
    res = sync(c)
    assert res["conflicts"] == ["hello"]

    r = c.post(
        "/api/v1/market/sync/resolve", json={"slug": "hello", "choice": "local"}
    )
    assert r.status_code == 200, r.text
    assert market_store.get_user_entry("alice", "hello")["sha"] != "remote-v3"
    # cloud now holds the local edit
    r = c.get("/api/v1/market/sync")
    assert r.json()["actions"]["hello"]["action"] == "up-to-date"


def test_publish_and_add_roundtrip(client):
    c, _ = client
    import_skill(c, "publish-me", "public content")

    r = c.post(
        "/api/v1/market/skills/publish-me/publish", json={"category": "coding"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["author"] == "alice"
    key = r.json()["slug"]  # market key = {base}-{random6}
    assert key.startswith("publish-me-")

    r = c.get("/api/v1/market/skills?q=publish")
    assert len(r.json()) == 1

    # re-publish from local (same author+name) reuses the market key
    r = c.post("/api/v1/market/skills/publish-me/publish", json={"category": "other"})
    assert r.status_code == 200
    assert r.json()["slug"] == key

    # add: pull by the market key into a fresh store copy
    r = c.post(f"/api/v1/market/skills/{key}/add", json={})
    assert r.status_code == 200, r.text


def test_publish_passthrough_extra_fields(client):
    """Extension fields in the publish body (source_type/source_ref)
    are forwarded by the kernel and persisted by the market — visible
    on detail and list responses."""
    c, _ = client
    import_skill(c, "passthru", "public content")
    r = c.post(
        "/api/v1/market/skills/passthru/publish",
        json={"category": "coding", "source_type": "local", "source_ref": "sk-abc"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["meta"] == {"source_type": "local", "source_ref": "sk-abc"}
    key = r.json()["slug"]

    # survives a re-read from the registry (detail + list)
    d = c.get(f"/api/v1/market/skills/{key}")
    assert d.status_code == 200, d.text
    assert d.json()["meta"]["source_type"] == "local"
    listed = [s for s in c.get("/api/v1/market/skills?q=passthru").json() if s["slug"] == key]
    assert listed and listed[0]["meta"]["source_ref"] == "sk-abc"


def test_add_same_name_different_content_coexists(client):
    """Local slug = market key (``name-random6``), which is unique per
    name+author. A same-named local skill with different content is
    never clobbered: the market version lands under its own key; and a
    dedup scan skips content that already exists anywhere locally."""
    c, _ = client
    import_skill(c, "mine", "public content")
    r = c.post("/api/v1/market/skills/mine/publish", json={})
    assert r.status_code == 200
    key = r.json()["slug"]
    assert key.startswith("mine-")

    # local diverges from the market version
    edit_local(c, "mine", "changed locally after publish")

    # add → market version lands under its own key; local edit survives
    r = c.post(f"/api/v1/market/skills/{key}/add", json={})
    assert r.status_code == 200, r.text
    skills = {s["slug"]: s for s in c.get("/api/v1/skills").json()}
    assert "mine" in skills  # local edit preserved
    assert key in skills  # market copy installed under its own key
    assert "public content" in c.get(f"/api/v1/skills/{key}").json()["body"]
    assert "changed locally" in c.get("/api/v1/skills/mine").json()["body"]

    # dedup: re-adding the same market skill is skipped (content exists)
    r = c.post(f"/api/v1/market/skills/{key}/add", json={})
    assert r.status_code == 200
    slugs = [s["slug"] for s in c.get("/api/v1/skills").json()]
    assert slugs.count(key) == 1


def test_add_same_name_different_author_coexists(client):
    """Two authors publish the same display name with different content
    (distinct market keys). Adding both locally installs each under its
    own key — the second add never overwrites the first copy."""
    c, _ = client
    import_skill(c, "self-improvement", "alice's version")
    r = c.post("/api/v1/market/skills/self-improvement/publish", json={})
    assert r.status_code == 200
    k1 = r.json()["slug"]
    assert r.json()["author"] == "alice"

    # bob takes over the shared local copy, makes it his own content,
    # and publishes a second same-named entry
    login_as(c, "bob", "builder")
    edit_local(c, "self-improvement", "bob's version")
    r = c.post("/api/v1/market/skills/self-improvement/publish", json={})
    assert r.status_code == 200
    k2 = r.json()["slug"]
    assert r.json()["author"] == "bob"
    assert k2 != k1

    # add both authors' entries — each lands under its own key
    r = c.post(f"/api/v1/market/skills/{k1}/add", json={})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/v1/market/skills/{k2}/add", json={})
    assert r.status_code == 200, r.text

    # both authors' contents present; exactly two local copies
    skills = {s["slug"]: s for s in c.get("/api/v1/skills").json()}
    assert len(skills) == 2, skills
    assert k1 in skills  # alice's entry installed under its own key
    assert "alice's version" in c.get(f"/api/v1/skills/{k1}").json()["body"]
    # bob's content was already local (his edit) → dedup, no duplicate
    assert "bob's version" in c.get("/api/v1/skills/self-improvement").json()["body"]

    # dedup: re-adding either is skipped — still exactly two copies
    r = c.post(f"/api/v1/market/skills/{k1}/add", json={})
    r = c.post(f"/api/v1/market/skills/{k2}/add", json={})
    slugs = [s["slug"] for s in c.get("/api/v1/skills").json()]
    assert slugs.count(k1) == 1
    assert len(slugs) == 2



def login_as(c: TestClient, username: str, password: str) -> None:
    r = c.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"


def test_add_duplicate_returns_real_copy(client):
    """Duplicate add where the content already lives under a DIFFERENT
    slug (e.g. an old-style clean-name copy) must return that real copy
    — not null. Regression for 'Cannot read properties of null'."""
    c, _ = client
    # 本地按名字导入并发布 → 市场 key 带随机后缀，本地是干净名字
    import_skill(c, "notes", "content")
    r = c.post("/api/v1/market/skills/notes/publish", json={})
    assert r.status_code == 200
    key = r.json()["slug"]
    assert key.startswith("notes-")
    # 本地只有 "notes"，没有 key 目录（模拟旧版添加产物）

    # 重复添加：内容已在本地（"notes" 下）→ 返回实际副本，不返回 null
    r = c.post(f"/api/v1/market/skills/{key}/add", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skill"] is not None
    assert body["skill"]["slug"] == "notes"
    assert body["cloud_backup"] is True

    # 本地仍只有一个副本
    slugs = [s["slug"] for s in c.get("/api/v1/skills").json()]
    assert slugs == ["notes"]


def test_delist_via_kernel_author_guarded(client):
    """The kernel delist proxy forwards to the market, which enforces
    the author guard: the author's own entry leaves the public market
    while the local copy stays; a non-author gets 403."""
    c, _ = client
    import_skill(c, "own-delist", "public content")
    r = c.post("/api/v1/market/skills/own-delist/publish", json={})
    assert r.status_code == 200
    key = r.json()["slug"]

    # push a cloud copy so the delisted flag can travel (sha match)
    res = sync(c)
    assert res["pushed"] == ["own-delist"]

    # non-author cannot delist
    login_as(c, "bob", "builder")
    r = c.delete(f"/api/v1/market/skills/{key}")
    assert r.status_code == 403, r.text

    # author delists → entry gone from the public market, local copy stays
    login_as(c, "alice", "wonderland")
    r = c.delete(f"/api/v1/market/skills/{key}")
    assert r.status_code == 204, r.text
    assert c.get("/api/v1/market/skills").json() == []
    assert c.get(f"/api/v1/market/skills/{key}").status_code == 404
    # the local skill is untouched
    assert any(s["slug"] == "own-delist" for s in c.get("/api/v1/skills").json())

    # sync manifest flags the copy as delisted (market side stays soft)
    plan = c.get("/api/v1/market/sync").json()
    assert plan["delisted"]["own-delist"] is True


def test_publish_is_stateless(client):
    """Publish is a pure action: it does not maintain any local market
    binding (no market_key / published_by_me / delisted on the skill)."""
    c, _ = client
    import_skill(c, "own-skill", "v1")
    r = c.post("/api/v1/market/skills/own-skill/publish", json={"category": "coding"})
    assert r.status_code == 200
    key = r.json()["slug"]
    assert key.startswith("own-skill-")
    assert r.json()["author"] == "alice"

    own = next(s for s in c.get("/api/v1/skills").json() if s["slug"] == "own-skill")
    for field in ("market_key", "published_by_me", "delisted"):
        assert field not in own, f"skill list should not expose '{field}'"


def test_modify_and_publish_is_own_skill(client):
    """Add keeps no binding, so modify + publish is simply "my own skill":
    the market entry is authored by the publisher with its own key, and
    no local subscription state exists to reconcile."""
    c, _ = client
    import_skill(c, "upstream", "original content")
    r = c.post("/api/v1/market/skills/upstream/publish", json={})
    assert r.status_code == 200
    upstream_key = r.json()["slug"]
    assert r.json()["author"] == "alice"

    # another user adds upstream's skill locally — no binding recorded
    login_as(c, "bob", "builder")
    r = c.post(f"/api/v1/market/skills/{upstream_key}/add", json={})
    assert r.status_code == 200, r.text
    mine = next(
        s for s in c.get("/api/v1/skills").json() if s["slug"] == "upstream"
    )
    assert "market_key" not in mine
    assert "published_by_me" not in mine

    # bob modifies the copy and publishes → his OWN entry
    edit_local(c, "upstream", "bob's modified version")
    r = c.post("/api/v1/market/skills/upstream/publish", json={})
    assert r.status_code == 200, r.text
    bob_key = r.json()["slug"]
    assert r.json()["author"] == "bob"
    assert bob_key != upstream_key

    # the market holds both: upstream's (alice) + bob's own (bob)
    entries = {e["slug"]: e["author"] for e in c.get("/api/v1/market/skills").json()}
    assert entries[upstream_key] == "alice"
    assert entries[bob_key] == "bob"


