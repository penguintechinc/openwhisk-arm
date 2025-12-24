"""
Trigger model for OpenWhisk event sources.

OpenWhisk triggers represent named event channels that can be fired by
external event sources (feeds) or manually. When a trigger fires, all
associated rules evaluate and may invoke their actions.

Triggers support:
- Parameters: Default key-value pairs merged with event data
- Annotations: Metadata for documentation and tooling
- Feeds: External event sources (e.g., /whisk.system/alarms/alarm)
"""


def define_trigger(db):
    """
    Define the Trigger model in PyDAL.

    Args:
        db: PyDAL database instance

    Returns:
        None - defines db.trigger table
    """
    db.define_table(
        'trigger',
        db.Field('namespace_id', 'reference namespace',
                 ondelete='CASCADE',
                 notnull=True,
                 label='Namespace'),
        db.Field('name', 'string',
                 length=256,
                 notnull=True,
                 label='Trigger Name'),
        db.Field('version', 'string',
                 length=32,
                 default='0.0.1',
                 notnull=True,
                 label='Version'),
        db.Field('publish', 'boolean',
                 default=False,
                 notnull=True,
                 label='Public'),
        db.Field('parameters', 'json',
                 default={},
                 notnull=True,
                 label='Default Parameters'),
        db.Field('annotations', 'json',
                 default={},
                 notnull=True,
                 label='Annotations'),
        db.Field('feed', 'string',
                 length=512,
                 label='Feed Action'),
        db.Field('created_at', 'datetime',
                 default=db.now,
                 writable=False,
                 label='Created At'),
        db.Field('updated_at', 'datetime',
                 default=db.now,
                 update=db.now,
                 writable=False,
                 label='Updated At'),
    )

    # Unique constraint on namespace + name
    db.trigger.namespace_id.requires = db.IS_IN_DB(
        db, 'namespace.id', '%(name)s'
    )
    db.trigger.name.requires = [
        db.IS_NOT_EMPTY(),
        db.IS_LENGTH(256)
    ]
