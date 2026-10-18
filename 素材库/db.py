# -*- coding: utf-8 -*-
"""问造社内部素材库。查询、补录、从 inbox 入库。"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "wenzao.db"
INBOX = ROOT / "inbox"
DONE = INBOX / "已入库"

KINDS = ("理念", "作品", "套件", "规则", "边界")
SHELVES = ("可以", "只演示", "不卖", "不排", "不适用")


def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            difficulty INTEGER,
            level TEXT,
            demand TEXT,
            howto TEXT,
            parts TEXT,
            cost TEXT,
            shelf TEXT,
            summary TEXT,
            body TEXT,
            status TEXT NOT NULL DEFAULT '在用',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(kind, title)
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            locator TEXT NOT NULL,
            note TEXT,
            UNIQUE(locator)
        );
        CREATE TABLE IF NOT EXISTS item_source (
            item_id INTEGER NOT NULL REFERENCES items(id),
            source_id INTEGER NOT NULL REFERENCES sources(id),
            PRIMARY KEY (item_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS tags (
            item_id INTEGER NOT NULL REFERENCES items(id),
            tag TEXT NOT NULL,
            PRIMARY KEY (item_id, tag)
        );
        """
    )
    conn.commit()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def source_id(conn, title, locator, note=""):
    conn.execute(
        "INSERT OR IGNORE INTO sources (title, locator, note) VALUES (?, ?, ?)",
        (title, locator, note),
    )
    row = conn.execute("SELECT id FROM sources WHERE locator = ?", (locator,)).fetchone()
    return row["id"]


