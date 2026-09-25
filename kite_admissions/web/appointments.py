"""Vista Appuntamenti."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("appointments", __name__, url_prefix="/appuntamenti")


@bp.get("/")
def index():
    return render_template("appointments/index.html", active="appointments")
