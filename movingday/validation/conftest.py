"""Options, one test per user in the batch, cached comparisons, and the report's address table."""
import html

import pytest

from movingday import runtime
from movingday.compare import compare_calendar, compare_contacts, compare_drive, compare_mail
from movingday.config import load_config
from movingday.ledger import Ledger
from movingday.mapping import load_mapping
from movingday.safeout import reason_for


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


def pytest_html_report_title(report):
    report.title = "Moving Day sign-off report"


def pytest_html_results_summary(prefix, summary, postfix, session):
    """The only place addresses appear: user key, source address, destination address."""
    rows = "".join(f"<tr><td>{u.key}</td><td>{html.escape(u.source)}</td><td>{html.escape(u.destination)}</td></tr>"
                   for u in _users(session.config))
    prefix.append("<h2>Users in this batch</h2><table><tr><th>User</th><th>Source address</th>"
                  f"<th>Destination address</th></tr>{rows}</table>")


_COLUMNS = ("condition", "expected", "actual")


def pytest_html_results_table_header(cells):
    cells[1:1] = [f"<th>{c.title()}</th>" for c in _COLUMNS]


def pytest_html_results_table_row(report, cells):
    """Condition, expected and actual from the check's user_properties (counts and plain values only)."""
    props = dict(report.user_properties)
    if report.failed and "actual" not in props:
        props["actual"] = "not measured: the check could not run"
    cells[1:1] = [f'<td class="col-{c}" style="white-space:pre-line">{html.escape(str(props.get(c, "")))}</td>'
                  for c in _COLUMNS]


def pytest_collection_modifyitems(items):
    """Condition (the docstring) and any data-independent expected value, known before any fixture runs."""
    for item in items:
        expected = getattr(item.module, "EXPECTED", {}).get(item.originalname, "\u2014")
        item.user_properties += [("condition", " ".join(item.function.__doc__.split())), ("expected", expected)]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """No captured output in the report; any error (check, fixture or teardown) shows its reason only."""
    report = yield
    report.sections = []  # pytest-html shows captured stdout/stderr/log whatever --show-capture says
    exc = call.excinfo.value if call.excinfo else None
    if report.failed and isinstance(exc, Exception) and not (isinstance(exc, AssertionError) and call.when == "call"):
        report.longrepr = f"error: {reason_for(exc)}"
        report.user_properties = [*report.user_properties, ("actual", report.longrepr)]
    return report


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
def results(cfg, apis, ledger, mapping):
    """One MailResult, CalResult, ContactResult and DriveResult per user; a comparison that raised is not repeated."""
    cache = {}

    def get(kind, u):
        if (kind, u.key) not in cache:
            try:
                if kind == "mail":
                    pre = int(ledger.get_meta(u.key, "preexisting") or 0)
                    cache[kind, u.key] = compare_mail(apis.src_gmail, apis.dst_gmail, u.source, u.destination, pre)
                elif kind == "contacts":
                    cache[kind, u.key] = compare_contacts(
                        apis.src_people, apis.dst_people, ledger, u.key, u.source, u.destination)
                elif kind == "drive":
                    cache[kind, u.key] = compare_drive(
                        apis.src_drive, apis.dst_drive, ledger, mapping, u.key, u.source, u.destination,
                        cfg.dest_domain)
                else:
                    cache[kind, u.key] = compare_calendar(
                        apis.src_cal, apis.dst_cal, ledger, mapping, u.key, u.source, u.destination,
                        cfg.dest_domain)
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


@pytest.fixture
def con(results, user):
    return results("contacts", user)


@pytest.fixture
def drv(results, user):
    return results("drive", user)