def upsert(conn, item, source=None, tags=None):
    if item.get("kind") not in KINDS:
        raise SystemExit(f"kind 必须是：{'、'.join(KINDS)}")
    shelf = item.get("shelf") or ""
    if shelf and shelf not in SHELVES:
        raise SystemExit(f"shelf 必须是：{'、'.join(SHELVES)}")
    ts = now()
    conn.execute(
        """
        INSERT INTO items (
            kind, title, difficulty, level, demand, howto, parts, cost, shelf,
            summary, body, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(kind, title) DO UPDATE SET
            difficulty=excluded.difficulty,
            level=excluded.level,
            demand=excluded.demand,
            howto=excluded.howto,
            parts=excluded.parts,
            cost=excluded.cost,
            shelf=excluded.shelf,
            summary=excluded.summary,
            body=excluded.body,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            item["kind"],
            item["title"],
            item.get("difficulty"),
            item.get("level") or "",
            item.get("demand") or "",
            item.get("howto") or "",
            item.get("parts") or "",
            item.get("cost") or "",
            shelf,
            item.get("summary") or "",
            item.get("body") or "",
            item.get("status") or "在用",
            ts,
            ts,
        ),
    )
    row = conn.execute(
        "SELECT id FROM items WHERE kind = ? AND title = ?",
        (item["kind"], item["title"]),
    ).fetchone()
    iid = row["id"]
    if source:
        sid = source_id(conn, source["title"], source["locator"], source.get("note") or "")
        conn.execute(
            "INSERT OR IGNORE INTO item_source (item_id, source_id) VALUES (?, ?)",
            (iid, sid),
        )
    for tag in tags or []:
        tag = tag.strip()
        if tag:
            conn.execute("INSERT OR IGNORE INTO tags (item_id, tag) VALUES (?, ?)", (iid, tag))
    return iid


def parse_note(text):
    lines = text.splitlines()
    fields = {}
    i = 0
    while i < len(lines) and lines[i].strip():
        if ":" not in lines[i]:
            break
        key, value = lines[i].split(":", 1)
        fields[key.strip()] = value.strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:]).strip()
    if "difficulty" in fields and fields["difficulty"]:
        fields["difficulty"] = int(fields["difficulty"])
    tags = [t.strip() for t in fields.pop("tags", "").split("、") if t.strip()]
    source = None
    if fields.get("source_locator"):
        source = {
            "title": fields.pop("source_title", fields.get("title", "未命名来源")),
            "locator": fields.pop("source_locator"),
            "note": fields.pop("source_note", ""),
        }
    else:
        fields.pop("source_title", None)
        fields.pop("source_note", None)
    fields["body"] = body or fields.get("body", "")
    return fields, source, tags


def cmd_import(conn):
    DONE.mkdir(parents=True, exist_ok=True)
    notes = sorted(p for p in INBOX.glob("*.md") if not p.name.startswith("_"))
    if not notes:
        print("inbox 里没有待入库的 md。")
        return
    for path in notes:
        fields, source, tags = parse_note(path.read_text(encoding="utf-8"))
        upsert(conn, fields, source, tags)
        target = DONE / path.name
        if target.exists():
            target = DONE / f"{path.stem}-{now().replace(':', '').replace(' ', '-') }{path.suffix}"
        path.replace(target)
        print(f"已入库：{fields.get('kind')} / {fields.get('title')}")
    conn.commit()


def cmd_list(conn, kind, difficulty, shelf):
    sql = "SELECT kind, difficulty, title, cost, shelf, summary FROM items WHERE status = '在用'"
    args = []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if difficulty:
        sql += " AND difficulty = ?"
        args.append(difficulty)
    if shelf:
        sql += " AND shelf = ?"
        args.append(shelf)
    sql += " ORDER BY kind, difficulty, id"
    rows = conn.execute(sql, args).fetchall()
    for row in rows:
        diff = row["difficulty"] if row["difficulty"] else "-"
        shelf_s = row["shelf"] or "-"
        cost = row["cost"] or "-"
        print(f"[{row['kind']}] 难度{diff} {row['title']} | {cost} | {shelf_s}")
        if row["summary"]:
            print(f"    {row['summary']}")
    print(f"共 {len(rows)} 条")


def cmd_search(conn, keyword):
    like = f"%{keyword}%"
    rows = conn.execute(
        """
        SELECT kind, difficulty, title, summary
        FROM items
        WHERE title LIKE ? OR summary LIKE ? OR body LIKE ?
           OR demand LIKE ? OR howto LIKE ? OR parts LIKE ?
        ORDER BY kind, id
        """,
        (like, like, like, like, like, like),
    ).fetchall()
    for row in rows:
        diff = f"难度{row['difficulty']} " if row["difficulty"] else ""
        print(f"[{row['kind']}] {diff}{row['title']}")
        if row["summary"]:
            print(f"    {row['summary']}")
    print(f"共 {len(rows)} 条")


def cmd_show(conn, title):
    row = conn.execute("SELECT * FROM items WHERE title = ?", (title,)).fetchone()
    if not row:
        print("没有这条。")
        return
    for key in row.keys():
        if row[key]:
            print(f"{key}: {row[key]}")
    sources = conn.execute(
        """
        SELECT s.title, s.locator FROM sources s
        JOIN item_source m ON m.source_id = s.id
        WHERE m.item_id = ?
        """,
        (row["id"],),
    ).fetchall()
    for src in sources:
        print(f"来源: {src['title']} | {src['locator']}")


def main():
    parser = argparse.ArgumentParser(description="问造社内部素材库")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("import-inbox")
    p_list = sub.add_parser("list")
    p_list.add_argument("--kind")
    p_list.add_argument("--difficulty", type=int)
    p_list.add_argument("--shelf")
    p_search = sub.add_parser("search")
    p_search.add_argument("keyword")
    p_show = sub.add_parser("show")
    p_show.add_argument("title")
    args = parser.parse_args()
    conn = connect()
    init(conn)
    if args.cmd == "init":
        print(DB)
    elif args.cmd == "import-inbox":
        cmd_import(conn)
    elif args.cmd == "list":
        cmd_list(conn, args.kind, args.difficulty, args.shelf)
    elif args.cmd == "search":
        cmd_search(conn, args.keyword)
    elif args.cmd == "show":
        cmd_show(conn, args.title)
    conn.close()


if __name__ == "__main__":
    main()
