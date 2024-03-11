import pytest

from mitup_bot.models.users import User


@pytest.fixture(params=[User.by_tg_user_id])
def user_query_list(request):
    return request.param
