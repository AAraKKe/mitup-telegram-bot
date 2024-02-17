import pytest

from mitup_bot.models.users import User


@pytest.fixture(params=[User.find_by_tg_user_id])
def user_query_list(request):
    return request.param
