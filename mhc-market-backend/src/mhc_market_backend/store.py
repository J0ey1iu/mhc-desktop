"""SQLite-backed market store (skills unpacked into rows).

Replaces the old file layout (``public/<slug>/skill.zip + meta.json``,
``users/<user>/<slug>/...``) with three SQLite tables. Skill content is
**not** stored as one zip blob: each skill is unpacked into rows of
``skill_files`` / ``user_files`` (one row per file, ``content`` = the
single file's bytes). ``skills`` / ``user_skills`` hold the metadata.

Why rows instead of a zip blob:

* single-file reads (preview) need no unzip
* file listing comes straight from SQL
* later, content can be FTS-indexed or the whole DB swapped to
  openGauss / Postgres with the same schema shape

The storage file is ``<data_root>/market.db``. On first open, any
legacy file-layout data under ``public`` / ``users`` / ``stories`` is
migrated into the DB.

Every skill is still a single version (latest wins). ``sha`` is the
*content* sha256 computed by the uploader over the skill files (never
the zip bytes, which carry timestamps); the server stores it verbatim
and recomputes it when an upload omits it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

MAX_ZIP_BYTES = 20 * 1024 * 1024  # 20 MiB — skills are markdown folders
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CATEGORIES = ("efficiency", "writing", "coding", "office", "other")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
  slug          TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  description   TEXT,
  category      TEXT,
  author        TEXT NOT NULL,
  icon          TEXT,
  meta          TEXT,
  sha           TEXT NOT NULL,
  size          INTEGER,
  downloads     INTEGER DEFAULT 0,
  featured      INTEGER DEFAULT 0,
  delisted      INTEGER DEFAULT 0,
  updated_at    INTEGER,
  published_at  INTEGER
);
CREATE TABLE IF NOT EXISTS skill_files (
  skill_slug  TEXT NOT NULL,
  rel_path    TEXT NOT NULL,
  content     BLOB,
  size        INTEGER,
  PRIMARY KEY (skill_slug, rel_path)
);
CREATE TABLE IF NOT EXISTS user_skills (
  user        TEXT NOT NULL,
  slug        TEXT NOT NULL,
  sha         TEXT NOT NULL,
  size        INTEGER,
  updated_at  INTEGER,
  base_sha    TEXT,
  PRIMARY KEY (user, slug)
);
CREATE TABLE IF NOT EXISTS user_files (
  user      TEXT NOT NULL,
  slug      TEXT NOT NULL,
  rel_path  TEXT NOT NULL,
  content   BLOB,
  size      INTEGER,
  PRIMARY KEY (user, slug, rel_path)
);
CREATE TABLE IF NOT EXISTS stories (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  author      TEXT,
  skill_slug  TEXT,
  content     TEXT,
  created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS reviews (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT NOT NULL,
  user        TEXT NOT NULL,
  rating      INTEGER NOT NULL,
  comment     TEXT,
  created_at  INTEGER NOT NULL,
  UNIQUE (slug, user)
);
CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
  slug UNINDEXED, title, description, body, tokenize='porter'
);

-- Aggregated usage metrics reported by desktop clients (counts per
-- end per day per kind, idempotent via a dedup key).
-- kind: skill_download | skill_invoke | skill_enable | session | message
CREATE TABLE IF NOT EXISTS usage_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key   TEXT NOT NULL UNIQUE,
  day         TEXT NOT NULL,
  end_id      TEXT NOT NULL,
  kind        TEXT NOT NULL,
  slug        TEXT,
  count       INTEGER DEFAULT 1,
  created_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_day_kind ON usage_events(day, kind);
CREATE INDEX IF NOT EXISTS idx_usage_kind_slug ON usage_events(kind, slug);
"""

# Text-file extensions indexed for full-text search (body of SKILL.md
# + scripts/references). Everything else (binary assets) is excluded.
TEXT_EXTS = {
    "md", "markdown", "txt", "py", "js", "ts", "tsx", "jsx",
    "json", "yaml", "yml", "html", "css", "sh", "toml",
}


