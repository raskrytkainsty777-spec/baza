"""Все таблицы. Импорт модулей регистрирует их в Base.metadata — это нужно
Alembic для autogenerate и приложению для relationship() по строковым именам."""
from .cities import LgCity, LgSetting
from .accounts import IgAccount, LgDonor, LgReject
from .search import LgSearchTask, LgCandidate
from .posts import LgPost
from .comments import LgComment
from .leads import LgLead, LgOutbox, LgInbox
from .jobs import LgJob, LgEvent, LgStatsDaily
from .cabinet import (
    CabClient, CabSession, CabCompany, CabSource, CabContact, CabBlacklist, CabInbox, CabIntegration,
    CabOutbox, CabAgent, CabResourceList, CabResource, CabTask, CabTaskAgent, CabFoundSource, CabPayout,
)

__all__ = [
    "LgCity", "LgSetting",
    "IgAccount", "LgDonor", "LgReject",
    "LgSearchTask", "LgCandidate",
    "LgPost", "LgComment",
    "LgLead", "LgOutbox", "LgInbox",
    "LgJob", "LgEvent", "LgStatsDaily",
    "CabClient", "CabSession", "CabCompany", "CabSource", "CabContact", "CabBlacklist", "CabInbox",
    "CabIntegration", "CabOutbox", "CabAgent", "CabResourceList", "CabResource", "CabTask", "CabTaskAgent",
    "CabFoundSource", "CabPayout",
]
