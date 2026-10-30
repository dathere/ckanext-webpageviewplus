import logging

import pytest


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.events = []

    def emit(self, record):
        if hasattr(record, "webpageviewplus"):
            self.events.append(record.webpageviewplus)


@pytest.fixture
def events_log():
    """The extension's structured events, as dicts, from a handler on the
    events logger itself (caplog's handler is replaced between test phases).
    It's re-enabled too: tests that load CKAN's logging config disable the
    loggers that already exist."""
    logger = logging.getLogger("ckanext.webpageviewplus.events")
    handler = _Collect()
    old = logger.level, logger.disabled
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logger.addHandler(handler)
    yield lambda: list(handler.events)
    logger.removeHandler(handler)
    logger.level, logger.disabled = old


@pytest.fixture
def counts_table(non_clean_db):
    """The plugin's tables. CKAN's test fixtures reset the database once per
    session, which drops plugin tables, so run each of the plugin's
    migrations (its own upgrade(), through alembic) whose table is missing."""
    import importlib.util
    import os

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from ckan import model

    versions = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migration", "webpage_view_plus", "versions")
    modules = []
    for name in os.listdir(versions):
        if name.endswith(".py"):
            spec = importlib.util.spec_from_file_location(
                "wvp_migration_" + name[:-3], os.path.join(versions, name))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
    by_parent = {m.down_revision: m for m in modules}
    module = by_parent.get(None)
    while module is not None:
        with model.meta.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                try:
                    with connection.begin_nested():
                        module.upgrade()
                except sa.exc.ProgrammingError:
                    pass  # its table is already there
        module = by_parent.get(module.revision)


_MISSING = object()


def _reset_storages():
    try:  # CKAN 2.12+
        from ckan.lib import files
    except ImportError:
        return
    files.storages.reset()
    files.storages.collect()


@pytest.fixture
def local_storage(tmp_path, ckan_config):
    """Keep uploads on the local filesystem under tmp_path. CKAN 2.12 stores
    them through ckan.files storages (its test config uses memory storage)
    and ignores ckan.storage_path; earlier versions ignore the ckan.files
    keys. Storages are rebuilt now and again afterwards, so later tests don't
    keep one pointing at this test's deleted folder."""
    values = {
        "ckan.storage_path": str(tmp_path),
        "ckan.files.default_storages.default": "wvp_local",
        "ckan.files.storage.wvp_local.type": "ckan:fs",
        "ckan.files.storage.wvp_local.path": str(tmp_path),
        "ckan.files.storage.wvp_local.initialize": True,
    }
    old = {key: ckan_config.get(key, _MISSING) for key in values}
    ckan_config.update(values)
    _reset_storages()
    yield tmp_path
    # Loading the app adds the storage's declared defaults too: drop them all.
    for key in [k for k in ckan_config if k.startswith("ckan.files.storage.wvp_local.")]:
        ckan_config.pop(key, None)
    for key, value in old.items():
        if value is _MISSING:
            ckan_config.pop(key, None)
        else:
            ckan_config[key] = value
    _reset_storages()