class MarketError(Exception):
    """Carries an HTTP status for the router to map."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _unique_key(con, base: str) -> str:
    """{base[:57]}-{random6}, unique in the skills table (re-rolls)."""
    import secrets as _s

    head = base[:57].rstrip("-")
    for _ in range(8):
        key = f"{head}-{_s.token_hex(3)}"
        if con.execute("SELECT 1 FROM skills WHERE slug = ?", (key,)).fetchone() is None:
            return key
    raise MarketError(500, "could not allocate a unique skill slug")


def _now() -> int:
    return int(time.time())


class MarketStore:
    """Single SQLite file, three domains: public / personal / stories."""

    def __init__(self, root: Path):
        self._db = Path(root) / "market.db"
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy(Path(root))

    # ── db plumbing ────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(_SCHEMA)
            # Column migrations for DBs created before ``delisted``
            # existed (soft delist keeps the row so personal-space
            # copies keep meta + get the flag).
            cols = {r[1] for r in con.execute("PRAGMA table_info(skills)")}
            if "delisted" not in cols:
                con.execute("ALTER TABLE skills ADD COLUMN delisted INTEGER DEFAULT 0")
            if "meta" not in cols:
                con.execute("ALTER TABLE skills ADD COLUMN meta TEXT")
            self._reconcile_display_names(con)
            con.commit()

    def _reconcile_display_names(self, con: sqlite3.Connection) -> None:
        """存量数据对齐：display_name 与 SKILL.md 的 name 一致。

        早期种子/Web 发布带入了花名（如 'Daily Standup Notes'），而技能
        的 MD 文件名是小写 slug（'daily-standup-notes'）。展示名与 MD
        不一致会让用户困惑；统一以 MD 名为准（幂等，重复执行无副作用）。"""
        for row in con.execute("SELECT slug, display_name FROM skills"):
            r = con.execute(
                "SELECT content FROM skill_files WHERE skill_slug = ? "
                "AND rel_path LIKE '%SKILL.md'",
                (row["slug"],),
            ).fetchone()
            if not r or not r["content"]:
                continue
            name = _md_name_from_files([("SKILL.md", r["content"])])
            if name and name != row["display_name"]:
                con.execute(
                    "UPDATE skills SET display_name = ? WHERE slug = ?",
                    (name, row["slug"]),
                )
                self._rebuild_fts(con, row["slug"])

    @staticmethod
    def _files(rows: list[sqlite3.Row]) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        for r in rows:
            content = r["content"]
            if content is None:
                continue
            out.append((r["rel_path"], content))
        return out

    # ── public registry ─────────────────────────────────────────────

    def list_public(
        self, *, q: str = "", category: str = "", sort: str = "downloads",
        featured: bool = False, limit: int | None = None, offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, args = self._public_where(q, category, featured)
        sql = (
            "SELECT *, "
            "(SELECT ROUND(AVG(rating),2) FROM reviews WHERE slug=skills.slug) "
            "AS rating_avg, "
            "(SELECT COUNT(*) FROM reviews WHERE slug=skills.slug) AS rating_count "
            "FROM skills"
        )
        if where:
            sql += " WHERE " + where
        order = (
            "downloads DESC, updated_at DESC"
            if sort == "downloads"
            else "updated_at DESC"
        )
        sql += " ORDER BY " + order
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        with self._connect() as con:
            return [self._with_meta(dict(r)) for r in con.execute(sql, args)]

    @staticmethod
    def _with_meta(row: dict[str, Any]) -> dict[str, Any]:
        """meta column is JSON text; surface it as a dict (extras
        passed through by the kernel, e.g. source_type/source_ref)."""
        raw = row.get("meta")
        row["meta"] = json.loads(raw) if raw else {}
        return row

    @staticmethod
    def _public_where(
        q: str, category: str, featured: bool
    ) -> tuple[str, list[Any]]:
        conds: list[str] = ["delisted = 0"]
        args: list[Any] = []
        if featured:
            conds.append("featured = 1")
        if category:
            conds.append("category = ?")
            args.append(category)
        if q:
            like = f"%{q.lower()}%"
            # Search body too; tokens are quoted to avoid FTS syntax errors
            # (skill_fts is empty on a fresh DB, which simply yields no rows).
            tokens = [t.replace('"', "") for t in q.split() if t.strip()]
            fts = " ".join(f'"{t}"' for t in tokens) if tokens else '""'
            conds.append(
                "(LOWER(display_name) LIKE ? OR LOWER(description) LIKE ? "
                "OR slug LIKE ? OR slug IN "
                "(SELECT slug FROM skill_fts WHERE skill_fts MATCH ?))"
            )
            args += [like, like, like, fts]
        return " AND ".join(conds), args

    def count_public(
        self, *, q: str = "", category: str = "", featured: bool = False,
    ) -> int:
        where, args = self._public_where(q, category, featured)
        sql = "SELECT COUNT(*) FROM skills"
        if where:
            sql += " WHERE " + where
        with self._connect() as con:
            return int(con.execute(sql, args).fetchone()[0])

    def get_public(self, slug: str) -> dict[str, Any]:
        """Public row. Delisted skills are not in the market anymore:
        they 404 from every public read path (detail / files / reviews)
        while the row itself stays for personal-space flags."""
        with self._connect() as con:
            r = con.execute("SELECT * FROM skills WHERE slug = ?", (slug,))
            row = r.fetchone()
        if row is None or row["delisted"]:
            raise MarketError(404, f"skill '{slug}' not found or delisted")
        return self._with_meta(dict(row))

    def publish(
        self,
        *,
        slug: str,
        user: str,
        zip_bytes: bytes,
        sha: str,
        display_name: str,
        description: str = "",
        category: str = "other",
        icon: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not SLUG_RE.match(slug):
            raise MarketError(
                400, "slug must be lowercase letters/digits/hyphens, <=64 chars"
            )
        if category not in CATEGORIES:
            raise MarketError(400, f"category must be one of {CATEGORIES}")
        _validate_zip(zip_bytes)
        files = _extract_zip(zip_bytes)
        if not sha:
            sha = content_sha_from_files(files)
        # 市场展示名 = SKILL.md frontmatter 的 name（技能的唯一规范名）。
        # 忽略调用方传入的花名，保证市场展示与 MD 文件一致。
        md_name = _md_name_from_files(files)
        if md_name:
            display_name = md_name

        with self._connect() as con:
            # Content dedup: an identical skill (same file-content sha)
            # can only exist once. Same author re-publishing the same
            # content is idempotent; a different author trying to upload
            # an exact copy is rejected.
            dup = con.execute(
                "SELECT slug, author FROM skills WHERE sha = ?", (sha,)
            ).fetchone()
            if dup is not None:
                if dup["author"] == user:
                    slug = dup["slug"]
                else:
                    raise MarketError(
                        409,
                        "an identical skill already exists in the market "
                        "(same content), republishing exact copies is not allowed",
                    )
            else:
                # Key = {base}-{random6}. Same name by the same author
                # reuses one stable key so edits overwrite instead of
                # forking endlessly.
                row = con.execute(
                    "SELECT slug FROM skills WHERE display_name = ? AND author = ?",
                    (display_name, user),
                ).fetchone()
                slug = row["slug"] if row is not None else _unique_key(con, slug)
            now = _now()
            con.execute(
                "INSERT INTO skills (slug, display_name, description, category, "
                "author, icon, sha, meta, size, downloads, delisted, updated_at, published_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET display_name=excluded.display_name, "
                "description=excluded.description, category=excluded.category, "
                "author=excluded.author, icon=excluded.icon, sha=excluded.sha, "
                "meta=excluded.meta, size=excluded.size, delisted=excluded.delisted, "
                "updated_at=excluded.updated_at",
                (
                    slug, display_name, description, category, user, icon, sha,
                    json.dumps(meta or {}),
                    sum(len(c) for _, c in files),
                    0,  # downloads preserved by the ON CONFLICT non-update
                    0,  # republishing resurrects a delisted entry
                    now,
                    now,
                ),
            )
            con.execute("DELETE FROM skill_files WHERE skill_slug = ?", (slug,))
            con.executemany(
                "INSERT INTO skill_files (skill_slug, rel_path, content, size) "
                "VALUES (?, ?, ?, ?)",
                [(slug, rel, content, len(content)) for rel, content in files],
            )
            self._rebuild_fts(con, slug)
            con.commit()
        return self.get_public(slug)

    def read_public_zip(self, slug: str) -> tuple[bytes, dict[str, Any]]:
        with self._connect() as con:
            r = con.execute("SELECT * FROM skills WHERE slug = ?", (slug,))
            row = r.fetchone()
            if row is None or row["delisted"]:
                raise MarketError(404, f"skill '{slug}' not found or delisted")
            meta = dict(row)
            files = self._files(
                con.execute(
                    "SELECT rel_path, content FROM skill_files "
                    "WHERE skill_slug = ? ORDER BY rel_path",
                    (slug,),
                ).fetchall()
            )
            con.execute(
                "UPDATE skills SET downloads = downloads + 1 WHERE slug = ?",
                (slug,),
            )
            con.commit()
        meta["downloads"] = meta.get("downloads", 0) + 1
        return _build_zip(slug, files), meta

    def delete_public(self, slug: str, user: str) -> None:
        """Author delist (soft). The public entry leaves the market —
        every public read path 404s and list/filter hides it — but the
        row stays so personal-space copies keep their metadata and get
        a ``delisted`` flag. Re-publishing by the author resurrects it."""
        with self._connect() as con:
            row = con.execute("SELECT author FROM skills WHERE slug = ?", (slug,))
            meta = row.fetchone()
            if meta is None:
                raise MarketError(404, f"skill '{slug}' not found")
            if meta["author"] != user:
                raise MarketError(403, "only the author can delist")
            con.execute(
                "UPDATE skills SET delisted = 1, updated_at = ? WHERE slug = ?",
                (_now(), slug),
            )
            con.commit()

    def get_files(self, slug: str) -> list[dict[str, Any]]:
        """File list (rel_path + text content) for a public skill —
        the market detail pane reads SKILL.md body from here."""
        self.get_public(slug)  # 404 if missing
        with self._connect() as con:
            rows = con.execute(
                "SELECT rel_path, content FROM skill_files "
                "WHERE skill_slug = ? ORDER BY rel_path",
                (slug,),
            ).fetchall()
        out = []
        for r in rows:
            name = r["rel_path"]
            content = r["content"]
            if isinstance(content, bytes):
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # skip binaries in the pane
            else:
                text = content
            out.append({"path": name, "content": text})
        return out

    def set_featured(self, slug: str, featured: bool) -> dict[str, Any]:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE skills SET featured = ? WHERE slug = ?",
                (1 if featured else 0, slug),
            )
            if cur.rowcount == 0:
                raise MarketError(404, f"skill '{slug}' not found")
            con.commit()
        return self.get_public(slug)

    def admin_delete_public(self, slug: str) -> None:
        """Operator delist — no owner guard (admin only)."""
        with self._connect() as con:
            cur = con.execute("DELETE FROM skills WHERE slug = ?", (slug,))
            if cur.rowcount == 0:
                raise MarketError(404, f"skill '{slug}' not found")
            con.execute("DELETE FROM skill_files WHERE skill_slug = ?", (slug,))
            con.execute("DELETE FROM skill_fts WHERE slug = ?", (slug,))
            con.execute("DELETE FROM reviews WHERE slug = ?", (slug,))
            con.commit()

    # ── ratings / reviews ───────────────────────────────────────────

    def get_rating(self, slug: str) -> dict[str, Any]:
        self.get_public(slug)  # 404 if missing or delisted
        with self._connect() as con:
            r = con.execute(
                "SELECT AVG(rating) AS avg, COUNT(*) AS count FROM reviews "
                "WHERE slug = ?",
                (slug,),
            ).fetchone()
        avg = r["avg"]
        return {"slug": slug, "average": round(float(avg), 2) if avg else 0.0, "count": r["count"]}

    def list_reviews(self, slug: str) -> list[dict[str, Any]]:
        self.get_public(slug)  # 404 if missing or delisted
        with self._connect() as con:
            rows = con.execute(
                "SELECT user, rating, comment, created_at FROM reviews "
                "WHERE slug = ? ORDER BY created_at DESC",
                (slug,),
            )
            return [dict(r) for r in rows]

    def add_review(
        self, *, slug: str, user: str, rating: int, comment: str = ""
    ) -> dict[str, Any]:
        if not 1 <= rating <= 5:
            raise MarketError(400, "rating must be 1-5")
        if comment and len(comment) > 2000:
            raise MarketError(400, "comment too long (>2000 chars)")
        self.get_public(slug)  # 404 if the skill doesn't exist
        with self._connect() as con:
            con.execute(
                "INSERT INTO reviews (slug, user, rating, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(slug, user) DO UPDATE SET rating=excluded.rating, "
                "comment=excluded.comment, created_at=excluded.created_at",
                (slug, user, rating, comment, _now()),
            )
            con.commit()
        return self.get_rating(slug)

    # ── full-text index maintenance ─────────────────────────────────

    def _rebuild_fts(self, con: sqlite3.Connection, slug: str) -> None:
        row = con.execute(
            "SELECT display_name, description FROM skills WHERE slug = ?", (slug,)
        ).fetchone()
        con.execute("DELETE FROM skill_fts WHERE slug = ?", (slug,))
        if row is None:
            return
        parts = [row["display_name"] or "", row["description"] or ""]
        rows = con.execute(
            "SELECT rel_path, content FROM skill_files WHERE skill_slug = ?", (slug,)
        )
        for r in rows:
            ext = (
                r["rel_path"].rsplit(".", 1)[-1].lower()
                if "." in r["rel_path"]
                else ""
            )
            if ext in TEXT_EXTS and r["content"]:
                parts.append(r["content"].decode("utf-8", "replace"))
        con.execute(
            "INSERT INTO skill_fts (slug, title, description, body) "
            "VALUES (?, ?, ?, ?)",
            (slug, row["display_name"], row["description"], " ".join(parts)),
        )

    # ── personal space ──────────────────────────────────────────────

    def list_user(self, user: str) -> list[dict[str, Any]]:
        """Cloud copies for ``user``, joined with public skill meta so
        the dashboard renders real names/icons.

        Two joins: by slug (exact copy) and by content sha (the add
        flow keys the cloud copy by the LOCAL slug while the market
        entry has a random key, so slug alone would miss it). The sha
        join also surfaces ``delisted`` + ``author``: a copy whose
        content matches a delisted market entry is flagged, and a copy
        that matches the user's own published entry is theirs.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT u.slug, u.sha, u.size, u.updated_at, "
                "COALESCE(sl.display_name, sh.display_name) AS display_name, "
                "COALESCE(sl.icon, sh.icon) AS icon, "
                "COALESCE(sl.category, sh.category) AS category, "
                "COALESCE(sl.slug, sh.slug, '') AS market_slug, "
                "COALESCE(sl.delisted, sh.delisted, 0) AS delisted, "
                "COALESCE(sl.author, sh.author, '') AS author "
                "FROM user_skills u "
                "LEFT JOIN skills sl ON sl.slug = u.slug "
                "LEFT JOIN skills sh ON sh.sha = u.sha AND sh.slug <> u.slug "
                "WHERE u.user = ? ORDER BY u.slug",
                (user,),
            )
            out = []
            for r in rows:
                d = dict(r)
                # A cloud copy may reference a skill that no longer
                # exists in the public registry; keep the copy but
                # fall back meta to the slug.
                d.setdefault("display_name", d["slug"])
                d["delisted"] = 1 if d.get("delisted") else 0
                d["author"] = str(d.get("author") or "")
                d["market_slug"] = str(d.get("market_slug") or "")
                out.append(d)
            return out

    def get_user_entry(self, user: str, slug: str) -> dict[str, Any]:
        with self._connect() as con:
            r = con.execute(
                "SELECT slug, sha, size, updated_at FROM user_skills "
                "WHERE user = ? AND slug = ?",
                (user, slug),
            ).fetchone()
        if r is None:
            raise MarketError(404, f"no cloud copy of '{slug}'")
        return dict(r)

    def put_user(
        self,
        *,
        user: str,
        slug: str,
        zip_bytes: bytes,
        sha: str,
        base_sha: str | None,
    ) -> dict[str, Any]:
        if not SLUG_RE.match(slug):
            raise MarketError(400, "invalid slug")
        _validate_zip(zip_bytes)
        files = _extract_zip(zip_bytes)
        if not sha:
            sha = content_sha_from_files(files)

        with self._connect() as con:
            if base_sha is not None:
                current = con.execute(
                    "SELECT sha FROM user_skills WHERE user = ? AND slug = ?",
                    (user, slug),
                ).fetchone()
                if current is not None and current["sha"] != base_sha:
                    raise MarketError(
                        409,
                        f"cloud copy of '{slug}' changed since your last sync",
                    )
            now = _now()
            con.execute(
                "INSERT INTO user_skills (user, slug, sha, size, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user, slug) DO UPDATE SET sha=excluded.sha, "
                "size=excluded.size, updated_at=excluded.updated_at",
                (user, slug, sha, sum(len(c) for _, c in files), now),
            )
            con.execute(
                "DELETE FROM user_files WHERE user = ? AND slug = ?",
                (user, slug),
            )
            con.executemany(
                "INSERT INTO user_files (user, slug, rel_path, content, size) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (user, slug, rel, content, len(content))
                    for rel, content in files
                ],
            )
            con.commit()
        return self.get_user_entry(user, slug)

    def edit_user(
        self,
        *,
        user: str,
        slug: str,
        description: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Edit the SKILL.md inside a user's cloud copy.

        Rewrites the frontmatter ``description`` and/or the markdown
        body, then recomputes the content sha (so a subsequent
        desktop sync sees the copy as up-to-date). Returns the new
        entry. 404 if the user has no copy of the skill.
        """
        with self._connect() as con:
            r = con.execute(
                "SELECT 1 FROM user_skills WHERE user = ? AND slug = ?",
                (user, slug),
            ).fetchone()
            if r is None:
                raise MarketError(404, f"no cloud copy of '{slug}'")
            rows = con.execute(
                "SELECT rel_path, content FROM user_files "
                "WHERE user = ? AND slug = ?",
                (user, slug),
            ).fetchall()
            files = self._files(rows)
        skill_md = next((c for rel, c in files if rel == "SKILL.md"), None)
        if skill_md is None:
            raise MarketError(400, f"no SKILL.md in cloud copy of '{slug}'")

        text = skill_md.decode("utf-8", "replace")
        if description is not None or body is not None:
            try:
                fm, old_body = _parse_skill_md(text)
                new_desc = description if description is not None else fm.get("description", "")
                new_body = body if body is not None else old_body
                # Keep the icon even if the incoming SKILL.md lacks an
                # `icon:` line — merge the public registry's icon so an
                # edit never silently drops it.
                extra = dict(fm.get("extra") or {})
                if not extra.get("icon"):
                    with self._connect() as con:
                        row = con.execute(
                            "SELECT icon FROM skills WHERE slug = ?", (slug,)
                        ).fetchone()
                    if row and row["icon"]:
                        extra["icon"] = row["icon"]
                text = _render_skill_md(
                    name=fm.get("name", slug),
                    description=str(new_desc or ""),
                    version=fm.get("version", ""),
                    license=fm.get("license", ""),
                    extra=extra,
                    body=new_body,
                )
            except Exception as e:
                raise MarketError(400, f"failed to rewrite SKILL.md: {e}") from None

        new_files = [
            (rel, text.encode("utf-8") if rel == "SKILL.md" else content)
            for rel, content in files
        ]
        sha = content_sha_from_files(new_files)
        with self._connect() as con:
            con.execute(
                "UPDATE user_skills SET sha = ?, size = ?, updated_at = ? "
                "WHERE user = ? AND slug = ?",
                (sha, sum(len(c) for _, c in new_files), _now(), user, slug),
            )
            con.execute(
                "UPDATE user_files SET content = ?, size = ? "
                "WHERE user = ? AND slug = ? AND rel_path = 'SKILL.md'",
                (text.encode("utf-8"), len(text.encode("utf-8")), user, slug),
            )
            con.commit()
        return self.get_user_entry(user, slug)

    def get_user_md(self, user: str, slug: str) -> dict[str, Any]:
        """Current SKILL.md (description + body) of a user's cloud copy."""
        with self._connect() as con:
            r = con.execute(
                "SELECT 1 FROM user_skills WHERE user = ? AND slug = ?",
                (user, slug),
            ).fetchone()
            if r is None:
                raise MarketError(404, f"no cloud copy of '{slug}'")
            rows = con.execute(
                "SELECT rel_path, content FROM user_files "
                "WHERE user = ? AND slug = ? ORDER BY rel_path",
                (user, slug),
            ).fetchall()
        files = self._files(rows)
        md = next((c for rel, c in files if rel == "SKILL.md"), None)
        if md is None:
            raise MarketError(400, f"no SKILL.md in cloud copy of '{slug}'")
        fm, body = _parse_skill_md(md.decode("utf-8", "replace"))
        return {
            "slug": slug,
            "name": fm.get("name", slug),
            "description": str(fm.get("description") or ""),
            "body": body,
        }

    def read_user_zip(self, user: str, slug: str) -> tuple[bytes, dict[str, Any]]:
        with self._connect() as con:
            r = con.execute(
                "SELECT slug, sha, size, updated_at FROM user_skills "
                "WHERE user = ? AND slug = ?",
                (user, slug),
            ).fetchone()
            if r is None:
                raise MarketError(404, f"no cloud copy of '{slug}'")
            meta = dict(r)
            files = self._files(
                con.execute(
                    "SELECT rel_path, content FROM user_files "
                    "WHERE user = ? AND slug = ? ORDER BY rel_path",
                    (user, slug),
                ).fetchall()
            )
        return _build_zip(slug, files), meta

    def delete_user(self, user: str, slug: str) -> None:
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM user_skills WHERE user = ? AND slug = ?",
                (user, slug),
            )
            if cur.rowcount == 0:
                raise MarketError(404, f"no cloud copy of '{slug}'")
            con.execute(
                "DELETE FROM user_files WHERE user = ? AND slug = ?",
                (user, slug),
            )
            con.commit()

    # ── stories (user-submitted promo articles) ────────────────────

    def list_stories(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM stories ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    def get_story(self, story_id: str) -> dict[str, Any]:
        with self._connect() as con:
            r = con.execute(
                "SELECT * FROM stories WHERE id = ?", (story_id,)
            ).fetchone()
        if r is None:
            raise MarketError(404, f"story '{story_id}' not found")
        return dict(r)

    def add_story(
        self, *, user: str, title: str, skill_slug: str, content: str
    ) -> dict[str, Any]:
        if not title.strip() or not content.strip():
            raise MarketError(400, "title and content are required")
        if len(content) > 64 * 1024:
            raise MarketError(400, "story too long (>64 KiB)")
        import uuid

        story = {
            "id": uuid.uuid4().hex[:12],
            "title": title.strip()[:200],
            "author": user,
            "skill_slug": skill_slug.strip(),
            "content": content,
            "created_at": _now(),
        }
        with self._connect() as con:
            con.execute(
                "INSERT INTO stories (id, title, author, skill_slug, content, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    story["id"], story["title"], story["author"],
                    story["skill_slug"], story["content"], story["created_at"],
                ),
            )
            con.commit()
        return story

    # ── ops: stats / diagnostics / backup ───────────────────────────

    def stats(self) -> dict[str, Any]:
        """Aggregate operational counters for the admin console."""
        with self._connect() as con:
            skills = con.execute(
                "SELECT COUNT(*) c, COALESCE(SUM(downloads), 0) d FROM skills"
            ).fetchone()
            featured = con.execute(
                "SELECT COUNT(*) FROM skills WHERE featured = 1"
            ).fetchone()[0]
            users = con.execute(
                "SELECT COUNT(DISTINCT u) FROM (SELECT user AS u FROM user_skills "
                "UNION SELECT user FROM reviews)"
            ).fetchone()[0]
            reviews = con.execute(
                "SELECT COUNT(*), COALESCE(AVG(rating), 0) FROM reviews"
            ).fetchone()
            stories = con.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        db_size = self._db.stat().st_size if self._db.exists() else 0
        return {
            "public_skills": skills["c"],
            "featured": featured,
            "downloads": skills["d"],
            "active_users": users,
            "reviews": reviews[0],
            "average_rating": round(float(reviews[1]), 2),
            "stories": stories,
            "db_size_bytes": db_size,
        }

    # ── usage / ops aggregation ─────────────────────────────────────

    @staticmethod
    def _day(ts: int) -> str:
        import datetime

        return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d")

    def record_usage(
        self,
        *,
        end_id: str,
        day: str,
        kind: str,
        slug: str | None = None,
        count: int = 1,
        dedup_key: str | None = None,
    ) -> None:
        """Record one idempotent usage event.

        ``dedup_key`` (e.g. ``f"{day}:{end_id}:{kind}:{slug or ''}"``)
        makes re-reports a no-op — a client re-sends the same batch,
        the counts don't double.
        """
        key = dedup_key or f"{day}:{end_id}:{kind}:{slug or ''}"
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO usage_events "
                "(event_key, day, end_id, kind, slug, count, created_at)"
                "VALUES (?,?,?,?,?,?,?)",
                (key, day, end_id, kind, slug, max(1, int(count)), _now()),
            )
            con.commit()

    def ops_overview(
        self, *, days: int = 30, include_today: bool = True
    ) -> dict[str, Any]:
        """Business KPIs for the ops dashboard."""
        import datetime

        today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        start = (
            datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        with self._connect() as con:
            def _sum(kind: str, slug: str | None = None) -> int:
                sql = "SELECT COALESCE(SUM(count),0) FROM usage_events WHERE kind=?"
                args: list[Any] = [kind]
                if slug:
                    sql += " AND slug=?"
                    args.append(slug)
                if include_today:
                    sql += " AND day<=?"
                    args.append(today)
                return con.execute(sql, args).fetchone()[0]

            # Distinct ends that reported in the window (MAU proxy).
            mau = con.execute(
                "SELECT COUNT(DISTINCT end_id) FROM usage_events WHERE day>=? AND kind='session'",
                (start,),
            ).fetchone()[0]
            download_total = con.execute(
                "SELECT COALESCE(SUM(downloads),0) FROM skills"
            ).fetchone()[0]
            skills_count = con.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            stories_count = con.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
            featured_count = con.execute(
                "SELECT COUNT(*) FROM skills WHERE featured=1"
            ).fetchone()[0]
        return {
            "mau": mau,
            "sessions": _sum("session"),
            "skill_downloads": _sum("skill_download"),
            "skill_invocations": _sum("skill_invoke"),
            "skill_enables": _sum("skill_enable"),
            "messages": _sum("message"),
            "total_downloads": download_total,
            "skills": skills_count,
            "stories": stories_count,
            "featured": featured_count,
            "window_days": days,
        }

    def ops_ranking(self, *, kind: str, days: int = 30, limit: int = 10) -> list[dict[str, Any]]:
        """Top-N by an aggregate kind, grouped per skill slug."""
        import datetime

        start = (
            datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        valid = {"skill_download", "skill_invoke", "skill_enable"}
        if kind not in valid:
            return []
        with self._connect() as con:
            rows = con.execute(
                "SELECT slug, SUM(count) c FROM usage_events "
                "WHERE kind=? AND slug IS NOT NULL AND day>=? "
                "GROUP BY slug ORDER BY c DESC LIMIT ?",
                (kind, start, limit),
            ).fetchall()
            # join skill display name
            out: list[dict[str, Any]] = []
            for r in rows:
                meta = con.execute(
                    "SELECT display_name, downloads FROM skills WHERE slug=?", (r["slug"],)
                ).fetchone()
                out.append(
                    {
                        "slug": r["slug"],
                        "display_name": meta["display_name"] if meta else r["slug"],
                        "count": r["c"],
                        "downloads": meta["downloads"] if meta else 0,
                    }
                )
            return out

    def ops_trend(self, *, days: int = 30) -> list[dict[str, Any]]:
        """Daily series for charts (zero-filled)."""
        import datetime

        end = datetime.datetime.now(datetime.UTC)
        start = end - datetime.timedelta(days=days - 1)
        with self._connect() as con:
            rows = con.execute(
                "SELECT day, kind, SUM(count) c FROM usage_events "
                "WHERE day>=? GROUP BY day, kind",
                (start.strftime("%Y-%m-%d"),),
            ).fetchall()
        by_day: dict[str, dict[str, int]] = {}
        for r in rows:
            by_day.setdefault(r["day"], {})[r["kind"]] = r["c"]
        out: list[dict[str, Any]] = []
        for i in range(days):
            d = (start + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            b = by_day.get(d, {})
            out.append(
                {
                    "date": d,
                    "downloads": b.get("skill_download", 0),
                    "invocations": b.get("skill_invoke", 0),
                    "sessions": b.get("session", 0),
                    "enables": b.get("skill_enable", 0),
                }
            )
        return out

    def ops_growth(self) -> dict[str, Any]:
        """Week-over-week growth for content metrics."""
        import datetime

        now = datetime.datetime.now(datetime.UTC)
        week_ago, two_weeks_ago = now - datetime.timedelta(days=7), now - datetime.timedelta(days=14)
        with self._connect() as con:
            def _count_since(ts: int) -> int:
                return con.execute("SELECT COUNT(*) FROM stories WHERE created_at>=?", (ts,)).fetchone()[0]
            return {
                "new_stories_week": _count_since(int(week_ago.timestamp())),
                "new_stories_prev_week": _count_since(int(two_weeks_ago.timestamp())) - _count_since(int(week_ago.timestamp())),
                "total_skills": con.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
                "total_stories": con.execute("SELECT COUNT(*) FROM stories").fetchone()[0],
                "featured": con.execute("SELECT COUNT(*) FROM skills WHERE featured=1").fetchone()[0],
            }

    def integrity(self) -> dict[str, Any]:
        """DB health: PRAGMA quick_check + sizes."""
        with self._connect() as con:
            rows = con.execute("PRAGMA quick_check").fetchall()
            journal = con.execute("PRAGMA journal_mode").fetchone()[0]
        ok = all(r[0] == "ok" for r in rows)
        db_size = self._db.stat().st_size if self._db.exists() else 0
        wal = self._db.with_name(self._db.name + "-wal")
        return {
            "ok": ok,
            "quick_check": rows[0][0],
            "journal_mode": journal,
            "db_size_bytes": db_size,
            "wal_size_bytes": wal.stat().st_size if wal.exists() else 0,
        }

    def create_backup(self) -> str:
        """Online SQLite backup to ``<data_root>/backups/market-<ts>.db``."""
        bdir = self._db.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        name = f"market-{_now()}.db"
        with sqlite3.connect(self._db) as src, sqlite3.connect(bdir / name) as dst:
            src.backup(dst)
        return name

    def list_backups(self) -> list[dict[str, Any]]:
        bdir = self._db.parent / "backups"
        out: list[dict[str, Any]] = []
        if bdir.is_dir():
            for f in sorted(bdir.glob("*.db"), reverse=True):
                out.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "created": int(f.stat().st_mtime),
                })
        return out

    def restore_backup(self, name: str) -> None:
        """Swap the live DB with a snapshot. Name must be a bare ``.db``
        filename under backups/ (no path traversal)."""
        if (
            not name.endswith(".db")
            or "/" in name
            or "\\" in name
            or ".." in name
        ):
            raise MarketError(400, "invalid backup name")
        src = self._db.parent / "backups" / name
        if not src.is_file():
            raise MarketError(404, f"backup '{name}' not found")
        with sqlite3.connect(src) as s, sqlite3.connect(self._db) as d:
            s.backup(d)

    # ── legacy migration ────────────────────────────────────────────

    def _migrate_legacy(self, root: Path) -> None:
        """Import old file-layout data (public/users/stories dirs) once.

        Triggered only when the DB has no rows yet, so repeated boots
        are idempotent. Leaves the old directories in place.
        """
        with self._connect() as con:
            has_public = con.execute("SELECT 1 FROM skills LIMIT 1").fetchone()
            has_user = con.execute("SELECT 1 FROM user_skills LIMIT 1").fetchone()
            has_story = con.execute("SELECT 1 FROM stories LIMIT 1").fetchone()
        pub = root / "public"
        if pub.is_dir() and not has_public:
            for d in sorted(pub.iterdir()):
                meta = d / "meta.json"
                zp = d / "skill.zip"
                if d.is_dir() and meta.is_file() and zp.is_file():
                    self.publish(
                        slug=d.name,
                        user=str(json.loads(meta.read_text("utf-8")).get("author", "")),
                        zip_bytes=zp.read_bytes(),
                        sha=str(json.loads(meta.read_text("utf-8")).get("sha", "")),
                        display_name=str(
                            json.loads(meta.read_text("utf-8")).get("display_name", d.name)
                        ),
                        description=str(
                            json.loads(meta.read_text("utf-8")).get("description", "")
                        ),
                        category=str(
                            json.loads(meta.read_text("utf-8")).get("category", "other")
                        ),
                        icon=str(json.loads(meta.read_text("utf-8")).get("icon", "")),
                    )
        users = root / "users"
        if users.is_dir() and not has_user:
            for user in sorted(users.iterdir()):
                if not user.is_dir():
                    continue
                for d in sorted(user.iterdir()):
                    meta = d / "meta.json"
                    zp = d / "skill.zip"
                    if meta.is_file() and zp.is_file():
                        m = json.loads(meta.read_text("utf-8"))
                        self.put_user(
                            user=user.name,
                            slug=d.name,
                            zip_bytes=zp.read_bytes(),
                            sha=str(m.get("sha", "")),
                            base_sha=None,
                        )
        stories = root / "stories"
        if stories.is_dir() and not has_story:
            for f in sorted(stories.glob("*.json")):
                try:
                    s = json.loads(f.read_text("utf-8"))
                except json.JSONDecodeError:
                    continue
                with self._connect() as con:
                    con.execute(
                        "INSERT INTO stories (id, title, author, skill_slug, "
                        "content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            s.get("id"), s.get("title"), s.get("author"),
                            s.get("skill_slug"), s.get("content"), s.get("created_at"),
                        ),
                    )
                    con.commit()


