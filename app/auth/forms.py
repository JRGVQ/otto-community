"""Auth forms (Flask-WTF)."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import Email, EqualTo, InputRequired, Length, Optional

from app.domains import normalize_email


def _clean(value):
    """Trim what the browser sent. A name or company pasted with a trailing space is
    the same name — storing the space only makes it look wrong later."""
    return value.strip() if isinstance(value, str) else value


# Applied to every e-mail field in the app: a pasted address keeps its stray space,
# and without this the visitor is told their perfectly good address is invalid.
EMAIL_FILTERS = [normalize_email]
TEXT_FILTERS = [_clean]


class RegisterForm(FlaskForm):
    # Organisation name. Optional: without one, the person's own name becomes the
    # workspace name (a one-person environment).
    company = StringField("Bedrijfsnaam", filters=TEXT_FILTERS, validators=[Optional(), Length(min=2, max=128)])
    name = StringField("Naam", filters=TEXT_FILTERS, validators=[InputRequired(), Length(min=2, max=128)])
    # check_deliverability=False: validate SYNTAX only, never DNS/MX. A network-restricted
    # container can't resolve MX records, and a deliverability check there would reject every
    # real address with "Invalid email address". Otto verifies reachability via the confirmation
    # mail instead, so a bad domain simply never activates.
    email = StringField("E-mail", filters=EMAIL_FILTERS,
                        validators=[InputRequired(), Email(check_deliverability=False), Length(max=255)])
    password = PasswordField("Wachtwoord", validators=[InputRequired(), Length(min=6, max=128)])
    confirm = PasswordField("Bevestig wachtwoord", validators=[InputRequired(), EqualTo("password", message="Wachtwoorden komen niet overeen.")])


class SetupForm(RegisterForm):
    """First-run setup: the first organisation + the instance administrator."""
    company = StringField("Bedrijfsnaam", filters=TEXT_FILTERS, validators=[InputRequired(), Length(min=2, max=128)])


class LoginForm(FlaskForm):
    email = StringField("E-mail", filters=EMAIL_FILTERS,
                        validators=[InputRequired(), Email(check_deliverability=False)])
    password = PasswordField("Wachtwoord", validators=[InputRequired()])
    remember = BooleanField("Aangemeld blijven")


class ForgotPasswordForm(FlaskForm):
    email = StringField("E-mail", filters=EMAIL_FILTERS,
                        validators=[InputRequired(), Email(check_deliverability=False), Length(max=255)])


class ResetPasswordForm(FlaskForm):
    password = PasswordField("Nieuw wachtwoord", validators=[InputRequired(), Length(min=6, max=128)])
    confirm = PasswordField("Bevestig wachtwoord", validators=[InputRequired(), EqualTo("password", message="Wachtwoorden komen niet overeen.")])


class AcceptInviteForm(FlaskForm):
    name = StringField("Naam", filters=TEXT_FILTERS, validators=[InputRequired(), Length(min=2, max=128)])
    password = PasswordField("Wachtwoord", validators=[InputRequired(), Length(min=6, max=128)])
    confirm = PasswordField("Bevestig wachtwoord", validators=[InputRequired(), EqualTo("password", message="Wachtwoorden komen niet overeen.")])
