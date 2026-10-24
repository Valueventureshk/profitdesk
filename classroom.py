"""Apps → Classroom (desk `classroom`, built 24 Oct 2026): the team's training library, laid out like Skool's classroom.

Sections (Front end, Back end, Store creation, Domain buying, CS handling, Fulfilment, SCM …) hold courses; a course
has a cover (uploaded image, else its title on a dark card) and lessons in order. A lesson is a recorded session
(uploaded video, a recording made in the browser, or a YouTube / Loom / Vimeo / Drive link), written notes, the
transcript and an AI step-by-step guide.

Transcripts: an uploaded video is turned into text automatically with OpenAI's Whisper (setting `openai_api_key`, the
same key as WhatsApp voice notes): the sound is taken out with ffmpeg (imageio-ffmpeg), cut into 10-minute pieces,
and each piece's segments come back with times, so the transcript reads "[12:04] …". The transcript can be edited and
downloaded (.txt / .md with the guide), and "Make step-by-step guide" asks Claude (Sonnet) to turn it into numbered
steps the team can follow, kept on the lesson and editable.

Levels: View = watch, read, download, tick lessons done (each person's progress is their own); Manage = everything
else (sections, courses, lessons, uploads, transcripts, guides). Drafts are only shown to Manage.
Files: <DB dir>/classroom/covers/<course>.<ext>, <DB dir>/classroom/lessons/<lesson>/video.<ext>.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import anthropic
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

import auth
import db

router = APIRouter()
DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "classroom")
MAX_VIDEO = 2 * 1024 ** 3                 # 2 GB per lesson video
VIDEO_EXT = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv",
             "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/webm": "webm", "audio/wav": "wav", "audio/x-m4a": "m4a"}
IMG_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
STARTER_SECTIONS = ("Front end", "Back end", "Store creation", "Domains", "Customer service", "Fulfilment", "SCM")
STEPS_MODEL = "claude-sonnet-5-5"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cls_sections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    position  REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cls_courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id  INTEGER,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cover_ext   TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',      -- draft | published
    position    REAL NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cls_lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL,
    title         TEXT NOT NULL,
    position      REAL NOT NULL DEFAULT 0,
    video_ext     TEXT,                              -- uploaded / recorded file
    video_name    TEXT,
    video_size    INTEGER,
    video_url     TEXT NOT NULL DEFAULT '',          -- or a YouTube / Loom / Vimeo / Drive link
    duration      REAL,
    notes         TEXT NOT NULL DEFAULT '',          -- written by the admin
    transcript    TEXT NOT NULL DEFAULT '',
    tr_status     TEXT NOT NULL DEFAULT 'none',      -- none | working | done | failed
    tr_error      TEXT,
    tr_at         TEXT,
    steps         TEXT NOT NULL DEFAULT '',          -- AI step-by-step guide (editable)
    steps_at      TEXT,
    created_by    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cls_lessons_course ON cls_lessons(course_id, position);
CREATE TABLE IF NOT EXISTS cls_progress (
    user_id   INTEGER NOT NULL,
    lesson_id INTEGER NOT NULL,
    done_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, lesson_id)
);
"""
_ready = {"ok": False}
_jobs: dict = {}          # lesson id -> asyncio task (transcribing)


def ensure():
    if _ready["ok"]:
        return
    with db._conn() as con:
        con.executescript(SCHEMA)
        if not con.execute("SELECT 1 FROM cls_sections LIMIT 1").fetchone():
            for i, n in enumerate(STARTER_SECTIONS):
                con.execute("INSERT INTO cls_sections (name, position) VALUES (?, ?)", (n, i))
        # a transcript that was being made when the server restarted is shown as failed (Try again)
        con.execute("UPDATE cls_lessons SET tr_status = 'failed', tr_error = 'Stopped by a restart: press Transcribe again.'"
                    " WHERE tr_status = 'working'")
    os.makedirs(os.path.join(DIR, "covers"), exist_ok=True)
    os.makedirs(os.path.join(DIR, "lessons"), exist_ok=True)
    _ready["ok"] = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _manage(request: Request) -> bool:
    return auth.at_least(_user(request), "classroom", "manage")


