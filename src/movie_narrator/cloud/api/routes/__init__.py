# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-family route-handler mixins.

Importing this package guarantees every route family is decorated into the
shared :data:`~movie_narrator.cloud.api._base._route_registry`.
"""

from .admin import _AdminRoutes
from .batches import _BatchesRoutes
from .schedules import _SchedulesRoutes
from .tasks import _TasksRoutes
from .webhooks import _WebhooksRoutes

__all__ = [
    "_AdminRoutes",
    "_BatchesRoutes",
    "_SchedulesRoutes",
    "_TasksRoutes",
    "_WebhooksRoutes",
]
