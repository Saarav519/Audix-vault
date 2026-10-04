import re
import secrets
import string

from django import forms
from django.contrib.auth import password_validation
from django.utils.text import slugify

from accounts.models import Role, User
from clients.models import DEFAULT_CATEGORIES, Category, Client, Store
from core.models import AppSettings

LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
              ".webp": "image/webp"}
LOGO_MAX = 2 * 1024 * 1024


def generate_password(length=14):
    alphabet = string.ascii_letters + string.digits
    alphabet = "".join(c for c in alphabet if c not in "Il1O0o")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def validate_logo(f):
    import os

    if f is None:
        return
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in LOGO_TYPES or ext == ".svg":
        raise forms.ValidationError("Upload a PNG, JPG or WebP logo.")
    if f.size > LOGO_MAX:
        raise forms.ValidationError("The logo must be 2 MB or smaller.")


def parse_emails(raw):
    raw = (raw or "").replace(";", ",").replace("\n", ",")
    out = []
    for e in (x.strip() for x in raw.split(",")):
        if not e:
            continue
        forms.EmailField().clean(e)
        out.append(e)
    return out


class LoginIdField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        value = (value or "").strip().lower()
        if not value:
            return value
        if not all(c.isalnum() or c in "._-@" for c in value):
            raise forms.ValidationError("Use letters, numbers, dots, dashes or underscores only.")
        if User.objects.filter(login_id__iexact=value).exists():
            raise forms.ValidationError("This login ID is already taken.")
        return value


