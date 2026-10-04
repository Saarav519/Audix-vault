from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Login ID", max_length=64,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "autocapitalize": "none"}),
    )
    password = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )
    error_messages = {
        "invalid_login": "That login ID and password do not match, or the account is not active.",
        "inactive": "This account is not active.",
    }


class ChangePasswordForm(PasswordChangeForm):
    pass
