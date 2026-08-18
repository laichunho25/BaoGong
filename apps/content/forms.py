"""The one input the education library takes from a reader.

A question is not stored anywhere (COMPLIANCE section 4): it goes to the
Advisor agent, comes back as an answer, and the only record left behind is the
``AgentRun``, whose input is a hash. So this form validates length and nothing
else - there is no model behind it to protect.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.form_styles import INPUT_CLASSES

#: Long enough for a real question, short enough that nobody pastes a contract
#: into a box that sends its contents to a model.
MAX_QUESTION_CHARS = 200


class AskForm(forms.Form):
    question = forms.CharField(
        label=_("问一个关于香港开公司的问题"),
        min_length=4,
        max_length=MAX_QUESTION_CHARS,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("例如：注册香港公司要多久？政府收费是多少？"),
                "autocomplete": "off",
            }
        ),
        help_text=_("回答只会引用本平台自己写的指南；引用不到就直接说没有可靠答案。"),
    )