def _need_manage(request: Request):
    if not _manage(request):
        raise HTTPException(403, "Only admins (Manage on Classroom) can change the classroom.")


def _who(request: Request) -> str:
    u = _user(request)
    return u.get("name") or u.get("email") or "?"


def _row(con, table: str, rid: int) -> dict:
    r = con.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Not found (it may have been removed).")
    return dict(r)


def _lesson_dir(lid: int) -> str:
    return os.path.join(DIR, "lessons", str(lid))


def _video_path(l: dict) -> str:
    return os.path.join(_lesson_dir(l["id"]), f"video.{l['video_ext']}") if l.get("video_ext") else ""


def embed_url(url: str) -> str:
    """A link the page can show in a player: YouTube / Loom / Vimeo / Google Drive; '' when it can't be embedded."""
    u = (url or "").strip()
    m = re.search(r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{6,})", u)
    if m:
        return f"https://www.youtube-nocookie.com/embed/{m.group(1)}"
    m = re.search(r"loom\.com/(?:share|embed)/([0-9a-f]{20,})", u)
    if m:
        return f"https://www.loom.com/embed/{m.group(1)}"
    m = re.search(r"vimeo\.com/(?:video/)?(\d+)", u)
    if m:
        return f"https://player.vimeo.com/video/{m.group(1)}"
    m = re.search(r"drive\.google\.com/file/d/([\w-]{10,})", u)
    if m:
        return f"https://drive.google.com/file/d/{m.group(1)}/preview"
    return ""


def _lesson_out(l: dict, full: bool, done: set) -> dict:
    out = {k: l[k] for k in ("id", "course_id", "title", "position", "video_ext", "video_name", "video_size", "video_url",
                             "duration", "tr_status", "tr_error", "tr_at", "steps_at", "updated_at")}
    out["has_video"] = bool(l.get("video_ext"))
    out["embed"] = embed_url(l.get("video_url"))
    out["has_transcript"] = bool((l.get("transcript") or "").strip())
    out["has_steps"] = bool((l.get("steps") or "").strip())
    out["done"] = l["id"] in done
    if full:
        out.update(notes=l["notes"], transcript=l["transcript"], steps=l["steps"])
    return out


def _done_set(con, uid) -> set:
    return {r[0] for r in con.execute("SELECT lesson_id FROM cls_progress WHERE user_id = ?", (uid or 0,))}


# ---------------------------------------------------------------- reading

@router.get("/api/classroom")
def api_home(request: Request):
    """Sections and courses, with each course's lesson count and this person's progress."""
    ensure()
    manage = _manage(request)
    uid = _user(request).get("id")
    with db._conn() as con:
        sections = [dict(r) for r in con.execute("SELECT * FROM cls_sections ORDER BY position, id")]
        courses = [dict(r) for r in con.execute("SELECT * FROM cls_courses ORDER BY position, id")]
        counts = {r[0]: r[1] for r in con.execute("SELECT course_id, COUNT(*) FROM cls_lessons GROUP BY course_id")}
        done = {r[0]: r[1] for r in con.execute(
            "SELECT l.course_id, COUNT(*) FROM cls_progress p JOIN cls_lessons l ON l.id = p.lesson_id"
            " WHERE p.user_id = ? GROUP BY l.course_id", (uid or 0,))}
    out = []
    for c in courses:
        if c["status"] != "published" and not manage:
            continue
        n = counts.get(c["id"], 0)
        out.append({**{k: c[k] for k in ("id", "section_id", "title", "description", "status", "position", "updated_at")},
                    "cover": f"/api/classroom/covers/{c['id']}?v={c['updated_at']}" if c.get("cover_ext") else None,
                    "lessons": n, "done": done.get(c["id"], 0),
                    "progress": round(done.get(c["id"], 0) / n * 100) if n else 0})
    return {"sections": sections, "courses": out, "manage": manage,
            "has_voice_key": bool(db.get_setting("openai_api_key")), "has_ai_key": bool(db.get_setting("anthropic_api_key"))}


