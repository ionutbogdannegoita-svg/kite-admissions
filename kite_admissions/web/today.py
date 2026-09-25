"""Vista Oggi: cosa fare adesso (SPEC §3, AC07)."""

from __future__ import annotations

from flask import Blueprint, render_template

from ..services import today as today_service
from ..timeutil import format_long_date
from . import get_db, state

bp = Blueprint("today", __name__)


@bp.get("/")
def index():
    now = state().now()
    view = today_service.build(get_db(), now)
    return render_template("today/index.html", active="today", view=view, today_label=format_long_date(view.today),
                           today_iso=view.today.isoformat())
