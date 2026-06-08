# Database Migrations

Quant-Trade now has an Alembic scaffold for schema changes.

## Current Baseline

- Alembic head revision: `20260605_0001`
- Initial revision: `alembic/versions/20260605_0001_initial_schema.py`
- Metadata source: `app.db.models.Base.metadata`

The existing `app.db.schema.initialize_schema()` remains as a compatibility
bootstrap for local startup and tests. New schema changes should be represented
as Alembic revisions instead of relying on `create_all` to alter existing
databases.

## Commands

Install dev dependencies first:

```bash
pip install -r requirements-dev.txt
```

Check migration heads:

```bash
alembic heads
```

Apply migrations using the configured `DATABASE_URL`:

```bash
alembic upgrade head
```

Create a new migration after changing models:

```bash
alembic revision --autogenerate -m "describe change"
```

Review generated migrations before applying them, especially on SQLite where
some schema operations require batch mode or table recreation.
