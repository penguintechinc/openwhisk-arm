"""Action model for OpenWhisk serverless functions."""

from pydal import Field


def define_action_table(db):
    """
    Define the Action table schema.

    Actions represent serverless functions that can be invoked.
    They contain code (stored in MinIO) and execution metadata.
    """
    db.define_table(
        'action',
        Field('namespace_id', 'reference namespace', ondelete='CASCADE', notnull=True),
        Field('package_id', 'reference package', ondelete='SET NULL'),
        Field('name', 'string', length=256, notnull=True),
        Field('version', 'string', default='0.0.1', notnull=True),
        Field('publish', 'boolean', default=False, notnull=True),

        # Execution fields
        Field('exec_kind', 'string', notnull=True),
        Field('exec_code_hash', 'string', length=64, notnull=True),
        Field('exec_image', 'string', length=512),
        Field('exec_binary', 'boolean', default=False, notnull=True),
        Field('exec_main', 'string', default='main', notnull=True),
        Field('exec_components', 'json'),

        # Limits
        Field('limits_timeout', 'integer', default=60000, notnull=True),
        Field('limits_memory', 'integer', default=256, notnull=True),
        Field('limits_logs', 'integer', default=10, notnull=True),
        Field('limits_concurrency', 'integer', default=1, notnull=True),

        # Metadata
        Field('parameters', 'json', default={}),
        Field('annotations', 'json', default={}),
        Field('created_at', 'datetime', default=lambda: db.now),
        Field('updated_at', 'datetime', update=lambda: db.now, default=lambda: db.now),
    )

    # Unique constraint on (namespace_id, package_id, name)
    db.action._create_unique_index('namespace_id', 'package_id', 'name')

    # Indexes for performance
    db.executesql('CREATE INDEX IF NOT EXISTS idx_action_namespace ON action(namespace_id);')
    db.executesql('CREATE INDEX IF NOT EXISTS idx_action_exec_kind ON action(exec_kind);')