@router.get("/api/classroom/courses/{cid}")
def api_course(cid: int, request: Request):
    ensure()
    with db._conn() as con:
        c = _row(con, "cls_courses", cid)
        if c["status"] != "published" and not _manage(request):
            raise HTTPException(404, "This course isn't published yet.")
        done = _done_set(con, _user(request).get("id"))
        lessons = [_lesson_out(dict(r), False, done) for r in con.execute(
            "SELECT * FROM cls_lessons WHERE course_id = ? ORDER BY position, id", (cid,))]
    c["cover"] = f"/api/classroom/covers/{cid}?v={c['updated_at']}" if c.get("cover_ext") else None
    c["lessons"] = lessons
    c["progress"] = round(sum(1 for l in lessons if l["done"]) / len(lessons) * 100) if lessons else 0
    return c


@router.get("/api/classroom/lessons/{lid}")
def api_lesson(lid: int, request: Request):
    ensure()
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
        c = _row(con, "cls_courses", l["course_id"])
        if c["status"] != "published" and not _manage(request):
            raise HTTPException(404, "This course isn't published yet.")
        out = _lesson_out(l, True, _done_set(con, _user(request).get("id")))
    out["course_title"] = c["title"]
    return out


@router.get("/api/classroom/covers/{cid}")
def api_cover(cid: int):
    ensure()
    with db._conn() as con:
        c = _row(con, "cls_courses", cid)
    p = os.path.join(DIR, "covers", f"{cid}.{c.get('cover_ext')}")
    if not c.get("cover_ext") or not os.path.exists(p):
        raise HTTPException(404, "No cover.")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=604800"})


@router.get("/api/classroom/video/{lid}")
def api_video(lid: int, request: Request):
    """The lesson's video, with range requests so it can be skipped through."""
    ensure()
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
        c = _row(con, "cls_courses", l["course_id"])
    if c["status"] != "published" and not _manage(request):
        raise HTTPException(404, "This course isn't published yet.")
    p = _video_path(l)
    if not p or not os.path.exists(p):
        raise HTTPException(404, "This lesson has no video file.")
    return FileResponse(p, filename=l.get("video_name") or os.path.basename(p), content_disposition_type="inline")


def _slug(s: str) -> str:
    return re.sub(r"[^\w-]+", "-", (s or "lesson").strip()).strip("-")[:60] or "lesson"


@router.get("/api/classroom/lessons/{lid}/download")
def api_download(lid: int, request: Request, kind: str = "transcript"):
    """transcript → .txt; guide → .md with the notes, the step-by-step guide and the transcript (to give to an AI)."""
    l = api_lesson(lid, request)
    if kind == "transcript":
        if not l["transcript"].strip():
            raise HTTPException(404, "No transcript yet.")
        body, name, mt = l["transcript"], f"{_slug(l['title'])}-transcript.txt", "text/plain; charset=utf-8"
    else:
        parts = [f"# {l['course_title']} · {l['title']}"]
        if l["notes"].strip():
            parts.append("## Notes\n\n" + l["notes"].strip())
        if l["steps"].strip():
            parts.append("## Step by step\n\n" + l["steps"].strip())
        if l["transcript"].strip():
            parts.append("## Transcript\n\n" + l["transcript"].strip())
        body, name, mt = "\n\n".join(parts) + "\n", f"{_slug(l['title'])}.md", "text/markdown; charset=utf-8"
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/classroom/progress")
def api_progress(payload: dict, request: Request):
    """Tick a lesson done (or not) for the person looking: anyone who can open the classroom."""
    ensure()
    uid = _user(request).get("id")
    if not uid:
        raise HTTPException(401, "Please log in again.")
    lid = int(payload.get("lesson") or 0)
    with db._conn() as con:
        _row(con, "cls_lessons", lid)
        if payload.get("done", True):
            con.execute("INSERT OR REPLACE INTO cls_progress (user_id, lesson_id, done_at) VALUES (?, ?, ?)", (uid, lid, _now()))
        else:
            con.execute("DELETE FROM cls_progress WHERE user_id = ? AND lesson_id = ?", (uid, lid))
    return {"ok": True}


# ---------------------------------------------------------------- admins: sections, courses, lessons