def _validate_zip(zip_bytes: bytes) -> None:
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise MarketError(400, "zip too large (>20 MiB)")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            if not any(n.endswith("SKILL.md") for n in zf.namelist()):
                raise MarketError(400, "zip does not contain SKILL.md")
    except zipfile.BadZipFile as e:
        raise MarketError(400, f"not a valid zip: {e}") from None


def _extract_zip(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Unpack a zip into (rel_path, bytes) rows, stripping the common
    top-level dir (Anthropic portable layout, e.g. ``<slug>/...``)."""
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        prefix = ""
        if names:
            first = {n.split("/", 1)[0] for n in names}
            if len(first) == 1:
                only = next(iter(first))
                if all(n == only or n.startswith(only + "/") for n in names):
                    prefix = only + "/"
        for name in names:
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not rel:
                continue
            out.append((rel, zf.read(name)))
    return out


def _build_zip(slug: str, files: list[tuple[str, bytes]]) -> bytes:
    """Re-pack unpacked files into a zip with a ``<slug>/`` top dir,
    matching the portable skill layout the kernel's import expects."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in sorted(files):
            zf.writestr(f"{slug}/{rel}", content)
    return buf.getvalue()



_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)(.*)\Z", re.DOTALL)


def _md_name_from_files(files: list[tuple[str, bytes]]) -> str:
    """SKILL.md frontmatter ``name`` of a skill bundle (canonical name).
    Empty when missing / unparseable — callers fall back to whatever
    display_name they were given."""
    md = next((c for rel, c in files if rel.endswith("SKILL.md")), None)
    if not md:
        return ""
    try:
        fm, _ = _parse_skill_md(md.decode("utf-8", "replace"))
        return str(fm.get("name") or "").strip()
    except Exception:
        return ""


