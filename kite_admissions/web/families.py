"""Viste Famiglie e Scheda famiglia."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("families", __name__, url_prefix="/famiglie")


@bp.get("/")
def index():
    return render_template("family/index.html", active="families")
