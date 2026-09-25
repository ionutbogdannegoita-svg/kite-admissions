"""Vista Oggi."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("today", __name__)


@bp.get("/")
def index():
    return render_template("today/index.html", active="today")