@router.post("/api/classroom/sections")
def api_section_save(payload: dict, request: Request):
    _need_manage(request)
    ensure()
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "Give the section a name.")
    with db._conn() as con:
        if payload.get("id"):
            con.execute("UPDATE cls_sections SET name = ? WHERE id = ?", (name, int(payload["id"])))
            return {"ok": True, "id": int(payload["id"])}
        pos = (con.execute("SELECT MAX(position) FROM cls_sections").fetchone()[0] or 0) + 1
        return {"ok": True, "id": con.execute("INSERT INTO cls_sections (name, position) VALUES (?, ?)", (name, pos)).lastrowid}


@router.delete("/api/classroom/sections/{sid}")
def api_section_delete(sid: int, request: Request):
    _need_manage(request)
    ensure()
    with db._conn() as con:
        con.execute("UPDATE cls_courses SET section_id = NULL WHERE section_id = ?", (sid,))
        con.execute("DELETE FROM cls_sections WHERE id = ?", (sid,))
    return {"ok": True}


@router.post("/api/classroom/reorder")
def api_reorder(payload: dict, request: Request):
    """{kind: section | course | lesson, ids: [...in the new order]}"""
    _need_manage(request)
    ensure()
    table = {"section": "cls_sections", "course": "cls_courses", "lesson": "cls_lessons"}.get(payload.get("kind"))
    if not table:
        raise HTTPException(400, "What should be reordered?")
    with db._conn() as con:
        for i, rid in enumerate(payload.get("ids") or []):
            con.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (i, int(rid)))
    return {"ok": True}


