import calendar
import datetime as dt
from dataclasses import dataclass

from mitup_bot.callback_data import DateCallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils.rich_message import RichContent, button_markup, disabled_button_markup, escape_text
from mitup_bot.views.datetime_format import month_name, weekday_names


def standalone(name: str) -> str:
    """A day or month name shown as the only word in its cell starts with a capital, whatever the
    locale does inside a sentence."""
    return name[:1].upper() + name[1:]


@dataclass
class Calendar:
    """The date picker, rendered as a rich table whose day cells are inline buttons.

    The green day is the selected one, or today until a selection exists, so the calendar always
    anchors the reader somewhere. Below the table, one arrow row moves by month and one by year;
    the back arrows never navigate before today's month or year, since earlier months hold no
    schedulable day, so an arrow disappears instead of leading somewhere useless.

    Two dates parameterize the rendering: `month` names the rendered month (any day within it),
    and `today` is today in the acting user's timezone, which bounds the back arrows. Keeping the
    timezone out of this class is what lets the caller decide whose "today" applies. Both
    callbacks already carry the meeting id; the day and arrow buttons only add their date.
    """

    month: dt.date
    selected: dt.date | None
    today: dt.date
    pick_callback: DateCallbackData
    nav_callback: DateCallbackData
    lang: str = "en"

    @property
    def content(self) -> RichContent:
        return RichContent.from_markup(self.table_markup() + self.month_nav_markup() + self.year_nav_markup())

    def table_markup(self) -> str:
        headers = "".join(f"<th>{escape_text(standalone(name))}</th>" for name in weekday_names(self.lang))
        marked = self.selected or self.today

        rows = []
        for week in calendar.Calendar().monthdatescalendar(self.month.year, self.month.month):
            cells = []
            for day in week:
                if day.month != self.month.month:
                    cells.append("<td></td>")
                    continue
                button = ButtonConfig(
                    text=str(day.day),
                    callback_data=self.pick_callback.with_date(day),
                    style="success" if day == marked else None,
                )
                cells.append(f'<td align="center">{button_markup(button)}</td>')
            rows.append(f"<tr>{''.join(cells)}</tr>")

        return f"<table compact><tr>{headers}</tr>{''.join(rows)}</table>"

    def month_nav_markup(self) -> str:
        previous = None
        if (self.month.year, self.month.month) > (self.today.year, self.today.month):
            previous = (dt.date(self.month.year, self.month.month, 1) - dt.timedelta(days=1)).replace(day=1)
        following = (dt.date(self.month.year, self.month.month, 28) + dt.timedelta(days=7)).replace(day=1)
        return self.nav_row_markup(standalone(month_name(self.month.month, self.lang)), previous, following)

    def year_nav_markup(self) -> str:
        previous = dt.date(self.month.year - 1, self.month.month, 1) if self.month.year > self.today.year else None
        return self.nav_row_markup(str(self.month.year), previous, dt.date(self.month.year + 1, self.month.month, 1))

    def nav_row_markup(self, label: str, back_date: dt.date | None, forward_date: dt.date) -> str:
        buttons = []
        if back_date is not None:
            buttons.append(
                button_markup(
                    ButtonConfig(
                        text=ButtonMessages.GO_BACK.text(lang=self.lang),
                        callback_data=self.nav_callback.with_date(back_date),
                    )
                )
            )
        buttons.append(disabled_button_markup(label))
        buttons.append(
            button_markup(
                ButtonConfig(
                    text=ButtonMessages.GO_FORWARD.text(lang=self.lang),
                    callback_data=self.nav_callback.with_date(forward_date),
                )
            )
        )
        return f"<tg-button-row>{''.join(buttons)}</tg-button-row>"
