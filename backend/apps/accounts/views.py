"""Manajemen peran user -- dibangun MELENGKAPI Django Admin, bukan
menggantikannya sepenuhnya. Pembuatan akun baru memakai
`UserCreationForm` bawaan Django (hashing password & validasi
kekuatan password mengikuti AUTH_PASSWORD_VALIDATORS otomatis --
tidak menulis ulang logic keamanan ini sendiri).
"""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .permissions import Roles, require_role

User = get_user_model()


class UserCreateForm(UserCreationForm):
    """UserCreationForm bawaan Django menangani hashing password dan
    validasi kekuatan password (AUTH_PASSWORD_VALIDATORS) secara
    otomatis -- form ini cuma menambah field email/nama/role di
    atasnya, tidak menyentuh logic keamanan intinya.
    """

    email = forms.EmailField(
        required=False,
        label="Email",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    first_name = forms.CharField(
        required=False,
        label="Nama Depan",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        required=False,
        label="Nama Belakang",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    role = forms.ChoiceField(
        choices=[(r, r) for r in Roles.ALL],
        label="Peran",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs["class"] = "form-control"
        self.fields["password1"].widget.attrs["class"] = "form-control"
        self.fields["password2"].widget.attrs["class"] = "form-control"


# Deskripsi ringkas "role ini boleh apa saja", dikompilasi manual dari
# seluruh pemakaian @require_role / require_role_by_method / APPROVERS
# di semua modul sistem. Bukan hasil scan otomatis -- kalau ada modul
# baru yang menambah/mengubah pembatasan role, tabel ini perlu
# disesuaikan manual juga.
ROLE_CAPABILITY_TABLE = [
    {
        "role": Roles.ADMIN,
        "label": "Admin",
        "summary": "Akses penuh ke seluruh sistem, termasuk konfigurasi Sumber OSINT.",
        "can": [
            "Semua yang bisa dilakukan Analyst dan Reviewer",
            "Kelola Sumber OSINT (tambah/edit sumber, seed URL, pola URL)",
            "Verifikasi sumber baru",
            "Jalankan/batalkan crawler",
        ],
    },
    {
        "role": Roles.ANALYST,
        "label": "Analyst",
        "summary": "Kerja harian: validasi artikel, bentuk sinyal, susun draft assessment/rekomendasi.",
        "can": [
            "Validasi/tandai artikel (relevan/tidak relevan)",
            "Bentuk kandidat sinyal dari artikel",
            "Kendali korelasi sinyal (gabung/pisah/tambah-lepas artikel bukti)",
            "Susun assessment ancaman & draft rekomendasi",
            "Jalankan crawler & proses ekstraksi entitas",
        ],
        "cannot": [
            "Konfirmasi/tolak sinyal secara resmi",
            "Terbitkan/tutup Peringatan Dini",
            "Tetapkan Rekomendasi jadi arahan resmi",
            "Validasi Indikator",
        ],
    },
    {
        "role": Roles.REVIEWER,
        "label": "Reviewer",
        "summary": "Pengesahan/approval -- langkah kedua setelah Analyst menyiapkan pekerjaan.",
        "can": [
            "Semua yang bisa dilakukan Analyst (kerja harian)",
            "Konfirmasi/tolak sinyal secara resmi",
            "Terbitkan/tutup Peringatan Dini",
            "Tetapkan Rekomendasi jadi arahan resmi",
            "Validasi Indikator",
            "Setujui evaluasi mutu (Pusat Mutu & Evaluasi)",
        ],
        "cannot": [
            "Kelola Sumber OSINT / jalankan crawler",
        ],
    },
    {
        "role": Roles.VIEWER,
        "label": "Viewer",
        "summary": "Hanya bisa melihat -- tidak ada aksi tulis/mutasi di halaman manapun.",
        "can": [
            "Lihat semua halaman & data (dashboard, sinyal, assessment, laporan, dst)",
        ],
        "cannot": [
            "Semua aksi tulis/submit di seluruh sistem",
        ],
    },
]


@require_role(Roles.ADMIN)
def user_create(request: HttpRequest) -> HttpResponse:
    """Buat user baru + langsung tetapkan perannya."""
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            role = form.cleaned_data["role"]
            group, _created = Group.objects.get_or_create(name=role)
            new_user.groups.set([group])

            new_user.email = form.cleaned_data.get("email", "")
            new_user.first_name = form.cleaned_data.get("first_name", "")
            new_user.last_name = form.cleaned_data.get("last_name", "")
            new_user.save(
                update_fields=["email", "first_name", "last_name"]
            )

            messages.success(
                request,
                f'User "{new_user.username}" berhasil dibuat dengan peran {role}.',
            )
            return redirect("accounts:user-role-list")
    else:
        form = UserCreateForm()

    context = {
        "page_title": "Tambah User Baru",
        "active_menu": "user_roles",
        "form": form,
    }

    return render(request, "accounts/user_create.html", context)


@require_role(Roles.ADMIN)
def user_role_list(request: HttpRequest) -> HttpResponse:
    """Daftar user beserta peran (Group) mereka saat ini."""
    query = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()

    users = (
        User.objects.all()
        .prefetch_related("groups")
        .order_by("username")
    )

    if query:
        from django.db.models import Q

        users = users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )

    if role_filter:
        users = users.filter(groups__name=role_filter)

    paginator = Paginator(users, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Precompute role saat ini per user (exact match, bukan substring
    # match) supaya template tidak perlu logic rapuh untuk pilih
    # opsi <select> yang terpilih.
    for u in page_obj:
        group_names = [group.name for group in u.groups.all()]
        u.current_role = group_names[0] if group_names else None

    context = {
        "page_title": "Manajemen Peran",
        "active_menu": "user_roles",
        "page_obj": page_obj,
        "role_choices": Roles.ALL,
        "query": query,
        "selected_role": role_filter,
        "capability_table": ROLE_CAPABILITY_TABLE,
        "total_users": User.objects.count(),
    }

    return render(request, "accounts/user_role_list.html", context)


@require_role(Roles.ADMIN)
def user_role_update(request: HttpRequest, user_id: int) -> HttpResponse:
    """Ubah peran satu user (replace keanggotaan Group)."""
    if request.method != "POST":
        return redirect("accounts:user-role-list")

    target_user = get_object_or_404(User, id=user_id)
    new_role = request.POST.get("role", "").strip()

    valid_roles = set(Roles.ALL)
    if new_role not in valid_roles:
        messages.error(request, "Peran tidak dikenali.")
        return redirect("accounts:user-role-list")

    if target_user.id == request.user.id and new_role != Roles.ADMIN:
        messages.error(
            request,
            "Tidak bisa mengubah peran akun sendiri menjadi bukan Admin "
            "lewat halaman ini (mencegah kehilangan akses tanpa sengaja). "
            "Minta Admin lain untuk mengubahnya kalau memang perlu.",
        )
        return redirect("accounts:user-role-list")

    group, _created = Group.objects.get_or_create(name=new_role)
    target_user.groups.set([group])

    messages.success(
        request,
        f'Peran "{target_user.username}" berhasil diubah menjadi {new_role}.',
    )

    return redirect("accounts:user-role-list")