@router.post("/api/classroom/courses")
def api_course_save(payload: dict, request: Request):
    _need_manage(request)
    ensure()
    title = str(payload.get("title") or "").strip()[:160]
    if not title:
        raise HTTPException(400, "Give the course a title.")
    f = {"title": title, "description": str(payload.get("description") or "").strip()[:2000],
         "section_id": int(payload["section_id"]) if payload.get("section_id") else None,
         "status": "published" if payload.get("status") == "published" else "draft", "updated_at": _now()}
    with db._conn() as con:
        if payload.get("id"):
            cid = int(payload["id"])
            _row(con, "cls_courses", cid)
            con.execute("UPDATE cls_courses SET title = ?, description = ?, section_id = ?, status = ?, updated_at = ? WHERE id = ?",
                        (*f.values(), cid))
            return {"ok": True, "id": cid}
        pos = (con.execute("SELECT MAX(position) FROM cls_courses").fetchone()[0] or 0) + 1
        cid = con.execute("INSERT INTO cls_courses (title, description, section_id, status, updated_at, position, created_by,"
                          " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*f.values(), pos, _who(request), _now())).lastrowid
    return {"ok": True, "id": cid}


@router.delete("/api/classroom/courses/{cid}")
def api_course_delete(cid: int, request: Request):
    _need_manage(request)
    ensure()
    with db._conn() as con:
        c = _row(con, "cls_courses", cid)
        lids = [r[0] for r in con.execute("SELECT id FROM cls_lessons WHERE course_id = ?", (cid,))]
        for lid in lids:
            con.execute("DELETE FROM cls_progress WHERE lesson_id = ?", (lid,))
        con.execute("DELETE FROM cls_lessons WHERE course_id = ?", (cid,))
        con.execute("DELETE FROM cls_courses WHERE id = ?", (cid,))
    for lid in lids:
        shutil.rmtree(_lesson_dir(lid), ignore_errors=True)
    if c.get("cover_ext"):
        try:
            os.remove(os.path.join(DIR, "covers", f"{cid}.{c['cover_ext']}"))
        except OSError:
            pass
    return {"ok": True}


@router.post("/api/classroom/courses/{cid}/cover")
async def api_cover_upload(cid: int, request: Request):
    _need_manage(request)
    ensure()
    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        raise HTTPException(400, "Choose an image.")
    ext = IMG_EXT.get((f.content_type or "").split(";")[0])
    if not ext:
        raise HTTPException(400, "Use a JPG, PNG, WebP or GIF image.")
    data = await f.read()
    if len(data) > 10 * 1024 ** 2:
        raise HTTPException(400, "That image is over 10 MB.")
    with db._conn() as con:
        c = _row(con, "cls_courses", cid)
        if c.get("cover_ext") and c["cover_ext"] != ext:
            try:
                os.remove(os.path.join(DIR, "covers", f"{cid}.{c['cover_ext']}"))
            except OSError:
                pass
        with open(os.path.join(DIR, "covers", f"{cid}.{ext}"), "wb") as out:
            out.write(data)
        con.execute("UPDATE cls_courses SET cover_ext = ?, updated_at = ? WHERE id = ?", (ext, _now(), cid))
    return {"ok": True}


@router.delete("/api/classroom/courses/{cid}/cover")
def api_cover_delete(cid: int, request: Request):
    _need_manage(request)
    ensure()
    with db._conn() as con:
        c = _row(con, "cls_courses", cid)
        con.execute("UPDATE cls_courses SET cover_ext = NULL, updated_at = ? WHERE id = ?", (_now(), cid))
    if c.get("cover_ext"):
        try:
            os.remove(os.path.join(DIR, "covers", f"{cid}.{c['cover_ext']}"))
        except OSError:
            pass
    return {"ok": True}


@router.post("/api/classroom/lessons")
def api_lesson_save(payload: dict, request: Request):
    """New lesson / edit title, link, notes, transcript text and guide text."""
    _need_manage(request)
    ensure()
    with db._conn() as con:
        if payload.get("id"):
            lid = int(payload["id"])
            l = _row(con, "cls_lessons", lid)
            sets, vals = [], []
            for k, cap in (("title", 200), ("video_url", 500), ("notes", 50000), ("transcript", 400000), ("steps", 50000)):
                if k in payload:
                    v = str(payload[k] or "").strip()[:cap]
                    if k == "title" and not v:
                        raise HTTPException(400, "Give the lesson a title.")
                    sets.append(f"{k} = ?")
                    vals.append(v)
            if "transcript" in payload and str(payload["transcript"] or "").strip() and l["tr_status"] != "done":
                sets.append("tr_status = 'done'")
            if sets:
                con.execute(f"UPDATE cls_lessons SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*vals, _now(), lid))
            return {"ok": True, "id": lid}
        cid = int(payload.get("course_id") or 0)
        _row(con, "cls_courses", cid)
        title = str(payload.get("title") or "").strip()[:200] or "New lesson"
        pos = (con.execute("SELECT MAX(position) FROM cls_lessons WHERE course_id = ?", (cid,)).fetchone()[0] or 0) + 1
        lid = con.execute("INSERT INTO cls_lessons (course_id, title, position, video_url, notes, created_by, created_at, updated_at)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (cid, title, pos, str(payload.get("video_url") or "").strip()[:500],
                           str(payload.get("notes") or "").strip()[:50000], _who(request), _now(), _now())).lastrowid
        con.execute("UPDATE cls_courses SET updated_at = ? WHERE id = ?", (_now(), cid))
    return {"ok": True, "id": lid}


@router.delete("/api/classroom/lessons/{lid}")
def api_lesson_delete(lid: int, request: Request):
    _need_manage(request)
    ensure()
    with db._conn() as con:
        _row(con, "cls_lessons", lid)
        con.execute("DELETE FROM cls_progress WHERE lesson_id = ?", (lid,))
        con.execute("DELETE FROM cls_lessons WHERE id = ?", (lid,))
    shutil.rmtree(_lesson_dir(lid), ignore_errors=True)
    return {"ok": True}


@router.post("/api/classroom/lessons/{lid}/video")
async def api_video_upload(lid: int, request: Request):
    """Upload (or record in the browser) the lesson's video. Transcribed automatically when the voice key is set."""
    _need_manage(request)
    ensure()
    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        raise HTTPException(400, "Choose the video file.")
    mime = (f.content_type or "").split(";")[0]
    ext = VIDEO_EXT.get(mime) or (os.path.splitext(f.filename or "")[1].lstrip(".").lower() or "")
    if ext not in set(VIDEO_EXT.values()):
        raise HTTPException(400, "Use a video (MP4, MOV, WebM, MKV) or audio file (MP3, M4A, WAV).")
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
    folder = _lesson_dir(lid)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f"upload.{ext}.part")
    size = 0
    with open(tmp, "wb") as out:
        while True:
            chunk = await f.read(4 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_VIDEO:
                out.close()
                os.remove(tmp)
                raise HTTPException(400, "That file is over 2 GB. Upload it to YouTube (unlisted) or Google Drive and paste the link instead.")
            out.write(chunk)
    old = _video_path(l)
    if old and os.path.exists(old):
        os.remove(old)
    os.replace(tmp, os.path.join(folder, f"video.{ext}"))
    duration = await asyncio.to_thread(_duration, os.path.join(folder, f"video.{ext}"))
    with db._conn() as con:
        con.execute("UPDATE cls_lessons SET video_ext = ?, video_name = ?, video_size = ?, duration = ?, updated_at = ? WHERE id = ?",
                    (ext, (f.filename or f"lesson.{ext}")[:200], size, duration, _now(), lid))
    started = False
    if db.get_setting("openai_api_key") and str(form.get("transcribe") or "1") != "0":
        start_transcript(lid)
        started = True
    return {"ok": True, "size": size, "transcribing": started}


# ---------------------------------------------------------------- transcripts (OpenAI Whisper) and AI guides

def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or ""


def _duration(path: str):
    """Length in seconds (from ffmpeg's report), or None."""
    exe = _ffmpeg()
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60)
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    except Exception:
        return None