def _parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    import yaml

    parsed = yaml.safe_load(m.group(1)) or {}
    return parsed if isinstance(parsed, dict) else {}, m.group(2).lstrip("\n")


def _render_skill_md(
    *, name: str, description: str, version: str, license: str,
    extra: dict[str, Any], body: str,
) -> str:
    import yaml

    fm: dict[str, Any] = {"name": name, "description": description}
    if version:
        fm["version"] = version
    if license:
        fm["license"] = license
    fm.update(extra or {})
    lines = ["---", yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip(), "---"]
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


def content_sha_from_files(files: list[tuple[str, bytes]]) -> str:
    """Content sha over unpacked rows. Matches the kernel's
    ``SkillStore.content_sha``: sha256 over each file's relative path +
    ``\\0`` + bytes + ``\\0``, sorted by path."""
    h = hashlib.sha256()
    for rel, data in sorted(files):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def content_sha_from_zip(zip_bytes: bytes) -> str:
    """Content sha for uploads that don't carry one (web frontend).

    Must match ``content_sha_from_files`` over the unpacked rows — the
    kernel sends its own sha; this is the fallback so both upload paths
    converge on the same fingerprint.
    """
    return content_sha_from_files(_extract_zip(zip_bytes))


def decode_b64_zip(data: str) -> bytes:
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as e:
        raise MarketError(400, f"invalid base64: {e}") from None
    return raw


__all__ = [
    "CATEGORIES",
    "MAX_ZIP_BYTES",
    "SLUG_RE",
    "MarketError",
    "MarketStore",
    "content_sha_from_zip",
    "decode_b64_zip",
]