class ClientCreateForm(forms.Form):
    name = forms.CharField(label="Company name", max_length=160)
    contact_emails = forms.CharField(label="Contact email(s)", required=False,
                                     help_text="Separate several emails with commas.")
    login_id = LoginIdField(label="Login ID", max_length=64)
    password = forms.CharField(label="Temporary password", min_length=10, max_length=128,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    logo = forms.FileField(label="Logo", required=False, help_text="PNG, JPG or WebP, up to 2 MB.")
    must_change_password = forms.BooleanField(label="Ask for a new password at first login", required=False,
                                              initial=True)
    notify_on_publish = forms.BooleanField(label="Email them when a new audit is published", required=False,
                                           initial=True)
    stores = forms.CharField(label="Stores", required=False, widget=forms.Textarea(attrs={"rows": 4}),
                             help_text="One store per line: code, name, city. You can add more later.")
    categories = forms.CharField(label="Categories", widget=forms.Textarea(attrs={"rows": 7}),
                                 initial="\n".join(DEFAULT_CATEGORIES),
                                 help_text="One category per line, in the order they should appear.")

    def clean_contact_emails(self):
        return ", ".join(parse_emails(self.cleaned_data.get("contact_emails")))

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        validate_logo(f)
        return f

    def clean_password(self):
        pw = self.cleaned_data["password"]
        password_validation.validate_password(pw)
        return pw

    def clean_stores(self):
        rows = []
        seen = set()
        for i, line in enumerate((self.cleaned_data.get("stores") or "").splitlines(), start=1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise forms.ValidationError(f"Line {i}: write code, name, city.")
            code = parts[0].upper()
            if code in seen:
                raise forms.ValidationError(f"Line {i}: store code {code} is repeated.")
            seen.add(code)
            rows.append((code[:30], parts[1][:120], (parts[2] if len(parts) > 2 else "")[:80]))
        return rows

    def clean_categories(self):
        names = []
        for line in (self.cleaned_data.get("categories") or "").splitlines():
            n = line.strip()[:80]
            if n and n.lower() not in {x.lower() for x in names}:
                names.append(n)
        if not names:
            raise forms.ValidationError("Add at least one category.")
        return names

    def unique_slug(self):
        base = slugify(self.cleaned_data["name"])[:60] or "client"
        slug, i = base, 2
        while Client.objects.filter(slug=slug).exists():
            slug = f"{base}-{i}"
            i += 1
        return slug


class ClientEditForm(forms.ModelForm):
    logo = forms.FileField(label="Replace logo", required=False, help_text="PNG, JPG or WebP, up to 2 MB.")

    class Meta:
        model = Client
        fields = ["name", "contact_emails", "notify_on_publish", "good_pct", "warn_pct", "cycle_days", "soon_days"]
        widgets = {"contact_emails": forms.TextInput()}
        help_texts = {"good_pct": "Leave empty to use the global setting.",
                      "warn_pct": "Leave empty to use the global setting."}

    def clean_contact_emails(self):
        return ", ".join(parse_emails(self.cleaned_data.get("contact_emails")))

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        validate_logo(f)
        return f

    def clean(self):
        data = super().clean()
        app = AppSettings.load()
        good = data.get("good_pct") if data.get("good_pct") is not None else app.good_pct
        warn = data.get("warn_pct") if data.get("warn_pct") is not None else app.warn_pct
        if good is not None and warn is not None and good > warn:
            raise forms.ValidationError("“Healthy up to” must not be more than “Watch up to”.")
        if data.get("cycle_days") is not None and data.get("soon_days") is not None:
            if data["soon_days"] >= data["cycle_days"]:
                raise forms.ValidationError("The due-soon window must be shorter than the audit cycle.")
        return data


class StoreForm(forms.ModelForm):
    class Meta:
        model = Store
        fields = ["code", "name", "city"]

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = client

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        qs = Store.objects.filter(client=self.client, code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This store code already exists for this client.")
        return code


STORE_CODE_RE = re.compile(r"^S(\d+)$", re.IGNORECASE)


def next_store_code(client) -> str:
    """Next free auto code for a client: S001, S002, ... (after the highest existing S-number)."""
    codes = {c.upper() for c in Store.objects.filter(client=client).values_list("code", flat=True)}
    numbers = [int(m.group(1)) for c in codes if (m := STORE_CODE_RE.match(c))]
    n = max(numbers, default=0) + 1
    while f"S{n:03d}" in codes:
        n += 1
    return f"S{n:03d}"


class QuickStoreForm(StoreForm):
    """Add a store from the Add audit page: code optional (auto-generated), no duplicate name + city."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        self.fields["name"].required = True

    def clean_code(self):
        if not (self.cleaned_data.get("code") or "").strip():
            return next_store_code(self.client)
        return super().clean_code()

    def clean(self):
        data = super().clean()
        name, city = (data.get("name") or "").strip(), (data.get("city") or "").strip()
        if name:
            same = Store.objects.filter(client=self.client, is_active=True, name__iexact=name, city__iexact=city).first()
            if same:
                raise forms.ValidationError(
                    f"{same.option_label} already exists for this client. Pick it from the store list instead.")
        return data


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "sort_order"]

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = client

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Category.objects.filter(client=self.client, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This category already exists for this client.")
        return name


class ClientLoginForm(forms.Form):
    login_id = LoginIdField(label="Login ID", max_length=64)
    name = forms.CharField(max_length=120, required=False)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=[(Role.CLIENT, "Head office (all stores)"),
                                      (Role.CLIENT_STORE, "Store manager (selected stores)")])
    stores = forms.ModelMultipleChoiceField(queryset=Store.objects.none(), required=False,
                                            widget=forms.CheckboxSelectMultiple)
    password = forms.CharField(label="Temporary password", min_length=10,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stores"].queryset = Store.objects.filter(client=client, is_active=True)

    def clean_password(self):
        pw = self.cleaned_data["password"]
        password_validation.validate_password(pw)
        return pw


class StaffUserForm(forms.Form):
    login_id = LoginIdField(label="Login ID", max_length=64)
    name = forms.CharField(max_length=120)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=[(Role.AUDITOR, "Auditor"), (Role.ADMIN, "Admin")])
    password = forms.CharField(label="Temporary password", min_length=10,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def clean_password(self):
        pw = self.cleaned_data["password"]
        password_validation.validate_password(pw)
        return pw


class AppSettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = ["good_pct", "warn_pct", "cycle_days", "soon_days", "auditor_can_publish",
                  "session_timeout_minutes", "signed_url_minutes", "notify_new_audit",
                  "notify_monthly_summary", "notify_missing_files"]

    def clean(self):
        data = super().clean()
        if data.get("good_pct") is not None and data.get("warn_pct") is not None and data["good_pct"] > data["warn_pct"]:
            raise forms.ValidationError("“Healthy up to” must not be more than “Watch up to”.")
        for f in ("session_timeout_minutes", "signed_url_minutes", "cycle_days"):
            if data.get(f) is not None and data[f] < 1:
                self.add_error(f, "Must be at least 1.")
        if data.get("signed_url_minutes") and data["signed_url_minutes"] > 60:
            self.add_error("signed_url_minutes", "Keep file links short: 60 minutes at most.")
        return data
