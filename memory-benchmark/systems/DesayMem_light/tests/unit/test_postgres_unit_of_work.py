import pytest

from desaymem_light.adapters.postgres.unit_of_work import PostgresUnitOfWork


class FakeConnection:
    def __init__(self) -> None:
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, sql, parameters=None):
        self.executed.append((sql, parameters))
        return self

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeConnectionContext:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.exited = False

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        self.exited = True


class FakePool:
    def __init__(self) -> None:
        self.raw_connection = FakeConnection()
        self.context = FakeConnectionContext(self.raw_connection)

    def connection(self):
        return self.context


@pytest.mark.asyncio
async def test_unit_of_work_commits_explicitly() -> None:
    pool = FakePool()
    async with PostgresUnitOfWork(pool) as unit:
        assert unit.messages.connection is pool.raw_connection
        await unit.commit()

    assert pool.raw_connection.commits == 1
    assert pool.raw_connection.rollbacks == 0
    assert pool.context.exited


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_without_commit() -> None:
    pool = FakePool()
    async with PostgresUnitOfWork(pool):
        pass

    assert pool.raw_connection.commits == 0
    assert pool.raw_connection.rollbacks == 1
