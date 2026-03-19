from __future__ import annotations

import os
import re
import sqlite3
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
    session,
    url_for,
)


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "complaints.db"


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["RECEIVER_PASSWORD"] = os.environ.get("RECEIVER_PASSWORD", "admin123")

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

        db = get_db()
        db.execute(
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
                SELECT id, sender_name, phone, area, subject, description, status, created_at
                FROM complaints
                ORDER BY id DESC
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT id, sender_name, phone, area, subject, description, status, created_at
                FROM complaints
                WHERE status = ?
                ORDER BY id DESC
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
        return render_template("complaint_detail.html", complaint=row)

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
    db.commit()


def require_receiver_auth() -> None:
    if not session.get("receiver_authed"):
        abort(403)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


PHONE_RE = re.compile(r"^[0-9 +()-]{7,20}$")


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
