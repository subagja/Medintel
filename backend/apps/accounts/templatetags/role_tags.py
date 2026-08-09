"""Template filter untuk cek role di dalam template, supaya tombol
aksi (Hapus, Batalkan, Simpan, dll) bisa disembunyikan dari role yang
tidak berwenang -- bukan cuma diblokir di backend setelah diklik.

Pemakaian di template:
    {% load role_tags %}
    {% if request.user|has_role:"Admin,Analyst" %}
        <button>Hapus</button>
    {% endif %}

    {# atau pakai konstanta siap pakai: #}
    {% if request.user|has_role:"contributors" %}...{% endif %}
    {% if request.user|has_role:"approvers" %}...{% endif %}
"""
from django import template

from apps.accounts.permissions import Roles, has_role as _has_role

register = template.Library()

_PRESETS = {
    "all": Roles.ALL,
    "contributors": Roles.CONTRIBUTORS,
    "approvers": Roles.APPROVERS,
}


@register.filter(name="has_role")
def has_role_filter(user, roles_arg: str) -> bool:
    roles_arg = (roles_arg or "").strip()

    if roles_arg.lower() in _PRESETS:
        roles = _PRESETS[roles_arg.lower()]
    else:
        roles = [
            role.strip()
            for role in roles_arg.split(",")
            if role.strip()
        ]

    return _has_role(user, *roles)
