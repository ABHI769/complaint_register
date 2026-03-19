from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.datastructures import FileStorage


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "complaints.db"
UPLOADS_DIR = BASE_DIR / "uploads"

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGES_PER_COMPLAINT = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB each


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["RECEIVER_PASSWORD"] = os.environ.get("RECEIVER_PASSWORD", "admin123")
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # request limit

    @app.before_request
    def _ensure_db() -> None:
        _ = get_db()
        init_db()

    @app.teardown_appcontext
    def _close_db(exception: Optional[BaseException]) -> None:  # noqa: ARG001
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/complaints")
    def submit_complaint() -> Any:
        form = ComplaintForm.from_request(request)
        errors = form.validate()
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("index.html", form=form), 400

        images = request.files.getlist("images")
        images = [img for img in images if img and getattr(img, "filename", "")]
        if len(images) > MAX_IMAGES_PER_COMPLAINT:
            flash(f"Please upload at most {MAX_IMAGES_PER_COMPLAINT} images.", "error")
            return render_template("index.html", form=form), 400

        image_validation_errors = validate_images(images)
        if image_validation_errors:
            for e in image_validation_errors:
                flash(e, "error")
            return render_template("index.html", form=form), 400

        db = get_db()
        cur = db.execute(
            """
            INSERT INTO complaints (
              sender_name, phone, area, subject, description, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                form.sender_name.strip(),
                form.phone.strip(),
                form.area.strip(),
                form.subject.strip(),
                form.description.strip(),
                utc_now_iso(),
            ),
        )
        complaint_id = int(cur.lastrowid)
        if images:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            for img in images:
                stored_name, original_name = save_image(img)
                db.execute(
                    """
                    INSERT INTO complaint_images (complaint_id, stored_filename, original_filename, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (complaint_id, stored_name, original_name, utc_now_iso()),
                )
        db.commit()

        flash("Complaint submitted successfully.", "success")
        return redirect(url_for("index"))

    @app.get("/receiver/login")
    def receiver_login() -> str:
        return render_template("receiver_login.html")

    @app.post("/receiver/login")
    def receiver_login_post() -> Any:
        password = (request.form.get("password") or "").strip()
        if password != app.config["RECEIVER_PASSWORD"]:
            flash("Incorrect password.", "error")
            return render_template("receiver_login.html"), 401
        session["receiver_authed"] = True
        return redirect(url_for("receiver_dashboard"))

    @app.post("/receiver/logout")
    def receiver_logout() -> Any:
        session.pop("receiver_authed", None)
        return redirect(url_for("receiver_login"))

    @app.get("/receiver")
    def receiver_dashboard() -> str:
        require_receiver_auth()
        status = (request.args.get("status") or "open").strip().lower()
        if status not in {"open", "resolved", "all"}:
            status = "open"

        db = get_db()
        if status == "all":
            rows = db.execute(
                """
                SELECT
                  c.id, c.sender_name, c.phone, c.area, c.subject, c.description, c.status, c.created_at,
                  (SELECT COUNT(*) FROM complaint_images ci WHERE ci.complaint_id = c.id) AS image_count
                FROM complaints c
                ORDER BY (CASE WHEN image_count > 0 THEN 1 ELSE 0 END) DESC, c.id DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT
                  c.id, c.sender_name, c.phone, c.area, c.subject, c.description, c.status, c.created_at,
                  (SELECT COUNT(*) FROM complaint_images ci WHERE ci.complaint_id = c.id) AS image_count
                FROM complaints c
                WHERE c.status = ?
                ORDER BY (CASE WHEN image_count > 0 THEN 1 ELSE 0 END) DESC, c.id DESC
                """,
                (status,),
            ).fetchall()

        return render_template("receiver.html", complaints=rows, status=status)

    @app.get("/receiver/complaints/<int:complaint_id>")
    def receiver_detail(complaint_id: int) -> str:
        require_receiver_auth()
        db = get_db()
        row = db.execute(
            """
            SELECT id, sender_name, phone, area, subject, description, status, created_at
            FROM complaints
            WHERE id = ?
            """,
            (complaint_id,),
        ).fetchone()
        if row is None:
            abort(404)
        images = db.execute(
            """
            SELECT id, stored_filename, original_filename, created_at
            FROM complaint_images
            WHERE complaint_id = ?
            ORDER BY id ASC
            """,
            (complaint_id,),
        ).fetchall()
        return render_template("complaint_detail.html", complaint=row, images=images)

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename: str) -> Any:
        require_receiver_auth()
        return send_from_directory(UPLOADS_DIR, filename, as_attachment=False)

    @app.post("/receiver/complaints/<int:complaint_id>/resolve")
    def receiver_resolve(complaint_id: int) -> Any:
        require_receiver_auth()
        db = get_db()
        db.execute("UPDATE complaints SET status='resolved' WHERE id = ?", (complaint_id,))
        db.commit()
        flash("Marked as resolved.", "success")
        return redirect(url_for("receiver_detail", complaint_id=complaint_id))

    @app.post("/receiver/complaints/<int:complaint_id>/reopen")
    def receiver_reopen(complaint_id: int) -> Any:
        require_receiver_auth()
        db = get_db()
        db.execute("UPDATE complaints SET status='open' WHERE id = ?", (complaint_id,))
        db.commit()
        flash("Reopened complaint.", "success")
        return redirect(url_for("receiver_detail", complaint_id=complaint_id))

    @app.post("/receiver/complaints/<int:complaint_id>/delete")
    def receiver_delete(complaint_id: int) -> Any:
        require_receiver_auth()
        db = get_db()

        row = db.execute("SELECT id, status FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if row is None:
            abort(404)
        if row["status"] != "resolved":
            flash("Only resolved complaints can be deleted.", "error")
            return redirect(url_for("receiver_detail", complaint_id=complaint_id))

        images = db.execute(
            "SELECT stored_filename FROM complaint_images WHERE complaint_id = ?",
            (complaint_id,),
        ).fetchall()
        for img in images:
            try:
                (UPLOADS_DIR / img["stored_filename"]).unlink(missing_ok=True)
            except Exception:
                # Best-effort file cleanup; DB deletion still proceeds.
                pass

        db.execute("DELETE FROM complaints WHERE id = ?", (complaint_id,))
        db.commit()
        flash("Deleted resolved complaint.", "success")
        return redirect(url_for("receiver_dashboard", status="resolved"))

    return app


def get_db() -> sqlite3.Connection:
    db = g.get("db")
    if db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        g.db = db
    return db


def init_db() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS complaints (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sender_name TEXT NOT NULL,
          phone TEXT NOT NULL,
          area TEXT NOT NULL,
          subject TEXT NOT NULL,
          description TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('open','resolved')),
          created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS complaint_images (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          complaint_id INTEGER NOT NULL,
          stored_filename TEXT NOT NULL,
          original_filename TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (complaint_id) REFERENCES complaints(id) ON DELETE CASCADE
        )
        """
    )
    db.commit()


def require_receiver_auth() -> None:
    if not session.get("receiver_authed"):
        abort(403)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


PHONE_RE = re.compile(r"^[0-9 +()-]{7,20}$")


def validate_images(images: list[FileStorage]) -> list[str]:
    errors: list[str] = []
    for idx, img in enumerate(images, start=1):
        filename = (img.filename or "").strip()
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            errors.append(f"Image {idx}: only JPG/JPEG, PNG, or WEBP allowed.")
            continue

        size = img.content_length
        if size is None:
            try:
                pos = img.stream.tell()
                img.stream.seek(0, os.SEEK_END)
                size = img.stream.tell()
                img.stream.seek(pos)
            except Exception:
                size = None
        if size is not None and size > MAX_IMAGE_BYTES:
            errors.append(f"Image {idx}: file is too large (max 5MB).")
    return errors


def save_image(img: FileStorage) -> tuple[str, str]:
    original = (img.filename or "image").strip()
    ext = Path(original).suffix.lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    target = UPLOADS_DIR / stored
    img.save(target)
    return stored, original


@dataclass(frozen=True)
class ComplaintForm:
    sender_name: str = ""
    phone: str = ""
    area: str = ""
    subject: str = ""
    description: str = ""

    @staticmethod
    def from_request(req: Any) -> "ComplaintForm":
        return ComplaintForm(
            sender_name=(req.form.get("sender_name") or ""),
            phone=(req.form.get("phone") or ""),
            area=(req.form.get("area") or ""),
            subject=(req.form.get("subject") or ""),
            description=(req.form.get("description") or ""),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.sender_name.strip():
            errors.append("Sender name is required.")
        if not self.phone.strip():
            errors.append("Phone number is required.")
        elif not PHONE_RE.match(self.phone.strip()):
            errors.append("Phone number looks invalid (use digits and +()- only).")
        if not self.area.strip():
            errors.append("Area of complaint is required.")
        if not self.subject.strip():
            errors.append("Subject is required.")
        if not self.description.strip():
            errors.append("Description is required.")
        elif len(self.description.strip()) < 10:
            errors.append("Description should be at least 10 characters.")
        return errors


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