def _audio_pieces(path: str, work: str) -> list:
    """The sound as 10-minute mono MP3 pieces (small enough for Whisper's 25 MB limit)."""
    exe = _ffmpeg()
    if not exe:
        raise RuntimeError("ffmpeg isn't available on the server.")
    out = os.path.join(work, "part%03d.mp3")
    p = subprocess.run([exe, "-hide_banner", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                        "-f", "segment", "-segment_time", "600", "-reset_timestamps", "1", out],
                       capture_output=True, text=True, timeout=3600)
    files = sorted(os.path.join(work, f) for f in os.listdir(work) if f.startswith("part") and f.endswith(".mp3"))
    if not files:
        raise RuntimeError("This file has no sound to transcribe" + (f" ({p.stderr.strip().splitlines()[-1][:160]})" if p.stderr.strip() else "."))
    return files


def _stamp(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


async def _transcribe(lid: int):
    key = db.get_setting("openai_api_key")
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
    path = _video_path(l)
    try:
        if not key:
            raise RuntimeError("Add the OpenAI key first (Settings → WhatsApp → voice notes key; it's the same key).")
        if not path or not os.path.exists(path):
            raise RuntimeError("Upload the video file first (links can't be transcribed).")
        work = tempfile.mkdtemp(prefix="cls-tr-")
        try:
            pieces = await asyncio.to_thread(_audio_pieces, path, work)
            lines = []
            async with httpx.AsyncClient(timeout=600) as client:
                for n, piece in enumerate(pieces):
                    with open(piece, "rb") as fh:
                        data = fh.read()
                    r = None
                    for attempt in range(3):
                        r = await client.post("https://api.openai.com/v1/audio/transcriptions",
                                              headers={"Authorization": f"Bearer {key}"},
                                              data={"model": "whisper-1", "response_format": "verbose_json"},
                                              files={"file": (f"part{n}.mp3", data, "audio/mpeg")})
                        if r.status_code < 500 and r.status_code != 429:
                            break
                        await asyncio.sleep(5 * (attempt + 1))
                    if r.status_code >= 400:
                        raise RuntimeError(f"OpenAI refused the transcription ({r.status_code}): {r.text[:200]}")
                    got = r.json()
                    for seg in got.get("segments") or []:
                        text = (seg.get("text") or "").strip()
                        if text:
                            lines.append(f"[{_stamp(n * 600 + float(seg.get('start') or 0))}] {text}")
                    if not got.get("segments") and (got.get("text") or "").strip():
                        lines.append(f"[{_stamp(n * 600)}] {got['text'].strip()}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with db._conn() as con:
            con.execute("UPDATE cls_lessons SET transcript = ?, tr_status = 'done', tr_error = NULL, tr_at = ?, updated_at = ? WHERE id = ?",
                        ("\n".join(lines), _now(), _now(), lid))
    except Exception as e:
        with db._conn() as con:
            con.execute("UPDATE cls_lessons SET tr_status = 'failed', tr_error = ? WHERE id = ?", (str(e)[:400], lid))
    finally:
        _jobs.pop(lid, None)


def start_transcript(lid: int):
    if lid in _jobs:
        return
    with db._conn() as con:
        con.execute("UPDATE cls_lessons SET tr_status = 'working', tr_error = NULL WHERE id = ?", (lid,))
    _jobs[lid] = asyncio.get_event_loop().create_task(_transcribe(lid))


@router.post("/api/classroom/lessons/{lid}/transcribe")
async def api_transcribe(lid: int, request: Request):
    _need_manage(request)
    ensure()
    if not db.get_setting("openai_api_key"):
        raise HTTPException(400, "Add the OpenAI key first (Settings → WhatsApp, the voice notes key). Whisper costs about US$0.36 an hour of video.")
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
    if not l.get("video_ext"):
        raise HTTPException(400, "Upload the video file first. Links (YouTube, Loom …) can't be transcribed here: paste their transcript instead.")
    start_transcript(lid)
    return {"ok": True}


STEPS_SYSTEM = """You turn a recorded training session for an e-commerce team (Shopify dropshipping stores: store building,
ads, customer service, fulfilment, suppliers) into a written guide staff can follow without watching the video.
Write in plain English, short sentences. Structure:
## What this covers (2-3 lines)
## Before you start (accounts, access, files needed; only if mentioned)
## Steps (numbered; one action per step; exact button / menu / field names as said in the session; add [mm:ss] from the
transcript where the step is shown so people can jump to it)
## Watch out for (mistakes, rules and tips the trainer stressed)
## Done when (how to check it worked)
Use only what is in the notes and transcript. Never invent settings, prices, links or steps; if something is unclear,
write "(unclear in the recording)". Keep store names, product names and numbers exactly as said."""


@router.post("/api/classroom/lessons/{lid}/steps")
async def api_steps(lid: int, request: Request):
    """Claude writes the step-by-step guide from the notes + transcript (kept on the lesson, editable)."""
    _need_manage(request)
    ensure()
    key = db.get_setting("anthropic_api_key")
    if not key:
        raise HTTPException(400, "Add the Anthropic key first (Settings → AI).")
    with db._conn() as con:
        l = _row(con, "cls_lessons", lid)
        c = _row(con, "cls_courses", l["course_id"])
    if not (l["transcript"].strip() or l["notes"].strip()):
        raise HTTPException(400, "There's no transcript or notes to write the guide from yet.")
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=2, timeout=300)
    try:
        resp = await client.messages.create(
            model=STEPS_MODEL, max_tokens=8000, system=STEPS_SYSTEM, output_config={"effort": "medium"},
            messages=[{"role": "user", "content": f"Course: {c['title']}\nLesson: {l['title']}\n\nNotes from the trainer:\n"
                                                  f"{l['notes'] or '(none)'}\n\nTranscript:\n{l['transcript'][:350000] or '(none)'}"}])
    except anthropic.APIError as e:
        raise HTTPException(400, f"The AI couldn't write the guide ({type(e).__name__}). Try again in a minute.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise HTTPException(400, "The AI returned nothing. Try again.")
    with db._conn() as con:
        con.execute("UPDATE cls_lessons SET steps = ?, steps_at = ?, updated_at = ? WHERE id = ?", (text, _now(), _now(), lid))
    return {"ok": True, "steps": text}


# ---------------------------------------------------------------- the Ask assistant / connector

def search(q: str, limit: int = 8) -> list:
    """Lessons whose title, notes, guide or transcript mention the words (published courses only)."""
    ensure()
    words = [w for w in re.findall(r"\w{3,}", (q or "").lower())][:6]
    with db._conn() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT l.id, l.title, l.notes, l.steps, l.transcript, c.title course FROM cls_lessons l JOIN cls_courses c"
            " ON c.id = l.course_id WHERE c.status = 'published'")]
    scored = []
    for r in rows:
        text = " ".join((r["title"], r["course"], r["notes"], r["steps"], r["transcript"])).lower()
        s = sum(text.count(w) for w in words) + sum(5 for w in words if w in r["title"].lower())
        if s or not words:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [{"lesson_id": r["id"], "course": r["course"], "lesson": r["title"], "link": f"/classroom?lesson={r['id']}",
             "guide": (r["steps"] or r["notes"])[:4000], "transcript_excerpt": r["transcript"][:1500]}
            for _, r in scored[:limit]]
