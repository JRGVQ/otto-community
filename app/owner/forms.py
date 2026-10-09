"""Owner backoffice forms."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import Email, InputRequired, Length

from app.auth.forms import EMAIL_FILTERS, TEXT_FILTERS


class CreateCompanyForm(FlaskForm):
    company = StringField("Bedrijfsnaam", filters=TEXT_FILTERS, validators=[InputRequired(), Length(min=2, max=128)])
    owner_name = StringField("Naam beheerder", filters=TEXT_FILTERS, validators=[InputRequired(), Length(min=2, max=128)])
    # check_deliverability=False for the same reason as the signup form: Otto only checks
    # syntax here, never DNS/MX — a host that can't resolve MX would otherwise reject every
    # real address. See app/auth/forms.py.
    owner_email = StringField("E-mail beheerder", filters=EMAIL_FILTERS,
                              validators=[InputRequired(), Email(check_deliverability=False)])
    password = PasswordField("Tijdelijk wachtwoord", validators=[InputRequired(), Length(min=6)])
