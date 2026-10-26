import pytest
from sqlalchemy import create_engine

from backend.src import household
from backend.src.db.session import migrate_member_columns


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch, tmp_path):
    # Settings come from the environment only, never a real frontend/.env.
    monkeypatch.setattr(household, "REPO_ROOT", tmp_path)
    for key in ("HOUSEHOLD_MEMBERS", "SELF_NAME", "STATEMENT_CARDS", "BANK_ACCOUNTS"):
        monkeypatch.delenv(key, raising=False)
    household.members.cache_clear()
    household.self_name.cache_clear()
    yield
    household.members.cache_clear()
    household.self_name.cache_clear()


def configure(monkeypatch, members, **settings):
    monkeypatch.setenv("HOUSEHOLD_MEMBERS", members)
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    household.members.cache_clear()
    household.self_name.cache_clear()


def test_members_and_their_cards(monkeypatch):
    configure(monkeypatch, "Jamie, Robin", STATEMENT_CARDS="Amex,Visa", BANK_ACCOUNTS="Checking")
    assert household.members() == ("Jamie", "Robin")
    assert (household.member_a(), household.member_b()) == ("Jamie", "Robin")
    assert household.member_for("Robin Amex") == "Robin"
    assert household.member_for("smith_robin") == "Robin"
    assert household.member_for("Chase") is None
    assert ("Robin Visa", "credit_card") in household.statement_sources()
    assert ("Jamie Checking", "bank_account") in household.statement_sources()


def test_a_one_person_household(monkeypatch):
    configure(monkeypatch, "Jamie")
    assert household.members() == ("Jamie",) and household.member_b() is None
    assert household.self_name() == ("jamie",)


def test_legacy_spend_columns_are_renamed_by_member_order(monkeypatch, tmp_path):
    configure(monkeypatch, "Jamie,Robin")
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE expenses (id INTEGER PRIMARY KEY, robin_spend REAL, jamie_spend REAL)")
        c.exec_driver_sql("INSERT INTO expenses VALUES (1, 2.5, 7.5)")
    migrate_member_columns(engine)
    migrate_member_columns(engine)  # idempotent
    with engine.begin() as c:
        assert c.exec_driver_sql("SELECT member_a_spend, member_b_spend FROM expenses").one() == (7.5, 2.5)
