"""Options, one test per user in the batch, cached comparisons, and the report's address table."""
import html

import pytest

from wsmigrate import runtime
from wsmigrate.compare import compare_calendar, compare_mail
from wsmigrate.config import load_config
from wsmigrate.ledger import Ledger
from wsmigrate.mapping import load_mapping


def pytest_addoption(parser):
    parser.addoption("--batch")
    parser.addoption("--config")
    parser.addoption("--expect-changes", action="store_true")


def _users(config):
    try:
        return load_mapping(load_config(config.getoption("--config")).mapping_file).batch(
            config.getoption("--batch"))
    except Exception:
        return []  # test_mapping_valid reports it


def pytest_generate_tests(metafunc):
    if "user" in metafunc.fixturenames:
        users = _users(metafunc.config)
        metafunc.parametrize("user", users, ids=[u.key for u in users])


def pytest_html_results_summary(prefix, summary, postfix, session):
    """The only place addresses appear: user key, source address, destination address."""
    rows = "".join(f"<tr><td>{u.key}</td><td>{html.escape(u.source)}</td><td>{html.escape(u.destination)}</td></tr>"
                   for u in _users(session.config))
    prefix.append("<h2>Users in this batch</h2><table><tr><th>User</th><th>Source address</th>"
                  f"<th>Destination address</th></tr>{rows}</table>")


@pytest.fixture(scope="session")
def cfg(request):
    return load_config(request.config.getoption("--config"))


@pytest.fixture(scope="session")
def mapping(cfg):
    return load_mapping(cfg.mapping_file)


@pytest.fixture(scope="session")
def apis(cfg):
    return runtime.get_apis(cfg)


@pytest.fixture(scope="session")
def ledger(cfg):
    led = Ledger(cfg.ledger_path)
    yield led
    led.close()


@pytest.fixture(scope="session")
def results(apis, ledger, mapping):
    """One MailResult and one CalResult per user; a comparison that raised is not repeated."""
    cache = {}

    def get(kind, u):
        if (kind, u.key) not in cache:
            try:
                if kind == "mail":
                    pre = int(ledger.get_meta(u.key, "preexisting") or 0)
                    cache[kind, u.key] = compare_mail(apis.src_gmail, apis.dst_gmail, u.source, u.destination, pre)
                else:
                    cache[kind, u.key] = compare_calendar(
                        apis.src_cal, apis.dst_cal, ledger, mapping, u.key, u.source, u.destination)
            except Exception as exc:
                cache[kind, u.key] = exc
        r = cache[kind, u.key]
        if isinstance(r, Exception):
            raise r
        return r

    return get


@pytest.fixture
def mail(results, user):
    return results("mail", user)


@pytest.fixture
def cal(results, user):
    return results("cal", user)
